"""Synthetic ALSA library only: no sound device or household audio is opened."""

import io
import json
import os
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "src/alice_mcp/native/pcm_player.c"


def wav(samples: bytes = b"\x01\x00" * 2205, rate: int = 22050) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples)
    return stream.getvalue()


@pytest.fixture
def player(tmp_path: Path) -> Path:
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("Synthetic PCM checks require an existing C compiler")
    stub = tmp_path / "alsa.c"
    stub.write_text(r'''
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdio.h>
__attribute__((constructor)) static void vendor_log(void) {
    if (getenv("TEST_VENDOR_STDOUT")) puts("PRIVATE_SENTINEL vendor log");
}
int snd_config_top(void **config) { *config = (void *)2; return 0; }
int snd_input_buffer_open(void **input, const char *text, long size) {
    if (size < 50 || !strstr(text, "type plug") || !strstr(text, "device 0") ||
        strstr(text, "device 2") || strstr(text, "type dmix") ||
        strstr(text, "capture") || strstr(text, "@hooks")) return -1;
    *input = (void *)3; return 0;
}
int snd_config_load(void *config, void *input) {
    (void)config; (void)input;
    if (getenv("TEST_STALL_CONFIG")) for (;;) pause();
    return getenv("TEST_POSITIVE_CONFIG") ? 1 : 0;
}
int snd_input_close(void *input) { (void)input; return 0; }
int snd_config_delete(void *config) { (void)config; return 0; }
int snd_pcm_open_lconf(void **pcm, const char *name, int stream, int flags, void *config) {
    if (getenv("TEST_FORBID_PCM_OPEN")) exit(91);
    if (strcmp(name, "alice_playback") || stream != 0 || flags != 1 ||
        config != (void *)2) return -1;
    *pcm = (void *)1; return 0;
}
static unsigned long written;
static int blocking;
int snd_pcm_nonblock(void *pcm, int nonblock) {
    (void)pcm;
    if (nonblock != 0 || written != 2205) return -EINVAL;
    if (getenv("TEST_NONBLOCK_FAILURE")) return -EIO;
    blocking = 1;
    return 0;
}
int snd_pcm_set_params(void *pcm, int format, int access, unsigned channels,
                       unsigned rate, int resample, unsigned latency) {
    (void)pcm;
    if (getenv("TEST_SETUP_FAILURE")) return -1;
    return format == 2 && access == 3 && channels == 1 && rate == 22050 &&
           resample == 1 && latency == 100000 ? 0 : -1;
}
long snd_pcm_writei(void *pcm, const void *data, unsigned long frames) {
    (void)pcm;
    static int calls;
    if (getenv("TEST_WRITE_FAILURE")) return -EIO;
    if (getenv("TEST_WRITE_AGAIN") && calls++ == 0) return -EAGAIN;
    unsigned long count = frames > 7 ? 7 : frames;
    if (getenv("TEST_WRITE_OFFSETS")) {
        const unsigned char *bytes = data;
        for (unsigned long i = 0; i < count; ++i)
            if (bytes[i * 2] != (written + i) % 100 || bytes[i * 2 + 1]) return -EINVAL;
    }
    written += count;
    return (long)count;
}
static int draining;
int snd_pcm_wait(void *pcm, int milliseconds) {
    (void)pcm; (void)milliseconds;
    return draining && getenv("TEST_DRAIN_RACE") ? -EIO : 1;
}
int snd_pcm_drain(void *pcm) {
    (void)pcm;
    draining = 1;
    if (getenv("TEST_STALL_DRAIN")) for (;;) pause();
    if (getenv("TEST_DRAIN_FAILURE")) return -EIO;
    if (getenv("TEST_DRAIN_RACE") && !blocking) return -EAGAIN;
    return 0;
}
int snd_pcm_drop(void *pcm) { (void)pcm; return 0; }
int snd_pcm_close(void *pcm) { (void)pcm; return getenv("TEST_CLOSE_FAILURE") ? -1 : 0; }
''')
    library = tmp_path / "alsa.so"
    executable = tmp_path / "pcm-player"
    subprocess.run([compiler, "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", str(stub),
                    "-o", str(library)], check=True, capture_output=True, timeout=30)
    subprocess.run([compiler, "-O2", "-Wall", "-Wextra", "-Werror",
                    f'-DPCM_ALSA_LIBRARY="{library}"', str(SOURCE), "-ldl", "-o", str(executable)],
                   check=True, capture_output=True, timeout=30)
    return executable


def test_pcm_player_writes_all_frames_without_mixer_or_capture_access(player: Path) -> None:
    result = subprocess.run([str(player), "--play"], input=wav(), capture_output=True, timeout=5)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "outcome": "complete", "playback_started": True, "playback_completed": True,
        "cleanup_verified": True, "frames_written": 2205, "sample_rate": 22050,
        "input_frames": 2205, "stage": "complete", "alsa_return_code": 0,
    }


def test_pcm_player_blocking_drain_avoids_nonblocking_wait_race(player: Path) -> None:
    result = subprocess.run([str(player), "--play"], input=wav(), capture_output=True, timeout=5,
                            env={**os.environ, "TEST_DRAIN_RACE": "1"})
    assert result.returncode == 0
    assert json.loads(result.stdout)["playback_completed"] is True


def test_pcm_player_partial_writes_and_eagain_preserve_sample_offsets(player: Path) -> None:
    data = b"".join(bytes((index % 100, 0)) for index in range(2205))
    result = subprocess.run([str(player), "--play"], input=wav(data), capture_output=True,
                            timeout=5, env={**os.environ, "TEST_WRITE_OFFSETS": "1",
                                            "TEST_WRITE_AGAIN": "1"})
    assert result.returncode == 0
    assert json.loads(result.stdout)["frames_written"] == 2205


def test_pcm_player_alarm_bounds_stuck_blocking_drain(player: Path) -> None:
    result = subprocess.run([str(player), "--play"], input=wav(), capture_output=True, timeout=28,
                            env={**os.environ, "TEST_STALL_DRAIN": "1"})
    assert result.returncode == 124 and result.stdout == b""


@pytest.mark.parametrize("data", [b"", b"PRIVATE_SENTINEL", wav()[:-1], wav() + b"extra",
                                  wav(b"\x00\x20"), wav(rate=8000),
                                  wav(b"\x01\x00" * 22050 * 21)],
                         ids=["empty", "not-wav", "truncated", "trailer", "loud", "rate", "long"])
def test_pcm_player_rejects_noncanonical_oversized_or_loud_wav(player: Path, data: bytes) -> None:
    result = subprocess.run([str(player), "--play"], input=data, capture_output=True, timeout=5)
    report = json.loads(result.stdout)
    assert result.returncode != 0 and report["outcome"] == "blocked"
    assert not report["playback_started"] and report["frames_written"] == 0
    assert "PRIVATE_SENTINEL" not in result.stdout.decode()


@pytest.mark.parametrize("failure", ["TEST_CLOSE_FAILURE", "TEST_WRITE_FAILURE",
                                     "TEST_DRAIN_FAILURE", "TEST_NONBLOCK_FAILURE"])
def test_pcm_player_never_calls_uncertain_playback_complete(player: Path, failure: str) -> None:
    result = subprocess.run([str(player), "--play"], input=wav(), capture_output=True, timeout=5,
                            env={**os.environ, failure: "1"})
    report = json.loads(result.stdout)
    assert result.returncode != 0 and report["outcome"] == "unknown"
    assert not report["playback_completed"]


def test_pcm_player_unclean_setup_is_unknown_even_before_first_frame(player: Path) -> None:
    result = subprocess.run([str(player), "--play"], input=wav(), capture_output=True, timeout=5,
                            env={**os.environ, "TEST_SETUP_FAILURE": "1",
                                 "TEST_CLOSE_FAILURE": "1"})
    report = json.loads(result.stdout)
    assert report["outcome"] == "unknown" and not report["cleanup_verified"]
    assert not report["playback_started"] and report["frames_written"] == 0


@pytest.mark.parametrize("failure,stage,code", [
    ("TEST_WRITE_FAILURE", "write", -5), ("TEST_DRAIN_FAILURE", "drain", -5),
    ("TEST_NONBLOCK_FAILURE", "blocking", -5), ("TEST_CLOSE_FAILURE", "cleanup", -1),
])
def test_pcm_failure_retains_exact_input_and_closed_stage_metadata(
    player: Path, failure: str, stage: str, code: int,
) -> None:
    result = subprocess.run([str(player), "--play"], input=wav(), capture_output=True, timeout=5,
                            env={**os.environ, failure: "1"})
    report = json.loads(result.stdout)
    assert report["input_frames"] == 2205
    assert report["stage"] == stage and report["alsa_return_code"] == code
    assert report["frames_written"] <= report["input_frames"]


def test_mini1_player_and_resident_use_the_reviewed_vendor_alsa_location() -> None:
    from alice_mcp.pcm_pilot import PLAYER_SHA256

    vendor_library = '"/system/vendor/quasar/libs/libasound.so.2"'
    assert vendor_library in SOURCE.read_text()
    resident = SOURCE.parent / "agent/pcm.go"
    assert vendor_library in resident.read_text()
    assert '"/usr/lib/libasound.so.2"' not in SOURCE.read_text() + resident.read_text()
    assert f'const pcmClientSHA256 = "{PLAYER_SHA256}"' in resident.read_text()


@pytest.mark.parametrize("positive", [False, True])
def test_pcm_diagnostic_loads_and_parses_without_reading_audio_or_opening_pcm(
    player: Path, positive: bool,
) -> None:
    result = subprocess.run([str(player), "--check"], input=b"", capture_output=True, timeout=5,
                            env={**os.environ, "TEST_FORBID_PCM_OPEN": "1",
                                 **({"TEST_POSITIVE_CONFIG": "1"} if positive else {})})
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "outcome": "ready", "diagnostic_only": True, "stage": "ready",
        "library_loaded": True, "configuration_parsed": True, "alsa_return_code": int(positive),
        "pcm_opened": False, "audio_played": False,
    }


def test_pcm_diagnostic_alarm_also_bounds_a_stuck_vendor_call(player: Path) -> None:
    result = subprocess.run([str(player), "--check"], input=b"", capture_output=True, timeout=7,
                            env={**os.environ, "TEST_STALL_CONFIG": "1"})
    assert result.returncode == 124 and result.stdout == b""


@pytest.mark.parametrize("mode", ["--check", "--play"])
def test_vendor_stdout_cannot_contaminate_the_json_channel(player: Path, mode: str) -> None:
    result = subprocess.run([str(player), mode], input=b"" if mode == "--check" else wav(),
                            capture_output=True, timeout=5,
                            env={**os.environ, "TEST_VENDOR_STDOUT": "1"})
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["outcome"] == ("ready" if mode == "--check" else "complete")
    assert b"PRIVATE_SENTINEL" not in result.stdout
