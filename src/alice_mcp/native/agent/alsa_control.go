package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"os"
	"runtime"
	"strconv"
	"syscall"
	"unsafe"
)

// Linux UAPI sound/asound.h. ARM EABI and amd64 align the value union at byte 72.
const alsaWord = strconv.IntSize / 8
const alsaValueSize = 72 + 128*alsaWord + 128

type alsaTransaction func(uintptr, []byte) error

func alsaLong(b []byte) int64 {
	if alsaWord == 4 {
		return int64(int32(binary.LittleEndian.Uint32(b)))
	}
	return int64(binary.LittleEndian.Uint64(b))
}
func putALSALong(b []byte, v int64) {
	if alsaWord == 4 {
		binary.LittleEndian.PutUint32(b, uint32(v))
	} else {
		binary.LittleEndian.PutUint64(b, uint64(v))
	}
}
func alsaRequest(number, size int) uintptr {
	return uintptr(3)<<30 | uintptr(size)<<16 | uintptr('U')<<8 | uintptr(number)
}

func openALSAControl(v *volumeConfig) (*os.File, error) {
	if !v.valid() || v.Backend != "alsa_ctl" || runtime.GOOS != "linux" ||
		runtime.GOARCH != "arm" && runtime.GOARCH != "amd64" {
		return nil, errors.New("ALSA control mapping unavailable")
	}
	f, err := os.OpenFile("/dev/snd/controlC"+strconv.Itoa(v.Card), os.O_RDWR|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	info, err := f.Stat()
	if err == nil {
		if s, ok := info.Sys().(*syscall.Stat_t); ok && info.Mode()&os.ModeCharDevice != 0 &&
			s.Rdev>>8&255 == 116 && s.Rdev&255 == uint64(v.Card*32) {
			return f, nil
		}
	}
	f.Close()
	return nil, errors.New("Unexpected ALSA control node")
}

func alsaTransactions(f *os.File) alsaTransaction {
	return func(request uintptr, b []byte) error {
		err := ioctl(f.Fd(), request, uintptr(unsafe.Pointer(&b[0])))
		runtime.KeepAlive(b)
		return err
	}
}

func alsaID(v *volumeConfig, size int) []byte {
	b := make([]byte, size)
	binary.LittleEndian.PutUint32(b, uint32(v.NumID))
	return b
}

func readALSAControl(v *volumeConfig, tx alsaTransaction) (int, bool) {
	if !v.valid() || v.Backend != "alsa_ctl" {
		return 0, false
	}
	info := alsaID(v, 272)
	if tx(alsaRequest(0x11, len(info)), info) != nil || binary.LittleEndian.Uint32(info) != uint32(v.NumID) ||
		binary.LittleEndian.Uint32(info[4:]) != 2 || string(bytes.TrimRight(info[16:60], "\x00")) != v.Control ||
		binary.LittleEndian.Uint32(info[64:]) != 2 || binary.LittleEndian.Uint32(info[68:])&3 != 3 ||
		binary.LittleEndian.Uint32(info[68:])&0x700 != 0 || binary.LittleEndian.Uint32(info[72:]) != uint32(v.Channels) ||
		alsaLong(info[80:]) != v.RawMin || alsaLong(info[80+alsaWord:]) != v.RawMax || alsaLong(info[80+2*alsaWord:]) != 1 {
		return 0, false
	}
	values := alsaID(v, alsaValueSize)
	if tx(alsaRequest(0x12, len(values)), values) != nil || binary.LittleEndian.Uint32(values) != uint32(v.NumID) {
		return 0, false
	}
	value := alsaLong(values[72:])
	if value < v.RawMin || value > v.RawMax {
		return 0, false
	}
	for i := 1; i < v.Channels; i++ {
		if alsaLong(values[72+i*alsaWord:]) != value {
			return 0, false
		}
	}
	span := v.RawMax - v.RawMin
	return int(((value-v.RawMin)*100 + span/2) / span), true
}

func changeALSAControl(v *volumeConfig, r volumeRequest, tx alsaTransaction) map[string]any {
	before, ok := readALSAControl(v, tx)
	if !ok || !validVolume(r) {
		return blocked("Reviewed ALSA playback control unavailable")
	}
	ceiling := v.MaxPercent
	if r.MaxPercent != nil {
		ceiling = min(ceiling, *r.MaxPercent)
	}
	target, ok := volumeTarget(r, before, ceiling)
	if !ok {
		return blocked("No volume change within the reviewed ceiling")
	}
	// Re-read metadata and current value immediately before the single write.
	if current, ok := readALSAControl(v, tx); !ok || current != before {
		return blocked("ALSA control changed before write")
	}
	values := alsaID(v, alsaValueSize)
	raw := v.RawMin + (v.RawMax-v.RawMin)*int64(target)/100
	for i := 0; i < v.Channels; i++ {
		putALSALong(values[72+i*alsaWord:], raw)
	}
	err := tx(alsaRequest(0x13, len(values)), values)
	after, verified := readALSAControl(v, tx)
	result := map[string]any{"outcome": "unknown", "previous_percent": before, "percent": nil}
	if verified {
		result["percent"] = after
	}
	if err == nil && verified && after == target {
		result["outcome"] = "applied"
	}
	return result
}
