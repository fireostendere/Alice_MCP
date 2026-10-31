package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestALSAControlLayoutMatchesLinuxUAPI(t *testing.T) {
	cc, err := exec.LookPath("cc")
	if err != nil {
		t.Skip("Linux UAPI/host C compiler unavailable")
	}
	root := t.TempDir()
	source := filepath.Join(root, "layout.c")
	binaryPath := filepath.Join(root, "layout")
	text := `#include <stdio.h>
#include <stddef.h>
#include <sys/ioctl.h>
#include <sound/asound.h>
int main(void) {
 printf("%zu %zu %zu %lu %lu", sizeof(struct snd_ctl_elem_info),
 sizeof(struct snd_ctl_elem_value), offsetof(struct snd_ctl_elem_value,value),
 (unsigned long)SNDRV_CTL_IOCTL_ELEM_INFO,(unsigned long)SNDRV_CTL_IOCTL_ELEM_WRITE);
 return 0;
}`
	if os.WriteFile(source, []byte(text), 0600) != nil {
		t.Fatal("cannot write ABI fixture")
	}
	if output, err := exec.Command(cc, source, "-o", binaryPath).CombinedOutput(); err != nil {
		t.Fatal(err, string(output))
	}
	output, err := exec.Command(binaryPath).Output()
	expected := fmt.Sprintf("272 %d 72 %d %d", alsaValueSize, alsaRequest(0x11, 272), alsaRequest(0x13, alsaValueSize))
	if err != nil || string(output) != expected {
		t.Fatal("ALSA UAPI layout mismatch", string(output), expected)
	}
}

func TestALSAControlUsesReviewedPlaybackMetadataAndWritesOnce(t *testing.T) {
	v := &volumeConfig{Backend: "alsa_ctl", Card: 0, Control: "PCM Playback Volume", MaxPercent: 30,
		NumID: 7, RawMin: 0, RawMax: 255, Channels: 2}
	if !v.valid() {
		t.Fatal("reviewed ioctl mapping rejected")
	}
	current, writes := int64(64), 0
	transaction := func(request uintptr, b []byte) error {
		if binary.LittleEndian.Uint32(b) != 7 {
			t.Fatal("wrong control ID")
		}
		switch request & 255 {
		case 0x11:
			binary.LittleEndian.PutUint32(b[4:], 2)
			copy(b[16:60], v.Control)
			binary.LittleEndian.PutUint32(b[64:], 2)
			binary.LittleEndian.PutUint32(b[68:], 3)
			binary.LittleEndian.PutUint32(b[72:], 2)
			putALSALong(b[80:], 0)
			putALSALong(b[80+alsaWord:], 255)
			putALSALong(b[80+2*alsaWord:], 1)
		case 0x12:
			putALSALong(b[72:], current)
			putALSALong(b[72+alsaWord:], current)
		case 0x13:
			writes++
			current = alsaLong(b[72:])
		default:
			t.Fatal("unexpected ALSA ioctl")
		}
		return nil
	}
	percent := 20
	got := changeALSAControl(v, volumeRequest{Percent: &percent}, transaction)
	if got["outcome"] != "applied" || writes != 1 || current != 51 || got["previous_percent"] != 25 {
		t.Fatal(got)
	}
	failed := func(request uintptr, b []byte) error {
		if request&255 == 0x13 {
			writes++
			return errors.New("uncertain write")
		}
		return transaction(request, b)
	}
	got = changeALSAControl(v, volumeRequest{Percent: &percent}, failed)
	if got["outcome"] != "unknown" || writes != 2 {
		t.Fatal("uncertain ioctl was retried", got)
	}
	bad := *v
	bad.Control = "Capture Volume"
	if bad.valid() {
		t.Fatal("capture control accepted")
	}
	bad = *v
	bad.Amixer = "/PRIVATE/amixer"
	bad.AmixerSHA256 = strings.Repeat("a", 64)
	if bad.valid() {
		t.Fatal("ambiguous backends accepted")
	}
}

func TestALSAControlMetadataMismatchBlocksBeforeWrite(t *testing.T) {
	v := &volumeConfig{Backend: "alsa_ctl", Control: "PCM Playback Volume", NumID: 7, RawMax: 255, Channels: 1, MaxPercent: 30}
	percent := 20
	got := changeALSAControl(v, volumeRequest{Percent: &percent}, func(request uintptr, b []byte) error {
		if request&255 != 0x11 {
			t.Fatal("write/read reached an unreviewed control")
		}
		return nil
	})
	if got["outcome"] != "blocked" {
		t.Fatal(got)
	}
}

func TestALSAControlCoarseIntegerStepsAndUnspecifiedStep(t *testing.T) {
	for _, step := range []int64{0, 1, 2, -1} {
		v := &volumeConfig{Backend: "alsa_ctl", Control: "Master", NumID: 7, RawMax: 32, Channels: 2, MaxPercent: 30}
		current, writes := int64(10), 0
		tx := func(request uintptr, b []byte) error {
			switch request & 255 {
			case 0x11:
				binary.LittleEndian.PutUint32(b[4:], 2)
				copy(b[16:60], v.Control)
				binary.LittleEndian.PutUint32(b[64:], 2)
				binary.LittleEndian.PutUint32(b[68:], 3)
				binary.LittleEndian.PutUint32(b[72:], 2)
				putALSALong(b[80+alsaWord:], 32)
				putALSALong(b[80+2*alsaWord:], step)
			case 0x12:
				putALSALong(b[72:], current)
				putALSALong(b[72+alsaWord:], current)
			case 0x13:
				writes++
				current = alsaLong(b[72:])
			}
			return nil
		}
		percent := 20
		got := changeALSAControl(v, volumeRequest{Percent: &percent}, tx)
		if step == 0 || step == 1 {
			if got["outcome"] != "applied" || got["percent"] != 19 || current != 6 || writes != 1 {
				t.Fatalf("step %d: coarse control: %v, writes %d", step, got, writes)
			}
		} else if got["outcome"] != "blocked" || writes != 0 {
			t.Fatal("unsupported step reached write", got)
		}
	}
}
