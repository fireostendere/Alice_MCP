package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

const pcmClientSHA256 = "123d9d4221aa2054ff150202c0f4f5a36b008c93335d7c80cd76846d1bcd6087"
const maxWAV = 44 + 48000*2*20
const maxPCMRequest = 2600000

type pcmConfig struct {
	Client           string `json:"client"`
	ALSAConfigSHA256 string `json:"alsa_config_sha256"`
	LibrarySHA256    string `json:"library_sha256"`
}

type pcmPayload struct {
	WAV    string `json:"wav_base64"`
	SHA256 string `json:"sha256"`
}

func decodePCM(p pcmPayload) ([]byte, bool) {
	if !hex64(p.SHA256) || len(p.WAV) > base64.StdEncoding.EncodedLen(maxWAV) {
		return nil, false
	}
	wav, err := base64.StdEncoding.Strict().DecodeString(p.WAV)
	if err != nil || len(wav) < 46 || len(wav) > maxWAV || len(wav)%2 != 0 ||
		base64.StdEncoding.EncodeToString(wav) != p.WAV {
		return nil, false
	}
	digest := sha256.Sum256(wav)
	if hex.EncodeToString(digest[:]) != p.SHA256 || string(wav[:4]) != "RIFF" ||
		binary.LittleEndian.Uint32(wav[4:]) != uint32(len(wav)-8) || string(wav[8:16]) != "WAVEfmt " ||
		binary.LittleEndian.Uint32(wav[16:]) != 16 || binary.LittleEndian.Uint16(wav[20:]) != 1 ||
		binary.LittleEndian.Uint16(wav[22:]) != 1 || binary.LittleEndian.Uint16(wav[32:]) != 2 ||
		binary.LittleEndian.Uint16(wav[34:]) != 16 || string(wav[36:40]) != "data" ||
		binary.LittleEndian.Uint32(wav[40:]) != uint32(len(wav)-44) {
		return nil, false
	}
	rate := binary.LittleEndian.Uint32(wav[24:])
	if rate < 16000 || rate > 48000 || binary.LittleEndian.Uint32(wav[28:]) != rate*2 ||
		uint32(len(wav)-44) > rate*2*20 {
		return nil, false
	}
	for i := 44; i < len(wav); i += 2 {
		sample := int16(binary.LittleEndian.Uint16(wav[i:]))
		if sample < -8191 || sample > 8191 {
			return nil, false
		}
	}
	return wav, true
}

func pcmFlags(raw []byte, frames, rate int) (map[string]any, bool) {
	var result struct {
		Outcome   string `json:"outcome"`
		Started   *bool  `json:"playback_started"`
		Completed *bool  `json:"playback_completed"`
		Clean     *bool  `json:"cleanup_verified"`
		Frames    *int   `json:"frames_written"`
		Rate      *int   `json:"sample_rate"`
		Input     *int   `json:"input_frames"`
		Stage     string `json:"stage"`
		Code      *int   `json:"alsa_return_code"`
	}
	if !decode(raw, &result) || result.Started == nil || result.Completed == nil ||
		result.Clean == nil || result.Frames == nil || result.Rate == nil || *result.Rate != rate ||
		result.Input == nil || *result.Input != frames || result.Code == nil ||
		*result.Code < -4095 || int64(*result.Code) > 2147483647 ||
		*result.Frames < 0 || *result.Frames > frames || *result.Started != (*result.Frames > 0) {
		return nil, false
	}
	switch result.Stage {
	case "request", "library", "symbols", "configuration", "open", "parameters", "clock",
		"write", "wait", "blocking", "drain", "cleanup", "complete":
	default:
		return nil, false
	}
	outcome := result.Outcome
	switch outcome {
	case "complete":
		if !*result.Started || !*result.Completed || !*result.Clean || *result.Frames != frames ||
			result.Stage != "complete" || *result.Code < 0 {
			return nil, false
		}
		outcome = "applied"
	case "blocked", "unknown":
		if *result.Completed || outcome == "blocked" && *result.Started {
			return nil, false
		}
	default:
		return nil, false
	}
	return map[string]any{"outcome": outcome, "playback_started": *result.Started,
		"playback_completed": *result.Completed, "cleanup_verified": *result.Clean,
		"frames_written": *result.Frames, "sample_rate": *result.Rate,
		"input_frames": *result.Input, "stage": result.Stage, "alsa_return_code": *result.Code}, true
}

func (n *nativeRuntime) playPCM(p pcmPayload, expected ...string) map[string]any {
	if !n.lock.TryLock() {
		return blocked("Another native action is active")
	}
	defer n.finishAction()
	started := time.Now()
	state := n.consumeState(expected)
	if state == "" {
		return blocked("Fresh state-bound preview required")
	}
	c, stamp, err := n.policy()
	if err != nil || c.LocalPCM == nil || !c.permits("speech.say") || n.model != "station-mini" {
		return blocked("Local PCM is not preauthorized by the resident policy")
	}
	wav, valid := decodePCM(p)
	if !valid {
		return blocked("Local PCM outside reviewed bounds")
	}
	library, err := filepath.EvalSymlinks("/system/vendor/quasar/libs/libasound.so.2")
	playback, nodeErr := os.Stat("/dev/snd/pcmC0D0p")
	if err != nil || nodeErr != nil || playback.Mode()&os.ModeCharDevice == 0 ||
		os.Geteuid() != 0 || !quasarRunning() || hashPath(firmwarePath) != c.FirmwareSHA256 ||
		hashPath("/etc/asound.conf") != c.LocalPCM.ALSAConfigSHA256 || hashPath(library) != c.LocalPCM.LibrarySHA256 {
		return blocked("Reviewed local playback firmware, ALSA or Quasar unavailable")
	}
	client, err := trustedFile(c.LocalPCM.Client, false)
	if err != nil {
		return blocked("Reviewed PCM player unavailable")
	}
	defer client.Close()
	digest, err := hashFile(client, 65536)
	_, fresh, policyErr := n.policy()
	if err != nil || digest != pcmClientSHA256 || policyErr != nil || !bytes.Equal(stamp, fresh) || !n.stateMatches(state) || time.Since(started) >= 300*time.Second {
		return blocked("Reviewed PCM player or policy changed")
	}
	rate, frames := int(binary.LittleEndian.Uint32(wav[24:])), (len(wav)-44)/2
	ctx, cancel := context.WithTimeout(context.Background(), 27*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "/proc/self/fd/3", "--play")
	command.ExtraFiles, command.Stdin, command.Stderr = []*os.File{client}, bytes.NewReader(wav), io.Discard
	// The inspected vendor library contains hw/plug/dmix; do not load external plugins.
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C", "LC_ALL=C", "HOME=/nonexistent", "ALSA_PLUGIN_DIR=/nonexistent"}
	command.WaitDelay = time.Second
	output := &boundedOutput{}
	command.Stdout = output
	execErr := command.Run()
	result, valid := pcmFlags(output.Bytes(), frames, rate)
	if !valid || execErr != nil && result["outcome"] == "applied" {
		result = map[string]any{"outcome": "unknown", "playback_started": false,
			"playback_completed": false, "cleanup_verified": false, "frames_written": 0,
			"sample_rate": rate, "input_frames": frames, "stage": "unverified", "alsa_return_code": nil}
	}
	_, fresh, policyErr = n.policy()
	running := quasarRunning()
	if policyErr != nil || !bytes.Equal(stamp, fresh) || !running ||
		command.ProcessState == nil || !command.ProcessState.Exited() {
		result["outcome"], result["playback_completed"], result["cleanup_verified"] = "unknown", false, false
	}
	result["quasar_running"], result["do_not_retry"], result["tts_backend"] = running, true, "local_pcm"
	result["evidence_class"], result["physical_effects_verified"] = "analytical", false
	return result
}
