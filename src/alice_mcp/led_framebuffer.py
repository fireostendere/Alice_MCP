"""Fixed Bergamot LED effect using stock shell utilities, without installation."""

from __future__ import annotations

import base64
import hashlib
import shlex
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp.core import SafetyError, SSHInput

SCRIPT = r'''
OWNER_UID=0
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
mode=$1; firmware=$2; request_sha=$3; expected=$4; effect=$5; cycles=$6; period=$7
blocked() { printf '{"outcome":"blocked"}\n'; exit 0; }
uid=$(id -u)
[ "$uid" = "$OWNER_UID" ] || blocked
for c in awk base64 cat cut dd flock grep id ls mkdir readlink sha256sum sleep tr wc; do
    command -v "$c" >/dev/null 2>&1 || blocked
done
if [ "$cycles" = show ]; then
    # Bounded show: 1-75 base64 18-byte frames, >=200 ms each, <=15 s in total.
    set -f
    case "$period" in ''|*[!0-9]*) blocked;; esac
    [ "${#period}" -le 5 ] && [ "$period" -ge 200 ] || blocked
    count=0
    for f in $effect; do
        case "$f" in *[!A-Za-z0-9+/]*) blocked;; esac
        [ "${#f}" = 24 ] || blocked
        count=$((count + 1))
    done
    [ "$count" -ge 1 ] && [ "$count" -le 75 ] && [ $((count * period)) -le 15000 ] || blocked
    pause=$((period / 1000)).$(printf %03d $((period % 1000)))
    limit=$((count * period / 1000 + 2))
fi
now() { awk '{ print int($1) }' /proc/uptime; }
trusted() {
    [ ! -L "$1" ] && [ -d "$1" ] || return 1
    set -- $(ls -ldn "$1")
    [ "$3" = "$uid" ] || return 1
    [ "$(printf %s "$1" | cut -c6)" != w ] && [ "$(printf %s "$1" | cut -c9)" != w ]
}
private() {
    trusted "$1" || return 1
    set -- $(ls -ldn "$1")
    [ "$1" = drwx------ ]
}
DATA=/data
if [ -L "$DATA" ]; then
    case "$(readlink "$DATA")" in
        /bkupgrade/data|bkupgrade/data) DATA=/bkupgrade/data; trusted /bkupgrade || blocked;;
        *) blocked;;
    esac
fi
BASE=$DATA/opt/alice-mcp
LOCK=$BASE/control.lock
FB=/dev/fb0
frame() { dd if="$FB" bs=18 count=1 | base64; }
write_frame() { printf %s "$1" | base64 -d | dd of="$FB" ibs=18 obs=18 count=1 conv=notrunc; }
profile() {
    trusted "$DATA" || return 1
    if [ -e "$DATA/opt" ] || [ -L "$DATA/opt" ]; then trusted "$DATA/opt" || return 1; fi
    if [ -e "$BASE" ] || [ -L "$BASE" ]; then private "$BASE" || return 1; fi
    for directory in /system /system/vendor /system/vendor/quasar; do
        trusted "$directory" || return 1
    done
    binary=/system/vendor/quasar/maind
    [ -f "$binary" ] && [ ! -L "$binary" ] || return 1
    size=$(wc -c < "$binary")
    case "$size" in ''|*[!0-9]*) return 1;; esac
    [ "${#size}" -le 9 ] && [ "$size" -le 134217728 ] || return 1
    actual=$(sha256sum "$binary" | awk '{print $1}')
    [ "$actual" = "$firmware" ] || return 1
    tr '\000' '\n' < /proc/device-tree/compatible | grep -qx 'yandex,bergamot' || return 1
    [ -c "$FB" ] && [ ! -L "$FB" ] || return 1
    [ "$(cat /sys/class/graphics/fb0/virtual_size)" = 6,1 ] &&
        [ "$(cat /sys/class/graphics/fb0/bits_per_pixel)" = 24 ] &&
        [ "$(cat /sys/class/graphics/fb0/stride)" = 18 ] || return 1
    FRAME=$(frame) || return 1
    case "$FRAME" in *[!A-Za-z0-9+/]*) return 1;; esac
    [ "${#FRAME}" = 24 ] || return 1
    roundtrip=$(printf %s "$FRAME" | base64 -d |
        dd ibs=18 obs=18 count=1 conv=notrunc | base64)
    [ "$roundtrip" = "$FRAME" ] || return 1
    sleep 0.001 || return 1
    boot=$(sha256sum /proc/sys/kernel/random/boot_id) || return 1
    STATE=$(printf '%s\n' "$actual" "$boot" "$FRAME" "$request_sha" |
        sha256sum | awk '{print $1}')
}
profile || blocked
if [ "$mode" = inspect ]; then
    printf '{"outcome":"ready","state_sha256":"%s"}\n' "$STATE"; exit 0
fi
[ "$mode" = apply ] || blocked
if [ ! -e "$DATA/opt" ]; then mkdir "$DATA/opt" || blocked; fi
trusted "$DATA/opt" || blocked
if [ ! -e "$BASE" ]; then mkdir "$BASE" || blocked; fi
private "$BASE" || blocked
if [ ! -e "$LOCK" ] && [ ! -L "$LOCK" ]; then
    (set -C; : > "$LOCK") || blocked
fi
[ -f "$LOCK" ] && [ ! -L "$LOCK" ] || blocked
set -- $(ls -ln "$LOCK")
[ "$1" = -rw------- ] && [ "$2" = 1 ] && [ "$3" = "$uid" ] || blocked
exec 9>> "$LOCK"
flock -n 9 || blocked
profile || blocked
if [ "$STATE" != "$expected" ]; then printf '{"outcome":"conflict"}\n'; exit 0; fi
original=$FRAME
done_ok=false
yielded=false
finish() {
    trap - 0 HUP INT TERM
    restored=false
    if [ "$yielded" != true ] && write_frame "$original" && [ "$(frame)" = "$original" ]; then
        restored=true
    fi
    outcome=unknown
    if [ "$done_ok" = true ] && { [ "$restored" = true ] || [ "$yielded" = true ]; }; then
        outcome=applied
    fi
    printf '{"outcome":"%s","command_succeeded":%s,"restoration_verified":%s,"yielded":%s}\n' \
        "$outcome" "$done_ok" "$restored" "$yielded"
}
trap finish 0
trap 'exit 0' HUP INT TERM
if [ "$cycles" = show ]; then
    # A changed readback means another writer (normally a firmware status indication)
    # owns the ring: stop and leave its state rather than restoring an older snapshot.
    # An unreadable framebuffer is not a yield: exit to the restore attempt instead.
    unchanged() { current=$(frame); [ "${#current}" = 24 ] || exit 0; [ "$current" = "$last" ]; }
    last=$original
    started=$(now)
    for f in $effect; do
        unchanged || { yielded=true; done_ok=true; exit 0; }
        write_frame "$f" || exit 0
        last=$f
        sleep "$pause" || exit 0
        [ $(($(now) - started)) -le "$limit" ] || exit 0
    done
    unchanged || yielded=true
    done_ok=true
    exit 0
fi
i=0
while [ "$i" -lt "$cycles" ]; do
    write_frame "$effect" && sleep "$period" && write_frame "$original" && sleep "$period" || exit 0
    i=$((i + 1))
done
done_ok=true
exit 0
'''


def command(request: dict[str, Any], *, source: str = SCRIPT) -> SSHInput:
    """Accept only existing led.effect/led.show parameters on the exact Mini 3 profile."""
    try:
        mode, action = request["mode"], request["action"]
        rt._require(rt._keys(request, {"schema", "mode", "model", "policy", "action", "params",
                                       "nonce"}, {"expected_sha256"}))
        rt._require(request["schema"] == rt.SCHEMA and request["model"] == "station-mini3"
                    and action in {"led.effect", "led.show"}
                    and rt._match(rt.HEX32, request["nonce"]))
        rt._require(mode in {"inspect", "apply"})
        expected = request.get("expected_sha256", "")
        rt._require(mode == "apply" and rt._match(rt.HEX64, expected)
                    or mode == "inspect" and "expected_sha256" not in request)
        policy = rt.validate_policy(request["policy"], request["model"])
        rt._require(rt.allows(policy, action))
        params = rt.validate_parameters(action, request["params"], policy, request["model"])
        normalized = dict(request, mode="inspect", policy=policy, params=params)
        normalized.pop("expected_sha256", None)
        digest = hashlib.sha256(rt._encode(normalized) + source.encode()).hexdigest()
        if action == "led.show":
            interval, frames = rt.show_frames(params, "station-mini3", policy.get("led_map"))
            effect, cycles, period = " ".join(
                base64.b64encode(bytes(f)).decode() for f in frames), "show", str(interval)
        else:
            effect = base64.b64encode(bytes(params[c] for c in ("blue", "green", "red"))
                                      * 6).decode()
            cycles, period = str(params["count"]), f"{params['period_ms'] / 1000:.3f}"
        args = [mode, policy["firmware_sha256"], digest, expected, effect, cycles, period]
        return SSHInput("sh -s -- " + " ".join(shlex.quote(a) for a in args), source.encode())
    except (ValueError, TypeError, KeyError):
        raise SafetyError("Invalid or unapproved Mini 3 framebuffer request") from None
