package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const wakeSourceSHA256 = "05a6632285c69795d5c59667697a1b26f3ccd0abe36156fc70168dd8585351c2"

// No installation path or helper bytes are accepted from MCP requests.
type wakeConfig struct {
	SourceClient   string `json:"source_client"`
	Detector       string `json:"detector"`
	DetectorSHA256 string `json:"detector_sha256"`
	ModelSHA256    string `json:"model_sha256"`
}

func (c *wakeConfig) valid() bool {
	return c != nil && filepath.IsAbs(c.SourceClient) && filepath.Clean(c.SourceClient) == c.SourceClient &&
		filepath.IsAbs(c.Detector) && filepath.Clean(c.Detector) == c.Detector &&
		hex64(c.DetectorSHA256) && hex64(c.ModelSHA256)
}

type wakeEvent struct {
	Model       string  `json:"model"`
	TimeMS      int     `json:"t_ms"`
	Probability float64 `json:"probability"`
	Trusted     bool    `json:"trusted"`
}

func parseWakeOutput(raw []byte, modelPin string, pcmBytes int) ([]wakeEvent, bool) {
	lines := bytes.Split(bytes.TrimSpace(raw), []byte("\n"))
	if len(raw) > 16384 || len(lines) < 2 || len(lines) > 66 || pcmBytes <= 0 || pcmBytes > 640000 {
		return nil, false
	}
	var ready struct {
		Event     string  `json:"event"`
		Rate      int     `json:"rate"`
		Threshold float64 `json:"threshold"`
		Window    int     `json:"window"`
		Cooldown  int     `json:"cooldown_ms"`
		Gain      int     `json:"gain_db"`
		Models    []struct {
			Name   string `json:"name"`
			SHA256 string `json:"sha256"`
			Stride int    `json:"stride"`
			Arena  int    `json:"arena_bytes"`
		} `json:"models"`
	}
	if !decode(lines[0], &ready) || ready.Event != "ready" || ready.Rate != 16000 || ready.Threshold != 0.98 ||
		ready.Window != 5 || ready.Cooldown != 2500 || ready.Gain != 0 || len(ready.Models) < 1 || len(ready.Models) > 8 {
		return nil, false
	}
	counts, last := map[string]int{}, map[string]int{}
	matched := false
	for _, m := range ready.Models {
		if _, exists := counts[m.Name]; exists || !hex64(m.SHA256) || m.Stride < 1 || m.Stride > 10 || m.Arena < 0 || m.Arena > 160*1024 {
			return nil, false
		}
		counts[m.Name] = 0
		if m.Name == "komp" && m.SHA256 == modelPin {
			matched = true
		}
	}
	if !matched {
		return nil, false
	}
	events := []wakeEvent{}
	for _, line := range lines[1 : len(lines)-1] {
		var e struct {
			Event       string  `json:"event"`
			Model       string  `json:"model"`
			TimeMS      int     `json:"t_ms"`
			Probability float64 `json:"probability"`
			Peak        float64 `json:"peak"`
			Last        float64 `json:"last"`
			Trusted     *bool   `json:"trusted"`
		}
		if !decode(line, &e) || e.Event != "wake" || e.Trusted == nil || *e.Trusted ||
			e.TimeMS <= last[e.Model] || e.TimeMS > pcmBytes/320*10 || e.Probability < 0.98 || e.Probability > 1 ||
			e.Peak < e.Probability || e.Peak > 1 || e.Last < 0 || e.Last > 1 {
			return nil, false
		}
		if _, exists := counts[e.Model]; !exists {
			return nil, false
		}
		counts[e.Model]++
		last[e.Model] = e.TimeMS
		if e.Model == "komp" {
			events = append(events, wakeEvent{e.Model, e.TimeMS, e.Probability, false})
		}
	}
	var end struct {
		Event      string `json:"event"`
		Reason     string `json:"reason"`
		AudioMS    int    `json:"audio_ms"`
		Detections []int  `json:"detections"`
	}
	if !decode(lines[len(lines)-1], &end) || end.Event != "end" || end.Reason != "eof" ||
		end.AudioMS != pcmBytes/320*10 || len(end.Detections) != len(ready.Models) {
		return nil, false
	}
	for i, m := range ready.Models {
		if end.Detections[i] != counts[m.Name] {
			return nil, false
		}
	}
	return events, true
}

// Observe the fixed listener's ownership; opening a socket is not enough evidence.
func wakePortOwned() bool {
	raw, err := readLocal("/proc/net/tcp", 65536)
	if err != nil {
		return false
	}
	inode := ""
	for _, line := range strings.Split(string(raw), "\n") {
		f := strings.Fields(line)
		if len(f) > 9 && (f[1] == "0100007F:267A" || f[1] == "00000000:267A") && f[3] == "0A" {
			if inode != "" {
				return false
			}
			inode = f[9]
		}
	}
	if inode == "" {
		return false
	}
	dirs, err := os.ReadDir("/proc")
	if err != nil {
		return false
	}
	for _, dir := range dirs {
		if _, err := strconv.ParseUint(dir.Name(), 10, 32); err != nil {
			continue
		}
		base := "/proc/" + dir.Name()
		name, _ := readLocal(base+"/comm", 64)
		if string(name) != "maind\n" {
			continue
		}
		fds, _ := os.ReadDir(base + "/fd")
		for _, fd := range fds {
			link, _ := os.Readlink(base + "/fd/" + fd.Name())
			if link == "socket:["+inode+"]" {
				return true
			}
		}
	}
	return false
}

type wakeOutput struct{ bytes.Buffer }

func (b *wakeOutput) Write(p []byte) (int, error) {
	if b.Len()+len(p) > 16384 {
		return 0, errors.New("Wake output limit")
	}
	return b.Buffer.Write(p)
}

type wakePCM struct {
	io.Writer
	count int
}

func (p *wakePCM) Write(b []byte) (int, error) {
	if p.count+len(b) > 640000 {
		return 0, errors.New("Wake audio limit")
	}
	n, err := p.Writer.Write(b)
	p.count += n
	return n, err
}

func pinnedWakeFile(path, pin string) (*os.File, error) {
	f, err := trustedFile(path, false)
	if err != nil {
		return nil, err
	}
	digest, err := hashFile(f, 2<<20)
	if err != nil || digest != pin {
		f.Close()
		return nil, errors.New("Wake artifact changed")
	}
	return f, nil
}

func runWakeChildren(c *wakeConfig, seconds int) ([]wakeEvent, bool) {
	source, err := pinnedWakeFile(c.SourceClient, wakeSourceSHA256)
	if err != nil {
		return nil, false
	}
	defer source.Close()
	detector, err := pinnedWakeFile(c.Detector, c.DetectorSHA256)
	if err != nil {
		return nil, false
	}
	defer detector.Close()
	return runWakeFiles(source, detector, c.ModelSHA256, seconds)
}

func runWakeFiles(source, detector *os.File, modelPin string, seconds int) ([]wakeEvent, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(seconds+5)*time.Second)
	defer cancel()
	det := exec.CommandContext(ctx, "/proc/self/fd/3", "--max-seconds", "30", "--gain-db", "0")
	src := exec.CommandContext(ctx, "/proc/self/fd/3", "--stream", "9850", strconv.Itoa(seconds))
	for _, cmd := range []*exec.Cmd{src, det} {
		cmd.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "LC_ALL=C", "HOME=/nonexistent"}
		cmd.WaitDelay = time.Second
	}
	det.ExtraFiles, src.ExtraFiles = []*os.File{detector}, []*os.File{source}
	var output, report wakeOutput
	det.Stdout, det.Stderr, src.Stderr = &output, io.Discard, &report
	input, err := det.StdinPipe()
	if err != nil {
		return nil, false
	}
	defer input.Close()
	pcm := &wakePCM{Writer: input}
	src.Stdout = pcm
	if det.Start() != nil {
		return nil, false
	}
	srcErr := src.Run()
	input.Close()
	if srcErr != nil {
		cancel()
	}
	detErr := det.Wait()
	if srcErr != nil || detErr != nil {
		return nil, false
	}
	var r struct {
		Outcome      string            `json:"outcome"`
		Error        string            `json:"error_code"`
		Connected    bool              `json:"connected"`
		Sent         bool              `json:"subscribe_sent"`
		Unsubscribed bool              `json:"unsubscribe_sent"`
		Frames       int               `json:"frames"`
		AudioFrames  int               `json:"audio_frames"`
		OtherFrames  int               `json:"other_frames"`
		Bytes        int               `json:"bytes"`
		First        int               `json:"first_audio_ms"`
		Elapsed      int               `json:"elapsed_ms"`
		Exported     bool              `json:"audio_exported"`
		Channels     []json.RawMessage `json:"channels"`
		Other        []json.RawMessage `json:"other"`
	}
	if !decode(report.Bytes(), &r) || r.Outcome != "complete" || r.Error != "none" || !r.Connected || !r.Sent ||
		!r.Unsubscribed || r.Exported || r.Bytes < pcm.count || r.Bytes > 32<<20 ||
		r.Frames != r.AudioFrames+r.OtherFrames || r.AudioFrames < 1 || r.OtherFrames < 0 ||
		r.First < 0 || r.Elapsed < 0 || r.Elapsed > (seconds+3)*1000 || len(r.Channels) != 1 || len(r.Other) != 0 {
		return nil, false
	}
	var channel struct {
		Name     string `json:"name"`
		Type     int    `json:"type"`
		Rate     int    `json:"sample_rate"`
		Format   int    `json:"format"`
		Frames   int    `json:"frames"`
		Bytes    int    `json:"bytes"`
		Duration int    `json:"duration_ms"`
		Estimate int    `json:"bytes_per_sample_estimate"`
		Peak     int    `json:"peak_s16"`
		Square   int64  `json:"mean_square_s16"`
	}
	if !decode(r.Channels[0], &channel) || channel.Name != "vqe_0" || channel.Type != 6 ||
		channel.Rate != 16000 || channel.Format != 1 || channel.Bytes != pcm.count ||
		channel.Bytes%2 != 0 || channel.Frames != r.AudioFrames {
		return nil, false
	}
	return parseWakeOutput(output.Bytes(), modelPin, pcm.count)
}

func (n *nativeRuntime) listenWake(seconds int, expected ...string) map[string]any {
	if !n.lock.TryLock() {
		return blocked("Another native action is active")
	}
	defer n.finishAction()
	state := n.consumeState(expected)
	if state == "" {
		return blocked("Fresh state-bound preview required")
	}
	c, _, err := n.policy()
	if err != nil || c.Wake == nil || !c.Wake.valid() || !c.permits("wake.listen") || seconds < 5 || seconds > 20 ||
		os.Geteuid() != 0 || n.firmware.read(firmwarePath) != c.FirmwareSHA256 || !wakePortOwned() || !n.stateMatches(state) {
		return blocked("Reviewed wake source, artifacts or policy unavailable")
	}
	events, complete := runWakeChildren(c.Wake, seconds)
	unchanged := n.stateMatches(state)
	result := map[string]any{"outcome": "unknown", "events": []wakeEvent{}, "trusted": false,
		"do_not_retry": true, "audio_exported": false, "quasar_unchanged": unchanged,
		"cleanup_verified": true}
	if complete && unchanged {
		result["outcome"], result["events"] = "complete", events
	}
	return result
}
