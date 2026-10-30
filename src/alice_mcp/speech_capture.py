"""One reviewed Mini 1 ASR client, existing preview/approval gate, no stored raw IPC/audio.

The only executable accepted is the exact locally reviewed production artifact.
SSH remains pinned/key-only; result text is untrusted data and is released once.
"""

from __future__ import annotations

import hashlib
import math
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller, SafetyError, SSHInput, Station, ssh_argv

# Change only after reviewing/rebuilding native/asr_once.c and its synthetic tests.
CLIENT_SHA256 = "ceeee7131f7ec3cb036e38979cc301a839152d32560b28f51d121d7bd8436f8b"
TTS_CLIENT_SHA256 = "24eead1c6922432273050d60f5930234cb8c138d3e8f48aebbf05ebb21f3edcb"
SETUP_SECONDS = 180
ERRORS = frozenset({"protocol_error", "not_idle", "timeout", "request_changed", "no_final_text",
                    "invalid_text", "recognition_failed", "frame_limit", "ipc_unavailable",
                    "ipc_io_error", "ipc_closed", "interrupted", "clock_error", "output_closed",
                    "signal_error", "tts_failed"})
PROFILE_ERRORS = frozenset({"runtime_tools_missing", "data_parent_untrusted", "firmware_mismatch",
                            "abi_mismatch", "quasar_not_running", "ipc_unavailable",
                            "temporary_path_busy", "preview_state_changed"})

# Only this bundled helper is sent; no supplied command, destination or IPC endpoint.
SCRIPT = r'''
OWNER_UID=0
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
mode=$1; cap=$2; seconds=$3; firmware=$4; binary_sha=$5; policy=$6; expected=$7
action=${8:-speech.listen}; input_sha=${9:-}
blocked() {
    printf '{"outcome":"blocked","capture_ready":false,"error_code":"%s"}\n' \
        "${1:-ipc_unavailable}"
    exit 0
}
uid=$(id -u)
[ "$uid" = "$OWNER_UID" ] || blocked
case "$cap" in ''|*[!0-9a-f]*) blocked;; esac
[ "${#cap}" = 32 ] || blocked
case "$seconds" in [5-9]|[1-5][0-9]|60) ;; *) blocked;; esac
case "$action" in
    speech.listen) [ -z "$input_sha" ] || blocked;;
    microphone.mute) [ "$seconds" = 5 ] && [ -z "$input_sha" ] || blocked;;
    microphone.mute_watch) [ "$seconds" = 20 ] && [ -z "$input_sha" ] || blocked;;
    speech.say)
        [ "$seconds" = 20 ] && [ "${#input_sha}" = 64 ] || blocked
        case "$input_sha" in *[!0-9a-f]*) blocked;; esac;;
    *) blocked;;
esac
for c in awk cat chmod cut grep id ls mkdir od pidof readlink rm rmdir sha256sum; do
    command -v "$c" >/dev/null 2>&1 || blocked runtime_tools_missing
done
trusted() {
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    set -- $(ls -ldn "$1")
    [ "$3" = "$uid" ] && [ "$(printf %s "$1" | cut -c6)" != w ] &&
        [ "$(printf %s "$1" | cut -c9)" != w ]
}
ipc() {
    awk '$2 == "0100007F:2693" && $4 == "0A" { n++ } END { exit n != 1 }' /proc/net/tcp
}
DATA=/data
if [ -L "$DATA" ]; then
    case "$(readlink "$DATA")" in
        /bkupgrade/data|bkupgrade/data) DATA=/bkupgrade/data;; *) blocked;;
    esac
fi
trusted "$DATA" && trusted "$DATA/opt" && [ -w "$DATA/opt" ] || blocked data_parent_untrusted
[ -f /system/vendor/quasar/maind ] && [ ! -L /system/vendor/quasar/maind ] ||
    blocked firmware_mismatch
actual=$(sha256sum /system/vendor/quasar/maind | awk '{ print $1 }')
[ "$actual" = "$firmware" ] || blocked firmware_mismatch
set -- $(od -An -t u1 -N 20 /system/vendor/quasar/maind)
[ "$#" = 20 ] && [ "$1 $2 $3 $4 $5 $6" = '127 69 76 70 1 1' ] || blocked abi_mismatch
shift 18
[ "$1 $2" = '40 0' ] || blocked abi_mismatch
pidof maind >/dev/null || blocked quasar_not_running
ipc || blocked
OUT=$DATA/opt/alice-mcp-asr-$cap
CLAIM=$DATA/opt/alice-mcp-asr-active
[ ! -e "$OUT" ] && [ ! -L "$OUT" ] && [ ! -e "$CLAIM" ] && [ ! -L "$CLAIM" ] ||
    blocked temporary_path_busy
boot=$(sha256sum /proc/sys/kernel/random/boot_id | awk '{ print $1 }')
STATE=$(printf '%s\n' "$actual" "$boot" "$DATA" "$cap" "$seconds" "$binary_sha" "$policy" \
    "$action" "$input_sha" |
    sha256sum | awk '{ print $1 }')
if [ "$mode" = inspect ]; then
    printf '{"outcome":"ready","state_sha256":"%s"}\n' "$STATE"
    exit 0
fi
[ "$mode" = apply ] && [ "$expected" = "$STATE" ] || blocked preview_state_changed
own_out=0; own_claim=0; child=
cleanup() {
    trap - 0 HUP INT TERM
    if [ -n "$child" ]; then kill "$child"; wait "$child"; child=; fi
    clean=true
    if [ "$own_out" = 1 ]; then
        rm -f "$OUT/client" && rmdir "$OUT" || clean=false
        [ ! -e "$OUT" ] && [ ! -L "$OUT" ] || clean=false
    fi
    if [ "$own_claim" = 1 ]; then rmdir "$CLAIM" || clean=false; fi
    running=false; if pidof maind >/dev/null; then running=true; fi
    printf '{"outcome":"cleanup","cleanup_verified":%s,"quasar_running":%s}\n' \
        "$clean" "$running"
}
mkdir "$CLAIM" || blocked
own_claim=1
trap cleanup 0
trap 'exit 0' HUP INT TERM
mkdir "$OUT" || exit 0
own_out=1
(set -C; printf '__CLIENT_OCTAL__' > "$OUT/client") || exit 0
[ "$(sha256sum "$OUT/client" | awk '{ print $1 }')" = "$binary_sha" ] || exit 0
chmod 500 "$OUT/client" || exit 0
if [ "$action" = speech.say ]; then
    [ "$(printf '__TEXT_OCTAL__' | sha256sum | awk '{ print $1 }')" = "$input_sha" ] || exit 0
    printf '__TEXT_OCTAL__' | "$OUT/client" --say "$seconds" "$cap" &
elif [ "$action" = microphone.mute ]; then
    "$OUT/client" --mute </dev/null &
elif [ "$action" = microphone.mute_watch ]; then
    "$OUT/client" --watch-mute </dev/null &
else
    "$OUT/client" --listen "$seconds" </dev/null &
fi
child=$!
wait "$child"
child=
exit 0
'''


def load_client(policy: dict[str, Any], action: str = "speech.listen") -> bytes:
    try:
        pin = TTS_CLIENT_SHA256 if action == "speech.say" else CLIENT_SHA256
        item = policy["tts_client" if action == "speech.say" else "speech_client"]
        if item["sha256"] != pin:
            raise ValueError
        path = Path(item["path"])
        if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError
        with path.open("rb") as source:
            content = source.read(65537)
        if len(content) > 65536 or hashlib.sha256(content).hexdigest() != pin:
            raise ValueError
        return content
    except (OSError, ValueError, KeyError, TypeError):
        raise SafetyError("Reviewed speech client missing or changed; no transfer") from None


def command(request: dict[str, Any], binary: bytes = b"") -> SSHInput:
    """Only validated internal requests from the existing approval orchestrator."""
    mode = request["mode"]
    if mode not in {"inspect", "apply"}:
        raise SafetyError("Invalid speech operation")
    policy = rt.validate_policy(request["policy"], request["model"])
    action = request["action"]
    if action not in {"speech.listen", "speech.say"}:
        raise SafetyError("Invalid speech action")
    params = rt.validate_parameters(action, request["params"], policy, request["model"])
    pin = TTS_CLIENT_SHA256 if action == "speech.say" else CLIENT_SHA256
    if (request["schema"] != rt.SCHEMA or not rt.allows(policy, action)
        or not rt._match(rt.HEX32, request["nonce"])):
        raise SafetyError("Invalid speech request")
    expected = request.get("expected_sha256", "")
    if (mode == "apply" and (not rt._match(rt.HEX64, expected)
                            or hashlib.sha256(binary).hexdigest() != pin)
        or mode == "inspect" and (expected or binary)):
        raise SafetyError("Invalid speech artifact or preview")
    text_input = b""
    if action == "speech.say":
        text = params["text"].encode("utf-8")
        text_input = len(text).to_bytes(4, "little") + text
    source = SCRIPT
    if mode == "inspect":
        # Do not even include a transfer/decode stanza in read-only preview traffic.
        source = source[:source.index('[ "$mode" = apply ]')]
    else:
        source = source.replace("__CLIENT_OCTAL__", "".join(f"\\{byte:03o}" for byte in binary))
        source = source.replace("__TEXT_OCTAL__", "".join(f"\\{byte:03o}" for byte in text_input))
    duration = params["duration_seconds"] if action == "speech.listen" else 20
    args = [mode, request["nonce"], str(duration), policy["firmware_sha256"], pin,
            hashlib.sha256(rt._encode(policy)).hexdigest(), expected, action,
            hashlib.sha256(text_input).hexdigest() if text_input else ""]
    return SSHInput("sh -s -- " + " ".join(shlex.quote(a) for a in args), source.encode())


class Session:
    """Bounded in-memory status; only a clean, exited capture can release a transcript."""

    def __init__(self, station: Station, capture_id: str, seconds: int) -> None:
        self.station, self.capture_id, self.seconds = station, capture_id, seconds
        self.started = time.monotonic()
        self.deadline = self.started
        self.phase = "starting"
        self.error = ""
        self.diagnostics: dict[str, int] = {}
        self.text: str | None = None
        self.cleanup = False
        self.cleanup_seen = False
        self.quasar = False
        self.changed, self.done = threading.Event(), threading.Event()
        self.lock = threading.RLock()

    def event(self, data: dict[str, Any]) -> None:
        with self.lock:
            kind = data.get("outcome")
            if kind == "awaiting_idle":
                if (self.phase != "starting" or set(data) != {
                    "outcome", "capture_ready", "remaining_seconds",
                } or data["capture_ready"] is not False
                    or not rt._integer(data["remaining_seconds"], 1, SETUP_SECONDS)):
                    raise ValueError("invalid_setup")
                self.deadline = self.started + data["remaining_seconds"]
                self.phase = "awaiting_idle"
            elif kind == "ready":
                elapsed = data.get("elapsed_seconds", 0)
                if (self.phase not in {"starting", "awaiting_idle"}
                    or not rt._keys(data, {"outcome", "capture_ready", "remaining_seconds"},
                                    {"elapsed_seconds"}) or data["capture_ready"] is not True
                    or not rt._integer(data["remaining_seconds"], 1, self.seconds)
                    or not rt._integer(elapsed, 0, SETUP_SECONDS)):
                    raise ValueError("invalid_ready")
                limit = self.started + SETUP_SECONDS + self.seconds
                self.deadline = min(limit, self.started + elapsed + data["remaining_seconds"])
                self.phase = "capturing"
            elif kind == "complete":
                if (self.phase != "capturing" or set(data) != {"outcome", "capture_ready", "text"}
                    or data["capture_ready"] is not False or time.monotonic() >= self.deadline):
                    raise ValueError("invalid_final")
                text = data["text"]
                if (not isinstance(text, str) or not 1 <= len(text) <= 256
                    or len(text.encode("utf-8")) > 512 or any(
                        ord(c) < 32 or 127 <= ord(c) <= 159 or 8234 <= ord(c) <= 8238
                        or 8294 <= ord(c) <= 8297 for c in text)):
                    raise ValueError("invalid_text")
                self.text, self.phase = text, "finishing"
            elif kind == "blocked":
                if (self.phase not in {"starting", "awaiting_idle", "capturing"}
                    or not rt._keys(data, {"outcome", "capture_ready", "error_code"},
                                    {"diagnostics"}) or data["capture_ready"] is not False
                    or data["error_code"] not in ERRORS | PROFILE_ERRORS):
                    raise ValueError("invalid_failure")
                if "diagnostics" in data:
                    d = data["diagnostics"]
                    if (not rt._keys(d, {"frames_received", "direct_states", "listener_states"})
                        or not all(rt._integer(v, 0, 4096) for v in d.values())
                        or d["direct_states"] + d["listener_states"] > d["frames_received"]):
                        raise ValueError("invalid_diagnostics")
                    self.diagnostics = dict(d)
                self.error, self.phase = data["error_code"], "blocked"
            elif kind == "cleanup":
                if (self.phase not in {"finishing", "blocked"} or set(data) != {
                    "outcome", "cleanup_verified", "quasar_running",
                } or type(data["cleanup_verified"]) is not bool
                    or type(data["quasar_running"]) is not bool or self.cleanup_seen):
                    raise ValueError("invalid_cleanup")
                self.cleanup_seen = True
                self.cleanup, self.quasar = data["cleanup_verified"], data["quasar_running"]
            else:
                raise ValueError("invalid_event")
            self.changed.set()

    def discard(self) -> None:
        with self.lock:
            self.text = None
            if self.phase == "complete":
                self.phase = "expired"

    def report(self, *, consume: bool = False) -> dict[str, Any]:
        with self.lock:
            remaining = max(0, math.floor(self.deadline - time.monotonic()))
            ready = self.phase == "capturing" and remaining >= 2 and not self.done.is_set()
            outcome = self.phase
            if outcome == "capturing" and not ready:
                outcome = "unknown"
            result: dict[str, Any] = {
                "outcome": outcome, "capture_id": self.capture_id, "capture_ready": ready,
                "remaining_seconds": remaining if ready else 0,
                "cleanup_verified": self.cleanup, "quasar_running": self.quasar,
                "do_not_retry": True, "evidence_class": "analytical",
                "untrusted_text": True,
            }
            if self.error:
                result["error_code"] = self.error
            if self.phase == "awaiting_idle":
                result["setup_remaining_seconds"] = remaining
            if self.diagnostics:
                result["diagnostics"] = dict(self.diagnostics)
            if self.phase == "complete" and consume and self.text is not None:
                result["text"], self.text, self.phase = self.text, None, "consumed"
            return result


def _worker(session: Session, request: SSHInput) -> None:
    process: subprocess.Popen[bytes] | None = None
    writer: threading.Thread | None = None
    write_failed = threading.Event()
    timer: threading.Timer | None = None
    try:
        process = subprocess.Popen(ssh_argv(session.station, request), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert process.stdin is not None and process.stdout is not None
        sink, stream = process.stdin, process.stdout

        def feed() -> None:
            try:
                with sink:
                    sink.write(request.data)
            except OSError:
                write_failed.set()

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        # Native setup <=180s, followed by <=60s capture, even if SSH/host disappears.
        def stop_transport() -> None:
            try:
                process.kill()
            except OSError:
                pass

        timer = threading.Timer(SETUP_SECONDS + session.seconds + 15, stop_transport)
        timer.daemon = True
        timer.start()
        count, total = 0, 0
        while True:
            line = stream.readline(4097)
            if not line:
                break
            count, total = count + 1, total + len(line)
            if count > 4 or len(line) > 4096 or total > 8192 or not line.endswith(b"\n"):
                raise ValueError("stream_limit")
            data = rt._json(line)
            if type(data) is not dict:
                raise ValueError("invalid_event")
            session.event(data)
        code = process.wait(timeout=3)
        writer.join(timeout=2)
        if code or write_failed.is_set() or writer.is_alive():
            raise ValueError("transport_failed")
        with session.lock:
            if not session.cleanup or not session.quasar:
                raise ValueError("cleanup_unverified")
            if session.phase == "finishing":
                session.phase = "complete"
            elif session.phase != "blocked":
                raise ValueError("missing_final")
    except Exception:
        with session.lock:
            session.text, session.phase = None, "unknown"
            if not session.error:
                session.error = "speech_capture_unverified"
    finally:
        if timer is not None:
            timer.cancel()
        if process is not None:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            if writer is not None:
                writer.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
        session.done.set()
        session.changed.set()
        # ponytail: two-minute RAM retention; add persistence only for a separately approved use.
        expiry = threading.Timer(120, session.discard)
        expiry.daemon = True
        expiry.start()


def invoke(controller: Controller, station: Station, request: dict[str, Any]) -> dict[str, Any]:
    binary = load_client(station.controls, request["action"])
    if request["mode"] == "inspect":
        raw = controller.runner(station, command(request))
        try:
            if len(raw) > 1024:
                raise ValueError
            result = rt._json(raw.encode("utf-8"))
            if (rt._keys(result, {"outcome", "capture_ready", "error_code"})
                and result["outcome"] == "blocked" and result["capture_ready"] is False
                and result["error_code"] in PROFILE_ERRORS):
                raise SafetyError("ASR preview blocked: " + result["error_code"])
            if (not rt._keys(result, {"outcome", "state_sha256"}) or result["outcome"] != "ready"
                or not rt._match(rt.HEX64, result["state_sha256"])):
                raise ValueError
            return dict(result)
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise SafetyError("ASR runtime unavailable or incompatible; no transfer") from None
    payload = command(request, binary)
    if request["action"] == "speech.say":
        return _tts_result(controller.runner(station, payload))
    # Existing controller execution lock serializes starts; one remote mkdir claim is definitive.
    sessions = controller.speech_sessions
    for key, value in list(sessions.items()):
        if value.done.is_set() and time.monotonic() - value.started > SETUP_SECONDS + 60 + 180:
            value.discard()
            del sessions[key]
    if (request["nonce"] in sessions or len(sessions) >= 16
        or any(not value.done.is_set() and value.station.id == station.id
               for value in sessions.values())):
        raise SafetyError("ASR session already active or bounded registry full; no retry")
    session = Session(station, request["nonce"], request["params"]["duration_seconds"])
    sessions[request["nonce"]] = session
    threading.Thread(target=_worker, args=(session, payload), daemon=True).start()
    session.changed.wait(timeout=10)
    return session.report()


def _tts_result(raw: str) -> dict[str, Any]:
    """TTS returns flags only; a private string or uncorrelated event is never success."""
    try:
        if len(raw) > 4096 or len(raw.splitlines()) != 2:
            raise ValueError
        native, cleanup = [rt._json(line.encode()) for line in raw.splitlines()]
        flags = {"request_sent", "request_may_have_been_sent", "tts_started", "tts_completed"}
        if (not rt._keys(native, {"outcome"} | flags, {"error_code"})
            or native["outcome"] not in {"complete", "blocked", "unknown"}
            or any(type(native[k]) is not bool for k in flags)
            or native["request_sent"] and not native["request_may_have_been_sent"]
            or native["tts_started"] and not native["request_sent"]
            or native["tts_completed"] and not native["tts_started"]
            or native["outcome"] == "complete" and not all(native[k] for k in flags)
            or native["outcome"] == "complete" and "error_code" in native
            or native["outcome"] == "blocked" and native["request_may_have_been_sent"]
            or native.get("error_code", "timeout") not in ERRORS):
            raise ValueError
        if (not rt._keys(cleanup, {"outcome", "cleanup_verified", "quasar_running"})
            or cleanup["outcome"] != "cleanup"
            or any(type(cleanup[k]) is not bool for k in ("cleanup_verified", "quasar_running"))):
            raise ValueError
        result = dict(native, cleanup_verified=cleanup["cleanup_verified"],
                      quasar_running=cleanup["quasar_running"], effect_observed=False)
        result["outcome"] = "applied" if native["outcome"] == "complete" else native["outcome"]
        if not cleanup["cleanup_verified"] or not cleanup["quasar_running"]:
            result.update(outcome="unknown", error_code="tts_cleanup_unverified")
        return result
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise SafetyError("TTS returned incompatible structured output; do not retry") from None


def status(controller: Controller, station: Station, capture_id: str) -> dict[str, Any]:
    session: Session = controller.speech_sessions[capture_id]
    if station != session.station or not rt.allows(station.controls, "speech.listen"):
        raise SafetyError("Speech station configuration changed or permission revoked")
    return session.report(consume=True)
