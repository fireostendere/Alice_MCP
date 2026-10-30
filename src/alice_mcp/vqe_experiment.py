"""Operator-only Mini 1 statistics experiment; not a public shell/upload tool.

Requires a separately authorized bounded Quasar interruption. Never call this as
an implicit setup step. A detached guard always returns the temporary launcher
profile to stock; there is no commit/persist mode and no PCM export.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import shlex
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from alice_mcp import speech_capture
from alice_mcp.core import (
    Controller,
    SafetyError,
    SSHInput,
    Station,
    _check_ssh_file,
    _unique,
    confirm,
)

PREFIX = "/bkupgrade/data/opt/alice-mcp-vqe-audit-"
VQE_SHA256 = "2357a939be8cf526787463d1316df78f7fe64098f7db1f7b17edf26b9aef64e2"
Q_SHA256 = "7de0724b28d274c0205c77f73e0509117eec305f12ba09abb4b1a9c4fd02bb52"
AUDIT_SHA256 = "23a8b891be5381a0b3247841f19fc23194bef1e0e22fa9d92f8d539284d69b95"
COLLECTOR_SHA256 = "7e63ca70a7d673b00c19b249b54dc29a0dcaf648690de485a0e99dd9f131cb47"
MUTE_SHA256 = "ee23d97a5340a16e2addb024c7b3a4f68415f9c26e8177423a1d2d2132a8d3fc"

CLASSIFY = r'''
launcher_kind() {
    tr '\000' '\n' < /proc/$1/cmdline | awk \
        -v binary="$Q/quasar_launcher2" -v stock="$Q/launch.json" \
        -v owned="$ROOT/launch.audit.json" '
        NR==1 { valid=($0==binary || $0=="./quasar_launcher2" || $0=="quasar_launcher2") }
        NR==2 { if ($0==stock || $0=="launch.json") kind="stock";
                else if ($0==owned) kind="owned" }
        END { if (NR==2 && valid && kind!="") print kind; else print "unknown" }'
}
'''

INSPECT = CLASSIFY + r'''
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
id=$1; firmware=$2; qpin=$3; launchpin=$4; vqepin=$5; payload=$6
Q=/system/vendor/quasar
blocked() { printf '{"outcome":"blocked","error_code":"profile_unavailable"}\n'; exit 0; }
[ "$(id -u)" = 0 ] || blocked
for tool in cp mv setsid sleep pidof readlink mkdir rmdir chmod tr grep awk cut \
    sha256sum head tail; do
    command -v "$tool" >/dev/null 2>&1 || blocked
done
[ "${#id}" = 32 ] || blocked
case "$id" in *[!0-9a-f]*) blocked;; esac
for pin in "$firmware" "$qpin" "$launchpin" "$vqepin" "$payload"; do
    [ "${#pin}" = 64 ] || blocked
    case "$pin" in *[!0-9a-f]*) blocked;; esac
done
ROOT=/bkupgrade/data/opt/alice-mcp-vqe-audit-$id
CLAIM=/bkupgrade/data/opt/alice-mcp-vqe-audit-active
[ -d /bkupgrade/data/opt ] && [ ! -L /bkupgrade/data/opt ] || blocked
for parent in / /bkupgrade /bkupgrade/data /bkupgrade/data/opt; do
    [ -d "$parent" ] && [ ! -L "$parent" ] || blocked
    meta=$(ls -ldn "$parent")
    owner=$(printf '%s\n' "$meta" | awk '{print $3}')
    mode=$(printf '%s\n' "$meta" | awk '{print $1}')
    [ "$owner" = 0 ] && [ "$(printf %s "$mode" | cut -c6)" != w ] &&
        [ "$(printf %s "$mode" | cut -c9)" != w ] || blocked
done
[ ! -e "$ROOT" ] && [ ! -L "$ROOT" ] && [ ! -e "$CLAIM" ] && [ ! -L "$CLAIM" ] || blocked
for pair in "maind:$firmware" "q:$qpin" "launch.json:$launchpin" \
    "libs/libyandex_evil.so:$vqepin"; do
    name=${pair%%:*}; pin=${pair#*:}
    [ -f "$Q/$name" ] && [ ! -L "$Q/$name" ] || blocked
    [ "$(sha256sum "$Q/$name" | cut -d ' ' -f 1)" = "$pin" ] || blocked
done
pidof maind >/dev/null || blocked
count=0
for p in $(pidof quasar_launcher2); do
    [ "$(launcher_kind "$p")" = stock ] || blocked
    count=$((count+1))
done
[ "$count" = 1 ] && [ -f /lib/ld-linux-armhf.so.3 ] || blocked
boot=$(sha256sum /proc/sys/kernel/random/boot_id | cut -d ' ' -f 1)
state=$(printf '%s\n' "$id" "$firmware" "$qpin" "$launchpin" "$vqepin" "$payload" "$boot" |
    sha256sum | cut -d ' ' -f 1)
'''

GUARD = '#!/bin/sh\n' + CLASSIFY + r'''
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
ROOT=$1; qpin=$2; launchpin=$3
Q=/system/vendor/quasar
case "$ROOT" in /bkupgrade/data/opt/alice-mcp-vqe-audit-*) ;; *) exit 2;; esac
id=${ROOT##*-}
[ "${#id}" = 32 ] || exit 2
case "$id" in *[!0-9a-f]*) exit 2;; esac
[ "$ROOT" = "/bkupgrade/data/opt/alice-mcp-vqe-audit-$id" ] || exit 2
printf '{"guard_ready":true}\n' > "$ROOT/guard.ready" || exit 2
sleep 60
valid=true; changed=false; command_ok=false
[ "$(sha256sum "$Q/q" | cut -d ' ' -f 1)" = "$qpin" ] || valid=false
[ "$(sha256sum "$Q/launch.json" | cut -d ' ' -f 1)" = "$launchpin" ] || valid=false
custom=0; stock=0; unknown=0
for p in $(pidof quasar_launcher2); do
    case "$(launcher_kind "$p")" in
        owned) custom=$((custom+1));; stock) stock=$((stock+1));;
        *) unknown=$((unknown+1));;
    esac
done
if [ "$valid" = true ] && [ "$unknown" = 0 ] && [ "$stock" = 0 ]; then
    if "$Q/q" restart >/dev/null 2>&1; then command_ok=true; fi
    changed=true
    sleep 3
fi
printf '{"guard_finished":true,"policy_unchanged":%s,' "$valid" > "$ROOT/restored.json"
printf '"rollback_requested":%s,"command_succeeded":%s}\n' \
    "$changed" "$command_ok" >> "$ROOT/restored.json"
'''

COLLECTOR_START = r'''
LD_LIBRARY_PATH="$Q/libs" setsid "$ROOT/collector" "$id" > "$ROOT/stats.jsonl" 2>/dev/null &
child=$!
printf '%s\n' "$child" > "$ROOT/collector.pid"
for n in 1 2 3; do [ -S "$ROOT/stats.sock" ] && break; sleep 1; done
[ -S "$ROOT/stats.sock" ] || exit 0
'''


def guard_source(deferred: bool) -> str:
    if type(deferred) is not bool:
        raise SafetyError("Invalid experiment mode")
    if not deferred:
        return GUARD
    clock = r'''
read -r tick ignored < /proc/uptime || exit 2
start=${tick%%.*}
case "$start" in ''|*[!0-9]*) exit 2;; esac
'''
    wait = r'''
while :; do
    read -r tick ignored < /proc/uptime || break
    now=${tick%%.*}
    case "$now" in ''|*[!0-9]*) break;; esac
    elapsed=$((now-start))
    [ "$elapsed" -ge 0 ] && [ "$elapsed" -lt 900 ] || break
    [ -f "$ROOT/finish" ] && break
    if [ "$elapsed" -ge 60 ]; then
        owned=0
        for p in $(pidof quasar_launcher2); do
            [ "$(launcher_kind "$p")" = owned ] && owned=$((owned+1))
        done
        [ "$owned" = 1 ] && pidof maind >/dev/null || break
    fi
    sleep 1
done
'''
    if GUARD.count("sleep 60") != 1:
        raise SafetyError("Rollback template changed")
    marker = 'printf \'{"guard_ready":true}\\n\' > "$ROOT/guard.ready" || exit 2'
    if GUARD.count(marker) != 1:
        raise SafetyError("Rollback readiness template changed")
    return GUARD.replace(marker, clock + marker).replace("sleep 60", wait)


APPLY = r'''
[ "$state" = "$7" ] || blocked
own_root=0; own_claim=0; guarded=0; reported=0; child=
cleanup() {
    trap - 0 HUP INT TERM
    if [ "$guarded" = 0 ]; then
        if [ -n "$child" ] && [ "$(readlink /proc/$child/exe)" = "$ROOT/collector" ]; then
            kill -TERM "$child"; wait "$child"
        fi
        if [ "$own_root" = 1 ]; then
            for name in audit.so collector launch.audit.json guard.sh stats.jsonl \
                collector.pid guard.pid guard.ready launcher.pid restored.json stats.sock \
                baseline.jsonl muted.jsonl unmuted.jsonl stats.new finish deferred; do
                rm -f "$ROOT/$name"
            done
            rmdir "$ROOT"
        fi
        if [ "$own_claim" = 1 ]; then rm -f "$CLAIM/id"; rmdir "$CLAIM"; fi
    fi
    if [ "$reported" = 0 ]; then
        printf '{"outcome":"unknown","do_not_retry":true,"metadata_only":true}\n'
    fi
}
trap cleanup 0
trap 'exit 0' HUP INT TERM
mkdir "$CLAIM" || exit 0
own_claim=1
printf '%s\n' "$id" > "$CLAIM/id" || exit 0
mkdir "$ROOT" || exit 0
own_root=1
chmod 700 "$CLAIM" "$ROOT" || exit 0
(set -C; printf '__AUDIT__' > "$ROOT/audit.so") || exit 0
(set -C; printf '__COLLECTOR__' > "$ROOT/collector") || exit 0
(set -C; printf '__PROFILE__' > "$ROOT/launch.audit.json") || exit 0
(set -C; printf '__GUARD__' > "$ROOT/guard.sh") || exit 0
[ "$(sha256sum "$ROOT/audit.so" | cut -d ' ' -f 1)" = '__AUDIT_PIN__' ] || exit 0
[ "$(sha256sum "$ROOT/collector" | cut -d ' ' -f 1)" = '__COLLECTOR_PIN__' ] || exit 0
[ "$(sha256sum "$ROOT/launch.audit.json" | cut -d ' ' -f 1)" = '__PROFILE_PIN__' ] || exit 0
[ "$(sha256sum "$ROOT/guard.sh" | cut -d ' ' -f 1)" = '__GUARD_PIN__' ] || exit 0
chmod 500 "$ROOT/audit.so" "$ROOT/collector" "$ROOT/guard.sh" || exit 0
chmod 600 "$ROOT/launch.audit.json" || exit 0
LD_LIBRARY_PATH="$Q/libs" LD_PRELOAD="$ROOT/audit.so" ALICE_VQE_AUDIT_SOCKET= \
    "$ROOT/collector" invalid >/dev/null 2>&1
[ "$?" = 2 ] || exit 0
__COLLECTOR_START__
setsid sh "$ROOT/guard.sh" "$ROOT" "$qpin" "$launchpin" </dev/null >/dev/null 2>&1 &
guard=$!
kill -0 "$guard" || exit 0
guarded=1
printf '%s\n' "$guard" > "$ROOT/guard.pid"
for n in 1 2 3; do [ -f "$ROOT/guard.ready" ] && break; sleep 1; done
[ -f "$ROOT/guard.ready" ] || exit 0
"$Q/q" stop >/dev/null 2>&1 || :
if pidof quasar_launcher2 >/dev/null; then exit 0; fi
cd /data/quasar || exit 0
export CA_CERTIFICATES="$Q/ca-certificates.crt" LD_LIBRARY_PATH="$Q/libs"
ulimit -n 10240
ulimit -s 1024
setsid "$Q/quasar_launcher2" "$ROOT/launch.audit.json" </dev/null >/dev/null 2>&1 &
launcher=$!
printf '%s\n' "$launcher" > "$ROOT/launcher.pid"
sleep 1
kill -0 "$launcher" || exit 0
reported=1
printf '{"outcome":"__START_OUTCOME__","capture_ready":false,'
printf '"metadata_only":true,"do_not_retry":true}\n'
'''


def profile(stock: dict[str, Any], identifier: str) -> dict[str, Any]:
    try:
        if not re.fullmatch("[a-f0-9]{32}", identifier):
            raise ValueError
        result = copy.deepcopy(stock)
        matches = 0
        for node in result["nodes"]:
            for task in node["tasks"]:
                envs = task.get("envs", [])
                if type(envs) is not list or any(
                    type(e) is not str or e.startswith("LD_PRELOAD=") for e in envs
                ):
                    raise ValueError
                if "--platformd" in task.get("arg", []):
                    matches += 1
                    root = PREFIX + identifier
                    task["envs"] = envs + ["LD_PRELOAD=" + root + "/audit.so",
                                           "ALICE_VQE_AUDIT_SOCKET=" + root + "/stats.sock"]
        if matches != 1:
            raise ValueError
        return dict(result)
    except Exception:
        raise SafetyError("Unknown or conflicting temporary VQE profile") from None


def _ssh(station: Station) -> Station:
    # Explicit experiment/recovery transport, never an automatic native-MCP fallback.
    _check_ssh_file(station.identity_file, private=True)
    _check_ssh_file(station.known_hosts_file)
    stamp = station.identity_file.stat()
    hosts = station.known_hosts_file.read_bytes()
    if len(hosts) > 65536:
        raise SafetyError("Recovery SSH trust file exceeds bounds")
    trust = hashlib.sha256(repr((stamp.st_dev, stamp.st_ino, stamp.st_ctime_ns,
                                 stamp.st_mtime_ns, stamp.st_size)).encode() + hosts).hexdigest()
    return replace(station, native_mcp={}, trust_sha256=trust)


@dataclass(frozen=True)
class Candidate:
    identifier: str
    stock: bytes = field(repr=False)
    audit: bytes = field(repr=False)
    collector: bytes = field(repr=False)
    deferred: bool = False

    @property
    def launch_pin(self) -> str:
        return hashlib.sha256(self.stock).hexdigest()

    @property
    def source(self) -> bytes:
        if (type(self.deferred) is not bool or len(self.stock) > 32768
            or hashlib.sha256(self.audit).hexdigest() != AUDIT_SHA256
            or hashlib.sha256(self.collector).hexdigest() != COLLECTOR_SHA256):
            raise SafetyError("Unreviewed VQE experiment artifact")
        modified = json.dumps(profile(json.loads(self.stock, object_pairs_hook=_unique),
                                      self.identifier), ensure_ascii=True,
                              separators=(",", ":")).encode()
        parts = {"AUDIT": self.audit, "COLLECTOR": self.collector,
                 "PROFILE": modified, "GUARD": guard_source(self.deferred).encode()}
        source = INSPECT + APPLY
        source = source.replace("__COLLECTOR_START__", ': > "$ROOT/deferred"' if self.deferred
                                else COLLECTOR_START)
        source = source.replace("__START_OUTCOME__", "prepared" if self.deferred else "capturing")
        for name, data in parts.items():
            source = source.replace("__" + name + "__", "".join(f"\\{b:03o}" for b in data))
            source = source.replace("__" + name + "_PIN__", hashlib.sha256(data).hexdigest())
        raw = source.encode()
        SSHInput("sh -s", raw)
        return raw

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.source).hexdigest()


def candidate(station: Station, stock: bytes, audit: bytes, collector: bytes,
              *, deferred: bool = False) -> Candidate:
    try:
        if (station.model != "station-mini" or len(stock) > 32768
            or hashlib.sha256(audit).hexdigest() != AUDIT_SHA256
            or hashlib.sha256(collector).hexdigest() != COLLECTOR_SHA256):
            raise ValueError
        item = Candidate(secrets.token_hex(16), stock, audit, collector, deferred)
        _ = item.source  # Validate canonical source and the helper-size ceiling before preview.
        return item
    except Exception:
        raise SafetyError("VQE candidate invalid or changed; no device transfer") from None


def _args(station: Station, item: Candidate, expected: str = "") -> str:
    values = [item.identifier, station.controls["firmware_sha256"], Q_SHA256,
              item.launch_pin, VQE_SHA256, item.digest, expected]
    return "sh -s -- " + " ".join(shlex.quote(v) for v in values)


def preview(controller: Controller, station_id: str, item: Candidate) -> dict[str, Any]:
    station = controller.station(station_id)
    if (not controller.writes or not station.preauthorize_all_actions
        or station.model != "station-mini"):
        raise SafetyError("VQE experiment policy or candidate unavailable")
    ssh = _ssh(station)
    inspect = INSPECT + '\nprintf \'{"outcome":"ready","state_sha256":"%s"}\\n\' "$state"\n'
    state = json.loads(controller.runner(ssh, SSHInput(_args(station, item), inspect.encode())))
    if (set(state) != {"outcome", "state_sha256"} or state["outcome"] != "ready"
        or not re.fullmatch("[a-f0-9]{64}", state["state_sha256"])):
        raise SafetyError("VQE experiment state is not ready")
    request = {"candidate": item.digest, "ssh_trust": ssh.trust_sha256,
               "inspect_sha256": hashlib.sha256(INSPECT.encode()).hexdigest()}
    digest = hashlib.sha256(repr((station, request, state["state_sha256"])).encode()).hexdigest()
    plan_id = controller.store_plan(station, "vqe.metadata_experiment", digest, request=request,
                                    remote_sha256=state["state_sha256"])
    return {"plan_id": plan_id, "expected_sha256": digest, "expires_in_seconds": 300,
            "metadata_only": True, "automatic_stock_rollback_seconds": 900 if item.deferred else 60,
            "impact": "Temporary platform VQE statistics observer; Quasar interruption, "
                      "no PCM export, vendor-file edit, persistence or reset"}


def apply(controller: Controller, plan_id: str, expected: str, item: Candidate) -> dict[str, Any]:
    try:
        plan = controller.take_plan(plan_id, expected)
        with controller._execution_lock:
            station = controller.station(plan.station.id)
            ssh = _ssh(station)
            if (station != plan.station or plan.action != "vqe.metadata_experiment"
                or not controller.writes or not station.preauthorize_all_actions
                or controller.clock() >= plan.expires
                or plan.request.get("ssh_trust") != ssh.trust_sha256
                or plan.request.get("candidate") != item.digest
                or plan.request.get("inspect_sha256")
                   != hashlib.sha256(INSPECT.encode()).hexdigest()):
                raise SafetyError("VQE experiment preview changed or expired")
            try:
                value = json.loads(controller.runner(ssh, SSHInput(
                    _args(station, item, plan.remote_sha256), item.source,
                )))
                if (value != {"outcome": "prepared" if item.deferred else "capturing",
                               "capture_ready": False, "metadata_only": True, "do_not_retry": True}
                    or any(type(value[k]) is not bool for k in
                           ("capture_ready", "metadata_only", "do_not_retry"))):
                    raise ValueError
                return dict(value, capture_id=item.identifier)
            except Exception:
                return {"outcome": "unknown", "do_not_retry": True,
                        "next_step": "Read experiment state; never repeat its start"}
    except (SafetyError, ValueError):
        return {"outcome": "blocked", "error": "VQE experiment policy, trust or preview changed"}


STATUS = CLASSIFY + r'''
LC_ALL=C; export LC_ALL
exec 2>/dev/null
id=$1
Q=/system/vendor/quasar
ROOT=/bkupgrade/data/opt/alice-mcp-vqe-audit-$id
CLAIM=/bkupgrade/data/opt/alice-mcp-vqe-audit-active
[ -d "$ROOT" ] && [ ! -L "$ROOT" ] && [ "$(cat "$CLAIM/id")" = "$id" ] || exit 1
if [ ! -f "$ROOT/restored.json" ]; then
    phase=capturing
    if [ -f "$ROOT/deferred" ]; then
        phase=prepared
        [ ! -f "$ROOT/finish" ] || phase=restoring
    fi
    printf '{"outcome":"%s","metadata_only":true,"do_not_retry":true}\n' "$phase"
    exit 0
fi
stock=true; count=0
for p in $(pidof quasar_launcher2); do
    [ "$(launcher_kind "$p")" = stock ] || stock=false
    count=$((count+1))
done
[ "$count" = 1 ] || stock=false
running=false; if pidof maind >/dev/null; then running=true; fi
unmapped=true
for p in $(pidof maind); do
    [ -r /proc/$p/maps ] || unmapped=false
    if grep -F "$ROOT/audit.so" /proc/$p/maps >/dev/null; then unmapped=false; fi
done
printf '{"outcome":"complete","stock_launcher":%s,"quasar_running":%s,' "$stock" "$running"
printf '"module_unmapped":%s,"metadata_only":true,"guard":' "$unmapped"
head -c 1024 "$ROOT/restored.json"
printf ',"statistics":'
if [ -f "$ROOT/stats.jsonl" ]; then
    tail -n 1 "$ROOT/stats.jsonl" | head -c 8192
else printf null; fi
printf '}\n'
'''

MEASURE_PHASE = CLASSIFY + r'''
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
id=$1; phase=$2; firmware=$3; collector_pin=$4; qpin=$5; vqepin=$6
Q=/system/vendor/quasar
[ "$(id -u)" = 0 ] && [ "${#id}" = 32 ] || exit 1
case "$id" in *[!0-9a-f]*) exit 1;; esac
case "$phase" in baseline|muted|unmuted) ;; *) exit 1;; esac
ROOT=/bkupgrade/data/opt/alice-mcp-vqe-audit-$id
CLAIM=/bkupgrade/data/opt/alice-mcp-vqe-audit-active
[ -d "$ROOT" ] && [ ! -L "$ROOT" ] && [ "$(cat "$CLAIM/id")" = "$id" ] || exit 1
[ -f "$ROOT/deferred" ] && [ ! -f "$ROOT/finish" ] && [ ! -f "$ROOT/restored.json" ] || exit 1
[ ! -e "$ROOT/$phase.jsonl" ] && [ ! -L "$ROOT/$phase.jsonl" ] || exit 1
[ ! -e "$ROOT/stats.sock" ] && [ ! -L "$ROOT/stats.sock" ] || exit 1
[ -f "$ROOT/collector" ] && [ ! -L "$ROOT/collector" ] || exit 1
[ "$(sha256sum "$ROOT/collector" | cut -d ' ' -f 1)" = "$collector_pin" ] || exit 1
[ "$(sha256sum "$Q/maind" | cut -d ' ' -f 1)" = "$firmware" ] || exit 1
[ "$(sha256sum "$Q/q" | cut -d ' ' -f 1)" = "$qpin" ] || exit 1
[ "$(sha256sum "$Q/libs/libyandex_evil.so" | cut -d ' ' -f 1)" = "$vqepin" ] || exit 1
owned=0
for p in $(pidof quasar_launcher2); do
    [ "$(launcher_kind "$p")" = owned ] && owned=$((owned+1))
done
[ "$owned" = 1 ] || exit 1
child=; claimed=0
cleanup_phase() {
    trap - 0 HUP INT TERM
    if [ -n "$child" ]; then
        if [ "$(readlink /proc/$child/exe)" = "$ROOT/collector" ]; then kill -TERM "$child"; fi
        wait "$child"
    fi
    if [ "$claimed" = 1 ]; then rmdir "$ROOT/measure-active"; fi
}
trap cleanup_phase 0
trap 'exit 1' HUP INT TERM
mkdir "$ROOT/measure-active" || exit 1
claimed=1
(set -C; : > "$ROOT/$phase.jsonl") || exit 1
LD_LIBRARY_PATH="$Q/libs" setsid "$ROOT/collector" "$id" </dev/null \
    > "$ROOT/$phase.jsonl" 2>/dev/null &
child=$!
printf '%s\n' "$child" > "$ROOT/collector.pid"
if [ "$phase" = baseline ]; then sleep 20; else sleep 8; fi
if [ "$(readlink /proc/$child/exe)" = "$ROOT/collector" ]; then kill -TERM "$child"; fi
wait "$child" || exit 1
child=
[ ! -e "$ROOT/stats.sock" ] && [ ! -f "$ROOT/restored.json" ] || exit 1
cp "$ROOT/$phase.jsonl" "$ROOT/stats.new" && mv "$ROOT/stats.new" "$ROOT/stats.jsonl" || exit 1
rmdir "$ROOT/measure-active" || exit 1
claimed=0
tail -n 1 "$ROOT/$phase.jsonl" | head -c 8192
'''

FINISH = r'''
LC_ALL=C; export LC_ALL
exec 2>/dev/null
id=$1
[ "${#id}" = 32 ] || exit 1
case "$id" in *[!0-9a-f]*) exit 1;; esac
ROOT=/bkupgrade/data/opt/alice-mcp-vqe-audit-$id
CLAIM=/bkupgrade/data/opt/alice-mcp-vqe-audit-active
[ -d "$ROOT" ] && [ ! -L "$ROOT" ] && [ "$(cat "$CLAIM/id")" = "$id" ] || exit 1
[ -f "$ROOT/deferred" ] && [ ! -f "$ROOT/restored.json" ] || exit 1
(set -C; : > "$ROOT/finish") || exit 1
printf '{"outcome":"finish_requested","do_not_retry":true}\n'
'''


def _check_statistics(stats: Any) -> None:
    integers = {"packets", "rejected", "types_bitmap", "type6_calls", "type6_frames",
                "nonfinite", "outside", "peak_millionths", "mean_square_billionths"}
    required = integers | {"outcome", "metadata_only", "cleanup_verified"}
    if (type(stats) is not dict or not required <= stats.keys()
        or set(stats) - required not in (set(), {"samples", "started_monotonic_ms"})
        or stats["outcome"] != "complete" or stats["metadata_only"] is not True
        or type(stats["cleanup_verified"]) is not bool
        or any(type(stats[k]) is not int or not 0 <= stats[k] <= 1000000000 for k in integers)):
        raise ValueError
    if "samples" in stats:
        samples = stats["samples"]
        if (type(stats["started_monotonic_ms"]) is not int
            or not 0 <= stats["started_monotonic_ms"] <= 10**15
            or type(samples) is not list or len(samples) > 64):
            raise ValueError
        previous = 0
        for sample in samples:
            if (type(sample) is not dict or set(sample) != {
                "t_ms", "calls", "peak_millionths", "mean_square_billionths",
            } or any(type(v) is not int for v in sample.values())
                or not previous <= sample["t_ms"] <= 46000
                or not 1 <= sample["calls"] <= 100000
                or not 0 <= sample["peak_millionths"] <= 1000000
                or not 0 <= sample["mean_square_billionths"] <= 1000000000):
                raise ValueError
            previous = sample["t_ms"]


def measure_phase(controller: Controller, station_id: str, identifier: str,
                   phase: str) -> dict[str, Any]:
    """One baseline (20s) or phase (8s), only after the owner's physical-state confirmation."""
    if (not re.fullmatch("[a-f0-9]{32}", identifier)
        or phase not in {"baseline", "muted", "unmuted"}):
        raise SafetyError("Invalid experiment phase")
    station = controller.station(station_id)
    if (not controller.writes or not station.preauthorize_all_actions
        or station.model != "station-mini"):
        raise SafetyError("Experiment phase is not authorized")
    try:
        args = [identifier, phase, station.controls["firmware_sha256"], COLLECTOR_SHA256,
                Q_SHA256, VQE_SHA256]
        request = SSHInput("sh -s -- " + " ".join(shlex.quote(v) for v in args),
                           MEASURE_PHASE.encode())
        result = json.loads(controller.runner(_ssh(station), request), object_pairs_hook=_unique)
        _check_statistics(result)
        if result["cleanup_verified"] is not True:
            raise ValueError
        return {"outcome": "complete", "phase": phase, "statistics": result,
                "metadata_only": True, "do_not_retry": True, "physical_effects_verified": False}
    except Exception:
        return {"outcome": "unknown", "phase": phase, "do_not_retry": True,
                "error": "Phase collection unverified; never repeat its start"}


def request_finish(controller: Controller, station_id: str, identifier: str) -> dict[str, Any]:
    if not re.fullmatch("[a-f0-9]{32}", identifier):
        raise SafetyError("Invalid experiment id")
    station = controller.station(station_id)
    if not controller.writes or not station.preauthorize_all_actions:
        raise SafetyError("Experiment finish is not authorized")
    try:
        request = SSHInput("sh -s -- " + shlex.quote(identifier), FINISH.encode())
        result = json.loads(controller.runner(_ssh(station), request), object_pairs_hook=_unique)
        if (result != {"outcome": "finish_requested", "do_not_retry": True}
            or result["do_not_retry"] is not True):
            raise ValueError
        return dict(result)
    except Exception:
        return {"outcome": "unknown", "do_not_retry": True,
                "error": "Finish dispatch unverified; inspect the already-armed guard"}


def status(controller: Controller, station_id: str, identifier: str) -> dict[str, Any]:
    if not re.fullmatch("[a-f0-9]{32}", identifier):
        raise SafetyError("Invalid experiment id")
    try:
        result = json.loads(controller.runner(_ssh(controller.station(station_id)), SSHInput(
            "sh -s -- " + shlex.quote(identifier), STATUS.encode(),
        )))
        if (set(result) == {"outcome", "metadata_only", "do_not_retry"}
            and result["outcome"] in {"capturing", "prepared", "restoring"}
            and result["metadata_only"] is True and result["do_not_retry"] is True):
            return dict(result)
        if (set(result) != {"outcome", "stock_launcher", "quasar_running", "module_unmapped",
                            "metadata_only", "guard", "statistics"}
            or result["outcome"] != "complete" or result["metadata_only"] is not True
            or any(type(result[k]) is not bool for k in
                   ("stock_launcher", "quasar_running", "module_unmapped"))):
            raise ValueError
        guard = result["guard"]
        if (type(guard) is not dict or set(guard) != {"guard_finished", "policy_unchanged",
                                                    "rollback_requested", "command_succeeded"}
            or any(type(v) is not bool for v in guard.values()) or not guard["guard_finished"]):
            raise ValueError
        if result["statistics"] is not None:
            _check_statistics(result["statistics"])
        restored = all(result[k] for k in ("stock_launcher", "quasar_running", "module_unmapped"))
        return dict(result, restoration_verified=restored, do_not_retry=True,
                    physical_effects_verified=False)
    except Exception:
        return {"outcome": "unknown", "do_not_retry": True,
                "error": "Experiment result or stock restoration unverified; inspect locally"}


def _phase_window(phase: dict[str, Any], start_ms: int = 1500, end_ms: int = 8000,
                  ) -> tuple[list[dict[str, int]], dict[str, Any]]:
    if phase.get("outcome") != "complete":
        raise ValueError
    stats = phase["statistics"]
    _check_statistics(stats)
    if stats["cleanup_verified"] is not True or stats["nonfinite"] or stats["outside"]:
        raise ValueError
    samples = stats.get("samples", [])
    selected = [s for s in samples if start_ms <= s["t_ms"] <= end_ms]
    diagnostics = {k: stats[k] for k in ("packets", "rejected", "types_bitmap", "type6_calls",
                                         "type6_frames", "nonfinite", "outside")}
    diagnostics.update(total_sample_count=len(samples), usable_sample_count=len(selected),
                        first_sample_ms=samples[0]["t_ms"] if samples else None,
                        last_sample_ms=samples[-1]["t_ms"] if samples else None)
    return selected, diagnostics


def baseline_ready(phase: dict[str, Any]) -> bool:
    """Observe type-6 data in the baseline's last eight seconds, not socket readiness."""
    try:
        samples, diagnostics = _phase_window(phase, 12000, 20000)
        return (phase.get("phase") == "baseline" and len(samples) >= 3
                and diagnostics["rejected"] == 0)
    except Exception:
        return False


def compare_phase_levels(muted: dict[str, Any], unmuted: dict[str, Any],
                         *, owner_confirmed: bool, settle_ms: int = 1500) -> dict[str, Any]:
    """Compare independently started windows, unaffected by time spent answering forms."""
    try:
        if owner_confirmed is not True or type(settle_ms) is not int or not 0 <= settle_ms <= 5000:
            raise ValueError
        result: dict[str, Any] = {"outcome": "complete", "electrical_power_cut_verified": False}
        diagnostics = {}
        counts = {}
        for name, phase in (("muted", muted), ("unmuted", unmuted)):
            if phase.get("outcome") != "complete" or phase.get("phase") != name:
                raise ValueError
            samples, diagnostics[name] = _phase_window(phase, settle_ms)
            counts[name] = len(samples)
            if len(samples) >= 3:
                result[name] = {"sample_count": len(samples),
                                "peak_millionths_max": max(s["peak_millionths"] for s in samples),
                                "mean_square_billionths_max": max(s["mean_square_billionths"]
                                                                  for s in samples)}
        if any(count < 3 for count in counts.values()):
            return {"outcome": "unknown", "error_code": "phase_coverage_missing",
                    "sample_counts": counts, "phase_diagnostics": diagnostics,
                    "electrical_power_cut_verified": False}
        low = result["muted"]["peak_millionths_max"] == 0 and (
            result["muted"]["mean_square_billionths_max"] == 0
        )
        high = result["unmuted"]["peak_millionths_max"] > 0 and (
            result["unmuted"]["mean_square_billionths_max"] > 0
        )
        result["level_relation"] = ("muted_below_resolution_unmuted_nonzero"
                                    if low and high else "not_resolved")
        return result
    except Exception:
        return {"outcome": "unknown", "error_code": "phase_metadata_invalid",
                "electrical_power_cut_verified": False}


def clock_marker(controller: Controller, station_id: str, identifier: str) -> int:
    """Read the collector's monotonic time for an owner-confirmed physical phase."""
    if not re.fullmatch("[a-f0-9]{32}", identifier):
        raise SafetyError("Invalid experiment id")
    source = r'''
exec 2>/dev/null
ROOT=/bkupgrade/data/opt/alice-mcp-vqe-audit-$1
[ -d "$ROOT" ] && [ ! -L "$ROOT" ] && [ -f "$ROOT/collector" ] || exit 1
[ "$(sha256sum "$ROOT/collector" | cut -d ' ' -f 1)" = "$2" ] || exit 1
LD_LIBRARY_PATH=/system/vendor/quasar/libs "$ROOT/collector" --clock
'''
    try:
        request = SSHInput("sh -s -- " + shlex.quote(identifier) + " " + COLLECTOR_SHA256,
                           source.encode())
        result = json.loads(controller.runner(_ssh(controller.station(station_id)), request))
        if (type(result) is not dict or set(result) != {"monotonic_ms"}
            or type(result["monotonic_ms"]) is not int
            or not 0 <= result["monotonic_ms"] <= 10**15):
            raise ValueError
        return int(result["monotonic_ms"])
    except Exception:
        raise SafetyError("Experiment phase clock unavailable") from None


def compare_mute_levels(report: dict[str, Any], phases: dict[str, Any],
                        *, settle_ms: int = 1500) -> dict[str, Any]:
    """Compare quantized levels in owner-confirmed phases, not electrical isolation."""
    try:
        if (report.get("restoration_verified") is not True
            or phases.get("owner_confirmed_muted_red") is not True
            or phases.get("owner_confirmed_unmuted_red_off") is not True
            or type(settle_ms) is not int or not 0 <= settle_ms <= 5000):
            raise ValueError
        stats = report["statistics"]
        if (type(stats["nonfinite"]) is not int or type(stats["outside"]) is not int
            or stats["nonfinite"] != 0 or stats["outside"] != 0):
            raise ValueError
        start = stats["started_monotonic_ms"]
        keys = ["muted_phase_start_ms", "muted_phase_end_ms", "unmuted_phase_start_ms",
                "unmuted_phase_end_ms"]
        times = [phases[k] for k in keys]
        if (type(start) is not int or any(type(v) is not int for v in times)
            or not start <= times[0] < times[1] <= times[2] < times[3]):
            raise ValueError
        samples = stats["samples"]
        if type(samples) is not list or len(samples) > 64:
            raise ValueError
        selected: dict[str, list[dict[str, int]]] = {"muted": [], "unmuted": []}
        for sample in samples:
            if (type(sample) is not dict or any(type(sample.get(k)) is not int for k in
                ("t_ms", "peak_millionths", "mean_square_billionths"))
                or not 0 <= sample["t_ms"] <= 46000
                or not 0 <= sample["peak_millionths"] <= 1000000
                or not 0 <= sample["mean_square_billionths"] <= 1000000000):
                raise ValueError
            when = start + sample["t_ms"]
            # ponytail: human-confirmed timing with a settling margin, not a GPIO latency guarantee.
            for name, left, right in [("muted", times[0], times[1]),
                                      ("unmuted", times[2], times[3])]:
                if left + settle_ms <= when <= right - 500:
                    selected[name].append(sample)
        if any(len(values) < 3 for values in selected.values()):
            return {"outcome": "unknown", "error_code": "phase_coverage_missing",
                    "sample_counts": {k: len(v) for k, v in selected.items()},
                    "electrical_power_cut_verified": False}
        result: dict[str, Any] = {"outcome": "complete", "electrical_power_cut_verified": False,
                                  "report_resolution_peak": 0.000001,
                                  "report_resolution_mean_square": 0.000000001}
        for name, values in selected.items():
            result[name] = {"sample_count": len(values),
                            "peak_millionths_max": max(v["peak_millionths"] for v in values),
                            "mean_square_billionths_max": max(v["mean_square_billionths"]
                                                              for v in values)}
        low = result["muted"]["peak_millionths_max"] == 0 and (
            result["muted"]["mean_square_billionths_max"] == 0
        )
        high = result["unmuted"]["peak_millionths_max"] > 0 and (
            result["unmuted"]["mean_square_billionths_max"] > 0
        )
        result["level_relation"] = ("muted_below_resolution_unmuted_nonzero"
                                    if low and high else "not_resolved")
        return result
    except Exception:
        return {"outcome": "unknown", "error_code": "phase_metadata_invalid",
                "electrical_power_cut_verified": False}


def cleanup(controller: Controller, station_id: str, item: Candidate) -> dict[str, Any]:
    """Remove only this experiment's files, after a fresh stock/unmapped check."""
    source = STATUS[:STATUS.index("printf '{\"outcome\":\"complete\"")]
    source += r'''
[ "$stock" = true ] && [ "$running" = true ] && [ "$unmapped" = true ] || exit 1
for name in guard collector launcher; do
    if [ -f "$ROOT/$name.pid" ]; then
        read -r pid < "$ROOT/$name.pid"
        case "$pid" in ''|*[!0-9]*) exit 1;; esac
        if [ "$name" = guard ]; then
            if tr '\000' '\n' < /proc/$pid/cmdline | grep -Fx "$ROOT/guard.sh" >/dev/null; then
                exit 1
            fi
        elif [ "$(readlink /proc/$pid/exe)" = "$ROOT/$name" ]; then exit 1; fi
    fi
done
[ "$(sha256sum /system/vendor/quasar/q | cut -d ' ' -f 1)" = "$2" ] || exit 1
[ "$(sha256sum /system/vendor/quasar/launch.json | cut -d ' ' -f 1)" = "$3" ] || exit 1
clean=true
for name in audit.so collector launch.audit.json guard.sh stats.jsonl collector.pid \
    guard.pid guard.ready launcher.pid restored.json baseline.jsonl muted.jsonl unmuted.jsonl \
    stats.new \
    finish deferred; do
    rm -f "$ROOT/$name" || clean=false
done
if [ -d "$ROOT/measure-active" ] && [ ! -L "$ROOT/measure-active" ]; then
    rmdir "$ROOT/measure-active" || clean=false
fi
rmdir "$ROOT" || clean=false
if [ "$clean" = true ]; then rm -f "$CLAIM/id" && rmdir "$CLAIM" || clean=false; fi
printf '{"outcome":"cleaned","cleanup_verified":%s,"do_not_retry":true}\n' "$clean"
'''
    try:
        if not controller.writes or not re.fullmatch("[a-f0-9]{32}", item.identifier):
            raise SafetyError("Writes disabled")
        args = "sh -s -- " + " ".join(shlex.quote(v) for v in
                                      (item.identifier, Q_SHA256, item.launch_pin))
        result = json.loads(controller.runner(_ssh(controller.station(station_id)),
                                               SSHInput(args, source.encode())))
        if (result != {"outcome": "cleaned", "cleanup_verified": True, "do_not_retry": True}
            or result["cleanup_verified"] is not True or result["do_not_retry"] is not True):
            raise ValueError
        return dict(result)
    except Exception:
        return {"outcome": "unknown", "cleanup_verified": False, "do_not_retry": True}


def parse_mute_result(raw: str) -> dict[str, Any]:
    try:
        if len(raw) > 4096 or len(raw.splitlines()) != 2:
            raise ValueError
        observed, trailer = [json.loads(line, object_pairs_hook=_unique)
                             for line in raw.splitlines()]
        if (type(observed) is not dict or observed.get("audio_captured") is not False
            or observed.get("mute_state") not in {"muted", "unmuted", "unknown"}):
            raise ValueError
        if observed.get("outcome") == "complete":
            required = {"outcome", "mute_state", "current_observation_only", "audio_captured"}
            extra = set(observed) - required
            if (not required <= observed.keys() or extra not in (
                set(), {"seen_states_bitmap", "observations"},
            )
                or observed["current_observation_only"] is not True
                or observed["mute_state"] == "unknown"):
                raise ValueError
            if extra and (type(observed["seen_states_bitmap"]) is not int
                          or observed["seen_states_bitmap"] not in {1, 2, 3}
                          or type(observed["observations"]) is not int
                          or not 1 <= observed["observations"] <= 4096):
                raise ValueError
        elif (set(observed) - {"diagnostics"}
              != {"outcome", "mute_state", "audio_captured", "error_code"}
              or observed["outcome"] != "unknown" or observed["mute_state"] != "unknown"
              or observed["error_code"] not in speech_capture.ERRORS):
            raise ValueError
        if "diagnostics" in observed:
            d = observed["diagnostics"]
            if (type(d) is not dict or set(d) != {"frames_received", "device_callbacks",
                                                 "typed_snapshots"}
                or any(type(v) is not int or not 0 <= v <= 4096 for v in d.values())
                or d["typed_snapshots"] > d["device_callbacks"]
                or d["device_callbacks"] > d["frames_received"]):
                raise ValueError
        if (type(trailer) is not dict or set(trailer) != {"outcome", "cleanup_verified",
                                                        "quasar_running"}
            or trailer["outcome"] != "cleanup"
            or any(type(trailer[k]) is not bool for k in ("cleanup_verified", "quasar_running"))):
            raise ValueError
        result = {**observed, "cleanup_verified": trailer["cleanup_verified"],
                  "quasar_running": trailer["quasar_running"], "do_not_retry": True,
                  "current_observation_only": True, "physical_effects_verified": False}
        if not trailer["cleanup_verified"] or not trailer["quasar_running"]:
            result.update(outcome="unknown", mute_state="unknown",
                          error_code="mute_cleanup_unverified")
        return result
    except Exception:
        raise SafetyError("Mute metadata output incompatible; do not retry") from None


def mute_snapshot(controller: Controller, station_id: str, binary: bytes,
                  *, watch: bool = False) -> dict[str, Any]:
    """One explicitly requested operator diagnostic, using the existing guarded shell runner."""
    station = controller.station(station_id)
    if (not controller.writes or not station.preauthorize_all_actions
        or type(watch) is not bool or station.model != "station-mini"
        or hashlib.sha256(binary).hexdigest() != MUTE_SHA256):
        raise SafetyError("Mute metadata helper or operator policy unavailable")
    ssh = _ssh(station)
    nonce = secrets.token_hex(16)
    inspect = speech_capture.SCRIPT[:speech_capture.SCRIPT.index('[ "$mode" = apply ]')]
    source = speech_capture.SCRIPT.replace("__CLIENT_OCTAL__",
                                            "".join(f"\\{b:03o}" for b in binary))
    source = source.replace("__TEXT_OCTAL__", "")

    def request(mode: str, expected: str = "") -> SSHInput:
        args = [mode, nonce, "20" if watch else "5", station.controls["firmware_sha256"],
                MUTE_SHA256, station.config_sha256, expected,
                "microphone.mute_watch" if watch else "microphone.mute", ""]
        return SSHInput("sh -s -- " + " ".join(shlex.quote(v) for v in args),
                        (inspect if mode == "inspect" else source).encode())

    state = json.loads(controller.runner(ssh, request("inspect")))
    if (set(state) != {"outcome", "state_sha256"} or state["outcome"] != "ready"
        or not re.fullmatch("[a-f0-9]{64}", state["state_sha256"])):
        raise SafetyError("Mute metadata preview unavailable")
    digest = hashlib.sha256(repr((station, ssh.trust_sha256, state, source)).encode()).hexdigest()
    identifier = controller.store_plan(station, "microphone.mute_probe", digest,
                                       remote_sha256=state["state_sha256"])
    plan = controller.take_plan(identifier, digest)
    with controller._execution_lock:
        fresh = controller.station(station_id)
        if (fresh != station or _ssh(fresh).trust_sha256 != ssh.trust_sha256
            or controller.clock() >= plan.expires):
            raise SafetyError("Mute metadata policy/trust changed or preview expired")
        try:
            return parse_mute_result(controller.runner(ssh, request("apply", plan.remote_sha256)))
        except Exception:
            return {"outcome": "unknown", "mute_state": "unknown", "do_not_retry": True,
                    "cleanup_verified": False, "audio_captured": False}


def operator_levels(controller: Controller, station_id: str, audit: bytes, collector: bytes,
                    *, ask: Callable[[str], str] = input,
                    wait: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Human-run calibration; never invoked by a background MCP/setup action."""
    report: dict[str, Any] = {"outcome": "unknown", "restoration_verified": False,
                              "cleanup_verified": False, "audio_exported": False,
                              "electrical_power_cut_verified": False, "do_not_retry": True}
    started = False
    phases: dict[str, dict[str, Any]] = {}
    item: Candidate | None = None
    try:
        if miss := confirm(ask(
                "Temporary VQE statistics profile: brief Quasar interruption; no speech recording. "
                "Rollback is requested at finish or at 900 seconds (15 minutes); stock startup "
                "then takes a few seconds. Be beside Mini 1. Type START 900 to begin: "),
                "START 900"):
            return dict(report, outcome="cancelled", **miss)
        station = controller.station(station_id)
        stock = controller.runner(_ssh(station), SSHInput(
            "sh -s", b"exec 2>/dev/null\nhead -c 32768 /system/vendor/quasar/launch.json\n",
        )).encode()
        item = candidate(station, stock, audit, collector, deferred=True)
        plan = preview(controller, station_id, item)
        result = apply(controller, plan["plan_id"], plan["expected_sha256"], item)
        if result.get("outcome") != "prepared":
            return dict(report, error_code="start_unverified_inspect_guard")
        started = True
        if miss := confirm(ask("First leave microphones ON (red OFF). Type BASELINE for a "
                               "20-second statistics-only readiness check before touching "
                               "mute: "), "BASELINE"):
            return dict(report, **miss)
        baseline = measure_phase(controller, station_id, item.identifier, "baseline")
        if baseline.get("outcome") != "complete":
            return report
        _, diagnostics = _phase_window(baseline, 12000, 20000)
        report["phase_diagnostics"] = {"baseline": diagnostics}
        if not baseline_ready(baseline):
            report["error_code"] = "baseline_not_ready"
            return report
        if miss := confirm(ask("Short-press microphone mute, RELEASE; red indicator must "
                               "stay on. Keep it muted. Type MUTED to start its 8-second level "
                               "window: "), "MUTED"):
            return dict(report, **miss)
        phases["muted"] = measure_phase(controller, station_id, item.identifier, "muted")
        if phases["muted"].get("outcome") != "complete":
            return report
        _, report["phase_diagnostics"]["muted"] = _phase_window(phases["muted"])
        if miss := confirm(ask("Short-press once again, RELEASE; red must disappear. "
                               "Type UNMUTED, then slowly repeat 'raz, dva, tri' for 8 seconds. "
                               "Do NOT say Alice or any activation word: "), "UNMUTED"):
            return dict(report, **miss)
        phases["unmuted"] = measure_phase(controller, station_id, item.identifier, "unmuted")
        if phases["unmuted"].get("outcome") == "complete":
            _, report["phase_diagnostics"]["unmuted"] = _phase_window(phases["unmuted"])
    except (EOFError, KeyboardInterrupt, SafetyError, OSError, ValueError):
        report["error_code"] = "operator_interrupted_or_unverified"
    finally:
        if started and item is not None:
            finish = request_finish(controller, station_id, item.identifier)
            for _ in range(15 if finish.get("outcome") == "finish_requested" else 1):
                wait(2)
                state = status(controller, station_id, item.identifier)
                if state.get("outcome") == "restoring":
                    continue
                if state.get("restoration_verified") is True:
                    report["restoration_verified"] = True
                    clean = cleanup(controller, station_id, item)
                    report["cleanup_verified"] = clean.get("cleanup_verified") is True
                break
    if (report["restoration_verified"] and report["cleanup_verified"]
        and phases.keys() == {"muted", "unmuted"}):
        comparison = compare_phase_levels(phases["muted"], phases["unmuted"], owner_confirmed=True)
        report["comparison"], report["outcome"] = comparison, comparison["outcome"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Human-run Mini 1 VQE level calibration")
    parser.add_argument("--station", required=True, help="Private station alias, never an endpoint")
    parser.add_argument("--audit", required=True, type=Path, help="Already-built pinned audit.so")
    parser.add_argument("--collector", required=True, type=Path,
                        help="Pinned, already-built collector")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("Use a terminal beside Mini 1, not an agent or piped confirmations")
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
    try:
        report = operator_levels(Controller(Path(inventory), writes=True), args.station,
                                  args.audit.read_bytes(), args.collector.read_bytes())
    except Exception:
        report = {"outcome": "blocked", "error_code": "operator_inputs_unavailable"}
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
