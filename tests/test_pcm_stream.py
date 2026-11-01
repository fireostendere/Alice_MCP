"""Streaming playback mode of pcm_player.c with a synthetic ALSA; never opens a device."""

import json
import shutil
import struct
import subprocess
import time
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "src/alice_mcp/native/pcm_player.c"
HW_PARAMS = ("access: RW_INTERLEAVED\nformat: S32_LE\nsubformat: STD\nchannels: 2\n"
             "rate: 48000 (48000/1)\nperiod_size: 4800\nbuffer_size: 4800\n")
STUB = r'''
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static FILE *log_file(void) {
    static FILE *f; if (!f) f = fopen(getenv("TEST_LOG"), "w"); return f; }
int snd_config_top(void **c) { *c = (void *)2; return 0; }
int snd_input_buffer_open(void **i, const char *t, long s) {
    (void)t; (void)s; *i = (void *)3; return 0; }
int snd_config_load(void *c, void *i) { (void)c; (void)i; return 0; }
int snd_input_close(void *i) { (void)i; return 0; }
int snd_config_delete(void *c) { (void)c; return 0; }
int snd_pcm_open_lconf(void **p, const char *n, int s, int f, void *c) {
    (void)n; (void)s; (void)f; (void)c; *p = (void *)1; return 0; }
int snd_pcm_set_params(void *p, int f, int a, unsigned ch, unsigned r, int rs, unsigned l) {
    (void)p; return f == 2 && a == 3 && ch == 1 && r == 48000 && rs == 1 && l == 100000 ? 0 : -1; }
int snd_pcm_nonblock(void *p, int n) { (void)p; fprintf(log_file(), "nonblock %d\n", n); return 0; }
static int xrun_once;
long snd_pcm_writei(void *p, const void *d, unsigned long frames) {
    (void)p; (void)d;
    if (getenv("TEST_XRUN") && !xrun_once++) { fprintf(log_file(), "xrun\n"); return -EPIPE; }
    fprintf(log_file(), "write %lu\n", frames); return (long)frames; }
int snd_pcm_wait(void *p, int m) { (void)p; (void)m; return 1; }
int snd_pcm_drain(void *p) { (void)p; fprintf(log_file(), "drain\n"); return 0; }
int snd_pcm_drop(void *p) { (void)p; fprintf(log_file(), "drop\n"); return 0; }
int snd_pcm_prepare(void *p) { (void)p; fprintf(log_file(), "prepare\n"); return 0; }
int snd_pcm_close(void *p) {
    (void)p; fprintf(log_file(), "close\n"); fflush(log_file()); return 0; }
'''


@pytest.fixture
def player(tmp_path: Path) -> Path:
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("Synthetic PCM checks require an existing C compiler")
    (tmp_path / "alsa.c").write_text(STUB)
    (tmp_path / "hw_params").write_text(HW_PARAMS)
    subprocess.run([compiler, "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                    str(tmp_path / "alsa.c"), "-o", str(tmp_path / "alsa.so")],
                   check=True, capture_output=True, timeout=30)
    subprocess.run([compiler, "-O2", "-Wall", "-Wextra", "-Werror",
                    f'-DPCM_ALSA_LIBRARY="{tmp_path / "alsa.so"}"',
                    f'-DPCM_HW_PARAMS_PATH="{tmp_path / "hw_params"}"',
                    "-DPCM_STREAM_WATCHDOG=1",
                    str(SOURCE), "-ldl", "-o", str(tmp_path / "pcm-player")],
                   check=True, capture_output=True, timeout=30)
    return tmp_path / "pcm-player"


def frame(kind: bytes, pcm: bytes = b"") -> bytes:
    return kind + struct.pack("<I", len(pcm)) + pcm


def run(player: Path, data: bytes, **env: str) -> tuple[int, dict, list[str]]:
    log = player.parent / "alsa.log"
    result = subprocess.run([str(player), "--stream"], input=data, capture_output=True,
                            timeout=10, env={"TEST_LOG": str(log), **env})
    return result.returncode, json.loads(result.stdout), log.read_text().splitlines()


def test_stream_plays_frames_back_to_back_flushes_instantly_and_drains_at_end(
        player: Path) -> None:
    pcm = struct.pack("<h", 100) * 4800  # 100 ms at 48 kHz
    code, report, calls = run(player, frame(b"A", pcm) + frame(b"A", pcm) + frame(b"F")
                              + frame(b"A", pcm) + frame(b"E"))
    assert code == 0
    assert calls == ["nonblock 0", "write 4800", "write 4800", "drop", "prepare",
                     "write 4800", "drain", "close"]
    assert report == {"outcome": "complete", "frames_written": 14400, "flushes": 1,
                      "underruns_recovered": 0, "stage": "complete", "alsa_return_code": 0,
                      "hardware_parameters_verified": True, "cleanup_verified": True}


def test_underrun_between_replies_is_recovered_not_fatal(player: Path) -> None:
    pcm = struct.pack("<h", 0) * 480
    code, report, calls = run(player, frame(b"A", pcm) + frame(b"E"), TEST_XRUN="1")
    assert code == 0 and report["underruns_recovered"] == 1
    assert calls[1:4] == ["xrun", "prepare", "write 480"]


@pytest.mark.parametrize("bad", [frame(b"A", struct.pack("<h", 9000) * 10),  # Too loud.
                                 frame(b"A", b"\0" * 3),                    # Unaligned.
                                 frame(b"A", b"\0" * 96002),                # Over one second.
                                 b"X\0\0\0\0", b"A\4\0"],                   # Unknown/short.
                         ids=["loud", "unaligned", "oversize", "unknown", "short"])
def test_malformed_or_loud_frames_stop_the_stream(player: Path, bad: bytes) -> None:
    code, report, calls = run(player, frame(b"A", b"\0" * 960) + bad + frame(b"A", b"\0" * 960))
    assert code == 1 and report["outcome"] == "unknown"
    assert calls.count("write 480") == 1 and "drop" in calls and calls[-1] == "close"


def test_end_of_input_without_end_frame_still_drains(player: Path) -> None:
    code, report, calls = run(player, frame(b"A", b"\0" * 960))
    assert code == 0 and report["outcome"] == "complete" and calls[-2:] == ["drain", "close"]


def test_quiet_wait_for_the_host_between_replies_is_not_a_hang(player: Path) -> None:
    # The watchdog bounds stuck ALSA calls only; a pause longer than it is a normal turn.
    process = subprocess.Popen([str(player), "--stream"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               env={"TEST_LOG": str(player.parent / "alsa.log")})
    assert process.stdin is not None
    process.stdin.write(frame(b"A", b"\0" * 960))
    process.stdin.flush()
    time.sleep(1.6)
    output, _ = process.communicate(frame(b"A", b"\0" * 960) + frame(b"E"), timeout=10)
    assert process.returncode == 0 and json.loads(output)["outcome"] == "complete"
