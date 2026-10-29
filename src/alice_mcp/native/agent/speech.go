package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"io"
	"os"
	"os/exec"
	"strconv"
	"sync"
	"time"
)

const asrClientSHA256 = "ceeee7131f7ec3cb036e38979cc301a839152d32560b28f51d121d7bd8436f8b"

type capture struct {
	mu                         sync.Mutex
	id, phase, text, code      string
	seconds                    int
	started, deadline, expires time.Time
	stamp                      []byte
	done, clean, quasar        bool
	changed                    chan struct{}
}

func newCapture(id string, seconds int, stamp []byte) *capture {
	now := time.Now()
	return &capture{id: id, seconds: seconds, stamp: stamp, phase: "starting", started: now,
		deadline: now.Add(180 * time.Second), changed: make(chan struct{}, 1)}
}

func (s *capture) event(raw []byte) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	var e struct {
		Outcome     string  `json:"outcome"`
		Ready       *bool   `json:"capture_ready"`
		Remaining   *int    `json:"remaining_seconds"`
		Elapsed     *int    `json:"elapsed_seconds"`
		Text        *string `json:"text"`
		Code        string  `json:"error_code"`
		Diagnostics *struct {
			Frames   int `json:"frames_received"`
			Direct   int `json:"direct_states"`
			Listener int `json:"listener_states"`
		} `json:"diagnostics"`
	}
	if !decode(raw, &e) || e.Ready == nil {
		return false
	}
	switch e.Outcome {
	case "awaiting_idle", "ready":
		if s.phase != "starting" && s.phase != "awaiting_idle" || e.Text != nil || e.Code != "" || e.Diagnostics != nil || e.Remaining == nil {
			return false
		}
		limit := s.seconds
		if e.Outcome == "awaiting_idle" {
			limit = 180
		}
		if *e.Remaining < 1 || *e.Remaining > limit || *e.Ready != (e.Outcome == "ready") {
			return false
		}
		if e.Elapsed != nil && (*e.Elapsed < 0 || *e.Elapsed > 180) {
			return false
		}
		if e.Outcome == "awaiting_idle" {
			if s.phase != "starting" || e.Elapsed != nil {
				return false
			}
			s.phase = "awaiting_idle"
			s.deadline = s.started.Add(time.Duration(*e.Remaining) * time.Second)
		} else {
			elapsed := 0
			if e.Elapsed != nil {
				elapsed = *e.Elapsed
			}
			s.phase = "capturing"
			s.deadline = s.started.Add(time.Duration(elapsed+*e.Remaining) * time.Second)
		}
	case "complete":
		if s.phase != "capturing" || *e.Ready || e.Text == nil || !validText(*e.Text) || e.Remaining != nil || e.Elapsed != nil || e.Diagnostics != nil || e.Code != "" || !time.Now().Before(s.deadline) {
			return false
		}
		s.phase, s.text = "finishing", *e.Text
	case "blocked":
		if s.phase != "starting" && s.phase != "awaiting_idle" && s.phase != "capturing" || *e.Ready || e.Text != nil || e.Remaining != nil || e.Elapsed != nil {
			return false
		}
		valid := false
		for _, code := range []string{"protocol_error", "not_idle", "timeout", "request_changed", "no_final_text", "invalid_text", "recognition_failed", "frame_limit", "ipc_unavailable", "ipc_io_error", "ipc_closed", "interrupted", "clock_error", "output_closed", "signal_error"} {
			valid = valid || code == e.Code
		}
		if !valid {
			return false
		}
		if d := e.Diagnostics; d != nil && (d.Frames < 0 || d.Frames > 4096 || d.Direct < 0 || d.Listener < 0 || d.Direct+d.Listener > d.Frames) {
			return false
		}
		s.phase, s.code = "blocked", e.Code
	default:
		return false
	}
	select {
	case s.changed <- struct{}{}:
	default:
	}
	return true
}

func (s *capture) finish(clean, quasar bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.done, s.clean, s.quasar = true, clean, quasar
	s.expires = time.Now().Add(120 * time.Second)
	if clean && quasar && s.phase == "finishing" {
		s.phase = "complete"
	} else if !clean || !quasar || s.phase != "blocked" {
		s.phase, s.text, s.code = "unknown", "", "speech_capture_unverified"
	}
	select {
	case s.changed <- struct{}{}:
	default:
	}
}

func (s *capture) report(consume bool) map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.done && !time.Now().Before(s.expires) {
		s.phase, s.text = "expired", ""
	}
	remaining := max(0, int(time.Until(s.deadline)/time.Second))
	ready := s.phase == "capturing" && remaining >= 2 && !s.done
	outcome := s.phase
	if outcome == "capturing" && !ready {
		outcome = "unknown"
	}
	r := map[string]any{"outcome": outcome, "capture_id": s.id, "capture_ready": ready,
		"remaining_seconds": 0, "cleanup_verified": s.clean, "quasar_running": s.quasar,
		"do_not_retry": true, "untrusted_text": true, "evidence_class": "analytical"}
	if ready {
		r["remaining_seconds"] = remaining
	}
	if s.phase == "awaiting_idle" {
		r["setup_remaining_seconds"] = remaining
	}
	if s.code != "" {
		r["error_code"] = s.code
	}
	if consume && s.phase == "complete" && s.text != "" {
		r["text"], s.text, s.phase = s.text, "", "consumed"
	}
	return r
}

func (n *nativeRuntime) listen(seconds int, expected ...string) map[string]any {
	if seconds < 5 || seconds > 60 || !n.lock.TryLock() {
		return blocked("Another native action is active or duration is invalid")
	}
	// The worker owns the action lock until the bounded child has exited.
	transferred := false
	defer func() {
		if !transferred {
			n.finishAction()
		}
	}()
	started := time.Now()
	state := n.consumeState(expected)
	if state == "" {
		return blocked("Fresh state-bound preview required")
	}
	c, stamp, err := n.policy()
	if err != nil || !c.permits("speech.listen") || os.Geteuid() != 0 || !quasarRunning() || hashPath(firmwarePath) != c.FirmwareSHA256 {
		return blocked("ASR policy, firmware or running Quasar unavailable")
	}
	f, err := trustedFile(c.ASRClient, false)
	if err != nil {
		return blocked("Reviewed ASR artifact unavailable")
	}
	digest, err := hashFile(f, 65536)
	_, fresh, policyErr := n.policy()
	if err != nil || digest != asrClientSHA256 || policyErr != nil || !bytes.Equal(stamp, fresh) || !n.stateMatches(state) || time.Since(started) >= 300*time.Second {
		f.Close()
		return blocked("Reviewed ASR artifact or policy changed")
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		f.Close()
		return blocked("Capture id unavailable")
	}
	s := newCapture(hex.EncodeToString(nonce[:]), seconds, stamp)
	n.sessionsMu.Lock()
	n.capture = s
	n.sessionsMu.Unlock()
	transferred = true
	go n.captureWorker(s, f)
	select {
	case <-s.changed:
	case <-time.After(5 * time.Second):
	}
	return s.report(false)
}

func (n *nativeRuntime) captureWorker(s *capture, f *os.File) {
	defer n.finishAction()
	defer f.Close()
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(180+s.seconds+5)*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "/proc/self/fd/3", "--listen", strconv.Itoa(s.seconds))
	cmd.ExtraFiles, cmd.Stderr = []*os.File{f}, io.Discard
	cmd.WaitDelay = 2 * time.Second
	stream, err := cmd.StdoutPipe()
	if err != nil || cmd.Start() != nil {
		s.finish(false, quasarRunning())
		return
	}
	scanner := bufio.NewScanner(stream)
	scanner.Buffer(make([]byte, 4096), 4096)
	valid, count, total := true, 0, 0
	for scanner.Scan() {
		count++
		total += len(scanner.Bytes())
		if count > 3 || total > 8192 || !s.event(scanner.Bytes()) {
			valid = false
			cancel()
			break
		}
	}
	if scanner.Err() != nil {
		valid = false
		cancel()
	}
	exitErr := cmd.Wait()
	s.finish(valid && exitErr == nil, quasarRunning())
	// ponytail: a single bounded RAM session; no household transcript files.
	time.AfterFunc(120*time.Second, func() { s.mu.Lock(); s.text = ""; s.mu.Unlock() })
}

func (n *nativeRuntime) captureStatus(id string) map[string]any {
	n.sessionsMu.Lock()
	s := n.capture
	n.sessionsMu.Unlock()
	c, stamp, err := n.policy()
	if s == nil || s.id != id {
		return blocked("Unknown capture")
	}
	if err != nil || !c.permits("speech.listen") || !bytes.Equal(stamp, s.stamp) {
		s.mu.Lock()
		s.text = ""
		s.mu.Unlock()
		return blocked("Capture policy changed or permission revoked")
	}
	return s.report(true)
}
