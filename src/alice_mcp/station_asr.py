"""Explicit on-station ASR source. Only bounded untrusted text crosses pinned SSH."""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp import io_channel_pilot as iop
from alice_mcp import wake_pilot as wp
from alice_mcp.core import Controller, SafetyError, SSHInput, Station, ssh_argv
from alice_mcp.vqe_experiment import _ssh

DIRECTORY = r"/bkupgrade/data/opt/alice-local-asr-[a-f0-9]{12}"
ACTION = "voice.station_asr"
WAKE_MODEL_SHA256 = "f5ea69530098109b717dc57d75808681da002d6cab95e5b7ee1d2f2e8f171bcf"
WAKE_HEADER = b"alice-mcp-wake-v1\n"
MANIFEST: dict[str, str] = {
    "recognizer": "79b42c44d65248b305eb376f3fb19be04209e3cd40d55ae4c08968c76b950fed",
    "libvosk.so.gz": "b9d8eda40ad8253b233969c640d0d5b368f8b64b69d4f66e265fdeb144dc5b59",
    "model/graph/phones/word_boundary.int": (
        "3f9bdbd100f979355f75a25e6a822b151f359272d7dd5eb391aa912f1b29628c"
    ),
    "model/graph/Gr.fst": "96edbc7326f8903dc2fcdce572b97fb64a0d5e22bff0f2609dd0e207346de5cc",
    "model/graph/HCLr.fst": "d79fc48e659ac9d5e5fe50971c5e5c149b1b243f1921c37bcccaf23321da9849",
    "model/graph/disambig_tid.int": (
        "f4fa84bbbd09e5909c1b6ba22ec93d15890fb398f52cae05ba111135035cf379"
    ),
    "model/am/final.mdl": "e4d240a98cd413490eb2b23764f726c8f5e8e596060fd4b922e676e0b8f1782d",
    "model/README": "a4d1205430020c1943e66056549cb3b696ec418c3dee242769aa4702dd238e1c",
    "model/conf/model.conf": "7b122c2c816bc53e8acf51f2f6d990f930b3756c31418ad0b37501fda9afb3cb",
    "model/conf/mfcc.conf": "886b5a3ccf4d9a2b1303ee51077bbb5e15d78467a8872fa84047e2ebcae0e671",
    "model/ivector/final.dubm": "3b3bc5aeffd08730e5ce8c17f28078fcf20851dfaac0538688ab9e711debcd1c",
    "model/ivector/global_cmvn.stats": (
        "ab09cd692d4beed987deb367fe40a85f911c0460206234c5ad958b6d3278651c"
    ),
    "model/ivector/final.ie": "f1ec665dff55313975fd979e8420d56e9c9870a2ea004b0259aa2e38620da4c8",
    "model/ivector/final.mat": "e514fa419089ebba694612f9afbc055026312d577afbe69170e63f86e4ef1781",
    "model/ivector/splice.conf": "826fe6c843d6c8c08b70b776d4433bb08e8449b348009f7d5f9e0861e2afd572",
    "model/ivector/online_cmvn.conf": (
        "c5787c9bfbc61f83bd505b43f34bfbed7f06ad7904a670667e572b68a6ed0f87"
    ),
    "capture": "05a6632285c69795d5c59667697a1b26f3ccd0abe36156fc70168dd8585351c2",
}

# One hour per verified session; the model stays loaded across microphone windows.
# There is no upload, arbitrary path, shell command or model choice in MCP input.
SCRIPT = (
    r"""
set -eu
umask 077
LC_ALL=C; export LC_ALL
exec 8>&2
exec 2>/dev/null
mode=$1; ROOT=$2; firmware=$3; expected=$4
port=9850
blocked() { printf '{"outcome":"blocked","error_code":"%s"}\n' "$1"; exit 1; }
[ "$(id -u)" = 0 ] || blocked not_owner
"""
    + iop.GUARDS
    + r"""
trusted /bkupgrade && trusted "$ROOT" || blocked data_parent_untrusted
cd "$ROOT"
@PINS@
fast_wake=false
if [ -e wake-detector ] || [ -L wake-detector ]; then
 [ -f wake-detector ] && [ ! -L wake-detector ] && [ -x wake-detector ] &&
 [ "$(sha256sum wake-detector | cut -d ' ' -f 1)" = @WAKE_PIN@ ] || blocked artifact_changed
 command -v tee >/dev/null || blocked runtime_tools_missing
 fast_wake=true
fi
[ "$(cat /sys/block/zram0/disksize)" = 167772160 ] || blocked swap_unavailable
[ "$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)" -gt 160000 ] || blocked swap_unavailable
awk '$2=="/tmp" && $3=="tmpfs" {ok=1} END {exit !ok}' /proc/mounts || blocked tmpfs_unavailable
DIR=/tmp/alice-mcp-station-asr
[ ! -e "$DIR" ] && [ ! -L "$DIR" ] || blocked temporary_path_busy
before=$(identity)
boot=$(sha256sum /proc/sys/kernel/random/boot_id | cut -d ' ' -f 1)
state=$(printf '%s\n' "$ROOT" "$actual" "$boot" "$before" "$inode" "$fast_wake" |
 sha256sum | cut -d ' ' -f 1)
if [ "$mode" = inspect ]; then
 printf '{"outcome":"ready","state_sha256":"%s","fast_wake":%s}\n' "$state" "$fast_wake"
 exit 0
fi
[ "$mode" = apply ] && [ "$expected" = "$state" ] || blocked preview_state_changed
mkdir "$DIR"
child=; producer=; watcher=; control=; detector=; splitter=; activity=
decoder_exit=255; wake_exit=255; phase=decoder_start
cleanup() {
 trap - EXIT HUP INT TERM
 if [ -n "$child" ] && [ "$(readlink /proc/$child/exe)" = "$ROOT/recognizer" ]; then
  kill -TERM "$child" 2>/dev/null || true
 fi
 if [ -n "$child" ]; then
  if wait "$child" 2>/dev/null; then decoder_exit=0; else decoder_exit=$?; fi
 fi
 for p in "$producer" "$watcher" "$control"; do
  [ -z "$p" ] || kill -TERM "$p" 2>/dev/null || true
 done
 for p in "$producer" "$watcher" "$control"; do
  [ -z "$p" ] || wait "$p" 2>/dev/null || true
 done
 if [ -n "$splitter" ] && tr '\000' '\n' <"/proc/$splitter/cmdline" |
    grep -Fx "$DIR/wake-in" >/dev/null; then kill -TERM "$splitter" 2>/dev/null || true; fi
 if [ -n "$detector" ] && [ "$(readlink /proc/$detector/exe)" = "$ROOT/wake-detector" ]; then
  kill -TERM "$detector" 2>/dev/null || true
 fi
 [ -z "$splitter" ] || wait "$splitter" 2>/dev/null || true
 [ -z "$activity" ] || wait "$activity" 2>/dev/null || true
 if [ -n "$detector" ]; then
  if wait "$detector" 2>/dev/null; then wake_exit=0; else wake_exit=$?; fi
 fi
 unchanged=false; [ "$(identity)" != "$before" ] || unchanged=true
 reason=$phase
 [ ! -f "$DIR/stop" ] || reason=stopped
 [ ! -f "$DIR/complete" ] || reason=complete
 [ ! -f "$DIR/failed" ] || reason=$(cat "$DIR/failed")
 low=0; [ ! -f "$DIR/memory_min" ] || low=$(cat "$DIR/memory_min")
 case "$low" in ''|*[!0-9]*) low=0;; esac
 rm -f "$DIR/in" "$DIR/out" "$DIR/meta" "$DIR/stop" "$DIR/complete" "$DIR/failed" \
  "$DIR/memory_min" "$DIR/wake-in" "$DIR/capture" "$DIR/activity-in"
 clean=false; rmdir "$DIR" && clean=true
 outcome=unknown
 if { [ "$reason" = complete ] || [ "$reason" = stopped ]; } &&
    [ "$clean" = true ] && [ "$unchanged" = true ]; then
  outcome=complete
 fi
 printf '{"event":"cleanup","outcome":"%s","reason":"%s",' "$outcome" "$reason"
 printf '"cleanup_verified":%s,"quasar_unchanged":%s,' "$clean" "$unchanged"
 printf '"decoder_exit":%s,"wake_exit":%s,"memory_low_kib":%s}\n' \
  "$decoder_exit" "$wake_exit" "$low"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
mkfifo "$DIR/in" "$DIR/out"
exec 6<&0
env -i PATH=/usr/bin:/bin LANG=C LC_ALL=C HOME=/nonexistent LD_LIBRARY_PATH=/lib:/usr/lib \
 "$ROOT/recognizer" "$ROOT/libvosk.so.gz" "$ROOT/model" 3600 pcm16 \
 <"$DIR/in" >"$DIR/out" 6<&- 8>&- &
child=$!
exec 3>"$DIR/in"
exec 4<"$DIR/out"
(
 while read -r request; do
  [ "$(readlink /proc/$child/exe)" = "$ROOT/recognizer" ] || exit 1
  case "$request" in
   stop) break;;
   interrupt) kill -USR1 "$child";;
   transcribe) kill -USR2 "$child";;
   *) exit 1;;
  esac
 done
 : >"$DIR/stop"
 [ "$(readlink /proc/$child/exe)" != "$ROOT/recognizer" ] || kill -TERM "$child"
) <&6 3>&- 4<&- 6<&- 8>&- &
control=$!
exec 6<&-
(
 low=512000
 while kill -0 "$child" 2>/dev/null; do
  free=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
  if [ "$free" -lt "$low" ]; then low=$free; printf '%s\n' "$low" >"$DIR/memory_min"; fi
  reason=
  [ "$free" -ge 12288 ] || reason=memory_guard
  [ "$(identity)" = "$before" ] || reason=quasar_changed
  if [ -n "$reason" ]; then
   printf '%s\n' "$reason" >"$DIR/failed"
   [ "$(readlink /proc/$child/exe)" != "$ROOT/recognizer" ] || kill -TERM "$child"
   exit 0
  fi
  sleep 1
 done
) 3>&- 4<&- 8>&- &
watcher=$!
read -r ready <&4
case "$ready" in '{"event":"ready",'*) ;; *) exit 1;; esac
printf '%s\n' "$ready"
phase=failed
if [ "$fast_wake" = true ]; then
 mkfifo "$DIR/wake-in" "$DIR/capture" "$DIR/activity-in"
 # The source supplies one hour; leave headroom so EOF ends the keyword helper normally.
 (
  printf 'alice-mcp-wake-v1\n'
  exec "$ROOT/wake-detector" --max-seconds 3660 --gain-db 0
 ) <"$DIR/wake-in" >&8 2>/dev/null 3>&- 4<&- 8>&- &
 detector=$!
 # Energy activity runs independently of Vosk's occasional decoding stalls.
 (
  od -An -v -t d2 -w320 | awk '
   { sum=0; for(i=1;i<=NF;i++) sum+=$i*$i; samples+=NF; t=int(samples/16);
     run=(NF && sum>=NF*2025) ? run+1 : 0;
     if(run>=3 && t-last>=100) {
      printf "{\"event\":\"activity\",\"audio_ms\":%d,\"trusted\":false}\n",t;
      fflush(); last=t;
     }
   }
   END {
    printf "{\"event\":\"activity_end\",\"audio_ms\":%d,\"trusted\":false}\n",int(samples/16);
    fflush()
   }'
 ) <"$DIR/activity-in" 3>&- 4<&- 8>&- &
 activity=$!
 tee "$DIR/wake-in" "$DIR/activity-in" <"$DIR/capture" >&3 3>&- 4<&- 8>&- &
 splitter=$!
fi
exec 8>&-
(
 if [ "$fast_wake" = true ]; then exec 5>"$DIR/capture"; else exec 5>&3; fi
 exec 3>&-
 source=
 stop_source() {
  trap - EXIT HUP INT TERM
  if [ -n "$source" ] && [ "$(readlink /proc/$source/exe)" = "$ROOT/capture" ]; then
   kill -TERM "$source" 2>/dev/null || true
   wait "$source" 2>/dev/null || true
  fi
  exit 0
 }
 trap stop_source EXIT HUP INT TERM
 i=0
 while [ "$i" -lt 60 ]; do
  "$ROOT/capture" --stream 9850 60 </dev/null >&5 2>"$DIR/meta" 5>&- 4<&- &
  source=$!
  if ! wait "$source"; then
   source=
   [ -f "$DIR/stop" ] || printf 'capture_failed\n' >"$DIR/failed"
   [ "$(readlink /proc/$child/exe)" != "$ROOT/recognizer" ] || kill -TERM "$child"
   exit 1
  fi
  source=
  i=$((i+1))
 done
 : >"$DIR/complete"
) 4<&- &
producer=$!
exec 3>&-
cat <&4
exec 4<&-
if wait "$child"; then decoder_exit=0; else decoder_exit=$?; child=; exit 1; fi
child=
wait "$producer" || exit 1
producer=
if [ "$fast_wake" = true ]; then
 wait "$splitter" || { printf 'wake_failed\n' >"$DIR/failed"; exit 1; }
 splitter=
 if wait "$detector"; then wake_exit=0; else
  wake_exit=$?; detector=
  printf 'wake_failed\n' >"$DIR/failed"; exit 1
 fi
 detector=
fi
exit 0
"""
)


def parse(raw: bytes) -> dict[str, Any]:
    try:
        if len(raw) > 8192:
            raise ValueError
        value = rt._json(raw)
        if type(value) is not dict:
            raise ValueError
        event = value.get("event")
        if event in {"activity", "activity_end"}:
            if set(value) != {"event", "audio_ms", "trusted"} or value["trusted"] is not False:
                raise ValueError
            counters = {"audio_ms": 3600000}
        elif event in {"partial", "final", "interrupt_partial", "interrupt_final"}:
            field = "partial" if event.endswith("partial") else "text"
            if (
                set(value) != {"event", "result", "audio_ms", "elapsed_ms", "trusted"}
                or value["trusted"] is not False
                or type(value["result"]) is not dict
                or set(value["result"]) != {field}
                or type(value["result"][field]) is not str
                or "\0" in value["result"][field]
            ):
                raise ValueError
            counters = {"audio_ms": 3600000, "elapsed_ms": 3660000}
        elif event == "ready":
            if set(value) != {"event", "rate", "load_ms"} or value["rate"] != 16000:
                raise ValueError
            counters = {"load_ms": 60000}
        elif event == "end":
            if set(value) != {"event", "audio_ms", "elapsed_ms", "rss_kib"}:
                raise ValueError
            counters = {"audio_ms": 3600000, "elapsed_ms": 3660000, "rss_kib": 512000}
        elif event == "cleanup":
            if (
                set(value) - {"wake_exit"} != {
                    "event", "outcome", "reason", "cleanup_verified", "quasar_unchanged",
                    "decoder_exit", "memory_low_kib"}
                or value["outcome"] not in {"complete", "unknown"}
                or value["reason"] not in {"stopped", "complete", "failed", "memory_guard",
                                           "quasar_changed", "decoder_start", "capture_failed",
                                           "wake_failed"}
                or any(type(value[k]) is not bool for k in ("cleanup_verified", "quasar_unchanged"))
            ):
                raise ValueError
            counters = {"decoder_exit": 255, "memory_low_kib": 512000}
            if "wake_exit" in value:
                counters["wake_exit"] = 255
        else:
            raise ValueError
        if any(
            type(value[k]) is not int or not 0 <= value[k] <= high for k, high in counters.items()
        ):
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise SafetyError("Invalid on-station ASR event") from None


class WakeEvents:
    """The pinned on-station model's independent stream; never command authority."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.previous = 0
        self.complete = False
        self.end_reason: str | None = None
        self.error_code: str | None = None

    def accept(self, raw: bytes) -> dict[str, Any] | None:
        try:
            if len(raw) > 4096 or self.complete:
                raise ValueError
            value = rt._json(raw)
            if (type(value) is dict and value.get("event") == "error"
                    and value.get("error") in wp.DETECTOR_ERRORS):
                self.error_code = value["error"]
            if type(value) is not dict or value.get("event") not in {"ready", "wake", "end"}:
                raise ValueError
            events = [*self.events, value]
            wp._detector(events, 0, limit_ms=3600000)
            kind = value["event"]
            if kind == "ready" and (len(value["models"]) != 1
                    or value["models"][0]["sha256"] != WAKE_MODEL_SHA256
                    or value["models"][0]["name"] != "komp"
                    or (value["threshold"], value["window"], value["cooldown_ms"]) != (
                        0.98, 5, 2500)):
                raise ValueError
            self.events = events
            if kind == "wake":
                if value["t_ms"] <= self.previous:
                    raise ValueError
                self.previous = value["t_ms"]
                return {"event": "wake", "audio_ms": self.previous, "trusted": False}
            if kind == "end":
                if value["audio_ms"] < self.previous:
                    raise ValueError
                self.complete, self.end_reason = True, value["reason"]
            return None
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise SafetyError("Invalid station keyword event") from None


def request(station: Station, mode: str, expected: str = "") -> SSHInput:
    root = station.controls["voice"]["asr_directory"]
    if not re.fullmatch(DIRECTORY, root) or mode not in {"inspect", "apply"}:
        raise SafetyError("Invalid on-station ASR configuration")
    if mode == "apply" and not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise SafetyError("Fresh ASR preview required")
    pins = []
    for name, pin in MANIFEST.items():
        # All names and hashes are release constants, never tool arguments.
        pins.append(
            f"[ -f {shlex.quote(name)} ] && [ ! -L {shlex.quote(name)} ] && "
            f"[ \"$(sha256sum {shlex.quote(name)} | cut -d ' ' -f 1)\" = {pin} ] "
            "|| blocked artifact_changed"
        )
    for directory in (
        "model",
        "model/am",
        "model/conf",
        "model/graph",
        "model/graph/phones",
        "model/ivector",
    ):
        pins.append(f"trusted {directory} || blocked data_parent_untrusted")
    source = (SCRIPT.replace("@PINS@", "\n".join(pins))
              .replace("@WAKE_PIN@", wp.FT_DETECTOR_SHA256).rstrip("\n").encode())
    # Dropbear rejects long exec requests. Check the complete fixed script before
    # executing it; the remaining stdin is exclusively the stop control channel.
    loader = (f'source=$(dd bs=1 count={len(source)} 2>/dev/null); '
              '[ "$(printf %s "$source" | sha256sum | cut -d \' \' -f 1)" = '
              + hashlib.sha256(source).hexdigest() + ' ] || exit 1; '
              'exec sh -c "$source" sh "$@"')
    command = (
        "sh -c "
        + shlex.quote(loader)
        + " sh "
        + " ".join(
            shlex.quote(v) for v in (mode, root, station.controls["firmware_sha256"], expected)
        )
    )
    return SSHInput(command, source)


def listen(
    controller: Controller,
    station_id: str,
    sink: Callable[[dict[str, Any]], None],
    *,
    stop: threading.Event,
    live: dict[str, Any],
    interrupt_only: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    station = controller.station(station_id)
    iop._permitted(controller, station)
    ssh = _ssh(station)
    preview = rt._json(controller.runner(ssh, request(station, "inspect")).encode())
    if (
        type(preview) is not dict
        or set(preview) != {"outcome", "state_sha256", "fast_wake"}
        or preview["outcome"] != "ready"
        or type(preview["fast_wake"]) is not bool
        or not re.fullmatch(r"[a-f0-9]{64}", preview["state_sha256"])
    ):
        raise SafetyError("Station ASR resources or artifacts unavailable")
    digest = hashlib.sha256(
        repr((station, ssh.trust_sha256, preview, MANIFEST)).encode()
    ).hexdigest()
    identifier = controller.store_plan(station, ACTION, digest)
    plan = controller.take_plan(identifier, digest)
    iop._permitted(controller, controller.station(station_id))
    if (
        controller.station(station_id) != station
        or controller.clock() >= plan.expires
        or _ssh(station).trust_sha256 != ssh.trust_sha256
    ):
        raise SafetyError("Station ASR preview changed")
    result: dict[str, Any] = {"outcome": "unknown", "do_not_retry": True, "audio_exported": False}
    finished = threading.Event()
    wake_failed = threading.Event()
    sink_lock = threading.Lock()
    wake_events = WakeEvents()
    activity_end: int | None = None
    activity_previous = 0
    live["wake_source"] = "station_mww" if preview["fast_wake"] else "station_vosk"
    launch = request(station, "apply", preview["state_sha256"])
    with subprocess.Popen(
        ssh_argv(ssh, launch),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if preview["fast_wake"] else subprocess.DEVNULL,
    ) as process:
        assert process.stdin is not None
        process.stdin.write(launch.data)
        process.stdin.flush()

        def cancel() -> None:
            deadline = time.monotonic() + 3670
            previous_mode = False
            while not finished.wait(0.2):
                try:
                    unchanged = controller.station(station_id) == station
                except Exception:
                    unchanged = False
                if (stop.is_set() or wake_failed.is_set() or not unchanged
                        or time.monotonic() > deadline):
                    live["stop_trigger"] = (
                        "requested" if stop.is_set() else "wake_failed" if wake_failed.is_set()
                        else "policy_changed" if not unchanged else "deadline")
                    try:
                        assert process.stdin is not None
                        process.stdin.write(b"stop\n")
                        process.stdin.flush()
                    except (OSError, ValueError):
                        pass
                    if not finished.wait(15):
                        process.terminate()
                    return
                if interrupt_only is not None:
                    mode = interrupt_only()
                    if mode != previous_mode:
                        try:
                            assert process.stdin is not None
                            process.stdin.write(b"interrupt\n" if mode else b"transcribe\n")
                            process.stdin.flush()
                        except (OSError, ValueError):
                            return  # The stream itself verifies completion; never resend.
                        previous_mode = mode

        def detect() -> None:
            stage = "read"
            framed = False
            preface_bytes = 0
            try:
                output = process.stderr
                assert output is not None
                for raw in iter(lambda: output.readline(4097), b""):
                    if not framed:
                        stage = "handshake"
                        if raw == WAKE_HEADER:
                            framed = True
                            continue
                        preface_bytes += len(raw)
                        if (preface_bytes > 4096 or not raw.endswith(b"\n")
                                or any(b not in (9, 10, 13) and not 32 <= b <= 126 for b in raw)):
                            raise SafetyError("Invalid SSH stream preface")
                        # SSH diagnostics are not detector events; require the explicit boundary.
                        live["wake_preface_bytes"] = preface_bytes
                        continue
                    stage = "parse"
                    live["wake_lines"] = live.get("wake_lines", 0) + 1
                    live["wake_line_bytes"] = len(raw)
                    event = wake_events.accept(raw)
                    if event is not None:
                        stage = "callback"
                        live["wake_audio_ms"] = event["audio_ms"]
                        with sink_lock:
                            sink(event)
                    stage = "read"
                if not wake_events.complete:
                    stage = "end" if framed else "handshake"
                    raise SafetyError("Station keyword stream incomplete")
            except Exception as error:
                live["wake_failure"] = stage
                live["wake_error_type"] = type(error).__name__
                if wake_events.error_code is not None:
                    live["wake_runtime_error"] = wake_events.error_code
                wake_failed.set()

        detector: threading.Thread | None = None
        monitor = threading.Thread(target=cancel, daemon=True)
        monitor.start()
        try:
            assert process.stdout is not None
            output = process.stdout
            previous = 0
            for raw in iter(lambda: output.readline(8193), b""):
                event = parse(raw)
                if event["event"] in {"activity", "activity_end"}:
                    if (not preview["fast_wake"] or activity_end is not None
                            or event["audio_ms"] < activity_previous):
                        raise SafetyError("Station activity event order invalid")
                    activity_previous = event["audio_ms"]
                    if event["event"] == "activity_end":
                        activity_end = activity_previous
                    else:
                        with sink_lock:
                            sink(event)
                    continue
                if event["event"] == "cleanup":
                    result.update(event)
                    continue
                if "audio_ms" in event:
                    if event["audio_ms"] < previous:
                        raise SafetyError("Station ASR event order invalid")
                    previous = event["audio_ms"]
                    live.update(audio_ms=previous, elapsed_ms=event["elapsed_ms"])
                if event["event"] == "ready":
                    live["load_ms"] = event["load_ms"]
                    event = {**event, "wake_source": live["wake_source"]}
                with sink_lock:
                    sink(event)
                if event["event"] == "ready" and preview["fast_wake"] and detector is None:
                    detector = threading.Thread(target=detect, daemon=True)
                    detector.start()
            status = process.wait(timeout=5)
            if detector is not None:
                detector.join(5)
                if (detector.is_alive() or wake_failed.is_set()
                        or not wake_events.complete
                        or wake_events.end_reason != "eof" and not stop.is_set()):
                    result.update(outcome="unknown", error_code="wake_detector_unverified")
                # microWakeWord consumes whole 10 ms blocks, leaving a shorter final tail.
                elif (activity_end is None
                      or not 0 <= activity_end - wake_events.events[-1]["audio_ms"] < 10):
                    result.update(outcome="unknown", error_code="activity_unverified")
            if status and result.get("reason") != "stopped":
                result["outcome"] = "unknown"
            if not result.get("cleanup_verified") or not result.get("quasar_unchanged"):
                result["outcome"] = "unknown"
        finally:
            finished.set()
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass  # A rejected remote start may already have closed the control pipe.
            if process.poll() is None:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            monitor.join(1)
            if detector is not None:
                detector.join(1)
    return result
