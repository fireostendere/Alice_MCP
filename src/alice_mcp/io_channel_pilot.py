"""Owner-run Mini 1 IO-channel level pilot (docs/IO_CHANNEL.md); not an MCP tool.

One temporary hash-pinned helper subscribes to one Quasar audio channel on a
Quasar-owned loopback port for at most 5 seconds, returns per-channel level numbers
and frame counts, unsubscribes and is removed. Audio never leaves station RAM and is
never stored. Quasar is not stopped or reconfigured. Uncertain outcomes are never
retried. Requires a typed owner confirmation after a fresh state-bound preview.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller, SafetyError, SSHInput, Station, _unique, confirm
from alice_mcp.vqe_experiment import _ssh

# Pin only after reviewing native/io_channel_levels.c and rebuilding it (docs/IO_CHANNEL.md).
HELPER_SHA256 = "05a6632285c69795d5c59667697a1b26f3ccd0abe36156fc70168dd8585351c2"
# ponytail: octal-in-script transfer fits ~29.6 KB helpers (this one: 19,664 B) in SSHInput's
# 120 KiB; switch to pcm_pilot's binary-stdin transfer if the helper grows past that.
ACTION = "io_channel.levels"
CHANNELS = ("main", "vqe", "raw")
# Fixed, schema-backed openings (docs/IO_CHANNEL.md, round 2); never caller-supplied bytes.
PREAMBLES = ("none", "listen_only", "full_envelope", "hub_retrieve_state")
PAYLOADS = frozenset({"message", "string_like", "unparsed", "absent", "empty"})
ERRORS = frozenset({"none", "no_audio_frames", "ipc_unavailable", "ipc_closed", "ipc_io_error",
                    "protocol_error", "frame_limit", "channel_limit", "interrupted",
                    "clock_error", "signal_error", "unsubscribe_unverified",
                    "format_changed", "output_error"})  # The last two: --stream only.
PROFILE_ERRORS = frozenset({"not_owner", "invalid_request", "runtime_tools_missing",
                            "data_parent_untrusted", "firmware_mismatch", "abi_mismatch",
                            "quasar_not_running", "ipc_unavailable", "temporary_path_busy",
                            "preview_state_changed"})

# Station guards shared with wake_pilot: data-parent trust, firmware/ABI pin, Quasar
# identity, a loopback listener owned by maind and no other alice-mcp claim.
GUARDS = r'''uid=$(id -u)
trusted() {
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    set -- $(ls -ldn "$1")
    [ "$3" = "$uid" ] && [ "$(printf %s "$1" | cut -c6)" != w ] &&
        [ "$(printf %s "$1" | cut -c9)" != w ]
}
DATA=/data
if [ -L "$DATA" ]; then
    case "$(readlink "$DATA")" in
        /bkupgrade/data|bkupgrade/data) DATA=/bkupgrade/data;; *) blocked data_parent_untrusted;;
    esac
fi
trusted "$DATA" && trusted "$DATA/opt" && [ -w "$DATA/opt" ] || blocked data_parent_untrusted
M=/system/vendor/quasar/maind
[ -f "$M" ] && [ ! -L "$M" ] || blocked firmware_mismatch
actual=$(sha256sum "$M" | cut -d ' ' -f 1)
[ "$actual" = "$firmware" ] || blocked firmware_mismatch
set -- $(od -An -t u1 -N 20 "$M")
[ "$#" = 20 ] && [ "$1 $2 $3 $4 $5 $6" = '127 69 76 70 1 1' ] || blocked abi_mismatch
shift 18
[ "$1 $2" = '40 0' ] || blocked abi_mismatch
pidof maind >/dev/null || blocked quasar_not_running
# Quasar identity: every maind PID with its kernel start time.
identity() {
    # BusyBox pidof also lists led_util players (a maind symlink): not Quasar itself.
    for p in $(pidof maind); do awk '$2 != "(led_util)" { print $1 ":" $22 }' "/proc/$p/stat"
    done |
        sort | sha256sum | cut -d ' ' -f 1
}
hex=$(printf '%04X' "$port")
inode=$(awk -v a="0100007F:$hex" -v b="00000000:$hex" \
    '($2 == a || $2 == b) && $4 == "0A" { n++; i = $10 } END { if (n == 1) print i }' \
    /proc/net/tcp)
case "$inode" in ''|*[!0-9]*) blocked ipc_unavailable;; esac
owned=0
for p in $(pidof maind); do
    if ls -l "/proc/$p/fd" | grep -F "socket:[$inode]" >/dev/null; then owned=1; fi
done
[ "$owned" = 1 ] || blocked ipc_unavailable
for c in "$DATA"/opt/alice-mcp-*-active /bkupgrade/data/opt/alice-mcp-vqe-audit-active; do
    [ ! -e "$c" ] && [ ! -L "$c" ] || blocked temporary_path_busy
done
'''

# Fixed helper; only validated port/channel/seconds/pins are passed as arguments.
SCRIPT = r'''
OWNER_UID=0
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
mode=$1; nonce=$2; port=$3; channel=$4; seconds=$5; firmware=$6; helper_sha=$7; expected=$8
preamble=$9
blocked() { printf '{"outcome":"blocked","error_code":"%s"}\n' "$1"; exit 0; }
[ "$(id -u)" = "$OWNER_UID" ] || blocked not_owner
case "$nonce" in ''|*[!0-9a-f]*) blocked invalid_request;; esac
[ "${#nonce}" = 32 ] || blocked invalid_request
case "$port" in ''|0*|*[!0-9]*) blocked invalid_request;; esac
[ "${#port}" -le 5 ] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || blocked invalid_request
case "$channel" in main|vqe|raw) ;; *) blocked invalid_request;; esac
case "$seconds" in [1-5]) ;; *) blocked invalid_request;; esac
case "$preamble" in none|listen_only|full_envelope|hub_retrieve_state) ;;
    *) blocked invalid_request;; esac
for pin in "$firmware" "$helper_sha"; do
    [ "${#pin}" = 64 ] || blocked invalid_request
    case "$pin" in *[!0-9a-f]*) blocked invalid_request;; esac
done
for c in awk chmod cut grep id ls mkdir od pidof readlink rm rmdir sha256sum sort; do
    command -v "$c" >/dev/null 2>&1 || blocked runtime_tools_missing
done
''' + GUARDS + r'''OUT=$DATA/opt/alice-mcp-iochan-$nonce
CLAIM=$DATA/opt/alice-mcp-iochan-active
[ ! -e "$OUT" ] && [ ! -L "$OUT" ] || blocked temporary_path_busy
boot=$(sha256sum /proc/sys/kernel/random/boot_id | cut -d ' ' -f 1)
before=$(identity)
STATE=$(printf '%s\n' "$actual" "$boot" "$DATA" "$nonce" "$port" "$channel" "$seconds" \
    "$helper_sha" "$before" "$inode" "$preamble" | sha256sum | cut -d ' ' -f 1)
if [ "$mode" = inspect ]; then
    printf '{"outcome":"ready","state_sha256":"%s"}\n' "$STATE"
    exit 0
fi
[ "$mode" = apply ] && [ "$expected" = "$STATE" ] || blocked preview_state_changed
own_out=0; own_claim=0; child=
cleanup() {
    trap - 0 HUP INT TERM
    if [ -n "$child" ]; then
        if [ "$(readlink /proc/$child/exe)" = "$OUT/client" ]; then kill -TERM "$child"; fi
        wait "$child"; child=
    fi
    clean=true
    if [ "$own_out" = 1 ]; then
        rm -f "$OUT/client" && rmdir "$OUT" || clean=false
        [ ! -e "$OUT" ] && [ ! -L "$OUT" ] || clean=false
    fi
    if [ "$own_claim" = 1 ]; then rmdir "$CLAIM" || clean=false; fi
    running=false; unchanged=false
    if pidof maind >/dev/null; then
        running=true
        [ "$(identity)" != "$before" ] || unchanged=true
    fi
    printf '{"outcome":"cleanup","cleanup_verified":%s,"quasar_running":%s,' "$clean" "$running"
    printf '"quasar_unchanged":%s}\n' "$unchanged"
}
mkdir "$CLAIM" || blocked temporary_path_busy
own_claim=1
trap cleanup 0
trap 'exit 0' HUP INT TERM
mkdir "$OUT" || exit 0
own_out=1
(set -C; printf '__HELPER_OCTAL__' > "$OUT/client") || exit 0
[ "$(sha256sum "$OUT/client" | cut -d ' ' -f 1)" = "$helper_sha" ] || exit 0
chmod 500 "$OUT/client" || exit 0
"$OUT/client" --levels "$port" "$channel" "$seconds" "$preamble" </dev/null &
child=$!
wait "$child"
child=
exit 0
'''


@dataclass(frozen=True)
class Pilot:
    port: int
    channel: str
    seconds: int
    helper: bytes = field(repr=False)
    preamble: str = "none"
    nonce: str = field(default_factory=lambda: secrets.token_hex(16))

    def validate(self) -> None:
        if (type(self.port) is not int or not 1024 <= self.port <= 65535
            or self.channel not in CHANNELS or self.preamble not in PREAMBLES
            or type(self.seconds) is not int or not 1 <= self.seconds <= 5
            or not re.fullmatch("[a-f0-9]{32}", self.nonce) or type(self.helper) is not bytes
            or len(SCRIPT) + 4 * len(self.helper) > 120 * 1024
            or hashlib.sha256(self.helper).hexdigest() != HELPER_SHA256):
            raise SafetyError("IO-channel pilot input or reviewed helper invalid; no transfer")

    @property
    def digest(self) -> str:
        return hashlib.sha256(repr((SCRIPT, self.port, self.channel, self.seconds, self.preamble,
                                    self.nonce,
                                    hashlib.sha256(self.helper).hexdigest())).encode()).hexdigest()


def _permitted(controller: Controller, station: Station) -> None:
    controls = station.controls
    if (not controller.writes or not station.preauthorize_all_actions
        or station.model != "station-mini"
        or not rt.allows(controls, "speech.listen")
        or not re.fullmatch("[a-f0-9]{64}", str(controls.get("firmware_sha256", "")))):
        raise SafetyError("IO-channel level pilot is not authorized by private policy")


def _request(station: Station, item: Pilot, mode: str, expected: str = "") -> SSHInput:
    item.validate()
    source = SCRIPT
    if mode == "inspect":
        source = source[:source.index('[ "$mode" = apply ]')]  # No transfer stanza at all.
    else:
        source = source.replace("__HELPER_OCTAL__", "".join(f"\\{b:03o}" for b in item.helper))
    args = [mode, item.nonce, str(item.port), item.channel, str(item.seconds),
            station.controls["firmware_sha256"], HELPER_SHA256, expected, item.preamble]
    return SSHInput("sh -s -- " + " ".join(shlex.quote(a) for a in args), source.encode())


def _json(line: str) -> Any:
    return json.loads(line, object_pairs_hook=_unique)


def ready_state(raw: str, errors: frozenset[str] = PROFILE_ERRORS) -> str:
    """The inspect stanza's state digest; a typed blocked code or any other shape refuses."""
    try:
        state = _json(raw) if len(raw) <= 1024 else None
    except (ValueError, TypeError):
        state = None
    if (type(state) is dict and set(state) == {"outcome", "error_code"}
        and state["outcome"] == "blocked" and str(state["error_code"]) in errors):
        raise SafetyError("Pilot blocked: " + state["error_code"])
    if (type(state) is not dict or set(state) != {"outcome", "state_sha256"}
        or state["outcome"] != "ready" or type(state["state_sha256"]) is not str
        or not re.fullmatch("[a-f0-9]{64}", state["state_sha256"])):
        raise SafetyError("Pilot state unavailable or incompatible; no transfer")
    return state["state_sha256"]


def preview(controller: Controller, station_id: str, item: Pilot) -> dict[str, Any]:
    station = controller.station(station_id)
    _permitted(controller, station)
    item.validate()
    ssh = _ssh(station)
    state = ready_state(controller.runner(ssh, _request(station, item, "inspect")))
    request = {"pilot": item.digest, "ssh_trust": ssh.trust_sha256}
    digest = hashlib.sha256(repr((station, request, state)).encode()).hexdigest()
    plan_id = controller.store_plan(station, ACTION, digest, request=request,
                                    remote_sha256=state)
    return {"plan_id": plan_id, "expected_sha256": digest, "expires_in_seconds": 300,
            "audio_exported": False,
            "impact": f"Temporary pinned helper opens Quasar loopback port {item.port} for "
                      f"{item.seconds} s with fixed opening '{item.preamble}' (channel "
                      f"'{item.channel}'); levels and numeric frame metadata only, no audio "
                      "stored or exported, no Quasar stop or configuration change"}


def _trailer(value: Any) -> dict[str, bool]:
    keys = ("cleanup_verified", "quasar_running", "quasar_unchanged")
    if (type(value) is not dict or set(value) != {"outcome", *keys}
        or value["outcome"] != "cleanup" or any(type(value[k]) is not bool for k in keys)):
        raise ValueError
    return {k: value[k] for k in keys}


def _integer(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _metadata(items: Any) -> None:
    """Numbers and fixed labels only; any other shape is rejected."""
    if type(items) is not list or len(items) > 16:
        raise ValueError
    for item in items:
        if (type(item) is not dict or set(item) != {"length", "kind", "top", "payload", "paths"}
            or not _integer(item["length"], 1, 256 * 1024)
            or not _integer(item["kind"], -1, 536870911) or item["payload"] not in PAYLOADS
            or type(item["top"]) is not list or len(item["top"]) > 8
            or type(item["paths"]) is not list or len(item["paths"]) > 16
            or any(type(t) is not list or len(t) != 2 or not _integer(t[0], 1, 536870911)
                   or t[1] not in {0, 1, 2, 5} or type(t[1]) is not int for t in item["top"])
            or any(type(path) is not list or len(path) not in {1, 2}
                   or not all(_integer(n, 1, 536870911) for n in path)
                   for path in item["paths"])):
            raise ValueError


def _helper(value: Any, preamble: str = "none") -> dict[str, Any]:
    flags = ("connected", "subscribe_sent", "unsubscribe_sent")
    counts = {"frames": 20000, "audio_frames": 20000, "other_frames": 20000,
              # The wake pilot streams up to 60 s; the window closes a few ms late.
              "bytes": 64 * 1024 * 1024, "first_audio_ms": 10000, "elapsed_ms": 61000}
    channel = {"type": 1000, "sample_rate": 768000, "format": 64, "frames": 20000,
               "bytes": 64 * 1024 * 1024, "duration_ms": 10**9,
               "bytes_per_sample_estimate": 2**31, "peak_s16": 32768, "mean_square_s16": 2**30}
    if (type(value) is not dict
        or set(value) != {"outcome", "error_code", "audio_exported", "channels", "other",
                          *flags, *counts}
        or value["outcome"] not in {"complete", "unknown", "blocked"}
        or value["error_code"] not in ERRORS or value["audio_exported"] is not False
        or any(type(value[k]) is not bool for k in flags)
        or not all(_integer(value[k], -1 if k == "first_audio_ms" else 0, high)
                   for k, high in counts.items())
        or type(value["channels"]) is not list or len(value["channels"]) > 16):
        raise ValueError
    for item in value["channels"]:
        if (type(item) is not dict or set(item) != {"name", *channel}
            or type(item["name"]) is not str
            or not re.fullmatch("[a-z0-9_]{1,24}|-", item["name"])
            or not all(_integer(item[k], -1 if k == "bytes_per_sample_estimate" else 0, high)
                       for k, high in channel.items())):
            raise ValueError
    _metadata(value["other"])
    complete = value["outcome"] == "complete"
    requested = preamble != "listen_only"
    if ((value["error_code"] == "none") != complete
        or not requested and (value["subscribe_sent"] or value["unsubscribe_sent"])
        or complete and not (value["connected"] and value["audio_frames"] and value["channels"])
        or complete and requested and not (value["subscribe_sent"]
                                           and value["unsubscribe_sent"])
        or value["outcome"] == "blocked" and value["connected"]
        or value["subscribe_sent"] and not value["connected"]
        or value["unsubscribe_sent"] and not value["subscribe_sent"]
        or not 0 <= value["frames"] - value["audio_frames"] - value["other_frames"] <= 1):
        raise ValueError
    for item in value["channels"]:
        square, peak = item["mean_square_s16"], item["peak_s16"]
        item["rms_dbfs"] = round(10 * math.log10(square / 32768**2), 2) if square else None
        item["peak_dbfs"] = round(20 * math.log10(peak / 32768), 2) if peak else None
    return dict(value)


def parse_result(raw: str, preamble: str = "none") -> dict[str, Any]:
    """Typed allowlisted metadata only; every uncertain shape is unknown and never retried."""
    base: dict[str, Any] = {"outcome": "unknown", "do_not_retry": True, "audio_exported": False}
    lines = raw.splitlines() if len(raw) <= 32768 else []
    try:
        if len(lines) == 1:
            value = _json(lines[0])
            if (type(value) is dict and set(value) == {"outcome", "error_code"}
                and value["outcome"] == "blocked" and str(value["error_code"]) in PROFILE_ERRORS):
                return {"outcome": "blocked", "error_code": value["error_code"],
                        "do_not_retry": True, "audio_exported": False}
            return dict(base, error_code="helper_output_missing", **_trailer(value))
        if len(lines) != 2:
            raise ValueError
        trailer = _trailer(_json(lines[1]))
    except (ValueError, TypeError):
        return dict(base, error_code="helper_output_invalid")
    try:
        result = _helper(_json(lines[0]), preamble)
    except (ValueError, TypeError):
        return dict(base, error_code="helper_output_invalid", **trailer)
    result.update(trailer, do_not_retry=True, evidence_class="analytical",
                  level_basis="s16le_assumed")
    if not all(trailer.values()):
        result.update(outcome="unknown", error_code="quasar_changed_or_cleanup_unverified")
    return result


def apply(controller: Controller, plan_id: str, expected: str, item: Pilot) -> dict[str, Any]:
    try:
        plan = controller.take_plan(plan_id, expected)
        with controller._execution_lock:
            station = controller.station(plan.station.id)
            _permitted(controller, station)
            ssh = _ssh(station)
            if (plan.action != ACTION or station != plan.station
                or controller.clock() >= plan.expires
                or plan.request != {"pilot": item.digest, "ssh_trust": ssh.trust_sha256}):
                raise SafetyError("IO-channel pilot preview changed or expired")
            request = _request(station, item, "apply", plan.remote_sha256)
            try:
                return parse_result(controller.runner(ssh, request), item.preamble)
            except Exception:
                return {"outcome": "unknown", "error_code": "transport_uncertain",
                        "do_not_retry": True, "audio_exported": False}
    except (SafetyError, ValueError):
        return {"outcome": "blocked", "error_code": "preview_changed_or_expired",
                "do_not_retry": True, "audio_exported": False}


def operator_levels(controller: Controller, station_id: str, item: Pilot, *,
                    ask: Any = input) -> dict[str, Any]:
    """Human-run: state-bound preview first, then an exact typed word, then one apply."""
    plan = preview(controller, station_id, item)
    try:
        answer = ask(f"Mini 1 will connect to Quasar loopback port {item.port} for "
                     f"{item.seconds} s (opening '{item.preamble}', channel '{item.channel}'). "
                     "Any room audio stays in "
                     "station RAM; only per-channel level numbers and frame counts return. "
                     "No audio is stored or exported and Quasar is not stopped. Make sure "
                     "people nearby agree. Type LEVELS to start: ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if miss := confirm(answer, "LEVELS"):
        try:
            controller.take_plan(plan["plan_id"], plan["expected_sha256"])  # Consume it.
        except SafetyError:
            pass
        return {"outcome": "cancelled", "do_not_retry": True, "audio_exported": False,
                **miss}
    return apply(controller, plan["plan_id"], plan["expected_sha256"], item)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-run Mini 1 IO-channel level pilot")
    parser.add_argument("--station", required=True, help="Private station alias, never an endpoint")
    parser.add_argument("--helper", required=True, type=Path,
                        help="Already-built helper matching the pinned SHA-256")
    parser.add_argument("--port", required=True, type=int, help="Port from io_channel_probe")
    parser.add_argument("--channel", choices=CHANNELS, default="vqe")
    parser.add_argument("--seconds", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--preamble", choices=PREAMBLES, default="none",
                        help="Fixed schema-backed opening (docs/IO_CHANNEL.md)")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error("Use a terminal beside Mini 1, not an agent or piped confirmation")
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
    try:
        with args.helper.open("rb") as source:
            helper = source.read(64 * 1024 + 1)
        controller = Controller(Path(inventory),
                                writes=os.environ.get("ALICE_MCP_ALLOW_WRITES") == "1")
        result = operator_levels(controller, args.station,
                                 Pilot(args.port, args.channel, args.seconds, helper,
                                       args.preamble))
    except SafetyError as error:
        result = {"outcome": "blocked", "error": str(error), "audio_exported": False}
    except Exception:
        result = {"outcome": "blocked", "error_code": "operator_inputs_unavailable",
                  "audio_exported": False}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if result["outcome"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
