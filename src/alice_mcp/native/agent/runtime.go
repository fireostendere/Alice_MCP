package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const ttsClientSHA256 = "24eead1c6922432273050d60f5930234cb8c138d3e8f48aebbf05ebb21f3edcb"
const firmwarePath = "/system/vendor/quasar/maind"
const vqePath = "/system/vendor/quasar/libs/libyandex_evil.so"
const documentedPlumLibrary = "fa7ad1544fe9ddfccedcf15acc031768345b0b8c7333bc1c0b47bc476dd4152f"

type nativeRuntime struct {
	configPath     string
	stationID      string
	model          string
	lock           sync.Mutex
	sessionsMu     sync.Mutex
	capture        *capture
	generation     uint64
	instance       string
	previewExpires time.Time
}

// Caller holds the action lock. Bind to policy/credential, boot, firmware and
// Quasar process identity, not merely a running/not-running flag.
func (n *nativeRuntime) stateStamp() string {
	if n.instance == "" {
		var nonce [16]byte
		if _, err := rand.Read(nonce[:]); err != nil {
			return ""
		}
		n.instance = hex.EncodeToString(nonce[:])
	}
	_, policy, err := n.policy()
	boot, bootErr := readLocal("/proc/sys/kernel/random/boot_id", 128)
	entries, procErr := os.ReadDir("/proc")
	identities := ""
	for _, entry := range entries {
		if _, err := strconv.ParseUint(entry.Name(), 10, 32); err != nil {
			continue
		}
		data, err := readLocal("/proc/"+entry.Name()+"/stat", 4096)
		end := bytes.LastIndexByte(data, ')')
		if err != nil || end < 0 || !bytes.Contains(data[:end+1], []byte("(maind)")) {
			continue
		}
		fields := strings.Fields(string(data[end+1:]))
		if len(fields) < 20 {
			return ""
		}
		identities += entry.Name() + ":" + fields[19] + ";"
	}
	firmware := hashPath(firmwarePath)
	if err != nil || bootErr != nil || procErr != nil || identities == "" || firmware == "" {
		return ""
	}
	stamp := sha256.Sum256([]byte(fmt.Sprintf("%x|%s|%s|%s|%s|%d", policy, boot, firmware, identities, n.instance, n.generation)))
	return hex.EncodeToString(stamp[:])
}

func (n *nativeRuntime) stateMatches(expected string) bool {
	return time.Now().Before(n.previewExpires) && hex64(expected) && n.stateStamp() == expected
}

func (n *nativeRuntime) consumeState(expected []string) string {
	if len(expected) != 1 || !n.stateMatches(expected[0]) {
		return ""
	}
	// Exclusively reserved until finishAction invalidates it, including failure.
	return expected[0]
}

func (n *nativeRuntime) previewState() string {
	if !time.Now().Before(n.previewExpires) {
		n.generation++
		n.previewExpires = time.Now().Add(300 * time.Second)
	}
	return n.stateStamp()
}

func (n *nativeRuntime) finishAction() {
	n.generation++
	n.previewExpires = time.Time{}
	n.lock.Unlock()
}

func blocked(message string) map[string]any {
	return map[string]any{"outcome": "blocked", "error": message}
}

func (n *nativeRuntime) policy() (config, []byte, error) {
	c, raw, err := loadConfig(n.configPath)
	if err != nil || c.StationID != n.stationID || c.Model != n.model {
		return config{}, nil, errors.New("Private station policy changed")
	}
	credential, err := readTrusted(c.CredentialFile, true, 128)
	if err != nil {
		return config{}, nil, errors.New("Private station policy changed")
	}
	stamp := sha256.Sum256(append(raw, credential...))
	return c, stamp[:], nil
}

func (n *nativeRuntime) controls() map[string]any {
	if !n.lock.TryLock() {
		return blocked("Another native action is active")
	}
	defer n.lock.Unlock()
	c, _, err := n.policy()
	if err != nil {
		return blocked("Private station policy unavailable")
	}
	actions := c.AllowedActions
	if actions == nil {
		actions = []string{}
	}
	preauthorized := []string{}
	if c.PreauthorizeAllActions {
		preauthorized = actions
	}
	backend := "yandex"
	if c.LocalPCM != nil {
		backend = "local_pcm"
	}
	return map[string]any{"station_id": n.stationID, "model": n.model,
		"writes_enabled": len(actions) != 0, "allowed_actions": actions,
		"preauthorize_all_actions": c.PreauthorizeAllActions, "preauthorized_actions": preauthorized,
		"transport": "streamable_http_tls", "independent_wake_word": false,
		"tts_backend":    backend,
		"state_contract": "alice.native.state.v1", "state_sha256": n.previewState(),
		"evidence_class": "caller", "physical_effects_verified": false}
}

func readLocal(path string, limit int64) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, errors.New("Local metadata unavailable")
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil || int64(len(data)) > limit {
		return nil, errors.New("Local metadata unavailable")
	}
	return data, nil
}

func hashFile(f *os.File, limit int64) (string, error) {
	h := sha256.New()
	size, err := io.Copy(h, io.LimitReader(f, limit+1))
	if err != nil || size > limit {
		return "", errors.New("Artifact unavailable or oversized")
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		return "", errors.New("Artifact unavailable")
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func hashPath(path string) string {
	f, err := trustedFile(path, false)
	if err != nil {
		return ""
	}
	defer f.Close()
	value, _ := hashFile(f, 64<<20)
	return value
}

func containsFixed(path, marker string, limit int64) bool {
	f, err := os.Open(path)
	if err != nil {
		return false
	}
	defer f.Close()
	reader := io.LimitReader(f, limit)
	buffer := make([]byte, 65536+len(marker))
	retained := 0
	for {
		count, err := reader.Read(buffer[retained:65536])
		size := retained + count
		if bytes.Contains(buffer[:size], []byte(marker)) {
			return true
		}
		if err != nil || count == 0 {
			return false
		}
		retained = min(size, len(marker)-1)
		copy(buffer[:retained], buffer[size-retained:size])
	}
}

func quasarRunning() bool {
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "pidof", "maind")
	command.Stdout, command.Stderr = io.Discard, io.Discard
	return command.Run() == nil
}

func kilobytes(data []byte, key string) (uint64, bool) {
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) == 3 && fields[0] == key && fields[2] == "kB" {
			value, err := strconv.ParseUint(fields[1], 10, 64)
			return value, err == nil && value < 1<<40
		}
	}
	return 0, false
}

func normalizeKernelMachine(value string) string {
	switch value {
	case "arm", "armv7l", "armv8l", "aarch64":
		return value
	}
	return "unknown"
}

func kernelMachine() string {
	var name syscall.Utsname
	if syscall.Uname(&name) != nil {
		return "unknown"
	}
	var value []byte
	for _, c := range name.Machine {
		if c == 0 {
			break
		}
		value = append(value, byte(c))
	}
	return normalizeKernelMachine(string(value))
}

func (n *nativeRuntime) probe(name string) map[string]any {
	if name == "microphone" {
		status, _ := readLocal("/proc/asound/card0/pcm2c/sub0/status", 4096)
		state := "unknown"
		if strings.TrimSpace(string(status)) == "closed" {
			state = "closed"
		}
		for _, line := range strings.Split(string(status), "\n") {
			fields := strings.Fields(line)
			if len(fields) == 2 && fields[0] == "state:" {
				switch fields[1] {
				case "RUNNING", "PREPARED", "OPEN":
					state = strings.ToLower(fields[1])
				}
			}
		}
		libraryHash := hashPath(vqePath)
		data := map[string]any{"quasar_running": quasarRunning(), "capture_state": state,
			"vqe_channel_declared":            containsFixed("/system/vendor/quasar/quasar.cfg", "vqe_0", 1<<20),
			"vqe_output_symbol_present":       containsFixed(vqePath, "YandexVqeC_GetOutputChannelData", 64<<20),
			"matches_documented_plum_library": libraryHash == documentedPlumLibrary,
			"independent_capture_verified":    false, "audio_captured": false,
			"quasar_changed": false}
		return map[string]any{"evidence_class": "analytical", "data": data}
	}
	if name != "health" {
		return blocked("Unknown read-only probe")
	}
	memory, memErr := readLocal("/proc/meminfo", 65536)
	uptime, upErr := readLocal("/proc/uptime", 256)
	self, selfErr := readLocal("/proc/self/status", 16384)
	total, totalOK := kilobytes(memory, "MemTotal:")
	available, availableOK := kilobytes(memory, "MemAvailable:")
	rss, rssOK := kilobytes(self, "VmRSS:")
	fields := strings.Fields(string(uptime))
	if memErr != nil || upErr != nil || selfErr != nil || !totalOK || !availableOK || !rssOK || len(fields) != 2 || available > total {
		return blocked("Health metadata unavailable")
	}
	seconds, err := strconv.ParseFloat(fields[0], 64)
	if err != nil || seconds < 0 || seconds > 1e12 {
		return blocked("Health metadata unavailable")
	}
	return map[string]any{"evidence_class": "analytical", "data": map[string]any{
		"uptime_seconds": seconds, "memory_total_kib": total, "memory_available_kib": available,
		"kernel_arch": kernelMachine(), "agent_rss_kib": rss}}
}

type boundedOutput struct{ bytes.Buffer }

func (b *boundedOutput) Write(data []byte) (int, error) {
	if b.Len()+len(data) > 4096 {
		return 0, errors.New("Native output limit")
	}
	return b.Buffer.Write(data)
}

type ttsResult struct {
	Outcome   string `json:"outcome"`
	Error     string `json:"error_code"`
	Sent      *bool  `json:"request_sent"`
	MaySent   *bool  `json:"request_may_have_been_sent"`
	Started   *bool  `json:"tts_started"`
	Completed *bool  `json:"tts_completed"`
}

func ttsFlags(output []byte) (map[string]any, bool) {
	var result ttsResult
	if !decode(output, &result) || result.Sent == nil || result.MaySent == nil || result.Started == nil || result.Completed == nil {
		return nil, false
	}
	sent, may, started, completed := *result.Sent, *result.MaySent, *result.Started, *result.Completed
	if sent && !may || started && !sent || completed && !started {
		return nil, false
	}
	if result.Outcome == "complete" {
		if !sent || !may || !started || !completed || result.Error != "" {
			return nil, false
		}
	} else if result.Outcome == "blocked" || result.Outcome == "unknown" {
		allowed := false
		for _, code := range []string{"protocol_error", "timeout", "invalid_text", "frame_limit", "ipc_unavailable", "ipc_io_error", "ipc_closed", "interrupted", "clock_error", "output_closed", "signal_error", "tts_failed"} {
			allowed = allowed || code == result.Error
		}
		if !allowed || completed || result.Outcome == "blocked" && may || result.Outcome == "unknown" && !may {
			return nil, false
		}
	} else {
		return nil, false
	}
	outcome := result.Outcome
	if outcome == "complete" {
		outcome = "applied"
	}
	value := map[string]any{"outcome": outcome, "request_sent": sent,
		"request_may_have_been_sent": may, "tts_started": started, "tts_completed": completed}
	if result.Error != "" {
		value["error_code"] = result.Error
	}
	return value, true
}

func (n *nativeRuntime) say(text string, expected ...string) map[string]any {
	if !n.lock.TryLock() {
		return blocked("Another native action is active")
	}
	defer n.finishAction()
	state := n.consumeState(expected)
	if state == "" {
		return blocked("Fresh state-bound preview required")
	}
	started := time.Now()
	c, policy, err := n.policy()
	if err == nil && c.LocalPCM != nil {
		return blocked("Local PCM mode forbids cloud text TTS")
	}
	if err != nil || !validText(text) || !c.permits("speech.say") {
		return blocked("Literal TTS is not preauthorized by the local policy")
	}
	if os.Geteuid() != 0 || !quasarRunning() || hashPath(firmwarePath) != c.FirmwareSHA256 {
		return blocked("Reviewed firmware or running Quasar unavailable")
	}
	firmware, err := trustedFile(firmwarePath, false)
	if err != nil {
		return blocked("Firmware ABI unavailable")
	}
	var header [20]byte
	_, err = io.ReadFull(firmware, header[:])
	_ = firmware.Close()
	if err != nil || !bytes.Equal(header[:6], []byte{127, 69, 76, 70, 1, 1}) || header[18] != 40 || header[19] != 0 {
		return blocked("Firmware ABI is not reviewed ARM32")
	}
	client, err := trustedFile(c.TTSClient, false)
	if err != nil {
		return blocked("Reviewed TTS artifact unavailable")
	}
	defer client.Close()
	digest, err := hashFile(client, 65536)
	if err != nil || digest != ttsClientSHA256 {
		return blocked("Reviewed TTS artifact changed")
	}
	_, fresh, err := n.policy()
	if err != nil || !bytes.Equal(policy, fresh) || !n.stateMatches(state) || time.Since(started) >= 300*time.Second {
		return blocked("Private policy changed or preview expired")
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return blocked("Request id unavailable")
	}
	packet := make([]byte, 4+len(text))
	binary.LittleEndian.PutUint32(packet, uint32(len(text)))
	copy(packet[4:], text)
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	// The verified open descriptor, not a mutable supplied path, selects the executable.
	command := exec.CommandContext(ctx, "/proc/self/fd/3", "--say", "20", hex.EncodeToString(nonce[:]))
	command.ExtraFiles = []*os.File{client}
	command.Stdin, command.Stderr = bytes.NewReader(packet), io.Discard
	output := &boundedOutput{}
	command.Stdout = output
	execErr := command.Run()
	result, valid := ttsFlags(output.Bytes())
	if !valid || execErr != nil && result["outcome"] == "applied" {
		result = map[string]any{"outcome": "unknown", "error_code": "native_execution_unverified",
			"request_may_have_been_sent": true}
	}
	result["cleanup_verified"] = command.ProcessState != nil && command.ProcessState.Exited()
	result["quasar_running"] = quasarRunning()
	result["do_not_retry"] = true
	result["evidence_class"] = "analytical"
	result["physical_effects_verified"] = false
	return result
}

func (c config) permits(action string) bool {
	if c.PreauthorizeAllActions {
		for _, allowed := range c.AllowedActions {
			if allowed == action {
				return true
			}
		}
	}
	return false
}
