package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func privateConfig(t *testing.T) (string, config) {
	t.Helper()
	directory := t.TempDir()
	c := config{StationID: "desk", Model: "station-mini",
		Listen:      net.JoinHostPort(net.IPv4(127, 0, 0, 1).String(), "18443"),
		Certificate: filepath.Join(directory, "certificate"), PrivateKey: filepath.Join(directory, "identity"),
		CredentialFile: filepath.Join(directory, "credential"), FirmwareSHA256: strings.Repeat("a", 64),
		TTSClient: filepath.Join(directory, "tts-once"), AllowedActions: []string{"speech.say"}}
	for _, path := range []string{c.Certificate, c.PrivateKey} {
		if os.WriteFile(path, []byte("synthetic"), 0600) != nil {
			t.Fatal("cannot create synthetic public/private file")
		}
	}
	if os.WriteFile(c.CredentialFile, []byte(testToken), 0600) != nil {
		t.Fatal("cannot create synthetic credential")
	}
	data, err := json.Marshal(c)
	if err != nil {
		t.Fatal("cannot encode synthetic configuration")
	}
	path := filepath.Join(directory, "configuration")
	if os.WriteFile(path, data, 0600) != nil {
		t.Fatal("cannot create synthetic configuration")
	}
	return path, c
}

func TestASROnlyReleasesOneFreshTextAfterExit(t *testing.T) {
	s := newCapture("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 10, []byte("synthetic"))
	if !s.event([]byte(`{"outcome":"awaiting_idle","capture_ready":false,"remaining_seconds":180}`)) ||
		!s.event([]byte(`{"outcome":"ready","capture_ready":true,"remaining_seconds":10,"elapsed_seconds":1}`)) {
		t.Fatal("valid ASR readiness rejected")
	}
	if !s.event([]byte(`{"outcome":"complete","capture_ready":false,"text":"synthetic"}`)) {
		t.Fatal("valid ASR final rejected")
	}
	if _, exists := s.report(true)["text"]; exists {
		t.Fatal("text escaped before cleanup")
	}
	s.finish(true, true)
	if s.report(true)["text"] != "synthetic" {
		t.Fatal("clean final missing")
	}
	if _, exists := s.report(true)["text"]; exists {
		t.Fatal("text was released twice")
	}
}

func TestASRRejectsStaleMalformedAndUncleanResults(t *testing.T) {
	for _, data := range []string{
		`{"outcome":"complete","capture_ready":false,"text":"PRIVATE_SENTINEL"}`,
		`{"outcome":"ready","capture_ready":true,"remaining_seconds":61}`,
		`{"outcome":"ready","capture_ready":true,"remaining_seconds":5,"private":"PRIVATE_SENTINEL"}`,
	} {
		s := newCapture("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 10, nil)
		if s.event([]byte(data)) {
			t.Fatal("invalid ASR event accepted")
		}
	}
	s := newCapture("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 10, nil)
	s.event([]byte(`{"outcome":"ready","capture_ready":true,"remaining_seconds":10}`))
	s.event([]byte(`{"outcome":"complete","capture_ready":false,"text":"PRIVATE_SENTINEL"}`))
	s.finish(true, false)
	if s.report(true)["outcome"] != "unknown" || s.text != "" {
		t.Fatal("unclean ASR text retained")
	}
	s = newCapture("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 10, nil)
	s.event([]byte(`{"outcome":"ready","capture_ready":true,"remaining_seconds":10}`))
	s.event([]byte(`{"outcome":"complete","capture_ready":false,"text":"PRIVATE_SENTINEL"}`))
	s.finish(true, true)
	s.expires = time.Now().Add(-time.Second)
	if s.report(true)["outcome"] != "expired" || s.text != "" {
		t.Fatal("expired ASR text retained")
	}
}

func TestDefaultNativePolicyDoesNotExecuteOrExposePrivateValues(t *testing.T) {
	path, _ := privateConfig(t)
	n := nativeRuntime{configPath: path, stationID: "desk", model: "station-mini"}
	result := n.say("PRIVATE_SENTINEL")
	encoded, _ := json.Marshal(result)
	if result["outcome"] != "blocked" || bytes.Contains(encoded, []byte("PRIVATE_SENTINEL")) || bytes.Contains(encoded, []byte(path)) {
		t.Fatal("default policy did not block without private output")
	}
}

func TestEveryNativeWriterRequiresPreviewBeforeRuntimeAccess(t *testing.T) {
	n := nativeRuntime{}
	for _, result := range []map[string]any{n.say("synthetic"), n.listen(10), n.playPCM(pcmPayload{})} {
		if result["outcome"] != "blocked" || !strings.Contains(result["error"].(string), "preview") {
			t.Fatal("unbound action was not blocked before runtime access")
		}
	}
	if n.generation != 3 {
		t.Fatal("blocked attempts did not invalidate previously observed state")
	}
}

func TestNativeControlsCannotObserveStateDuringAction(t *testing.T) {
	n := nativeRuntime{}
	n.lock.Lock()
	if n.controls()["outcome"] != "blocked" || n.say("synthetic")["outcome"] != "blocked" {
		t.Fatal("active action was not exclusive")
	}
	n.finishAction()
	if n.generation != 1 || !n.lock.TryLock() {
		t.Fatal("action did not invalidate state before releasing lock")
	}
	n.lock.Unlock()
}

func TestNativePolicyBindsCredentialRotation(t *testing.T) {
	path, c := privateConfig(t)
	n := nativeRuntime{configPath: path, stationID: "desk", model: "station-mini"}
	_, before, err := n.policy()
	if err != nil {
		t.Fatal("synthetic policy unavailable")
	}
	if os.WriteFile(c.CredentialFile, []byte(strings.Repeat("b", 64)), 0600) != nil {
		t.Fatal("cannot rotate synthetic credential")
	}
	_, after, err := n.policy()
	if err != nil || bytes.Equal(before, after) {
		t.Fatal("credential rotation did not invalidate the policy snapshot")
	}
}

func TestNativeConfigurationRejectsWrongTypesAndUnknownActions(t *testing.T) {
	path, _ := privateConfig(t)
	raw, _ := os.ReadFile(path)
	for _, change := range []struct{ old, replacement string }{
		{`"preauthorize_all_actions":false`, `"preauthorize_all_actions":"true"`},
		{`"speech.say"`, `"shell"`},
		{`"station-mini"`, `"station-lite"`},
	} {
		modified := strings.Replace(string(raw), change.old, change.replacement, 1)
		if modified == string(raw) || os.WriteFile(path, []byte(modified), 0600) != nil {
			t.Fatal("cannot prepare invalid synthetic policy")
		}
		if _, _, err := loadConfig(path); err == nil {
			t.Fatal("invalid native policy accepted")
		}
	}
}

func TestNativeOutputRequiresCorrelationFlagsAndNeverExportsPrivateFields(t *testing.T) {
	success := `{"outcome":"complete","request_sent":true,"request_may_have_been_sent":true,"tts_started":true,"tts_completed":true}`
	if result, ok := ttsFlags([]byte(success)); !ok || result["outcome"] != "applied" {
		t.Fatal("valid correlated synthetic completion rejected")
	}
	for _, data := range []string{
		strings.Replace(success, `"tts_started":true`, `"tts_started":false`, 1),
		strings.Replace(success, `"request_sent":true,`, "", 1),
		strings.TrimSuffix(success, "}") + `,"private":"PRIVATE_SENTINEL"}`,
		`{"outcome":"unknown","error_code":"PRIVATE_SENTINEL","request_sent":true,"request_may_have_been_sent":true,"tts_started":false,"tts_completed":false}`,
	} {
		if _, ok := ttsFlags([]byte(data)); ok {
			t.Fatal("unverified or private native result accepted")
		}
	}
}

func TestBoundedChildOutput(t *testing.T) {
	output := &boundedOutput{}
	if _, err := output.Write(make([]byte, 4096)); err != nil {
		t.Fatal("bounded output rejected")
	}
	if _, err := output.Write([]byte("x")); err == nil || output.Len() != 4096 {
		t.Fatal("oversized native output accepted")
	}
}

func TestKernelMachineIsNotTheCompilerArchitecture(t *testing.T) {
	for input, expected := range map[string]string{
		"aarch64": "aarch64", "armv7l": "armv7l", "armv8l": "armv8l",
		"arm": "arm", "amd64": "unknown", "PRIVATE_SENTINEL": "unknown",
	} {
		if normalizeKernelMachine(input) != expected {
			t.Fatal("kernel architecture confused with agent ABI or arbitrary text")
		}
	}
}

func syntheticWAV() []byte {
	audio := make([]byte, 44+22050*2)
	copy(audio, "RIFF")
	binary.LittleEndian.PutUint32(audio[4:], uint32(len(audio)-8))
	copy(audio[8:], "WAVEfmt ")
	binary.LittleEndian.PutUint32(audio[16:], 16)
	binary.LittleEndian.PutUint16(audio[20:], 1)
	binary.LittleEndian.PutUint16(audio[22:], 1)
	binary.LittleEndian.PutUint32(audio[24:], 22050)
	binary.LittleEndian.PutUint32(audio[28:], 44100)
	binary.LittleEndian.PutUint16(audio[32:], 2)
	binary.LittleEndian.PutUint16(audio[34:], 16)
	copy(audio[36:], "data")
	binary.LittleEndian.PutUint32(audio[40:], uint32(len(audio)-44))
	return audio
}

func TestLocalPCMRequiresExactQuietBoundedWAVAndDigest(t *testing.T) {
	audio := syntheticWAV()
	makePayload := func(data []byte) pcmPayload {
		sum := sha256.Sum256(data)
		return pcmPayload{WAV: base64.StdEncoding.EncodeToString(data), SHA256: hex.EncodeToString(sum[:])}
	}
	if data, ok := decodePCM(makePayload(audio)); !ok || !bytes.Equal(data, audio) {
		t.Fatal("valid synthetic PCM rejected")
	}
	loud := append([]byte{}, audio...)
	loud[44], loud[45] = 0, 32
	for _, data := range [][]byte{audio[:43], append(append([]byte{}, audio...), 0), loud} {
		if _, ok := decodePCM(makePayload(data)); ok {
			t.Fatal("unbounded/malformed/loud WAV accepted")
		}
	}
	bad := makePayload(audio)
	bad.SHA256 = strings.Repeat("b", 64)
	if _, ok := decodePCM(bad); ok {
		t.Fatal("changed PCM digest accepted")
	}
}

func TestLocalPCMModeCannotInvokeCloudTextTTS(t *testing.T) {
	path, c := privateConfig(t)
	c.LocalPCM = &pcmConfig{Client: "/PRIVATE/pcm-player", ALSAConfigSHA256: strings.Repeat("b", 64),
		LibrarySHA256: strings.Repeat("c", 64)}
	c.PreauthorizeAllActions = true
	raw, _ := json.Marshal(c)
	if os.WriteFile(path, raw, 0600) != nil {
		t.Fatal("synthetic config unavailable")
	}
	n := nativeRuntime{configPath: path, stationID: "desk", model: "station-mini"}
	if _, _, err := loadConfig(path); err != nil {
		t.Fatal("local PCM configuration rejected")
	}
	result := n.say("PRIVATE_SENTINEL")
	if result["outcome"] != "blocked" || !strings.Contains(result["error"].(string), "preview") {
		t.Fatal("missing state-bound preview did not block before checking runtime")
	}
}

func TestLocalPCMOnlyAcceptsCorrelatedCompletion(t *testing.T) {
	valid := []byte(`{"outcome":"complete","playback_started":true,"playback_completed":true,"cleanup_verified":true,"frames_written":2205,"sample_rate":22050,"input_frames":2205,"stage":"complete","alsa_return_code":0,"hardware_parameters_verified":true,"playback_elapsed_ms":100}`)
	if result, ok := pcmFlags(valid, 2205, 22050); !ok || result["outcome"] != "applied" {
		t.Fatal("valid synthetic playback completion rejected")
	}
	for _, invalid := range []string{
		strings.Replace(string(valid), `"frames_written":2205`, `"frames_written":2204`, 1),
		strings.Replace(string(valid), `"cleanup_verified":true`, `"cleanup_verified":false`, 1),
		strings.Replace(string(valid), `"input_frames":2205`, `"input_frames":2206`, 1),
		strings.Replace(string(valid), `"stage":"complete"`, `"stage":"PRIVATE_SENTINEL"`, 1),
		strings.Replace(string(valid), `"alsa_return_code":0`, `"alsa_return_code":true`, 1),
		strings.Replace(string(valid), `"hardware_parameters_verified":true`, `"hardware_parameters_verified":false`, 1),
		strings.Replace(string(valid), `,"hardware_parameters_verified":true`, ``, 1),
		strings.Replace(string(valid), `"playback_elapsed_ms":100`, `"playback_elapsed_ms":-1`, 1),
		strings.Replace(string(valid), `"playback_elapsed_ms":100`, `"playback_elapsed_ms":25001`, 1),
		strings.Replace(string(valid), `"playback_elapsed_ms":100`, `"playback_elapsed_ms":true`, 1),
		strings.TrimSuffix(string(valid), "}") + `,"private":"PRIVATE_SENTINEL"}`,
	} {
		if _, ok := pcmFlags([]byte(invalid), 2205, 22050); ok {
			t.Fatal("unverified PCM completion accepted")
		}
	}
	blocked := []byte(`{"outcome":"blocked","playback_started":false,"playback_completed":false,"cleanup_verified":true,"frames_written":0,"sample_rate":22050,"input_frames":2205,"stage":"hardware","alsa_return_code":0,"hardware_parameters_verified":false,"playback_elapsed_ms":-1}`)
	if result, ok := pcmFlags(blocked, 2205, 22050); !ok || result["outcome"] != "blocked" {
		t.Fatal("pre-write hardware mismatch rejected as invalid protocol")
	}
}

func TestResidentPreviewExpiresWithoutReadOnlyRenewal(t *testing.T) {
	n := nativeRuntime{}
	n.previewState()
	expires, generation := n.previewExpires, n.generation
	if !time.Now().Before(expires) || time.Until(expires) > 300*time.Second {
		t.Fatal("resident preview lacks a bounded deadline")
	}
	n.previewState()
	if !n.previewExpires.Equal(expires) || n.generation != generation {
		t.Fatal("read-only revalidation renewed the old preview")
	}
	n.previewExpires = time.Now().Add(-time.Second)
	n.previewState()
	if n.generation == generation || !n.previewExpires.After(expires) {
		t.Fatal("expired state token was not invalidated")
	}
}

func TestAllowedActionsAllExpandsToConfiguredImplementedActions(t *testing.T) {
	path, _ := privateConfig(t)
	raw, _ := os.ReadFile(path)
	list := `"allowed_actions":["speech.say"],`
	write := func(replacement string) {
		if os.WriteFile(path, []byte(strings.Replace(string(raw), list, replacement, 1)), 0600) != nil {
			t.Fatal("cannot prepare synthetic policy")
		}
	}
	for _, replacement := range []string{`"allowed_actions":"all",`, ""} {
		write(replacement)
		c, _, err := loadConfig(path)
		// Only actions whose local prerequisites exist: no ASR client, mixer or media here.
		if err != nil || strings.Join(c.AllowedActions, ",") != "speech.say,led.show" {
			t.Fatalf("%q did not expand to every configured implemented action", replacement)
		}
		if !c.permits("led.show") && c.PreauthorizeAllActions || c.permits("shell") {
			t.Fatal("expanded allowlist permits the wrong actions")
		}
	}
	write(`"allowed_actions":[],`)
	if c, _, err := loadConfig(path); err != nil || len(c.AllowedActions) != 0 {
		t.Fatal("an explicit empty allowlist was not kept empty")
	}
	for _, replacement := range []string{`"allowed_actions":"everything",`,
		`"allowed_actions":["all","speech.say"],`, `"allowed_actions":["shell"],`} {
		write(replacement)
		if _, _, err := loadConfig(path); err == nil {
			t.Fatal("invalid allowlist accepted: " + replacement)
		}
	}
}
