package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// ponytail: fixed 10-point step; make it policy data if owners need finer steps.
const volumeStep = 10

var serviceNames = []string{"maind", "quasar_launcher2", "dropbear", "shairport-sync", "updatesd"}

// Embedded in config; encoding/json promotes these fields (unknown ones are still rejected).
type audioPolicy struct {
	Volume *volumeConfig     `json:"volume,omitempty"`
	Media  map[string]string `json:"media,omitempty"`
}

// Private, operator-reviewed mixer mapping. Absent mapping means no volume action.
type volumeConfig struct {
	Amixer       string `json:"amixer"`
	AmixerSHA256 string `json:"amixer_sha256"`
	Card         int    `json:"card"`
	Control      string `json:"control"`
	MaxPercent   int    `json:"max_percent"`
}

type volumeRequest struct {
	Percent    *int   `json:"percent,omitempty"`
	Step       string `json:"step,omitempty"`
	MaxPercent *int   `json:"max_percent,omitempty"`
}

type mediaPayload struct {
	Asset   string `json:"asset"`
	Seconds int    `json:"duration_seconds"`
	WAV     string `json:"wav_base64"`
	SHA256  string `json:"sha256"`
}

func alias(value string) bool {
	if len(value) < 1 || len(value) > 32 {
		return false
	}
	for i, c := range value {
		if !(c >= 'a' && c <= 'z' || i > 0 && (c >= '0' && c <= '9' || c == '-')) {
			return false
		}
	}
	return true
}

// Name screening is defense in depth; parseMixer's capability check is the real guard.
func playbackControl(name string) bool {
	if len(name) < 1 || len(name) > 43 || strings.TrimSpace(name) != name {
		return false
	}
	for i, c := range name {
		if !(c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || i > 0 && (c >= '0' && c <= '9' || c == ' ' || c == '-' || c == '_')) {
			return false
		}
	}
	lower := strings.ToLower(name)
	for _, word := range []string{"capture", "mic", "adc", "boost", "gain", "input", "loopback"} {
		if strings.Contains(lower, word) {
			return false
		}
	}
	return true
}

func (v *volumeConfig) valid() bool {
	return v != nil && filepath.IsAbs(v.Amixer) && filepath.Clean(v.Amixer) == v.Amixer && hex64(v.AmixerSHA256) &&
		v.Card >= 0 && v.Card <= 7 && v.MaxPercent >= 0 && v.MaxPercent <= 100 && playbackControl(v.Control)
}

func validMedia(media map[string]string) bool {
	if len(media) < 1 || len(media) > 32 {
		return false
	}
	for name, digest := range media {
		if !alias(name) || !hex64(digest) {
			return false
		}
	}
	return true
}

func validVolume(r volumeRequest) bool {
	if r.Percent != nil {
		return r.Step == "" && r.MaxPercent == nil && *r.Percent >= 0 && *r.Percent <= 100
	}
	return (r.Step == "quieter" || r.Step == "louder") && r.MaxPercent != nil &&
		*r.MaxPercent >= 0 && *r.MaxPercent <= 100
}

// A step never raises above the ceiling and "quieter" never raises at all.
func volumeTarget(r volumeRequest, current, ceiling int) (int, bool) {
	switch {
	case r.Percent != nil:
		return *r.Percent, *r.Percent <= ceiling
	case r.Step == "louder":
		target := min(current+volumeStep, ceiling)
		return target, target > current
	case r.Step == "quieter":
		target := min(max(current-volumeStep, 0), ceiling)
		return target, target < current
	}
	return 0, false
}

func percentValues(line string) ([]int, bool) {
	var values []int
	for {
		start := strings.IndexByte(line, '[')
		if start < 0 {
			return values, true
		}
		line = line[start+1:]
		end, closing := strings.Index(line, "%]"), strings.IndexByte(line, ']')
		if end < 0 || end+1 != closing {
			continue
		}
		digits := line[:end]
		if len(digits) < 1 || len(digits) > 3 || strings.Trim(digits, "0123456789") != "" {
			return nil, false
		}
		value, _ := strconv.Atoi(digits)
		if value > 100 {
			return nil, false
		}
		values = append(values, value)
	}
}

// Only one exact playback-only control with equal channel percentages is accepted.
func parseMixer(output []byte, control string) (int, bool) {
	headers, playback, percent := 0, false, -1
	for _, line := range strings.Split(string(output), "\n") {
		line = strings.TrimSpace(line)
		switch {
		case line == "":
		case strings.HasPrefix(line, "Simple mixer control "):
			headers++
			if line != "Simple mixer control '"+control+"',0" {
				return 0, false
			}
		case strings.HasPrefix(line, "Capabilities:"):
			for _, capability := range strings.Fields(line)[1:] {
				switch capability {
				case "pvolume":
					playback = true
				case "pvolume-joined", "pswitch", "pswitch-joined":
				default:
					return 0, false
				}
			}
		case strings.Contains(line, "Capture"):
			return 0, false
		case strings.Contains(line, ": Playback "):
			values, ok := percentValues(line)
			if !ok || len(values) > 1 {
				return 0, false
			}
			for _, value := range values {
				if percent >= 0 && value != percent {
					return 0, false
				}
				percent = value
			}
		}
	}
	return percent, headers == 1 && playback && percent >= 0
}

// The verified open descriptor, not a mutable path, selects the reviewed mixer utility.
func runMixer(mixer *os.File, v *volumeConfig, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "/proc/self/fd/3", append([]string{"-c", strconv.Itoa(v.Card)}, args...)...)
	command.ExtraFiles, command.Stdin, command.Stderr = []*os.File{mixer}, nil, io.Discard
	// Same environment as the reviewed SSH helper; vendor ALSA lives under quasar/libs.
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "LC_ALL=C", "HOME=/nonexistent",
		"LD_LIBRARY_PATH=/system/vendor/quasar:/system/vendor/quasar/libs"}
	command.WaitDelay = time.Second
	output := &boundedOutput{}
	command.Stdout = output
	err := command.Run()
	return output.Bytes(), err
}

func readMixer(mixer *os.File, v *volumeConfig) (int, bool) {
	output, err := runMixer(mixer, v, "sget", v.Control)
	if err != nil {
		return 0, false
	}
	return parseMixer(output, v.Control)
}

func changeVolume(mixer *os.File, v *volumeConfig, r volumeRequest) map[string]any {
	before, ok := readMixer(mixer, v)
	if !ok {
		return blocked("Reviewed playback-only mixer control unavailable")
	}
	ceiling := v.MaxPercent
	if r.MaxPercent != nil {
		ceiling = min(ceiling, *r.MaxPercent)
	}
	target, ok := volumeTarget(r, before, ceiling)
	if !ok {
		return blocked("No volume change within the reviewed ceiling")
	}
	_, err := runMixer(mixer, v, "sset", v.Control, strconv.Itoa(target)+"%")
	after, verified := readMixer(mixer, v)
	result := map[string]any{"outcome": "applied", "previous_percent": before, "percent": after}
	if !verified {
		result["percent"] = nil
	}
	if err != nil || !verified || after != target {
		result["outcome"] = "unknown"
	}
	return result
}

func (n *nativeRuntime) pinnedMixer(c config) (*os.File, bool) {
	if !c.Volume.valid() {
		return nil, false
	}
	mixer, err := trustedFile(c.Volume.Amixer, false)
	if err != nil {
		return nil, false
	}
	digest, err := hashFile(mixer, 4<<20)
	if err != nil || digest != c.Volume.AmixerSHA256 {
		mixer.Close()
		return nil, false
	}
	return mixer, true
}

func (n *nativeRuntime) setVolume(r volumeRequest, expected ...string) map[string]any {
	if !n.lock.TryLock() {
		return blocked("Another native action is active")
	}
	defer n.finishAction()
	state := n.consumeState(expected)
	if state == "" {
		return blocked("Fresh state-bound preview required")
	}
	c, stamp, err := n.policy()
	if err != nil || !c.permits("audio.volume") || !validVolume(r) {
		return blocked("Volume is not preauthorized by the resident policy")
	}
	if os.Geteuid() != 0 || !quasarRunning() || hashPath(firmwarePath) != c.FirmwareSHA256 {
		return blocked("Reviewed firmware or running Quasar unavailable")
	}
	mixer, ok := n.pinnedMixer(c)
	if !ok {
		return blocked("Reviewed mixer utility unavailable or changed")
	}
	defer mixer.Close()
	_, fresh, err := n.policy()
	if err != nil || !bytes.Equal(stamp, fresh) || !n.stateMatches(state) {
		return blocked("Private policy changed or preview expired")
	}
	result := changeVolume(mixer, c.Volume, r)
	if result["outcome"] == "blocked" {
		return result
	}
	result["quasar_running"], result["do_not_retry"] = quasarRunning(), true
	result["evidence_class"], result["physical_effects_verified"] = "analytical", false
	return result
}

func (n *nativeRuntime) playMedia(m mediaPayload, expected ...string) map[string]any {
	return n.playLocal("audio.play", pcmPayload{WAV: m.WAV, SHA256: m.SHA256}, func(c config, wav []byte) bool {
		rate := int(binary.LittleEndian.Uint32(wav[24:]))
		return alias(m.Asset) && c.Media[m.Asset] == m.SHA256 && m.Seconds >= 1 && m.Seconds <= 10 &&
			len(wav)-44 <= rate*2*m.Seconds
	}, expected...)
}

func commName(name string) string { return name[:min(len(name), 15)] }

func servicesProbe() map[string]any {
	running := map[string]bool{}
	entries, _ := os.ReadDir("/proc")
	for _, entry := range entries {
		if _, err := strconv.ParseUint(entry.Name(), 10, 32); err != nil {
			continue
		}
		data, err := readLocal("/proc/"+entry.Name()+"/stat", 4096)
		start, end := bytes.IndexByte(data, '('), bytes.LastIndexByte(data, ')')
		if err == nil && start >= 0 && end > start {
			running[string(data[start+1:end])] = true
		}
	}
	// ponytail: kernel comm names are 15 bytes; a same-prefix process also counts as present.
	result := map[string]any{"agent": true}
	for _, name := range serviceNames {
		result[name] = running[commName(name)]
	}
	return result
}

func storageProbe(path string) (map[string]any, bool) {
	var stat syscall.Statfs_t
	if syscall.Statfs(path, &stat) != nil || stat.Bsize <= 0 || stat.Bfree > stat.Blocks || stat.Bavail > stat.Blocks {
		return nil, false
	}
	unit := uint64(stat.Bsize) / 1024
	if unit == 0 {
		return nil, false
	}
	return map[string]any{"total_kib": uint64(stat.Blocks) * unit,
		"used_kib": uint64(stat.Blocks-stat.Bfree) * unit, "free_kib": uint64(stat.Bavail) * unit}, true
}

func (n *nativeRuntime) audioProbe() map[string]any {
	info, err := os.Stat("/dev/snd")
	data := map[string]any{"sound_devices": err == nil && info.IsDir(),
		"volume_control": "unconfigured", "volume_percent": nil}
	if !n.lock.TryLock() {
		data["volume_control"] = "busy"
		return map[string]any{"evidence_class": "analytical", "data": data}
	}
	defer n.lock.Unlock()
	c, _, policyErr := n.policy()
	if policyErr == nil && c.Volume != nil {
		data["volume_control"] = "unavailable"
		if mixer, ok := n.pinnedMixer(c); ok {
			if value, ok := readMixer(mixer, c.Volume); ok {
				data["volume_control"], data["volume_percent"] = "ready", value
			}
			mixer.Close()
		}
	}
	return map[string]any{"evidence_class": "analytical", "data": data}
}

// Read-only typed probes; raw process, mixer and filesystem output never leaves the agent.
func (n *nativeRuntime) typedProbe(name string) (map[string]any, bool) {
	switch name {
	case "storage":
		data, ok := storageProbe("/data")
		if !ok {
			return blocked("Storage metadata unavailable"), true
		}
		return map[string]any{"evidence_class": "analytical", "data": data}, true
	case "services":
		return map[string]any{"evidence_class": "analytical", "data": servicesProbe()}, true
	case "audio":
		return n.audioProbe(), true
	}
	return nil, false
}
