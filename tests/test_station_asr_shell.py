"""Synthetic capture/keyword/decoder executables; no station, microphone or network."""
import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from alice_mcp import io_channel_pilot as iop
from alice_mcp import station_asr as asr
from alice_mcp.core import Controller, SSHInput


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("noise", ["none", "before", "after", "oversize", "missing_header",
                                  "activity_missing", "activity_tail", "mode"])
def test_one_pcm_stream_reaches_both_decoders_and_all_owned_processes_exit(
    tmp_path: Path, failed: bool, monkeypatch: pytest.MonkeyPatch, noise: str,
) -> None:
    cc = shutil.which("cc")
    if not cc or not shutil.which("dash"):
        pytest.skip("C compiler and dash required")
    ready = {"event": "ready", "rate": 16000, "threshold": 0.98, "window": 5,
             "cooldown_ms": 2500, "gain_db": 0, "models": [{"name": "komp",
                 "sha256": asr.WAKE_MODEL_SHA256, "stride": 3, "arena_bytes": 24624}]}
    source = tmp_path / "stub.c"
    source.write_text(r'''
#include <stdio.h>
#include <time.h>
#include <signal.h>
static volatile sig_atomic_t stop_mode;
static void mode_signal(int number) { stop_mode = number == SIGUSR1; }
int main(void) {
 unsigned char pcm[320] = {0}; int total=0; size_t n;
 if (MODE == 1 && MODE_TEST) {
   signal(SIGUSR1, mode_signal); signal(SIGUSR2, mode_signal);
   struct timespec delay = {0,500000000}; nanosleep(&delay,0);
 }
 if (MODE == 0) {
   if (VOICE) for (int i=0;i<320;i+=2) pcm[i]=100;
   fwrite(pcm,1,sizeof(pcm),stdout); return 0;
 }
 if (MODE == 2 && NOISE) puts("transport setup ok");
 if (MODE == 1) puts("{\"event\":\"ready\",\"rate\":16000,\"load_ms\":1}");
 else puts(READY);
 fflush(stdout);
 if (MODE == 2 && FAIL) return 3;
 while ((n=fread(pcm,1,sizeof(pcm),stdin)) > 0) {
   if (!total && MODE == 2) {
     puts("{\"event\":\"wake\",\"model\":\"komp\",\"t_ms\":10,"
          "\"probability\":0.9961,\"peak\":0.9961,\"last\":0.9961,\"trusted\":false}");
     fflush(stdout);
   }
   total += (int)n;
 }
 if (MODE == 1) {
   printf("{\"event\":\"%s\",\"result\":{\"text\":\"synthetic command\"},"
          "\"audio_ms\":%d,\"elapsed_ms\":%d,\"trusted\":false}\n",
          stop_mode ? "interrupt_final" : "final",total/32,total/32+1);
   printf("{\"event\":\"end\",\"audio_ms\":%d,\"elapsed_ms\":%d,\"rss_kib\":1}\n",total/32,total/32+1);
 } else printf("{\"event\":\"end\",\"reason\":\"eof\",\"audio_ms\":%d,"
               "\"detections\":[1]}\n",total/32);
 return 0;
}
''')
    for mode, name in enumerate(("capture", "recognizer", "wake-detector")):
        subprocess.run([cc, f"-DMODE={mode}", f"-DFAIL={int(failed)}",
                        f"-DMODE_TEST={int(noise == 'mode')}",
                        f"-DVOICE={int(noise == 'none')}",
                        f"-DNOISE={int(noise == 'after')}",
                        "-DREADY=" + json.dumps(json.dumps(ready)), str(source),
                        "-o", str(tmp_path / name)], check=True, capture_output=True)
    (tmp_path / "model").mkdir()
    pin = hashlib.sha256((tmp_path / "wake-detector").read_bytes()).hexdigest()
    guards = ('actual=$firmware; inode=1; DATA=$ROOT\n'
              'identity() { printf synthetic; }\ntrusted() { [ -d "$1" ]; }\n')
    script = (asr.SCRIPT.replace(iop.GUARDS, guards)
              .replace('[ "$(id -u)" = 0 ]', f'[ "$(id -u)" = {os.getuid()} ]')
              .replace('trusted /bkupgrade', 'trusted "$ROOT"')
              .replace('@PINS@', '').replace('@WAKE_PIN@', pin)
              .replace('[ "$(cat /sys/block/zram0/disksize)" = 167772160 ]', ':')
              .replace('[ "$(awk \'/^SwapTotal:/ {print $2}\' /proc/meminfo)" -gt 160000 ]', ':')
              .replace('awk \'$2=="/tmp" && $3=="tmpfs" {ok=1} END {exit !ok}\' /proc/mounts', ':')
              .replace('DIR=/tmp/alice-mcp-station-asr', f'DIR={tmp_path}/live'))
    if noise == "before":
        script = "printf 'transport setup ok\\n' >&2\n" + script
    elif noise == "oversize":
        script = "printf '" + "x" * 4097 + "\\n' >&2\n" + script
    elif noise == "missing_header":
        script = script.replace("printf 'alice-mcp-wake-v1\\n'", "printf 'bad_header\\n'")
    elif noise == "activity_tail":
        script = script.replace("int(samples/16);", "int(samples/16)+4;")
    elif noise == "activity_missing":
        script = script.replace("od -An -v -t d2 -w320", "printf ''")
    def command(mode: str, state: str = "") -> list[str]:
        return ["dash", "-c", script, "sh", mode, str(tmp_path), "a" * 64, state]

    preview = subprocess.run(command("inspect"), capture_output=True, check=True, timeout=5)
    state = json.loads(preview.stdout)
    assert state["fast_wake"] is True
    c = Controller(None, writes=True, runner=lambda *_: preview.stdout.decode())
    station = SimpleNamespace(id="synthetic", trust_sha256="b" * 64)
    monkeypatch.setattr(c, "station", lambda _: station)
    monkeypatch.setattr(asr.iop, "_permitted", lambda *_: None)
    monkeypatch.setattr(asr, "_ssh", lambda value: value)
    monkeypatch.setattr(asr, "request", lambda *a: SSHInput("synthetic", b""))
    monkeypatch.setattr(asr, "ssh_argv", lambda *_: command("apply", state["state_sha256"]))
    events = []
    live = {}
    result = asr.listen(c, station.id, events.append, stop=threading.Event(), live=live,
                        interrupt_only=(lambda: True) if noise == "mode" else None)
    assert result["cleanup_verified"] and result["quasar_unchanged"]
    valid = not failed and noise in {"none", "before", "activity_tail", "mode"}
    assert result["outcome"] == ("complete" if valid else "unknown")
    if noise == "activity_missing" and not failed:
        assert result["error_code"] == "activity_unverified"
    elif not valid:
        stage = ("parse" if noise == "after" else "handshake"
                 if noise in {"oversize", "missing_header"} else "end")
        assert live["wake_failure"] == stage
        assert live["wake_error_type"] == "SafetyError"
        if failed:
            assert result["wake_exit"] != 0
    assert not (tmp_path / "live").exists()
    assert events[0]["event"] == "ready" and events[0]["wake_source"] == "station_mww"
    if valid:
        assert [e for e in events if e["event"] == "wake"] == [
            {"event": "wake", "audio_ms": 10, "trusted": False}]
        assert events[-1]["audio_ms"] == live["audio_ms"] == 600
        assert live["wake_source"] == "station_mww"
        assert live.get("wake_preface_bytes", 0) == (19 if noise == "before" else 0)
        activity = [e for e in events if e["event"] == "activity"]
        assert bool(activity) == (noise == "none")
        assert all(0 < e["audio_ms"] <= 600 and e["trusted"] is False for e in activity)
        assert any(e["event"] == "interrupt_final" for e in events) == (noise == "mode")
