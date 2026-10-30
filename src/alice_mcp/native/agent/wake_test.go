package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestResidentPreauthorizationDefaultsOnAndExplicitFalsePersists(t *testing.T) {
	path, _ := privateConfig(t)
	raw, _ := os.ReadFile(path)
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil {
		t.Fatal("fixture invalid")
	}
	delete(fields, "preauthorize_all_actions")
	raw, _ = json.Marshal(fields)
	if os.WriteFile(path, raw, 0600) != nil {
		t.Fatal("cannot update fixture")
	}
	c, _, err := loadConfig(path)
	if err != nil || !c.PreauthorizeAllActions {
		t.Fatal("owner default not applied")
	}
	fields["preauthorize_all_actions"] = json.RawMessage("false")
	raw, _ = json.Marshal(fields)
	os.WriteFile(path, raw, 0600)
	c, _, err = loadConfig(path)
	if err != nil || c.PreauthorizeAllActions {
		t.Fatal("explicit revocation overwritten")
	}
}

func TestWakeChildrenPipePCMAndCloseBeforeReturning(t *testing.T) {
	root := t.TempDir()
	pin := strings.Repeat("a", 64)
	source := `#!/bin/sh
dd if=/dev/zero bs=32000 count=1 2>/dev/null
printf '%s\n' '{"outcome":"complete","error_code":"none","connected":true,"subscribe_sent":true,"unsubscribe_sent":true,"frames":1,"audio_frames":1,"other_frames":0,"bytes":32026,"first_audio_ms":0,"elapsed_ms":1000,"audio_exported":false,"channels":[{"name":"vqe_0","type":6,"sample_rate":16000,"format":1,"frames":1,"bytes":32000,"duration_ms":1000,"bytes_per_sample_estimate":2,"peak_s16":0,"mean_square_s16":0}],"other":[]}' >&2
`
	detector := `#!/bin/sh
bytes=$(wc -c)
[ "$bytes" -eq 32000 ] || exit 1
printf '%s\n' '{"event":"ready","rate":16000,"threshold":0.98,"window":5,"cooldown_ms":2500,"gain_db":0,"models":[{"name":"komp","sha256":"` + pin + `","stride":3,"arena_bytes":24624}]}' '{"event":"end","reason":"eof","audio_ms":1000,"detections":[0]}'
`
	files := []*os.File{}
	for i, text := range []string{source, detector} {
		path := filepath.Join(root, []string{"source", "detector"}[i])
		if os.WriteFile(path, []byte(text), 0500) != nil {
			t.Fatal("cannot create synthetic child")
		}
		f, err := os.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		defer f.Close()
		files = append(files, f)
	}
	events, ok := runWakeFiles(files[0], files[1], pin, 1)
	if !ok || len(events) != 0 {
		t.Fatal("bounded children did not complete", events)
	}
	if _, ok := runWakeFiles(files[0], files[1], strings.Repeat("b", 64), 1); ok {
		t.Fatal("wrong embedded model accepted")
	}
}

func TestWakeResultRequiresCompleteBoundedUntrustedEvents(t *testing.T) {
	pin := strings.Repeat("a", 64)
	ready := `{"event":"ready","rate":16000,"threshold":0.98,"window":5,"cooldown_ms":2500,"gain_db":0,"models":[{"name":"komp","sha256":"` + pin + `","stride":3,"arena_bytes":24624}]}`
	wake := `{"event":"wake","model":"komp","t_ms":480,"probability":0.99,"peak":0.9961,"last":0.9883,"trusted":false}`
	end := `{"event":"end","reason":"eof","audio_ms":1000,"detections":[1]}`
	raw := ready + "\n" + wake + "\n" + end + "\n"
	events, ok := parseWakeOutput([]byte(raw), pin, 32000)
	if !ok || len(events) != 1 || events[0].Trusted || events[0].Model != "komp" {
		t.Fatal("complete wake output was not delivered as untrusted data")
	}
	for _, bad := range []string{
		strings.Replace(raw, `"trusted":false`, `"trusted":true`, 1),
		strings.Replace(raw, `,"trusted":false`, "", 1),
		strings.Replace(raw, `"audio_ms":1000`, `"audio_ms":990`, 1),
		strings.Replace(raw, `"detections":[1]`, `"detections":[0]`, 1),
		strings.Replace(raw, pin, strings.Repeat("b", 64), 1),
		strings.Replace(raw, `"t_ms":480`, `"t_ms":1001`, 1),
		strings.Replace(raw, `"probability":0.99`, `"probability":1.01`, 1),
		ready + "\n" + end + "\n" + wake,
		raw + "PRIVATE_SENTINEL",
	} {
		if _, ok := parseWakeOutput([]byte(bad), pin, 32000); ok {
			t.Fatal("invalid detector output was accepted")
		}
	}
}

func TestWakeConfigAndNoUnboundExecution(t *testing.T) {
	c := &wakeConfig{SourceClient: "/PRIVATE/io-channel", Detector: "/PRIVATE/mww",
		DetectorSHA256: strings.Repeat("a", 64), ModelSHA256: strings.Repeat("b", 64)}
	if !c.valid() {
		t.Fatal("reviewed wake configuration rejected")
	}
	for _, change := range []func(*wakeConfig){
		func(c *wakeConfig) { c.Detector = "relative" },
		func(c *wakeConfig) { c.SourceClient = "/PRIVATE/../helper" },
		func(c *wakeConfig) { c.DetectorSHA256 = "bad" },
	} {
		bad := *c
		change(&bad)
		if bad.valid() {
			t.Fatal("unsafe helper configuration accepted")
		}
	}
	n := nativeRuntime{}
	if n.listenWake(10)["outcome"] != "blocked" {
		t.Fatal("wake child started without a fresh preview")
	}
}
