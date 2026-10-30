package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

const testCapture = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

type fakeBus struct {
	writes [][]byte
	failAt int
}

func (b *fakeBus) Write(data []byte) (int, error) {
	b.writes = append(b.writes, append([]byte(nil), data...))
	if len(b.writes) == b.failAt {
		return 0, errors.New("synthetic bus failure")
	}
	return len(data), nil
}

func (b *fakeBus) Close() error { return nil }

// Synthetic root with the same evidence the SSH capture helper leaves behind.
func ledDevice(t *testing.T) (string, *fakeBus) {
	t.Helper()
	root := t.TempDir()
	out := filepath.Join(root, "data/opt/alice-mcp/led-captures", testCapture)
	if os.MkdirAll(out, 0700) != nil || os.MkdirAll(filepath.Join(root, "sys/bus/i2c/devices/0-003c"), 0755) != nil ||
		os.MkdirAll(filepath.Join(root, "proc/sys/kernel/random"), 0755) != nil {
		t.Fatal("cannot create synthetic device")
	}
	for _, dir := range []string{out, filepath.Dir(out), filepath.Dir(filepath.Dir(out))} {
		_ = os.Chmod(dir, 0700)
	}
	boot := []byte("synthetic-boot\n")
	sum := sha256.Sum256(boot)
	uptime, _ := os.ReadFile("/proc/uptime")
	now, _ := strconv.ParseFloat(strings.Fields(string(uptime))[0], 64)
	files := map[string]string{
		"sys/bus/i2c/devices/0-003c/name":            "sn3236\n",
		"proc/sys/kernel/random/boot_id":             string(boot),
		"data/opt/alice-mcp/control.lock":            "",
		filepath.Join(out, "firmware")[len(root)+1:]: strings.Repeat("a", 64) + "\n",
		filepath.Join(out, "phase")[len(root)+1:]:    "complete\n",
		filepath.Join(out, "cleanup")[len(root)+1:]:  "1\n",
		filepath.Join(out, "boot")[len(root)+1:]:     hex.EncodeToString(sum[:]) + "\n",
		filepath.Join(out, "ended")[len(root)+1:]:    strconv.Itoa(int(now)) + "\n",
		filepath.Join(out, "records")[len(root)+1:]: "record=01" + strings.Repeat("0c0804", 6) +
			"\nrecord=25aa\nrecord=01" + strings.Repeat("00", 18) + "\nrecord=25aa\n",
	}
	for name, value := range files {
		if os.WriteFile(filepath.Join(root, name), []byte(value), 0600) != nil {
			t.Fatal("cannot create synthetic evidence")
		}
	}
	bus := &fakeBus{}
	oldRoot, oldOpen := ledRoot, openLED
	ledRoot = root
	openLED = func() (io.WriteCloser, error) { return bus, nil }
	t.Cleanup(func() { ledRoot, openLED = oldRoot, oldOpen })
	return root, bus
}

func payload(frames ...string) ledPayload {
	return ledPayload{CaptureID: testCapture, IntervalMS: 200, Frames: frames}
}

func TestLEDPayloadIsBoundedDimPWMOnly(t *testing.T) {
	dim := strings.Repeat("10", 18)
	if _, ok := decodeLED(payload(dim, strings.Repeat("00", 18))); !ok {
		t.Fatal("valid bounded show rejected")
	}
	many := make([]string, 76)
	for i := range many {
		many[i] = dim
	}
	for _, p := range []ledPayload{
		payload(strings.Repeat("11", 18)), payload(strings.Repeat("0A", 18)), payload(dim[:34]),
		payload(many...), payload(), {CaptureID: testCapture, IntervalMS: 100, Frames: []string{dim}},
		{CaptureID: testCapture, IntervalMS: 1 << 30, Frames: []string{dim}},
		{CaptureID: testCapture, IntervalMS: 5000, Frames: []string{dim, dim, dim, dim}},
		{CaptureID: "../../" + testCapture[6:], IntervalMS: 200, Frames: []string{dim}},
	} {
		if _, ok := decodeLED(p); ok {
			t.Fatal("unbounded or malformed LED payload accepted")
		}
	}
}

func TestLEDShowWritesFramesThenIdleAndClaimsNoReadback(t *testing.T) {
	_, bus := ledDevice(t)
	frame := strings.Repeat("100000", 6)
	result := runShow(payload(frame), strings.Repeat("a", 64))
	if result["outcome"] != "applied" || result["restoration_verified"] != false ||
		result["do_not_retry"] != true || result["physical_effects_verified"] != false {
		t.Fatal("bounded show did not report write-only success")
	}
	encoded, _ := hex.DecodeString(frame)
	want := [][]byte{append([]byte{0x01}, encoded...), {0x25, 0xaa}, append([]byte{0x01}, make([]byte, 18)...), {0x25, 0xaa}}
	got, _ := json.Marshal(bus.writes)
	expected, _ := json.Marshal(want)
	if string(got) != string(expected) {
		t.Fatal("unexpected SN3236 writes")
	}
}

func TestLEDShowNeedsTheSameFreshIdleEvidenceAsTheShellPath(t *testing.T) {
	for _, damage := range []string{"non_idle", "wrong_boot", "stale", "claim", "driver", "firmware", "permissions", "lock"} {
		root, bus := ledDevice(t)
		out := filepath.Join(root, "data/opt/alice-mcp/led-captures", testCapture)
		switch damage {
		case "non_idle":
			_ = os.WriteFile(filepath.Join(out, "records"), []byte("record=01"+strings.Repeat("01", 18)+"\nrecord=25aa\nrecord=01"+strings.Repeat("02", 18)+"\nrecord=25aa\n"), 0600)
		case "wrong_boot":
			_ = os.WriteFile(filepath.Join(out, "boot"), []byte(strings.Repeat("0", 64)), 0600)
		case "stale":
			// Hosted runners may be younger than 30 minutes; zero is then fresh.
			ended, err := os.ReadFile(filepath.Join(out, "ended"))
			at, parseErr := strconv.ParseInt(strings.TrimSpace(string(ended)), 10, 64)
			if err != nil || parseErr != nil {
				t.Fatal("cannot read fixture clock")
			}
			_ = os.WriteFile(filepath.Join(out, "ended"), []byte(strconv.FormatInt(at-1801, 10)), 0600)
		case "claim":
			_ = os.Mkdir(filepath.Join(root, "data/opt/alice-mcp/led-capture-active"), 0700)
		case "driver":
			_ = os.Symlink("/sys/bus/i2c/drivers/sn3236", filepath.Join(root, "sys/bus/i2c/devices/0-003c/driver"))
		case "firmware":
			_ = os.WriteFile(filepath.Join(out, "firmware"), []byte(strings.Repeat("b", 64)), 0600)
		case "permissions":
			_ = os.Chmod(out, 0755)
		case "lock":
			_ = os.Remove(filepath.Join(root, "data/opt/alice-mcp/control.lock"))
		}
		if runShow(payload(strings.Repeat("10", 18)), strings.Repeat("a", 64))["outcome"] != "blocked" || len(bus.writes) != 0 {
			t.Fatal("LED show ran without valid idle evidence: " + damage)
		}
	}
}

func TestLEDFailureAttemptsIdleOnceWithoutReplayingFrames(t *testing.T) {
	_, bus := ledDevice(t)
	bus.failAt = 3 // Second frame fails.
	result := runShow(payload(strings.Repeat("10", 18), strings.Repeat("01", 18)), strings.Repeat("a", 64))
	if result["outcome"] != "unknown" || result["command_succeeded"] != false || result["restoration_command_succeeded"] != true {
		t.Fatal("failed show was not unknown with an idle attempt")
	}
	if len(bus.writes) != 5 || bus.writes[3][0] != 0x01 || bus.writes[3][1] != 0 {
		t.Fatal("frames were retried or idle was not sent")
	}
}

func TestLEDShowRequiresFreshPreviewAndPermission(t *testing.T) {
	n := nativeRuntime{}
	if result := n.showLED(payload(strings.Repeat("10", 18))); result["outcome"] != "blocked" ||
		!strings.Contains(result["error"].(string), "preview") {
		t.Fatal("unbound LED show was not blocked")
	}
	path, c := privateConfig(t)
	raw, _ := os.ReadFile(path)
	if os.WriteFile(path, []byte(strings.Replace(string(raw), `"speech.say"`, `"led.show"`, 1)), 0600) != nil {
		t.Fatal("cannot prepare synthetic policy")
	}
	if loaded, _, err := loadConfig(path); err != nil || loaded.AllowedActions[0] != "led.show" {
		t.Fatal("Mini 1 LED policy rejected")
	}
	c.Model = "station-mini3"
	c.AllowedActions = []string{"led.show"}
	data, _ := json.Marshal(c)
	if os.WriteFile(path, data, 0600) != nil {
		t.Fatal("cannot prepare synthetic policy")
	}
	if _, _, err := loadConfig(path); err == nil {
		t.Fatal("LED adapter enabled without a Mini 1 profile")
	}
}

func TestLEDToolIsTypedAndNeverReceivesRawRegisters(t *testing.T) {
	calls, shows := 0, 0
	s := testServer(t, &calls)
	s.show = func(p ledPayload, expected ...string) map[string]any {
		shows++
		return map[string]any{"outcome": "applied"}
	}
	frame := strings.Repeat("10", 18)
	for _, parameters := range []string{
		`{"capture_id":"` + testCapture + `","interval_ms":200,"frames":["` + strings.Repeat("ff", 18) + `"]}`,
		`{"capture_id":"` + testCapture + `","interval_ms":200,"frames":["` + frame + `"],"register":37}`,
		`{"capture_id":"` + testCapture + `","interval_ms":200,"frames":["` + frame + `"],"bus":1}`,
		`{"capture_id":"` + testCapture + `","interval_ms":20,"frames":["` + frame + `"]}`,
	} {
		body := `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_station_action","arguments":{"station_id":"desk","expected_state_sha256":"` + testToken + `","action":"led.show","parameters":` + parameters + `}}}`
		if w := request(t, s, body, testToken); shows != 0 || !strings.Contains(w.Body.String(), `"error"`) {
			t.Fatal("hostile LED request reached the runtime")
		}
	}
	body := `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_station_action","arguments":{"station_id":"desk","expected_state_sha256":"` + testToken + `","action":"led.show","parameters":{"capture_id":"` + testCapture + `","interval_ms":200,"frames":["` + frame + `"]}}}}`
	if w := request(t, s, body, testToken); shows != 1 || calls != 0 || !strings.Contains(w.Body.String(), `"applied"`) {
		t.Fatal("typed LED show was lost, repeated or routed to speech")
	}
}
