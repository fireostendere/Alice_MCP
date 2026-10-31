"""Stock Quasar through its own local IPC: spotter events, its volume level and wake word.

One pinned ARM client (asr_once.c built with ASR_ONCE_SPOTTER):
- `--watch-spotter`: a CommandSpotterCapability listener on aliced printing only phrases
  of the stock command model. Quasar acts on them itself; the host only uses «стоп» and
  «хватит» to stop its own dialogue. Closing SSH stdin stops it.
- `--send-*`: one fixed request in Quasar's own full-message form. Volume goes through
  Alice's text path, so Quasar sets its own level, ring, click and app/VQE state. The
  activation toggle stops or starts only the stock wake word; Quasar keeps running.
Results are verified on the station (Quasar's saved level, its spotter log lines).
"""

from __future__ import annotations

import contextlib
import hashlib
import shlex
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp import io_channel_pilot as iop
from alice_mcp import native_transport
from alice_mcp.core import Controller, SafetyError, SSHInput, Station, ssh_argv
from alice_mcp.vqe_experiment import _ssh
from alice_mcp.wake_simulate import read_pinned

CLIENT_SHA256 = "efb7fd7d532d35bc4cdb6f026244fc6d1e873efed72840c91abb22e734ade3d5"
PHRASES = frozenset({
    "дальше", "назад", "выше", "ниже", "вправо", "влево", "вверх", "вниз", "домой",
    "продолжи", "играй", "стоп", "хватит", "пауза", "следующий", "громче", "тише",
    "включи свет", "включи телевизор", "выключи свет", "выключи телевизор",
    "включи блютуз", "выключи блютуз", "лайк", "дизлайк",
})
ERRORS = frozenset({"ipc_unavailable", "ipc_io_error", "ipc_closed", "timeout", "frame_limit",
                    "protocol_error", "output_closed", "signal_error", "clock_error"})

# The client arrives on stdin after this fixed script; the rest of stdin is the stop channel.
CONTROL_ERRORS = frozenset({"not_owner", "invalid_request", "firmware_mismatch",
                            "quasar_not_running", "ipc_unavailable", "state_unavailable",
                            "preview_state_changed", "at_limit", "temporary_path_busy",
                            "artifact_changed", "send_unverified", "not_observed"})
CONTROL_SCRIPT = r"""set -u
umask 077
exec 2>/dev/null
mode=$1; firmware=$2; size=$3; pin=$4; stamp=$5; command=$6; arg=$7; expected=${8:-}
blocked() { printf '{"outcome":"blocked","error_code":"%s"}\n' "$1"; exit 0; }
unknown() { printf '{"outcome":"unknown","error_code":"%s"}\n' "$1"; exit 0; }
[ "$(id -u)" = 0 ] || blocked not_owner
case "$mode" in inspect|apply|inspect_verified|apply_verified) ;; *) blocked invalid_request;; esac
case "$command:$arg" in
 --send-volume-level:[0-9]|--send-volume-level:10) ;;
 --send-volume-step:louder|--send-volume-step:quieter) ;;
 --send-activation:on|--send-activation:off) ;;
 *) blocked invalid_request;;
esac
# *_verified: the host just matched the resident's firmware/boot/Quasar stamp to one that
# a full hash here already proved; otherwise hash the firmware now.
case "$mode" in *_verified) ;; *)
 [ "$(sha256sum /system/vendor/quasar/maind | cut -d ' ' -f 1)" = "$firmware" ] ||
  blocked firmware_mismatch;;
esac
pidof maind >/dev/null || blocked quasar_not_running
awk '$2 == "0100007F:2693" && $4 == "0A" { n++ } END { exit n != 1 }' /proc/net/tcp ||
 blocked ipc_unavailable
V=/data/quasar/last_vendor_volume
L=/data/quasar/daemons_logs/maind.log
volume=$(cat "$V") || blocked state_unavailable
case "$volume" in ''|*[!0-9]*) blocked state_unavailable;; esac
[ "$volume" -le 10000 ] || blocked state_unavailable
boot=$(sha256sum /proc/sys/kernel/random/boot_id | cut -d ' ' -f 1)
state=$(printf '%s\n' "$firmware" "$boot" "$pin" "$stamp" "$command" "$arg" "$volume" |
 sha256sum | cut -d ' ' -f 1)
if [ "${mode%_verified}" = inspect ]; then
 printf '{"outcome":"ready","state_sha256":"%s"}\n' "$state"
 exit 0
fi
[ "${mode%_verified}" = apply ] && [ "$expected" = "$state" ] || blocked preview_state_changed
if [ "$command:$arg" = --send-volume-step:louder ] && [ "$volume" -ge 10000 ]; then
 blocked at_limit
fi
if [ "$command:$arg" = --send-volume-step:quieter ] && [ "$volume" -le 0 ]; then
 blocked at_limit
fi
DIR=$(mktemp -d /tmp/alice-mcp-quasar.XXXXXX) || blocked temporary_path_busy
trap 'rm -rf "$DIR"' EXIT
head -c "$size" >"$DIR/client"
[ "$(sha256sum "$DIR/client" | cut -d ' ' -f 1)" = "$pin" ] || blocked artifact_changed
chmod 500 "$DIR/client"
lines=$(wc -l <"$L") || lines=0
"$DIR/client" "$command" "$arg" >/dev/null 2>&1 || unknown send_unverified
i=0
while [ "$i" -lt 25 ]; do
 sleep 0.2
 i=$((i + 1))
 case "$command" in
  --send-activation)
   [ "$arg" = on ] && word=onPhraseSpotterBegin || word=onPhraseSpotterStop
   if tail -n +$((lines + 1)) "$L" | grep -q "$word"; then
    printf '{"outcome":"applied","activation":"%s"}\n' "$arg"
    exit 0
   fi;;
  *)
   now=$(cat "$V") || continue
   case "$now" in ''|*[!0-9]*) continue;; esac
   done=false
   case "$arg" in
    louder) [ "$now" -gt "$volume" ] && done=true;;
    quieter) [ "$now" -lt "$volume" ] && done=true;;
    *) [ "$now" -eq $((arg * 1000)) ] && done=true;;
   esac
   if [ "$done" = true ]; then
    printf '{"outcome":"applied","previous_level":%s,"level":%s}\n' \
     $((volume / 1000)) $((now / 1000))
    exit 0
   fi;;
 esac
done
unknown not_observed
"""

SCRIPT = r"""set -eu
umask 077
exec 2>/dev/null
firmware=$1; size=$2; pin=$3
[ "$(id -u)" = 0 ] || exit 3
[ "$(sha256sum /system/vendor/quasar/maind | cut -d ' ' -f 1)" = "$firmware" ] || exit 3
pidof maind >/dev/null || exit 3
DIR=$(mktemp -d /tmp/alice-mcp-spotter.XXXXXX)
trap 'rm -rf "$DIR"' EXIT
trap 'exit 0' HUP INT TERM
dd bs=1 count="$size" of="$DIR/client" 2>/dev/null
[ "$(sha256sum "$DIR/client" | cut -d ' ' -f 1)" = "$pin" ] || exit 3
chmod 500 "$DIR/client"
"$DIR/client" --watch-spotter 3600 || true
"""


def parse(raw: bytes) -> dict[str, Any]:
    try:
        if len(raw) > 512:
            raise ValueError
        value = rt._json(raw)
        if type(value) is not dict:
            raise ValueError
        if value.get("event") == "spotter":
            if (set(value) != {"event", "phrase", "trusted"} or value["trusted"] is not False
                    or value["phrase"] not in PHRASES):
                raise ValueError
            return value
        if value.get("event") == "end" and type(value.get("spotted")) is int:
            if value.get("outcome") == "complete" and set(value) == {
                    "event", "outcome", "reason", "spotted"} and value["reason"] in {
                    "stopped", "deadline"}:
                return value
            if value.get("outcome") == "unknown" and set(value) == {
                    "event", "outcome", "error_code", "spotted"} and value["error_code"] in ERRORS:
                return value
        raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise SafetyError("Invalid stock spotter event") from None


def _loader(script: str, args: tuple[str, ...]) -> tuple[str, bytes]:
    source = script.rstrip("\n").encode()
    # Dropbear rejects long exec requests: check the fixed script before running it.
    loader = (f"source=$(dd bs=1 count={len(source)} 2>/dev/null); "
              f'[ "$(printf %s "$source" | sha256sum | cut -d \' \' -f 1)" = '
              f"{hashlib.sha256(source).hexdigest()} ] || exit 1; "
              'exec sh -c "$source" sh "$@"')
    return "sh -c " + shlex.quote(loader) + " sh " + " ".join(map(shlex.quote, args)), source


def request(firmware: str, client: bytes, *, pin: str | None = None) -> SSHInput:
    pin = pin or CLIENT_SHA256
    if hashlib.sha256(client).hexdigest() != pin or not rt._match(rt.HEX64, firmware):
        raise SafetyError("Reviewed spotter client missing or changed; no transfer")
    command, source = _loader(SCRIPT, (firmware, str(len(client)), pin))
    return SSHInput(command, source + client)


def control(action: str, params: dict[str, Any]) -> tuple[str, str]:
    if action == "alice.activation":
        return "--send-activation", "on" if params["enabled"] else "off"
    if "step" in params:
        return "--send-volume-step", params["step"]
    return "--send-volume-level", str(params["level"])


def control_request(firmware: str, mode: str, command: str, arg: str, client: bytes = b"",
                    expected: str = "", *, stamp: str = "-", pin: str | None = None) -> SSHInput:
    pin = pin or CLIENT_SHA256
    apply = mode.startswith("apply")
    if (mode not in {"inspect", "apply", "inspect_verified", "apply_verified"}
            or not rt._match(rt.HEX64, firmware) or apply != bool(client)
            or apply != bool(expected) or client and hashlib.sha256(client).hexdigest() != pin
            or mode.endswith("_verified") and not rt._match(rt.HEX64, stamp)):
        raise SafetyError("Reviewed Quasar client missing or changed; no transfer")
    args = (mode, firmware, str(len(client)), pin, stamp, command, arg,
            *([expected] if expected else []))
    command_line, source = _loader(CONTROL_SCRIPT, args)
    return SSHInput(command_line, source + client)


def parse_control(raw: str) -> dict[str, Any]:
    try:
        value = rt._json(raw.strip().splitlines()[-1].encode())
        if type(value) is not dict:
            raise ValueError
        keys, outcome = set(value), value.get("outcome")
        if outcome == "ready" and keys == {"outcome", "state_sha256"}:
            if not rt._match(rt.HEX64, value["state_sha256"]):
                raise ValueError
        elif outcome in {"blocked", "unknown"} and keys == {"outcome", "error_code"}:
            if value["error_code"] not in CONTROL_ERRORS:
                raise ValueError
        elif outcome == "applied" and keys == {"outcome", "activation"}:
            if value["activation"] not in {"on", "off"}:
                raise ValueError
        elif outcome == "applied" and keys == {"outcome", "previous_level", "level"}:
            if not all(type(value[k]) is int and 0 <= value[k] <= 10
                       for k in ("previous_level", "level")):
                raise ValueError
        else:
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
        return {"outcome": "unknown", "error_code": "send_unverified"}


# Station id -> resident state stamp under which a full SSH firmware hash last passed.
_verified: dict[str, str] = {}


def invoke(controller: Controller, station: Station, request: dict[str, Any]) -> dict[str, Any]:
    """controls.invoke for quasar.volume / alice.activation over the pinned SSH entry.

    With a resident, its fresh firmware/boot/Quasar stamp (cached hash, ~0.1 s) replaces
    re-hashing maind over SSH (~0.8 s) once a full check passed under that same stamp.
    """
    command, arg = control(request["action"], request["params"])
    firmware = station.controls["firmware_sha256"]
    resident = controller.station(station.id)
    stamp = (native_transport._state(controller, resident, "led.show")
             if resident.native_mcp else "-")
    verified = stamp != "-" and _verified.get(station.id) == stamp
    suffix = "_verified" if verified else ""
    if request["mode"] == "inspect":
        result = parse_control(controller.runner(station, control_request(
            firmware, "inspect" + suffix, command, arg, stamp=stamp)))
        if result["outcome"] == "ready" and stamp != "-":
            _verified[station.id] = stamp
        return result
    client = read_pinned(Path(station.controls["quasar_client"]), CLIENT_SHA256, 64 * 1024)
    return parse_control(controller.runner(station, control_request(
        firmware, "apply" + suffix, command, arg, client, request["expected_sha256"],
        stamp=stamp)))


def stream(process: Any, sink: Callable[[dict[str, Any]], None],
           stop: threading.Event) -> dict[str, Any]:
    def closer() -> None:
        while process.poll() is None and not stop.wait(0.5):
            pass
        with contextlib.suppress(OSError, ValueError):
            process.stdin.close()

    threading.Thread(target=closer, name="alice-spotter-stop", daemon=True).start()
    summary: dict[str, Any] = {"event": "end", "outcome": "unknown",
                               "error_code": "ipc_closed", "spotted": 0}
    for raw in process.stdout:
        event = parse(raw.rstrip(b"\n"))
        if event["event"] == "end":
            summary = event
            break
        sink(event)
    return summary


def watch(controller: Controller, station_id: str, sink: Callable[[dict[str, Any]], None],
          *, stop: threading.Event) -> dict[str, Any]:
    """One bounded (≤1 h) listener session; read-only, so a new session may follow."""
    station = controller.station(station_id)
    iop._permitted(controller, station)
    client = read_pinned(Path(station.controls["quasar_client"]), CLIENT_SHA256, 64 * 1024)
    ssh = _ssh(station)
    launch = request(station.controls["firmware_sha256"], client)
    with subprocess.Popen(ssh_argv(ssh, launch), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        assert process.stdin is not None
        process.stdin.write(launch.data)
        process.stdin.flush()
        try:
            return stream(process, sink, stop)
        finally:
            with contextlib.suppress(OSError, ValueError):
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
