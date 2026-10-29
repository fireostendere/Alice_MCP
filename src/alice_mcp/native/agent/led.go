package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

// Mini 1 SN3236 show: the host maps an effect to at most 75 frames of 18 PWM
// bytes <= 0x10. Only PWM 0x01..0x12 and update 0x25 at bus 0/address 0x3c are
// written; no current/GCC, shutdown, reset, force or register readback.
type ledPayload struct {
	CaptureID  string   `json:"capture_id"`
	IntervalMS int      `json:"interval_ms"`
	Frames     []string `json:"frames"`
}

// Test-only prefix for synthetic sysfs/data trees; /proc/uptime is always real.
var ledRoot = ""

var openLED = func() (io.WriteCloser, error) {
	f, err := os.OpenFile("/dev/i2c-0", os.O_RDWR|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, errors.New("LED bus unavailable")
	}
	var funcs uint
	info, err := f.Stat()
	// I2C_FUNCS must offer plain I2C; I2C_SLAVE (not FORCE) fails if a driver owns 0x3c.
	if err == nil && info.Mode()&os.ModeCharDevice != 0 &&
		ioctl(f.Fd(), 0x0705, uintptr(unsafe.Pointer(&funcs))) == nil && funcs&1 != 0 &&
		ioctl(f.Fd(), 0x0703, 0x3c) == nil {
		return f, nil
	}
	_ = f.Close()
	return nil, errors.New("LED bus unavailable")
}

func ioctl(fd, request, argument uintptr) error {
	if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, request, argument); errno != 0 {
		return errno
	}
	return nil
}

func decodeLED(p ledPayload) ([][]byte, bool) {
	if !hex64(p.CaptureID+p.CaptureID) || p.IntervalMS < 200 || p.IntervalMS > 15000 ||
		len(p.Frames) < 1 || len(p.Frames) > 75 || len(p.Frames)*p.IntervalMS > 15000 {
		return nil, false
	}
	frames := make([][]byte, 0, len(p.Frames))
	for _, text := range p.Frames {
		frame, err := hex.DecodeString(text)
		if err != nil || len(frame) != 18 || hex.EncodeToString(frame) != text {
			return nil, false
		}
		for _, value := range frame {
			if value > 0x10 {
				return nil, false
			}
		}
		frames = append(frames, frame)
	}
	return frames, true
}

func readPlain(path string, limit int64) (string, bool) {
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return "", false
	}
	defer f.Close()
	info, err := f.Stat()
	data, readErr := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil || readErr != nil || !info.Mode().IsRegular() || int64(len(data)) > limit {
		return "", false
	}
	return strings.TrimRight(string(data), "\n"), true
}

func privateDir(path string) bool {
	info, err := os.Lstat(path)
	if err != nil {
		return false
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && info.IsDir() && info.Mode().Perm() == 0700 && stat.Uid == uint32(os.Geteuid())
}

func absent(path string) bool {
	_, err := os.Lstat(path)
	return errors.Is(err, os.ErrNotExist)
}

// Records must alternate six-identical-triple PWM frames with 25 aa updates, contain
// traffic and end in the observed all-zero idle frame, exactly like the shell helper.
func idleRecords(text string) bool {
	lines := strings.Split(text, "\n")
	zero := "01" + strings.Repeat("00", 18)
	nonzero, last := false, ""
	if len(lines) < 4 || len(lines) > 64 || len(lines)%2 != 0 {
		return false
	}
	for i, line := range lines {
		if i%2 == 1 {
			if line != "record=25aa" {
				return false
			}
			continue
		}
		data, ok := strings.CutPrefix(line, "record=")
		decoded, err := hex.DecodeString(data)
		if !ok || err != nil || len(decoded) != 19 || decoded[0] != 1 || hex.EncodeToString(decoded) != data {
			return false
		}
		for z := 1; z < 6; z++ {
			if data[2+6*z:8+6*z] != data[2:8] {
				return false
			}
		}
		nonzero, last = nonzero || data != zero, data
	}
	return nonzero && last == zero
}

// Same-boot, same-firmware, <=30-minute cleaned capture left by the SSH helper.
func idleEvidence(capture, firmware string) (string, bool) {
	data := ledRoot + "/data"
	if info, err := os.Lstat(data); err != nil {
		return "", false
	} else if info.Mode()&os.ModeSymlink != 0 {
		target, err := os.Readlink(data)
		if err != nil || target != "/bkupgrade/data" && target != "bkupgrade/data" {
			return "", false
		}
		data = ledRoot + "/bkupgrade/data"
	}
	base := data + "/opt/alice-mcp"
	out := base + "/led-captures/" + capture
	device := ledRoot + "/sys/bus/i2c/devices/0-003c/"
	if !hex64(capture+capture) || !privateDir(base) || !privateDir(base+"/led-captures") ||
		!privateDir(out) || !absent(base+"/led-capture-active") || !absent(device+"driver") {
		return "", false
	}
	for _, trace := range []string{"/sys/kernel/tracing", "/sys/kernel/debug/tracing"} {
		if !absent(ledRoot + trace + "/instances/alice-led-" + capture) {
			return "", false
		}
	}
	name, nameOK := readPlain(device+"name", 64)
	boot, bootErr := readLocal(ledRoot+"/proc/sys/kernel/random/boot_id", 128)
	uptime, upErr := readLocal("/proc/uptime", 256)
	fields := strings.Fields(string(uptime))
	ended, endedOK := readPlain(out+"/ended", 32)
	records, recordsOK := readPlain(out+"/records", 40000)
	if !nameOK || name != "sn3236" && name != "SN3236" || bootErr != nil || upErr != nil ||
		len(fields) != 2 || !endedOK || !recordsOK {
		return "", false
	}
	now, nowErr := strconv.ParseFloat(fields[0], 64)
	end, endErr := strconv.ParseInt(ended, 10, 64)
	age := int64(now) - end
	sum := sha256.Sum256(boot)
	value := func(name string) string {
		text, _ := readPlain(out+"/"+name, 256)
		return text
	}
	return base, nowErr == nil && endErr == nil && age >= 0 && age <= 1800 &&
		value("firmware") == firmware && value("phase") == "complete" && value("cleanup") == "1" &&
		value("boot") == hex.EncodeToString(sum[:]) && idleRecords(records)
}

func writePWM(bus io.Writer, frame []byte) error {
	if _, err := bus.Write(append([]byte{0x01}, frame...)); err != nil {
		return err
	}
	_, err := bus.Write([]byte{0x25, 0xaa})
	return err
}

func playLED(bus io.Writer, frames [][]byte, interval time.Duration) map[string]any {
	deadline := time.Now().Add(time.Duration(len(frames))*interval + 2*time.Second)
	done := true
	for _, frame := range frames {
		if writePWM(bus, frame) != nil {
			done = false
			break
		}
		time.Sleep(interval)
		if time.Now().After(deadline) {
			done = false
			break
		}
	}
	// One attempt at the captured idle zero frame; PWM is write-only, so no readback.
	restored := writePWM(bus, make([]byte, 18)) == nil
	outcome := "unknown"
	if done && restored {
		outcome = "applied"
	}
	return map[string]any{"outcome": outcome, "command_succeeded": done,
		"restoration_command_succeeded": restored, "restoration_verified": false}
}

// ponytail: agent termination mid-show skips the idle frame (as SIGKILL does for the
// shell helper); add a signal-time restore if shows ever get longer than 15 s.
func runShow(p ledPayload, firmware string) map[string]any {
	frames, ok := decodeLED(p)
	base, evidence := idleEvidence(p.CaptureID, firmware)
	if !ok || !evidence {
		return blocked("Recent same-boot idle LED capture required")
	}
	// Serialize with the cooperating SSH helpers on their existing owner-only lock.
	lock, err := os.OpenFile(base+"/control.lock", os.O_WRONLY|os.O_APPEND|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return blocked("LED control lock unavailable")
	}
	defer lock.Close()
	info, err := lock.Stat()
	if err != nil {
		return blocked("LED control lock unavailable")
	}
	stat, statOK := info.Sys().(*syscall.Stat_t)
	if !statOK || !info.Mode().IsRegular() || info.Mode().Perm() != 0600 ||
		stat.Uid != uint32(os.Geteuid()) || stat.Nlink != 1 ||
		syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		return blocked("LED control lock unavailable")
	}
	bus, err := openLED()
	if err != nil {
		return blocked("LED bus unavailable or owned by a kernel driver")
	}
	defer bus.Close()
	result := playLED(bus, frames, time.Duration(p.IntervalMS)*time.Millisecond)
	result["quasar_running"] = quasarRunning()
	result["do_not_retry"] = true
	result["evidence_class"] = "analytical"
	result["physical_effects_verified"] = false
	return result
}

func (n *nativeRuntime) showLED(p ledPayload, expected ...string) map[string]any {
	if !n.lock.TryLock() {
		return blocked("Another native action is active")
	}
	defer n.finishAction()
	if n.consumeState(expected) == "" {
		return blocked("Fresh state-bound preview required")
	}
	c, _, err := n.policy()
	if err != nil || n.model != "station-mini" || !c.permits("led.show") {
		return blocked("LED show is not preauthorized by the local policy")
	}
	if os.Geteuid() != 0 || hashPath(firmwarePath) != c.FirmwareSHA256 {
		return blocked("Reviewed firmware unavailable")
	}
	return runShow(p, c.FirmwareSHA256)
}
