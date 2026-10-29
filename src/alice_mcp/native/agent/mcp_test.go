package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

const testToken = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

func testServer(t *testing.T, calls *int) *server {
	t.Helper()
	credentialPath := filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(credentialPath, []byte(testToken), 0600); err != nil {
		t.Fatal("cannot create synthetic token")
	}
	return &server{
		stationID: "desk", model: "station-mini", tokenFile: credentialPath,
		controls: func() map[string]any {
			return map[string]any{"station_id": "desk", "model": "station-mini",
				"allowed_actions": []string{"speech.say"}, "preauthorize_all_actions": true}
		},
		probe: func(name string) map[string]any {
			return map[string]any{"data": map[string]any{"kernel_arch": "armv7l"}}
		},
		run: func(text string, expected ...string) map[string]any {
			*calls++
			return map[string]any{"outcome": "unknown", "request_sent": true,
				"do_not_retry": true, "error_code": "timeout"}
		},
	}
}

func request(t *testing.T, s *server, body, token string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(http.MethodPost, "https://station.invalid/mcp", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Accept", "application/json, text/event-stream")
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	return w
}

func TestNoAuthenticationNeverReachesAnAction(t *testing.T) {
	calls := 0
	s := testServer(t, &calls)
	for _, credentialValue := range []string{"", "wrong", testToken + "0"} {
		w := request(t, s, `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`, credentialValue)
		if w.Code != http.StatusUnauthorized || calls != 0 {
			t.Fatal("unauthenticated request was not rejected")
		}
	}
}

func TestInitializationAndOnlyTypedTools(t *testing.T) {
	calls := 0
	s := testServer(t, &calls)
	initialize := request(t, s, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"synthetic","version":"1"}}}`, testToken)
	if initialize.Code != 200 || !strings.Contains(initialize.Body.String(), `"protocolVersion":"2025-11-25"`) {
		t.Fatal("MCP initialization failed")
	}
	notification := request(t, s, `{"jsonrpc":"2.0","method":"notifications/initialized"}`, testToken)
	if notification.Code != 202 || notification.Body.Len() != 0 {
		t.Fatal("initialization notification must not fabricate an RPC response")
	}
	listed := request(t, s, `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`, testToken)
	var response struct {
		Result struct {
			Tools []struct{ Name string } `json:"tools"`
		} `json:"result"`
	}
	if json.Unmarshal(listed.Body.Bytes(), &response) != nil || len(response.Result.Tools) != 5 {
		t.Fatal("typed tool catalog unavailable")
	}
	for _, tool := range response.Result.Tools {
		if tool.Name != "get_capabilities" && tool.Name != "get_station_controls" && tool.Name != "station_probe" && tool.Name != "run_station_action" && tool.Name != "get_capture_status" {
			t.Fatal("unexpected native tool")
		}
	}
	if calls != 0 {
		t.Fatal("inspection performed an action")
	}
}

func TestOneActionIsNotRetriedAndDoesNotEchoText(t *testing.T) {
	calls := 0
	s := testServer(t, &calls)
	w := request(t, s, `{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"run_station_action","arguments":{"station_id":"desk","expected_state_sha256":"`+testToken+`","action":"speech.say","parameters":{"text":"PRIVATE_SENTINEL"}}}}`, testToken)
	if w.Code != 200 || calls != 1 || strings.Contains(w.Body.String(), "PRIVATE_SENTINEL") || !strings.Contains(w.Body.String(), `"do_not_retry":true`) {
		t.Fatal("action result lost one-shot/privacy guarantees")
	}
}

func TestNativeWriteWithoutStateBindingNeverDispatches(t *testing.T) {
	calls := 0
	s := testServer(t, &calls)
	for _, binding := range []string{"", `,"expected_state_sha256":"invalid"`} {
		w := request(t, s, `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_station_action","arguments":{"station_id":"desk","action":"speech.say","parameters":{"text":"synthetic"}`+binding+`}}}`, testToken)
		if calls != 0 || !strings.Contains(w.Body.String(), `"error"`) {
			t.Fatal("unbound write reached the runtime")
		}
	}
}

func TestPCMRequestIsTypedOnceAndNeverRoutedToCloud(t *testing.T) {
	calls, pcmCalls := 0, 0
	s := testServer(t, &calls)
	audio := syntheticWAV()
	sum := sha256.Sum256(audio)
	s.pcm = func(p pcmPayload, expected ...string) map[string]any {
		pcmCalls++
		if p.SHA256 != hex.EncodeToString(sum[:]) {
			t.Fatal("PCM payload changed")
		}
		return map[string]any{"outcome": "applied", "tts_backend": "local_pcm", "do_not_retry": true}
	}
	params := map[string]any{"name": "run_station_action", "arguments": map[string]any{
		"station_id": "desk", "expected_state_sha256": testToken, "action": "speech.say", "parameters": pcmPayload{
			WAV: base64.StdEncoding.EncodeToString(audio), SHA256: hex.EncodeToString(sum[:])}}}
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
	w := request(t, s, string(body), testToken)
	if w.Code != 200 || pcmCalls != 1 || calls != 0 || !strings.Contains(w.Body.String(), `"tts_backend":"local_pcm"`) {
		t.Fatal("local PCM was lost, repeated or routed to cloud")
	}
}

func TestWrongTargetsUnknownFieldsAndHostileTextNeverExecute(t *testing.T) {
	calls := 0
	s := testServer(t, &calls)
	for _, arguments := range []string{
		`{"station_id":"other","action":"speech.say","parameters":{"text":"synthetic"}}`,
		`{"station_id":"desk","action":"shell","parameters":{"command":"id"}}`,
		`{"station_id":"desk","action":"speech.say","parameters":{"text":"synthetic","path":"/PRIVATE/other"}}`,
		`{"station_id":"desk","action":"speech.say","parameters":{"text":"a\nline"}}`,
		`{"station_id":"desk","action":"speech.say","parameters":{"text":""}}`,
		`{"station_id":"desk","action":"speech.say","parameters":{"text":"a\u202eb"}}`,
		`{"station_id":"desk","action":"speech.say","parameters":{"text":"\ud800"}}`,
		`{"station_id":"other","station_id":"desk","action":"speech.say","parameters":{"text":"synthetic"}}`,
		`{"station_id":"desk","action":"speech.say","parameters":{"text":"` + strings.Repeat("x", 257) + `"}}`,
	} {
		arguments = strings.Replace(arguments, `"action":`, `"expected_state_sha256":"`+testToken+`","action":`, 1)
		body := `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_station_action","arguments":` + arguments + `}}`
		w := request(t, s, body, testToken)
		if calls != 0 || !strings.Contains(w.Body.String(), `"error"`) {
			t.Fatal("invalid action reached the runtime")
		}
	}
}

func TestValidSurrogatePairsAndLiteralEscapesArePreserved(t *testing.T) {
	for _, data := range []string{`{"text":"\ud83d\ude00"}`, `{"text":"literal \\ud800"}`} {
		var value struct{ Text string }
		if !decode([]byte(data), &value) || !validText(value.Text) {
			t.Fatal("valid scalar text rejected")
		}
	}
}

func TestOriginWrongContentTypeAndOversizedBodyAreRejected(t *testing.T) {
	calls := 0
	s := testServer(t, &calls)
	for _, mode := range []string{"origin", "content", "oversized", "get"} {
		r := httptest.NewRequest("POST", "https://station.invalid/mcp", bytes.NewReader(bytes.Repeat([]byte("x"), 20000)))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Authorization", "Bearer "+testToken)
		switch mode {
		case "origin":
			r.Header.Set("Origin", "https://untrusted.invalid")
		case "content":
			r.Header.Set("Content-Type", "text/plain")
		case "get":
			r.Method = "GET"
		}
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		if w.Code < 400 || calls != 0 {
			t.Fatal("unsafe HTTP request accepted")
		}
	}
}

func TestTokenRevocationAndSymlinkFailClosed(t *testing.T) {
	calls := 0
	s := testServer(t, &calls)
	if os.WriteFile(s.tokenFile, []byte(strings.Repeat("b", 64)), 0600) != nil {
		t.Fatal("cannot replace synthetic token")
	}
	w := request(t, s, `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`, testToken)
	if w.Code != http.StatusUnauthorized {
		t.Fatal("old token was not revoked")
	}
	target := s.tokenFile + ".actual"
	if os.Rename(s.tokenFile, target) != nil || os.Symlink(target, s.tokenFile) != nil {
		t.Fatal("cannot prepare synthetic symlink")
	}
	w = request(t, s, `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`, strings.Repeat("b", 64))
	if w.Code != http.StatusUnauthorized || calls != 0 {
		t.Fatal("symlink token was trusted")
	}
}

func TestTLSOnlyListener(t *testing.T) {
	calls := 0
	s := testServer(t, &calls)
	tlsServer := httptest.NewUnstartedServer(s)
	tlsServer.TLS = tlsSettings()
	tlsServer.StartTLS()
	defer tlsServer.Close()
	r, err := http.NewRequest("POST", tlsServer.URL+"/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/list"}`))
	if err != nil {
		t.Fatal("request construction failed")
	}
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer "+testToken)
	response, err := tlsServer.Client().Do(r)
	if err != nil {
		t.Fatal("trusted TLS client failed")
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	if response.StatusCode != 200 || response.TLS == nil || response.TLS.Version < 0x0304 {
		t.Fatal("TLS 1.3 was not enforced")
	}
}

func TestStandardPythonMCPClient(t *testing.T) {
	python := os.Getenv("ALICE_TEST_PYTHON")
	if python == "" {
		t.Skip("optional standard MCP SDK interoperability check")
	}
	calls := 0
	s := testServer(t, &calls)
	var count atomic.Int32
	s.run = func(text string, expected ...string) map[string]any {
		count.Add(1)
		return map[string]any{"outcome": "unknown", "do_not_retry": true}
	}
	peer := httptest.NewUnstartedServer(s)
	peer.TLS = tlsSettings()
	peer.StartTLS()
	defer peer.Close()
	certificate := filepath.Join(t.TempDir(), "certificate")
	data := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: peer.Certificate().Raw})
	if os.WriteFile(certificate, data, 0600) != nil {
		t.Fatal("cannot create synthetic trust certificate")
	}
	script := `import asyncio,ssl,sys,httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def run():
    context=ssl.create_default_context(cafile=sys.argv[2])
    async with httpx.AsyncClient(verify=context,headers=[("Authorization","Bearer "+sys.argv[3])]) as client:
        async with streamable_http_client(sys.argv[1],http_client=client,terminate_on_close=False) as (read,write,_):
            async with ClientSession(read,write) as session:
                await session.initialize()
                listed=await session.list_tools()
                assert len(listed.tools)==5
                result=await session.call_tool("run_station_action",{"station_id":"desk","expected_state_sha256":"` + testToken + `","action":"speech.say","parameters":{"text":"synthetic"}})
                assert result.structuredContent["outcome"]=="unknown" and result.structuredContent["do_not_retry"]
asyncio.run(run())
`
	command := exec.Command(python, "-c", script, peer.URL+"/mcp", certificate, testToken)
	if err := command.Run(); err != nil || count.Load() != 1 {
		t.Fatal("standard MCP SDK interoperability failed")
	}
}
