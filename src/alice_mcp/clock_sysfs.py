"""display.show on the Mini 3 (Bergamot) white segment clock, using stock shell only.

The clock is plain `/sys/class/leds` brightness nodes without a trigger, read on one unit;
Bergamot carries no Python. Node names come from this fixed profile, never from the request.
"""

from __future__ import annotations

import hashlib
import shlex
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp.core import SafetyError, SSHInput

# Digits 0-3 left to right, segments A-G; then the colon dots, then marks forced dark so a
# stale degree sign or middle dot does not join the text.
COLON = ("clockDH", "clockDL")
BLANK = ("clockDEGREE", "clockDM")
NODES = ([f"bergamot:white:clock{letter}{digit}" for digit in range(4) for letter in "ABCDEFG"]
         + [f"bergamot:white:{name}" for name in COLON + BLANK])

# shortcut: the trust and lock prelude repeats led_framebuffer.SCRIPT; share it once a
# third stock-shell adapter appears.
SCRIPT = r'''
OWNER_UID=0
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
set -f
mode=$1; firmware=$2; request_sha=$3; expected=$4
LEDS=/sys/class/leds
blocked() { printf '{"outcome":"blocked"}\n'; exit 0; }
uid=$(id -u)
[ "$uid" = "$OWNER_UID" ] || blocked
for c in awk cat cut flock grep id ls mkdir readlink sha256sum sleep tr wc; do
    command -v "$c" >/dev/null 2>&1 || blocked
done
# One frame: one level per Bergamot clock node, at most 90/255, shown for 1-15 s.
case "$PERIOD" in ''|*[!0-9]*) blocked;; esac
[ "${#PERIOD}" -le 5 ] && [ "$PERIOD" -ge 1000 ] && [ "$PERIOD" -le 15000 ] || blocked
nodes=0
for n in $NODES; do
    case "$n" in *[!A-Za-z0-9:]*) blocked;; bergamot:white:clock*) ;; *) blocked;; esac
    nodes=$((nodes + 1))
done
[ "$nodes" = 32 ] || blocked
case "$FRAME" in ''|*[!0-9,]*|,*|*,|*,,*) blocked;; esac
values=0
IFS=,
for v in $FRAME; do
    [ "${#v}" -le 2 ] && [ "$v" -le 90 ] || blocked
    values=$((values + 1))
done
unset IFS
[ "$values" = "$nodes" ] || blocked
pause=$((PERIOD / 1000)).$(printf %03d $((PERIOD % 1000)))
limit=$((PERIOD / 1000 + 2))
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
levels() {  # Brightness of every node, comma separated, read without forking.
    out=
    for n in $NODES; do
        read -r v < "$LEDS/$n/brightness" || return 1
        case "$v" in ''|*[!0-9]*) return 1;; esac
        out=$out,$v
    done
    echo "${out#,}"
}
write_levels() {
    IFS=,; set -- $1; unset IFS
    for n in $NODES; do
        printf '%s\n' "$1" > "$LEDS/$n/brightness" || return 1
        shift
    done
}
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
    tr '\000' '\n' < /proc/device-tree/compatible | grep -qx yandex,bergamot || return 1
    for n in $NODES; do
        [ -d "$LEDS/$n" ] || return 1
        read -r max < "$LEDS/$n/max_brightness" || return 1
        case "$max" in ''|*[!0-9]*) return 1;; esac
        [ "${#max}" -le 5 ] && [ "$max" -ge 90 ] || return 1
        # A node driven by a kernel trigger is not the plain clock read on Bergamot.
        if [ -e "$LEDS/$n/trigger" ]; then
            t=$(cat "$LEDS/$n/trigger") || return 1
            case "$t" in "[none]"*|*" [none]"*) ;; *) return 1;; esac
        fi
    done
    ORIGINAL=$(levels) || return 1
    sleep 0.001 || return 1
    boot=$(sha256sum /proc/sys/kernel/random/boot_id) || return 1
    STATE=$(printf '%s\n' "$actual" "$boot" "$ORIGINAL" "$request_sha" |
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
original=$ORIGINAL
done_ok=false
yielded=false
finish() {
    trap - 0 HUP INT TERM
    restored=false
    if [ "$yielded" != true ] && write_levels "$original" && [ "$(levels)" = "$original" ]; then
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
# A changed readback means another writer (the firmware redrawing the time) owns the nodes:
# stop and leave its state rather than an older snapshot.
current=$(levels) || exit 0
[ "$current" = "$original" ] || { yielded=true; done_ok=true; exit 0; }
started=$(now)
write_levels "$FRAME" || exit 0
# A driver that does not read back what was written cannot be supervised: restore.
[ "$(levels)" = "$FRAME" ] || exit 0
sleep "$pause" || exit 0
[ $(($(now) - started)) -le "$limit" ] || exit 0
current=$(levels) || exit 0
[ "$current" = "$FRAME" ] || yielded=true
done_ok=true
exit 0
'''


def frame(params: dict[str, Any]) -> list[int]:
    """Validated display.show parameters -> one level per `NODES` entry; pure."""
    level = params["level"]
    return ([level if segment in rt.SEGMENTS[char] else 0
             for char in params["text"].rjust(4) for segment in "ABCDEFG"]
            + [level if params["colon"] else 0] * len(COLON) + [0] * len(BLANK))


def command(request: dict[str, Any], *, source: str = SCRIPT) -> SSHInput:
    """Accept only a validated display.show for the Mini 3 clock."""
    try:
        mode, action, model = request["mode"], request["action"], request["model"]
        rt._require(rt._keys(request, {"schema", "mode", "model", "policy", "action", "params",
                                       "nonce"}, {"expected_sha256"}))
        rt._require(request["schema"] == rt.SCHEMA and rt._match(rt.HEX32, request["nonce"])
                    and action == "display.show" and model in rt.CLOCKS)
        rt._require(mode in {"inspect", "apply"})
        expected = request.get("expected_sha256", "")
        rt._require(mode == "apply" and rt._match(rt.HEX64, expected)
                    or mode == "inspect" and "expected_sha256" not in request)
        policy = rt.validate_policy(request["policy"], model)
        rt._require(rt.allows(policy, action))
        params = rt.validate_parameters(action, request["params"], policy, model)
        normalized = dict(request, mode="inspect", policy=policy, params=params)
        normalized.pop("expected_sha256", None)
        digest = hashlib.sha256(rt._encode(normalized) + source.encode()).hexdigest()
        head = "".join(f"{name}={shlex.quote(str(value))}\n" for name, value in (
            ("NODES", " ".join(NODES)), ("FRAME", ",".join(map(str, frame(params)))),
            ("PERIOD", params["duration_seconds"] * 1000)))
        args = [mode, policy["firmware_sha256"], digest, expected]
        return SSHInput("sh -s -- " + " ".join(shlex.quote(a) for a in args),
                        (head + source).encode())
    except (ValueError, TypeError, KeyError):
        raise SafetyError("Invalid or unapproved clock display request") from None
