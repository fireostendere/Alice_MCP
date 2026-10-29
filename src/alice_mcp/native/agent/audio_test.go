package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

const playbackOnly = `Simple mixer control 'Master',0
  Capabilities: pvolume pvolume-joined pswitch pswitch-joined
  Playback channels: Mono
  Limits: Playback 0 - 255
  Mono: Playback 64 [25%] [-20.00dB] [on]
`

func reviewedVolume(t *testing.T) (string, config) {
	t.Helper()
	path, c := privateConfig(t)
	c.AllowedActions = append(c.AllowedActions, "audio.volume")
	c.Volume = &volumeConfig{Amixer: "/PRIVATE/amixer", AmixerSHA256: strings.Repeat("d", 64),
		Card: 0, Control: "Master", MaxPercent: 30}
	raw, _ := json.Marshal(c)
	if os.WriteFile(path, raw, 0600) != nil {
		t.Fatal("synthetic config unavailable")
	}
	return path, c
}

func TestTypedAudioPolicyFailsClosedWithoutReviewedMapping(t *testing.T) {
	path, c := reviewedVolume(t)
	if _, _, err := loadConfig(path); err != nil {
		t.Fatal("reviewed synthetic volume mapping rejected")
	}
	for _, change := range []func(*config){
		func(c *config) { c.Volume = nil },
		func(c *config) { c.Volume.Control = "Capture" },
		func(c *config) { c.Volume.Control = "Mic Boost" },
		func(c *config) { c.Volume.Control = "Master;id" },
		func(c *config) { c.Volume.MaxPercent = 101 },
		func(c *config) { c.Volume.AmixerSHA256 = "PRIVATE_SENTINEL" },
		func(c *config) { c.Volume.Amixer = "amixer" },
		func(c *config) { c.AllowedActions = append(c.AllowedActions, "audio.play") },
		func(c *config) {
			c.AllowedActions = append(c.AllowedActions, "audio.play")
			c.LocalPCM = &pcmConfig{Client: "/PRIVATE/pcm-player", ALSAConfigSHA256: strings.Repeat("b", 64),
				LibrarySHA256: strings.Repeat("c", 64)}
			c.Media = map[string]string{"PRIVATE path": strings.Repeat("e", 64)}
		},
	} {
		changed := c
		volume := *c.Volume
		changed.Volume = &volume
		changed.AllowedActions = append([]string{}, c.AllowedActions...)
		change(&changed)
		raw, _ := json.Marshal(changed)
		if os.WriteFile(path, raw, 0600) != nil {
			t.Fatal("synthetic config unavailable")
		}
		if _, _, err := loadConfig(path); err == nil {
			t.Fatal("unreviewed audio mapping accepted")
		}
	}
	c.AllowedActions = append(c.AllowedActions, "audio.play")
	c.LocalPCM = &pcmConfig{Client: "/PRIVATE/pcm-player", ALSAConfigSHA256: strings.Repeat("b", 64),
		LibrarySHA256: strings.Repeat("c", 64)}
	c.Media = map[string]string{"chime": strings.Repeat("e", 64)}
	raw, _ := json.Marshal(c)
	if os.WriteFile(path, raw, 0600) != nil {
		t.Fatal("synthetic config unavailable")
	}
	if _, _, err := loadConfig(path); err != nil {
		t.Fatal("registered media policy rejected")
	}
}

func TestMixerParserAcceptsOnlyOnePlaybackOnlyControl(t *testing.T) {
	if value, ok := parseMixer([]byte(playbackOnly), "Master"); !ok || value != 25 {
		t.Fatal("playback-only synthetic mixer rejected")
	}
	stereo := strings.Replace(playbackOnly, "  Mono: Playback 64 [25%] [-20.00dB] [on]\n",
		"  Front Left: Playback 64 [25%] [on]\n  Front Right: Playback 64 [25%] [on]\n", 1)
	if value, ok := parseMixer([]byte(stereo), "Master"); !ok || value != 25 {
		t.Fatal("equal stereo playback rejected")
	}
	for _, output := range []string{
		"",
		strings.Replace(playbackOnly, "pswitch-joined", "cvolume", 1),
		strings.Replace(playbackOnly, "pvolume pvolume-joined", "volume", 1),
		strings.Replace(playbackOnly, "pvolume pvolume-joined ", "", 1),
		playbackOnly + "  Mono: Capture 10 [4%] [on]\n",
		strings.Replace(stereo, "Front Right: Playback 64 [25%]", "Front Right: Playback 65 [26%]", 1),
		strings.Replace(playbackOnly, "'Master'", "'PCM'", 1),
		playbackOnly + playbackOnly,
		strings.Replace(playbackOnly, "[25%]", "[+25%]", 1),
		strings.Replace(playbackOnly, "[25%]", "[250%]", 1),
	} {
		if _, ok := parseMixer([]byte(output), "Master"); ok {
			t.Fatal("ambiguous, capture or malformed mixer output accepted")
		}
	}
}

func TestVolumeTargetsStayWithinCeilingAndNeverRaiseImplicitly(t *testing.T) {
	percent := func(v int) volumeRequest { return volumeRequest{Percent: &v} }
	step := func(s string, ceiling int) volumeRequest { return volumeRequest{Step: s, MaxPercent: &ceiling} }
	for _, c := range []struct {
		request          volumeRequest
		current, ceiling int
		target           int
		ok               bool
	}{
		{percent(20), 50, 30, 20, true},
		{percent(31), 10, 30, 0, false},
		{step("louder", 30), 10, 30, 20, true},
		{step("louder", 30), 25, 30, 30, true},
		{step("louder", 30), 30, 30, 0, false},
		{step("louder", 30), 80, 30, 0, false},
		{step("quieter", 30), 25, 30, 15, true},
		{step("quieter", 30), 80, 30, 30, true},
		{step("quieter", 30), 0, 30, 0, false},
		{step("quieter", 30), 5, 30, 0, true},
	} {
		target, ok := volumeTarget(c.request, c.current, c.ceiling)
		if ok != c.ok || ok && target != c.target {
			t.Fatal("volume target escaped the bounded step/ceiling contract")
		}
	}
	for _, r := range []volumeRequest{{}, percent(-1), percent(101), {Step: "louder"},
		{Step: "max", MaxPercent: new(int)}, {Percent: new(int), Step: "louder", MaxPercent: new(int)}} {
		if validVolume(r) {
			t.Fatal("untyped volume request accepted")
		}
	}
}

func sha(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// A synthetic amixer: no real mixer, ALSA library or station is used.
func fakeMixer(t *testing.T, exitCode int, ignoreSet bool) (*os.File, string) {
	t.Helper()
	directory := t.TempDir()
	state := filepath.Join(directory, "state")
	if os.WriteFile(state, []byte("25"), 0600) != nil {
		t.Fatal("synthetic mixer state unavailable")
	}
	set := `printf '%s' "${5%\%}" > ` + state
	if ignoreSet {
		set = "true"
	}
	script := "#!/bin/sh\n[ \"$1 $2 $4\" = '-c 0 Master' ] || exit 9\n" +
		"if [ \"$3\" = sset ]; then " + set + "; exit " + strconv.Itoa(exitCode) + "; fi\n" +
		"v=$(cat " + state + ")\nprintf \"Simple mixer control 'Master',0\\n  Capabilities: pvolume pswitch\\n" +
		"  Mono: Playback $v [$v%%] [on]\\n\"\n"
	path := filepath.Join(directory, "amixer")
	if os.WriteFile(path, []byte(script), 0700) != nil {
		t.Fatal("synthetic mixer unavailable")
	}
	f, err := trustedFile(path, false)
	if err != nil {
		t.Fatal("synthetic mixer untrusted")
	}
	t.Cleanup(func() { f.Close() })
	return f, state
}

func TestVolumeChangeReportsPriorValueAndVerifiesReadback(t *testing.T) {
	v := &volumeConfig{Control: "Master", MaxPercent: 30}
	ceiling := 30
	mixer, state := fakeMixer(t, 0, false)
	result := changeVolume(mixer, v, volumeRequest{Step: "quieter", MaxPercent: &ceiling})
	value, _ := os.ReadFile(state)
	if result["outcome"] != "applied" || result["previous_percent"] != 25 || result["percent"] != 15 || string(value) != "15" {
		t.Fatal("bounded step did not capture prior value or verify readback")
	}
	restore := 25
	if changeVolume(mixer, v, volumeRequest{Percent: &restore})["percent"] != 25 {
		t.Fatal("prior value could not be restored with a bounded set")
	}
	tooLoud := 31
	if changeVolume(mixer, v, volumeRequest{Percent: &tooLoud})["outcome"] != "blocked" {
		t.Fatal("volume above the resident ceiling was written")
	}
	for _, mode := range []struct {
		code   int
		ignore bool
	}{{1, false}, {0, true}} {
		mixer, _ := fakeMixer(t, mode.code, mode.ignore)
		result := changeVolume(mixer, v, volumeRequest{Step: "quieter", MaxPercent: &ceiling})
		if result["outcome"] != "unknown" || result["previous_percent"] != 25 {
			t.Fatal("failed or unverified mixer write was not reported as unknown")
		}
	}
}

func TestTypedProbesReturnOnlyAllowlistedValues(t *testing.T) {
	storage, ok := storageProbe(t.TempDir())
	if !ok || storage["total_kib"].(uint64) == 0 || storage["free_kib"].(uint64) > storage["total_kib"].(uint64) ||
		storage["used_kib"].(uint64) > storage["total_kib"].(uint64) || len(storage) != 3 {
		t.Fatal("storage probe returned untyped or implausible values")
	}
	if _, ok := storageProbe(filepath.Join(t.TempDir(), "missing")); ok {
		t.Fatal("missing storage volume fabricated metrics")
	}
	services := servicesProbe()
	if len(services) != len(serviceNames)+1 || services["agent"] != true {
		t.Fatal("service probe shape changed")
	}
	for _, name := range serviceNames {
		if _, ok := services[name].(bool); !ok {
			t.Fatal("service state is not a boolean")
		}
	}
	if commName("quasar_launcher2") != "quasar_launcher" || commName("maind") != "maind" {
		t.Fatal("kernel process-name truncation ignored")
	}
	n := nativeRuntime{}
	audio := n.probe("audio")["data"].(map[string]any)
	if audio["volume_control"] != "unconfigured" || audio["volume_percent"] != nil || len(audio) != 3 {
		t.Fatal("audio probe without reviewed mapping did not fail closed")
	}
}

func TestTypedAudioWritersRequirePreviewBeforeRuntimeAccess(t *testing.T) {
	n := nativeRuntime{}
	five := 5
	for _, result := range []map[string]any{n.setVolume(volumeRequest{Percent: &five}), n.playMedia(mediaPayload{})} {
		if result["outcome"] != "blocked" || !strings.Contains(result["error"].(string), "preview") {
			t.Fatal("unbound audio action was not blocked before runtime access")
		}
	}
	if n.generation != 2 {
		t.Fatal("blocked audio attempts did not invalidate previously observed state")
	}
}

func TestTypedAudioActionsRouteOnceAndRejectUntypedParameters(t *testing.T) {
	calls := 0
	s := testServer(t, &calls)
	volumes, plays := 0, 0
	s.volume = func(r volumeRequest, expected ...string) map[string]any {
		volumes++
		return map[string]any{"outcome": "applied", "do_not_retry": true}
	}
	s.media = func(m mediaPayload, expected ...string) map[string]any {
		plays++
		if m.Asset != "chime" || m.Seconds != 2 {
			t.Fatal("media request changed")
		}
		return map[string]any{"outcome": "applied", "do_not_retry": true}
	}
	call := func(action, parameters string) string {
		body := `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_station_action","arguments":{"station_id":"desk","expected_state_sha256":"` +
			testToken + `","action":"` + action + `","parameters":` + parameters + `}}}`
		return request(t, s, body, testToken).Body.String()
	}
	audio := syntheticWAV()
	payload, _ := json.Marshal(mediaPayload{Asset: "chime", Seconds: 2,
		WAV: base64.StdEncoding.EncodeToString(audio), SHA256: sha(audio)})
	for _, valid := range [][2]string{{"audio.volume", `{"percent":20}`},
		{"audio.volume", `{"step":"quieter","max_percent":30}`}, {"audio.play", string(payload)}} {
		if !strings.Contains(call(valid[0], valid[1]), `"do_not_retry":true`) {
			t.Fatal("typed audio action rejected")
		}
	}
	if volumes != 2 || plays != 1 || calls != 0 {
		t.Fatal("typed audio action lost, repeated or routed to speech")
	}
	for _, invalid := range [][2]string{
		{"audio.volume", `{"percent":101}`}, {"audio.volume", `{"percent":true}`},
		{"audio.volume", `{"step":"max","max_percent":30}`}, {"audio.volume", `{"step":"louder"}`},
		{"audio.volume", `{"percent":5,"step":"louder","max_percent":30}`},
		{"audio.volume", `{"percent":5,"control":"Capture"}`},
		{"audio.play", strings.Replace(string(payload), `"asset":"chime"`, `"asset":"chime","path":"/PRIVATE/x.wav"`, 1)},
		{"audio.play", strings.Replace(string(payload), `"duration_seconds":2`, `"duration_seconds":11`, 1)},
		{"audio.play", strings.Replace(string(payload), `"asset":"chime"`, `"asset":"../x"`, 1)},
		{"audio.play", `{"asset":"chime","duration_seconds":2,"wav_base64":"","sha256":"` + testToken + `"}`},
	} {
		if !strings.Contains(call(invalid[0], invalid[1]), `"error"`) {
			t.Fatal("untyped audio parameters reached the runtime")
		}
	}
	if volumes != 2 || plays != 1 {
		t.Fatal("invalid audio parameters executed")
	}
}
