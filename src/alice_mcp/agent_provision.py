"""Operator-only bootstrap of exact native artifacts, never a public upload/shell tool."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import math
import re
import secrets
import shlex
import ssl
import subprocess
import tarfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alice_mcp.core import Controller, SafetyError, Station, _check_ssh_file, _unique, ssh_argv
from alice_mcp.speech_capture import CLIENT_SHA256 as ASR_CLIENT_SHA256
from alice_mcp.speech_capture import TTS_CLIENT_SHA256

AGENT_SHA256 = "6a4bb805d26dea1c144ee0ec310b0776b9deaf673c4dd7e852ae79093cc2db2f"
NAMES = ("agent", "tts-once", "asr-once", "config.json", "certificate.pem", "private-key.pem",
         "credential", "start")
MAX_PACKAGE = 20 * 1024 * 1024

WAIT_AGENT = r'''wait_agent() {
    for attempt in 1 2 3; do
        sleep 1
        if kill -0 "$1" 2>/dev/null && [ "$(readlink "/proc/$1/exe")" = "$2" ]; then
            return 0
        fi
    done
    return 1
}
'''

START = r'''#!/bin/sh
umask 077
__WAIT_AGENT__
ROOT=@ROOT@
pidfile=$ROOT/agent.pid
valid_pid() {
    [ -f "$pidfile" ] && [ ! -L "$pidfile" ] || return 1
    read -r pid < "$pidfile"
    case "$pid" in ''|*[!0-9]*) return 1;; esac
    [ "$pid" -gt 1 ] && [ "$(readlink "/proc/$pid/exe")" = "$ROOT/agent" ]
}
case "${1:-start}" in start|stop|status) ;; *) exit 2;; esac
# A stale/busy claim requires operator inspection, never automatic lock removal.
lock=$ROOT/.agent-lock
[ ! -e "$lock" ] && [ ! -L "$lock" ] || exit 1
mkdir "$lock" 2>/dev/null || exit 1
owner=$lock/owner
[ -d "$lock" ] && [ ! -L "$lock" ] || exit 1
# Only an exclusive owner record authorizes cleanup, even if mkdir was nonexclusive.
(set -C; printf '%s\n' "$$" > "$owner") || exit 1
child=; published=0
cleanup() {
    result=$?
    trap - 0 HUP INT TERM
    if [ -n "$child" ]; then
        kill -TERM "$child" 2>/dev/null
        wait "$child" 2>/dev/null
        if [ "$published" = 1 ]; then rm -f "$pidfile"; fi
    fi
    if [ -f "$owner" ] && [ ! -L "$owner" ] && [ "$(cat "$owner")" = "$$" ]; then
        rm "$owner" && rmdir "$lock" || result=1
    else result=1; fi
    exit "$result"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM
case "${1:-start}" in
    start)
        if valid_pid && kill -0 "$pid" 2>/dev/null; then exit 0; fi
        [ ! -L "$pidfile" ] || exit 1
        if [ -e "$pidfile" ]; then
            [ -f "$pidfile" ] && rm "$pidfile" || exit 1
        fi
        setsid "$ROOT/agent" -config "$ROOT/config.json" </dev/null >/dev/null 2>&1 &
        child=$!
        (set -C; printf '%s\n' "$child" > "$pidfile") || exit 1
        published=1
        chmod 600 "$pidfile" || exit 1
        wait_agent "$child" "$ROOT/agent" && valid_pid && [ "$pid" = "$child" ] || exit 1
        child=;;
    stop)
        valid_pid || exit 1
        kill -TERM "$pid" || exit 1
        for attempt in 1 2 3; do
            if ! valid_pid || ! kill -0 "$pid" 2>/dev/null; then
                rm "$pidfile"; exit $?
            fi
            sleep 1
        done
        exit 1;;
    status) valid_pid && kill -0 "$pid" 2>/dev/null;;
    *) exit 2;;
esac
'''.replace("__WAIT_AGENT__", WAIT_AGENT)

SCRIPT = r'''
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
__WAIT_AGENT__
mode=$1; cap=$2; firmware=$3; package=$4; expected=$5; autostart=$6; layout=$7; needed=$8
shift 8
agent_sha=$1; tts_sha=$2; asr_sha=$3; config_sha=$4; cert_sha=$5; key_sha=$6
credential_sha=$7; start_sha=$8
blocked() { printf '{"outcome":"blocked","error_code":"%s"}\n' "$1"; exit 0; }
[ "$(id -u)" = 0 ] || blocked owner_required
case "$cap" in ''|*[!0-9a-f]*) blocked invalid_request;; esac
[ "${#cap}" = 32 ] || blocked invalid_request
case "$mode" in inspect|apply) ;; *) blocked invalid_request;; esac
case "$autostart" in 0|1) ;; *) blocked invalid_request;; esac
case "$layout" in direct|bkupgrade) ;; *) blocked invalid_request;; esac
for value in "$firmware" "$package" "$agent_sha" "$tts_sha" "$asr_sha" "$config_sha" "$cert_sha" \
    "$key_sha" "$credential_sha" "$start_sha"; do
    [ "${#value}" = 64 ] || blocked invalid_request
    case "$value" in *[!0-9a-f]*) blocked invalid_request;; esac
done
case "$needed" in ''|*[!0-9]*) blocked invalid_request;; esac
trusted() {
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    set -- $(ls -ldn "$1")
    [ "$3" = 0 ] && [ "$(printf %s "$1" | cut -c6)" != w ] &&
        [ "$(printf %s "$1" | cut -c9)" != w ]
}
DATA=/data
if [ -L "$DATA" ]; then
    case "$(readlink "$DATA")" in
        /bkupgrade/data|bkupgrade/data) DATA=/bkupgrade/data;;
        *) blocked data_layout_unavailable;;
    esac
fi
case "$layout:$DATA" in
    direct:/data|bkupgrade:/bkupgrade/data) ;; *) blocked data_layout_changed;;
esac
trusted "$DATA" && trusted "$DATA/opt" || blocked data_parent_untrusted
actual=$(sha256sum /system/vendor/quasar/maind | awk '{print $1}')
[ "$actual" = "$firmware" ] && pidof maind >/dev/null || blocked firmware_or_quasar_changed
DEST=$DATA/opt/alice-mcp-agent
INIT=/etc/init.d/S99zz-alice-mcp
[ ! -e "$DEST" ] && [ ! -L "$DEST" ] || blocked destination_exists
[ ! -e "$INIT" ] && [ ! -L "$INIT" ] || blocked startup_entry_exists
listeners=$(awk '$2 ~ /:480B$/ && $4=="0A" {n++} END {print n+0}' /proc/net/tcp /proc/net/tcp6)
[ "$listeners" = 0 ] || blocked native_port_busy
free=$(df -Pk "$DATA" | awk 'NR==2 {print $4}')
case "$free" in ''|*[!0-9]*) blocked space_unavailable;; esac
[ "$free" -ge "$needed" ] || blocked space_unavailable
if [ "$autostart" = 1 ]; then
    trusted /etc && trusted /etc/init.d || blocked startup_parent_untrusted
    awk '$2=="/" && $4 ~ /(^|,)rw(,|$)/ {found=1} END {exit !found}' /proc/mounts ||
        blocked rootfs_not_writable
fi
boot=$(sha256sum /proc/sys/kernel/random/boot_id | awk '{print $1}')
state=$(printf '%s\n' "$actual" "$boot" "$DATA" "$package" "$cap" "$autostart" |
    sha256sum | awk '{print $1}')
if [ "$mode" = inspect ]; then
    printf '{"outcome":"ready","state_sha256":"%s"}\n' "$state"; exit 0
fi
[ "$expected" = "$state" ] || blocked preview_state_changed
own=0; hook=0; started=0; child=; reason=bootstrap_unverified
cleanup() {
    trap - 0 HUP INT TERM
    clean=true
    if [ "$started" = 0 ]; then
        if [ -n "$child" ] && [ "$(readlink "/proc/$child/exe")" = "$DEST/agent" ]; then
            kill -TERM "$child"; wait "$child"
        fi
        if [ "$hook" = 1 ]; then
            if [ "$(sha256sum "$INIT" | awk '{print $1}')" = "$hook_sha" ]; then
                rm -f "$INIT"
            else clean=false; fi
        fi
        if [ "$own" = 1 ]; then
            for name in agent tts-once asr-once config.json certificate.pem private-key.pem \
                credential start agent.pid .package.tar; do
                rm -f "$DEST/$name" || clean=false
            done
            rmdir "$DEST" || clean=false
        fi
        printf '{"outcome":"unknown","error_code":"%s",' "$reason"
        printf '"staging_cleaned":%s,"do_not_retry":true}\n' "$clean"
    else
        registered=false; if [ "$hook" = 1 ]; then registered=true; fi
        printf '{"outcome":"installed","quasar_changed":false,'
        printf '"autostart_registered":%s,' "$registered"
        printf '"package_verified":true,"staging_cleaned":true,"do_not_retry":true}\n'
    fi
}
trap cleanup 0
trap 'exit 0' HUP INT TERM
mkdir "$DEST" || exit 0
own=1
chmod 700 "$DEST" || exit 0
(set -C; cat > "$DEST/.package.tar") || exit 0
[ "$(sha256sum "$DEST/.package.tar" | awk '{print $1}')" = "$package" ] || exit 0
tar xf "$DEST/.package.tar" -C "$DEST" || exit 0
check() {
    [ -f "$DEST/$1" ] && [ ! -L "$DEST/$1" ] &&
        [ "$(sha256sum "$DEST/$1" | awk '{print $1}')" = "$2" ]
}
check agent "$agent_sha" && check tts-once "$tts_sha" && check asr-once "$asr_sha" &&
    check config.json "$config_sha" &&
    check certificate.pem "$cert_sha" && check private-key.pem "$key_sha" &&
    check credential "$credential_sha" && check start "$start_sha" || exit 0
chmod 500 "$DEST/agent" "$DEST/tts-once" "$DEST/asr-once" "$DEST/start" || exit 0
chmod 600 "$DEST/config.json" "$DEST/certificate.pem" "$DEST/private-key.pem" \
    "$DEST/credential" || exit 0
rm -f "$DEST/.package.tar" || exit 0
if [ "$autostart" = 1 ]; then
    hook_sha=$(printf '#!/bin/sh\nexec %s/start "$@"\n' "$DEST" | sha256sum | awk '{print $1}')
    (set -C; printf '#!/bin/sh\nexec %s/start "$@"\n' "$DEST" > "$INIT") || exit 0
    hook=1
    chmod 500 "$INIT" || exit 0
fi
setsid "$DEST/agent" -config "$DEST/config.json" </dev/null >/dev/null 2>&1 &
child=$!
printf '%s\n' "$child" > "$DEST/agent.pid" || exit 0
chmod 600 "$DEST/agent.pid" || exit 0
wait_agent "$child" "$DEST/agent" || exit 0
started=1
exit 0
'''.replace("__WAIT_AGENT__", WAIT_AGENT)


@dataclass(frozen=True)
class AgentBundle:
    package: bytes = field(repr=False)
    sha256: str
    layout: str
    autostart: bool
    hashes: tuple[str, ...]


def destination(layout: str) -> str:
    if layout not in {"direct", "bkupgrade"}:
        raise SafetyError("Unsupported native-agent data layout")
    return ("/data" if layout == "direct" else "/bkupgrade/data") + "/opt/alice-mcp-agent"


def start_script(layout: str) -> str:
    return START.replace("@ROOT@", destination(layout))


def _validate_content(station: Station, content: dict[str, bytes], layout: str) -> None:
    root = destination(layout)
    if (station.model != "station-mini" or set(content) != set(NAMES)
        or hashlib.sha256(content["agent"]).hexdigest() != AGENT_SHA256
        or hashlib.sha256(content["tts-once"]).hexdigest() != TTS_CLIENT_SHA256
        or hashlib.sha256(content["asr-once"]).hexdigest() != ASR_CLIENT_SHA256
        or content["start"] != start_script(layout).encode()
        or not re.fullmatch(b"[a-f0-9]{64}", content["credential"])):
        raise ValueError("unreviewed provisioning content")
    value = json.loads(content["config.json"], object_pairs_hook=_unique)
    expected = {"station_id": station.id, "model": station.model,
                "certificate_file": root + "/certificate.pem",
                "private_key_file": root + "/private-key.pem", "token_file": root + "/credential",
                "firmware_sha256": station.controls["firmware_sha256"],
                "tts_client": root + "/tts-once", "asr_client": root + "/asr-once",
                "allowed_actions": ["speech.say", "speech.listen"],
                "preauthorize_all_actions": True}
    if (type(value) is not dict or set(value) != set(expected) | {"listen"}
        or any(value[k] != v for k, v in expected.items())
        or value["preauthorize_all_actions"] is not True):
        raise ValueError("unapproved provisioning configuration")
    address = urlsplit("//" + value["listen"])
    ip = ipaddress.ip_address(address.hostname or "")
    if address.port != 18443 or not ip.is_private or ip.is_unspecified:
        raise ValueError("unapproved provisioning listener")


def _archive(content: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in NAMES:
            info = tarfile.TarInfo(name)
            info.size = len(content[name])
            info.mode = 0o500 if name in {"agent", "tts-once", "asr-once", "start"} else 0o600
            archive.addfile(info, io.BytesIO(content[name]))
    return output.getvalue()


def _verify(station: Station, package: AgentBundle) -> None:
    try:
        if (len(package.package) > MAX_PACKAGE or type(package.autostart) is not bool
            or hashlib.sha256(package.package).hexdigest() != package.sha256):
            raise ValueError
        content: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(package.package), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != len(NAMES):
                raise ValueError
            for member in members:
                if (member.name not in NAMES or member.name in content or not member.isfile()
                    or not 0 <= member.size <= MAX_PACKAGE or member.pax_headers):
                    raise ValueError
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError
                with stream:
                    content[member.name] = stream.read(MAX_PACKAGE + 1)
        _validate_content(station, content, package.layout)
        if (package.hashes != tuple(hashlib.sha256(content[name]).hexdigest() for name in NAMES)
            or _archive(content) != package.package):
            raise ValueError
    except Exception:
        raise SafetyError("Native-agent bundle invalid or changed; no transfer") from None


def bundle(station: Station, directory: Path, *, layout: str,
           autostart: bool = True) -> AgentBundle:
    try:
        if station.model != "station-mini" or type(autostart) is not bool:
            raise ValueError
        content: dict[str, bytes] = {}
        for name in NAMES[:-1]:
            path = directory / name
            _check_ssh_file(path, private=name in {"config.json", "private-key.pem", "credential"})
            with path.open("rb") as source:
                data = source.read(MAX_PACKAGE + 1 if name == "agent" else 65537)
            if len(data) > (MAX_PACKAGE if name == "agent" else 65536):
                raise ValueError
            content[name] = data
        content["start"] = start_script(layout).encode()
        _validate_content(station, content, layout)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(directory / "certificate.pem", directory / "private-key.pem")
        package = _archive(content)
        if len(package) > MAX_PACKAGE:
            raise ValueError
        return AgentBundle(package, hashlib.sha256(package).hexdigest(), layout, autostart,
                           tuple(hashlib.sha256(content[name]).hexdigest() for name in NAMES))
    except Exception:
        raise SafetyError("Native-agent bundle invalid or changed; no transfer") from None


def _command(station: Station, package: AgentBundle, nonce: str, expected: str = "") -> str:
    args = ["apply" if expected else "inspect", nonce, station.controls["firmware_sha256"],
            package.sha256, expected, "1" if package.autostart else "0", package.layout,
            str(2 * (len(package.package) // 1024 + 1) + 4096), *package.hashes]
    return "sh -c " + shlex.quote(SCRIPT) + " sh " + " ".join(shlex.quote(x) for x in args)


def _permitted(controller: Controller, station: Station) -> None:
    if (not controller.writes or not station.preauthorize_all_actions or station.native_mcp
        or station.model != "station-mini"):
        raise SafetyError("Native-agent provisioning is not authorized or already configured")


def preview(controller: Controller, station_id: str, package: AgentBundle) -> dict[str, Any]:
    station = controller.station(station_id)
    _permitted(controller, station)
    _verify(station, package)
    nonce = secrets.token_hex(16)
    response = json.loads(controller.runner(station, _command(station, package, nonce)))
    if (set(response) != {"outcome", "state_sha256"} or response["outcome"] != "ready"
        or not re.fullmatch("[a-f0-9]{64}", response["state_sha256"])):
        raise SafetyError("Native-agent bootstrap profile is not ready")
    request = {"bundle_sha256": package.sha256, "nonce": nonce, "layout": package.layout,
               "autostart": package.autostart,
               "helper_sha256": hashlib.sha256(SCRIPT.encode()).hexdigest()}
    digest = hashlib.sha256(repr((station, request, response["state_sha256"])).encode()).hexdigest()
    identifier = controller.store_plan(station, "native_agent.install", digest, request=request,
                                        remote_sha256=response["state_sha256"])
    return {"plan_id": identifier, "expected_sha256": digest, "expires_in_seconds": 300,
            "impact": "Install one reviewed native TLS agent and its own startup entry; "
                      "preserve Quasar, vendor profiles and SSH. No microphone recording."}


def _transfer(station: Station, command: str, data: bytes) -> dict[str, Any]:
    if len(data) > MAX_PACKAGE:
        raise SafetyError("Native-agent transfer exceeds reviewed size")
    with subprocess.Popen(ssh_argv(station, command), stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        assert process.stdin is not None and process.stdout is not None
        sink, source = process.stdin, process.stdout
        output: list[bytes] = []
        failed: list[bool] = []

        def write() -> None:
            try:
                with sink:
                    sink.write(data)
            except OSError:
                failed.append(True)

        def read() -> None:
            body = source.read(4097)
            output.append(body)
            if len(body) > 4096:
                process.kill()

        writer = threading.Thread(target=write, daemon=True)
        reader = threading.Thread(target=read, daemon=True)
        writer.start()
        reader.start()
        try:
            status = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
            raise SafetyError("Native-agent transfer uncertain; do not retry") from None
        writer.join(timeout=2)
        reader.join(timeout=2)
        if (status or failed or writer.is_alive() or reader.is_alive() or not output
            or len(output[0]) > 4096):
            raise SafetyError("Native-agent transfer uncertain; do not retry")
        result = json.loads(output[0])
        if (type(result) is not dict
            or result.get("outcome") not in {"installed", "blocked", "unknown"}
            or result.keys() - {"outcome", "error_code", "quasar_changed", "autostart_registered",
                               "package_verified", "staging_cleaned", "do_not_retry"}):
            raise SafetyError("Native-agent transfer returned incompatible metadata")
        for key in ("quasar_changed", "autostart_registered", "package_verified",
                    "staging_cleaned", "do_not_retry"):
            if key in result and type(result[key]) is not bool:
                raise SafetyError("Native-agent transfer returned incompatible metadata")
        if result.get("quasar_changed", False) is not False:
            raise SafetyError("Native-agent transfer returned incompatible metadata")
        result.pop("error_code", None)
        return dict(result)


def apply(controller: Controller, plan_id: str, expected_sha256: str,
          package: AgentBundle) -> dict[str, Any]:
    try:
        plan = controller.take_plan(plan_id, expected_sha256)
        with controller._execution_lock:
            station = controller.station(plan.station.id)
            _permitted(controller, station)
            _verify(station, package)
            if (plan.action != "native_agent.install" or station != plan.station
                or not math.isfinite(plan.expires) or controller.clock() >= plan.expires
                or hashlib.sha256(package.package).hexdigest() != package.sha256
                or plan.request.get("bundle_sha256") != package.sha256
                or plan.request.get("layout") != package.layout
                or plan.request.get("autostart") != package.autostart
                or plan.request.get("helper_sha256")
                   != hashlib.sha256(SCRIPT.encode()).hexdigest()):
                raise SafetyError("Native-agent preview changed or expired")
            request = plan.request
            state = json.loads(controller.runner(
                station, _command(station, package, request["nonce"]),
            ))
            if state != {"outcome": "ready", "state_sha256": plan.remote_sha256}:
                raise SafetyError("Native-agent bootstrap state changed")
            if controller.clock() >= plan.expires:
                raise SafetyError("Native-agent preview expired during validation")
            try:
                return _transfer(station, _command(station, package, request["nonce"],
                                                  plan.remote_sha256), package.package)
            except Exception:
                return {"outcome": "unknown", "do_not_retry": True,
                        "next_step": "Inspect the owned agent state before any further action"}
    except SafetyError as error:
        return {"outcome": "blocked", "error": str(error)}
