"""Fixed Mini 1 SN3236 capture, recorded update and bounded low-brightness presets.

No supplied program, path, bus, address or filter. The host still owns approval.
Capture writes only its own trace instance; replay accepts only the fixed 25 aa update.
Pulse/presets require same-boot observed PWM/update pairs ending with an idle zero frame.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp.core import SafetyError, SSHInput

# Kept inline so no executable/script installation on the station is necessary.
SCRIPT = r'''
OWNER_UID=0
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
mode=$1; cap=$2; seconds=$3; firmware=$4; policy=$5; expected=$6
frames=${7:-}
blocked() { printf 'outcome=blocked\n'; exit 0; }
number() { case "$1" in ''|*[!0-9]*) return 1;; esac; test "${#1}" -le 10; }
uid=$(id -u)
[ "$uid" = "$OWNER_UID" ] || blocked
case "$cap" in *[!0-9a-f]*|'') blocked;; esac
[ "${#cap}" = 32 ] || blocked
for c in awk cat cut flock grep head id ls mkdir mv readlink rm rmdir sed setsid \
    sha256sum sleep wc; do
    command -v "$c" >/dev/null 2>&1 || blocked
done
DATA=/data
if [ -L "$DATA" ]; then
    case "$(readlink "$DATA")" in
        /bkupgrade/data|bkupgrade/data) DATA=/bkupgrade/data;; *) blocked;;
    esac
fi
BASE=$DATA/opt/alice-mcp
CAPTURES=$BASE/led-captures
OUT=$CAPTURES/$cap
CLAIM=$BASE/led-capture-active
FILTER='adapter_nr == 0 && addr == 60 && len <= 256'
role=launcher
trusted() {
    [ ! -L "$1" ] || return 1
    set -- $(ls -ldn "$1")
    [ "$3" = "$uid" ] || return 1
    case "$1" in d*) ;; *) return 1;; esac
    [ "$(printf %s "$1" | cut -c6)" != w ] && [ "$(printf %s "$1" | cut -c9)" != w ]
}
private() {
    trusted "$1" || return 1
    set -- $(ls -ldn "$1")
    [ "$1" = drwx------ ]
}
make_private() {
    if [ ! -e "$1" ]; then mkdir "$1" || return 1; fi
    private "$1"
}
get() {
    [ -f "$OUT/$1" ] && [ ! -L "$OUT/$1" ] || return 1
    head -c 256 "$OUT/$1"
}
put() { printf '%s\n' "$2" > "$OUT/$1"; }
phase() {
    printf '%s\n' "$1" > "$OUT/phase.$role" && mv "$OUT/phase.$role" "$OUT/phase"
}
now() { awk '{ print int($1) }' /proc/uptime; }
identity() {
    number "$1" || return 1
    [ -r "/proc/$1/stat" ] || return 1
    sed 's/.*) //' "/proc/$1/stat" | awk '$1 != "Z" && $1 != "X" { print $20 }'
}
alive() {
    p=$(get "$1") && start=$(get "$2") || return 1
    number "$p" && number "$start" && [ "$(identity "$p")" = "$start" ]
}
inode() { ls -id "$1" | awk '{ print $1 }'; }
same_instance() {
    [ -d "$INSTANCE" ] && [ ! -L "$INSTANCE" ] && [ "$(inode "$INSTANCE")" = "$(get inode)" ]
}
stop_own() {
    same_instance || return 1
    printf '0\n' > "$INSTANCE/tracing_on" || return 1
    printf '0\n' > "$INSTANCE/events/i2c/i2c_write/enable" || return 1
    [ "$(cat "$INSTANCE/tracing_on")" = 0 ] &&
        [ "$(cat "$INSTANCE/events/i2c/i2c_write/enable")" = 0 ]
}
remove_claim() {
    private "$CLAIM" && [ "$(cat "$CLAIM/id")" = "$cap" ] || return 1
    rm "$CLAIM/id" && rmdir "$CLAIM"
}
profile() {
    trusted "$DATA" || return 1
    if [ -e "$DATA/opt" ] || [ -L "$DATA/opt" ]; then trusted "$DATA/opt" || return 1; fi
    name=$(cat /sys/bus/i2c/devices/0-003c/name)
    case "$name" in sn3236|SN3236) ;; *) return 1;; esac
    [ -f /system/vendor/quasar/maind ] && [ ! -L /system/vendor/quasar/maind ] || return 1
    actual=$(sha256sum /system/vendor/quasar/maind | awk '{ print $1 }')
    [ "$actual" = "$firmware" ] || return 1
    ROOT=
    for root in /sys/kernel/tracing /sys/kernel/debug/tracing; do
        if [ -r "$root/available_events" ]; then ROOT=$root; break; fi
    done
    [ -n "$ROOT" ] && [ ! -L "$ROOT" ] && [ ! -L "$ROOT/instances" ] || return 1
    awk -v root="$ROOT" '
        $2 == root && $3 == "tracefs" { ok=1 }
        $2 == "/sys/kernel/debug" && $3 == "debugfs" && root == "/sys/kernel/debug/tracing" { ok=1 }
        END { exit !ok }
    ' /proc/mounts || return 1
    [ -d "$ROOT/instances" ] && [ -w "$ROOT/instances" ] || return 1
    grep -qx 'i2c:i2c_write' "$ROOT/available_events" || return 1
    format=$ROOT/events/i2c/i2c_write/format
    grep -Eq 'field:[[:space:]]*int[[:space:]]+adapter_nr;' "$format" || return 1
    grep -Eq 'field:[[:space:]]*(__u16|u16|unsigned short)[[:space:]]+addr;' "$format" || return 1
    grep -Eq 'field:[[:space:]]*(__u16|u16|unsigned short)[[:space:]]+len;' "$format" || return 1
    pattern='field:[[:space:]]*__data_loc[[:space:]]+(__u8|u8|unsigned char)\[\][[:space:]]+buf;'
    grep -Eq "$pattern" "$format" || return 1
    cpus=$(grep -c '^processor' /proc/cpuinfo)
    number "$cpus" && [ "$cpus" -ge 1 ] && [ "$cpus" -le 16 ] || return 1
    boot=$(sha256sum /proc/sys/kernel/random/boot_id | awk '{ print $1 }')
    fmt=$(sha256sum "$format" | awk '{ print $1 }')
    STATE=$(printf '%s\n' "$ROOT" "$actual" "$fmt" "$name" "$boot" "$cpus" "$policy" |
        sha256sum | awk '{ print $1 }')
    INSTANCE=$ROOT/instances/alice-led-$cap
}
ready() {
    [ "$(get phase)" = capturing ] || return 1
    end=$(get deadline) && current=$(now) || return 1
    number "$end" && number "$current" || return 1
    remaining=$((end-current))
    [ "$remaining" -ge 2 ] && [ "$remaining" -le 60 ] || return 1
    alive worker worker_start && alive watchdog watchdog_start && same_instance || return 1
    [ "$(cat "$INSTANCE/current_tracer")" = nop ] && [ "$(cat "$INSTANCE/tracing_on")" = 1 ] &&
    [ "$(cat "$INSTANCE/events/i2c/i2c_write/enable")" = 1 ] &&
    [ "$(cat "$INSTANCE/events/i2c/i2c_write/filter")" = "$FILTER" ]
}
report() {
    private "$BASE" && private "$CAPTURES" && private "$OUT" || blocked
    [ "$(get firmware)" = "$firmware" ] && [ "$(get policy)" = "$policy" ] || blocked
    outcome=unknown; is_ready=0; remaining=0; clean=0; trunc=0
    if [ "$(get cleanup)" = 1 ] && [ ! -e "$INSTANCE" ]; then clean=1; fi
    if ready; then outcome=capturing; is_ready=1
    else remaining=0
        if [ "$(get phase)" = complete ] && [ "$clean" = 1 ]; then outcome=complete; fi
    fi
    [ "$(get truncated)" = 1 ] && trunc=1
    printf 'outcome=%s\ncapture_id=%s\ncapture_ready=%s\n' "$outcome" "$cap" "$is_ready"
    printf 'remaining_seconds=%s\ncleanup_verified=%s\ntruncated=%s\n' \
        "$remaining" "$clean" "$trunc"
    if [ "$outcome" = complete ]; then
        [ -f "$OUT/records" ] && [ ! -L "$OUT/records" ] || return
        head -c 40000 "$OUT/records"
    fi
}
collect() {
    [ -r "$INSTANCE/trace" ] || return 1
    head -c 1048577 "$INSTANCE/trace" > "$OUT/raw" || return 1
    bytes=$(wc -c < "$OUT/raw")
    number "$bytes" || return 1
    [ "$bytes" -le 1048576 ] || put truncated 1
    awk '
        /entries-in-buffer\/entries-written:/ {
            s=$0; sub(/^.*entries-in-buffer\/entries-written: */, "", s); split(s,n,"/")
            if (n[2]+0 > n[1]+0) lost=1
        }
        /: i2c_write: i2c-0 #[0-9]+ a=0*3c f=[0-9a-f]+ l=[0-9]+ \[[0-9a-f-]*\]/ {
            s=$0; sub(/^.*: i2c_write: /,"",s)
            t=s; sub(/^.* l=/,"",t); length_value=t+0
            if (length_value > 256) next
            sub(/^.*\[/,"",s); sub(/\].*$/,"",s)
            count=0; if (s != "") count=split(s,parts,"-")
            if (count != length_value) next
            data=""; valid=1
            for (i=1;i<=count;i++) {
                if (parts[i] !~ /^[0-9a-f][0-9a-f]$/) valid=0
                data=data parts[i]
            }
            if (!valid) next
            retained[records % 64]="record=" data; records++
            if (records > 64) lost=1
        }
        END {
            first=records > 64 ? records-64 : 0
            for (i=first;i<records;i++) print retained[i % 64]
            if (lost) print "1" > trunc_file
        }
    ' trunc_file="$OUT/truncated" "$OUT/raw" > "$OUT/records" || return 1
    rm "$OUT/raw"
    put ended "$(now)"
}
cleanup() {
    trap - 0 HUP INT TERM
    if [ "$(get phase)" = complete ] && [ "$(get cleanup)" = 1 ] && [ ! -e "$INSTANCE" ]; then
        return
    fi
    # Only one of the worker, watchdog or signal handler performs cleanup.
    mkdir "$OUT/cleaning" || return
    ok=0
    if [ "$own_instance" = 1 ] && same_instance; then
        stopped=0; stop_own && stopped=1
        if [ "$normal" = 1 ] && [ "$stopped" = 1 ]; then collect || normal=0; else normal=0; fi
        if rmdir "$INSTANCE" && [ ! -e "$INSTANCE" ]; then ok=1; fi
    fi
    if [ "$own_claim" = 1 ]; then remove_claim || ok=0; fi
    put cleanup "$ok"
    if [ "$normal" = 1 ] && [ "$ok" = 1 ]; then phase complete; else phase unknown; fi
}
watch() {
    trap - 0 HUP INT TERM
    role=watchdog
    sleep "$wait_seconds" || exit 1
    same_instance || exit 0
    normal=1
    cleanup
}
replay_state() {
    private "$BASE" && private "$CAPTURES" && private "$OUT" || return 1
    [ "$(get firmware)" = "$firmware" ] && [ "$(get phase)" = complete ] &&
        [ "$(get cleanup)" = 1 ] && [ ! -e "$INSTANCE" ] || return 1
    [ ! -e "$CLAIM" ] && [ ! -L "$CLAIM" ] || return 1
    [ ! -L /sys/bus/i2c/devices/0-003c/driver ] || return 1
    [ -f "$OUT/records" ] && [ ! -L "$OUT/records" ] || return 1
    size=$(wc -c < "$OUT/records")
    number "$size" && [ "$size" -le 40000 ] || return 1
    grep -qx 'record=25aa' "$OUT/records" || return 1
    evidence=$(sha256sum "$OUT/records" | awk '{ print $1 }')
    STATE=$(printf '%s\n' "$STATE" "$cap" "$evidence" 'sn3236-25aa-once' |
        sha256sum | awk '{ print $1 }')
}
idle_state() {
    i2cset --help 2>&1 | head -c 4096 | grep -Eq '^[[:space:]]*i[[:space:]]+I2C block data' ||
        return 1
    [ "${#boot}" = 64 ] && [ "$(get boot)" = "$boot" ] || return 1
    ended=$(get ended) && number "$ended" || return 1
    age=$(($(now)-ended))
    [ "$age" -ge 0 ] && [ "$age" -le 1800 ] || return 1
    awk '
        NR % 2 == 1 {
            data=substr($0,8)
            if (length(data)!=38 || data !~ /^01[0-9a-f]+$/) bad=1
            triple=substr(data,3,6)
            for (i=1;i<6;i++) if (substr(data,3+i*6,6)!=triple) bad=1
            last=data
            if (data!="01000000000000000000000000000000000000") nonzero=1
        }
        NR % 2 == 0 { if ($0!="record=25aa") bad=1 }
        END { exit (bad || NR<4 || NR>64 || NR%2 || !nonzero ||
            last!="01000000000000000000000000000000000000") }
    ' "$OUT/records" || return 1
    STATE=$(printf '%s\n' "$STATE" "$boot" "$ended" "$seconds" 'sn3236-pwm16-observed-idle' |
        sha256sum | awk '{ print $1 }')
}
pulse_state() {
    case "$seconds" in 0|1|2) ;; *) return 1;; esac
    idle_state
}
animation_state() {
    case "$seconds" in
        blink|rainbow_swirl) frame_count=12; interval=0.5;;
        rainbow) frame_count=1; interval=3;;
        *) return 1;;
    esac
    case "$frames" in ''|*[!0-9a-f\ ]*) return 1;; esac
    set -- $frames
    [ "$#" = "$frame_count" ] || return 1
    for frame_hex do
        [ "${#frame_hex}" = 36 ] || return 1
        rest=$frame_hex
        while [ -n "$rest" ]; do
            tail=${rest#??}; byte=${rest%"$tail"}; rest=$tail
            case "$byte" in 00|10) ;; *) return 1;; esac
        done
    done
    sleep 0.001 && idle_state || return 1
    STATE=$(printf '%s\n' "$STATE" "$frames" 'sn3236-animation-v1' |
        sha256sum | awk '{ print $1 }')
}
write_pwm() { i2cset -y 0 0x3c 0x01 "$@" i >/dev/null 2>&1; }
pulse_frame() {
    kind=$1
    set --
    index=0
    while [ "$index" -lt 18 ]; do
        value=0x00
        if [ "$kind" = color ] && [ "$((index % 3))" = "$seconds" ]; then value=0x10; fi
        set -- "$@" "$value"
        index=$((index+1))
    done
    write_pwm "$@"
}
animation_frame() {
    rest=$1
    set --
    while [ -n "$rest" ]; do
        tail=${rest#??}; byte=${rest%"$tail"}; rest=$tail
        case "$byte" in 00|10) set -- "$@" "0x$byte";; *) return 1;; esac
    done
    [ "$#" = 18 ] && write_pwm "$@"
}
led_finish() {
    trap - 0 HUP INT TERM
    restored=1
    pulse_frame idle || restored=0
    i2cset -y 0 0x3c 0x25 0xaa b >/dev/null 2>&1 || restored=0
    outcome=unknown
    if [ "$done_ok" = 1 ] && [ "$restored" = 1 ]; then outcome=applied; fi
    printf 'outcome=%s\ncommand_succeeded=%s\nrestoration_command_succeeded=%s\n' \
        "$outcome" "$done_ok" "$restored"
    printf 'restoration_verified=0\n'
}
profile || blocked
case "$mode" in
replay_inspect|replay_apply|pulse_inspect|pulse_apply|animation_inspect|animation_apply)
    command -v i2cset >/dev/null 2>&1 || blocked
    replay_state || blocked
    case "$mode" in
        pulse_*) pulse_state || blocked;; animation_*) animation_state || blocked;;
    esac
    if [ "$mode" != "${mode%_inspect}" ]; then
        printf 'outcome=ready\nstate_sha256=%s\n' "$STATE"; exit 0
    fi
    LOCK=$BASE/control.lock
    [ -f "$LOCK" ] && [ ! -L "$LOCK" ] || blocked
    set -- $(ls -ln "$LOCK")
    [ "$1" = -rw------- ] && [ "$2" = 1 ] && [ "$3" = "$uid" ] || blocked
    exec 9>> "$LOCK"
    flock -n 9 || blocked
    profile && replay_state || blocked
    case "$mode" in
        pulse_*) pulse_state || blocked;; animation_*) animation_state || blocked;;
    esac
    if [ "$expected" != "$STATE" ]; then
        printf 'outcome=conflict\nstate_sha256=%s\n' "$STATE"; exit 0
    fi
    if [ "$mode" = pulse_apply ] || [ "$mode" = animation_apply ]; then
        done_ok=0
        trap led_finish 0
        trap 'exit 0' HUP INT TERM
        if [ "$mode" = pulse_apply ]; then
            pulse_frame color && i2cset -y 0 0x3c 0x25 0xaa b >/dev/null 2>&1 && sleep 1 || exit 0
        else
            for frame_hex in $frames; do
                animation_frame "$frame_hex" &&
                    i2cset -y 0 0x3c 0x25 0xaa b >/dev/null 2>&1 && sleep "$interval" || exit 0
            done
        fi
        done_ok=1
        exit 0
    elif i2cset -y 0 0x3c 0x25 0xaa b >/dev/null 2>&1; then
        printf 'outcome=applied\ncommand_succeeded=1\n'
    else
        printf 'outcome=unknown\ncommand_succeeded=0\n'
    fi
    ;;
inspect|apply)
    case "$seconds" in [5-9]|[1-5][0-9]|60) ;; *) blocked;; esac
    [ ! -e "$OUT" ] && [ ! -L "$OUT" ] && [ ! -e "$INSTANCE" ] || blocked
    [ ! -e "$CLAIM" ] && [ ! -L "$CLAIM" ] || blocked
    for directory in "$BASE" "$CAPTURES"; do
        if [ -e "$directory" ] || [ -L "$directory" ]; then private "$directory" || blocked; fi
    done
    set -- "$CAPTURES"/*
    if [ -e "$1" ] || [ -L "$1" ]; then [ "$#" -lt 16 ] || blocked; fi
    if [ "$mode" = inspect ]; then printf 'outcome=ready\nstate_sha256=%s\n' "$STATE"; exit 0; fi
    if [ "$expected" != "$STATE" ]; then
        printf 'outcome=conflict\nstate_sha256=%s\n' "$STATE"; exit 0
    fi
    if [ ! -e "$DATA/opt" ]; then mkdir "$DATA/opt" || blocked; fi
    trusted "$DATA/opt" && make_private "$BASE" && make_private "$CAPTURES" || blocked
    mkdir "$OUT" && private "$OUT" || blocked
    put firmware "$firmware"; put policy "$policy"; put cleanup 0; put truncated 0
    put boot "$boot"
    put deadline "$(($(now)+seconds))"; phase starting
    setsid sh -c "$ALICE_CAPTURE_CODE" sh worker "$cap" "$seconds" "$firmware" "$policy" "$STATE" \
        </dev/null >/dev/null 2>&1 &
    for n in 1 2 3; do ready && break; [ "$(get phase)" = unknown ] && break; sleep 1; done
    report
    ;;
worker)
    private "$BASE" && private "$CAPTURES" && private "$OUT" &&
        [ "$expected" = "$STATE" ] || blocked
    role=worker; own_instance=0; own_claim=0; normal=0
    trap cleanup 0
    trap 'normal=0; exit 1' HUP INT TERM
    LOCK=$BASE/control.lock
    if [ ! -e "$LOCK" ]; then (set -C; : > "$LOCK") || exit 1; fi
    [ -f "$LOCK" ] && [ ! -L "$LOCK" ] || exit 1
    set -- $(ls -ln "$LOCK")
    [ "$1" = -rw------- ] && [ "$2" = 1 ] && [ "$3" = "$uid" ] || exit 1
    exec 9>> "$LOCK"
    flock -n 9 || exit 1
    mkdir "$CLAIM" || exit 1
    own_claim=1; printf '%s\n' "$cap" > "$CLAIM/id"
    mkdir "$INSTANCE" || exit 1
    own_instance=1; put inode "$(inode "$INSTANCE")"
    put worker "$$"; put worker_start "$(identity "$$")"
    printf '0\n' > "$INSTANCE/tracing_on" || exit 1
    printf '0\n' > "$INSTANCE/events/enable" || exit 1
    [ "$(cat "$INSTANCE/current_tracer")" = nop ] || exit 1
    printf '16\n' > "$INSTANCE/buffer_size_kb" || exit 1
    [ "$(cat "$INSTANCE/buffer_size_kb")" = 16 ] || exit 1
    printf '%s\n' "$FILTER" > "$INSTANCE/events/i2c/i2c_write/filter" || exit 1
    [ "$(cat "$INSTANCE/events/i2c/i2c_write/filter")" = "$FILTER" ] || exit 1
    wait_seconds=$(($(get deadline)-$(now)))
    [ "$wait_seconds" -ge 1 ] && [ "$wait_seconds" -le 60 ] || exit 1
    (watch) &
    watcher=$!
    put watchdog "$watcher"; put watchdog_start "$(identity "$watcher")"
    printf '1\n' > "$INSTANCE/events/i2c/i2c_write/enable" || exit 1
    printf '1\n' > "$INSTANCE/tracing_on" || exit 1
    phase capturing
    if wait "$watcher"; then normal=1; fi
    ;;
capture_status) report;;
*) blocked;;
esac
'''


def animation_frames(mode: str) -> tuple[int, tuple[bytes, ...]]:
    """Fixed logical-channel presets, not a calibrated RGB/physical-zone mapping."""
    palette = (bytes((16, 0, 0)), bytes((16, 16, 0)), bytes((0, 16, 0)),
               bytes((0, 16, 16)), bytes((0, 0, 16)), bytes((16, 0, 16)))
    if mode == "blink":
        return 500, tuple(frame for color in palette for frame in (color * 6, bytes(18)))
    if mode == "rainbow":
        return 3000, (b"".join(palette),)
    if mode == "rainbow_swirl":
        return 500, tuple(b"".join(palette[(zone - step) % 6] for zone in range(6))
                          for step in range(12))
    raise ValueError("invalid_animation_mode")


def command(request: dict[str, Any], *, source: str = SCRIPT) -> SSHInput:
    """Build a fixed helper invocation. `source` exists only for offline tracefs tests."""
    try:
        action = request["action"]
        if (request["schema"] != rt.SCHEMA or request["model"] != "station-mini"
            or action not in {"led.capture", "led.replay", "led.pulse", "led.animate"}
            or not rt._match(rt.HEX32, request["nonce"])):
            raise ValueError
        policy = rt.validate_policy(request["policy"], request["model"])
        if action not in policy.get("allowed_actions", []):
            raise ValueError
        mode = request["mode"]
        expected = request.get("expected_sha256", "")
        if mode == "capture_status":
            if (action != "led.capture" or request["params"] != {"capture_id": request["nonce"]}
                or expected):
                raise ValueError
            duration = 0
        elif mode in {"inspect", "apply"}:
            params = rt.validate_parameters(action, request["params"], policy, request["model"])
            duration = params.get("duration_seconds", params.get("channel", params.get("mode", 0)))
            if (mode == "apply" and not rt._match(rt.HEX64, expected)
                or mode == "inspect" and expected):
                raise ValueError
        else:
            raise ValueError
        if set(request) != {"schema", "mode", "model", "policy", "action", "params", "nonce"} | (
            {"expected_sha256"} if mode == "apply" else set()
        ):
            raise ValueError
        policy_sha = hashlib.sha256(rt._encode(policy)).hexdigest()
        remote_mode = {"led.replay": "replay_", "led.pulse": "pulse_",
                       "led.animate": "animation_"}.get(action, "") + mode
        record_id = (request["params"]["capture_id"] if action != "led.capture"
                     else request["nonce"])
        args = [remote_mode, record_id, str(duration), policy["firmware_sha256"],
                 policy_sha, expected]
        if action == "led.animate":
            _, frames = animation_frames(request["params"]["mode"])
            args.append(" ".join(frame.hex() for frame in frames))
        body = ("ALICE_CAPTURE_CODE=" + shlex.quote(source)
                + "; export ALICE_CAPTURE_CODE\n" + source)
        return SSHInput("sh -s -- " + " ".join(shlex.quote(a) for a in args), body.encode())
    except (ValueError, TypeError, KeyError):
        raise SafetyError("Invalid or unapproved LED capture request") from None


def decode(text: str) -> dict[str, Any]:
    """Only typed LED records cross SSH; never return trace lines or arbitrary labels."""
    try:
        if len(text) > 65536:
            raise ValueError
        data: dict[str, Any] = {}
        records: list[dict[str, Any]] = []
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if not sep:
                raise ValueError
            if key == "record":
                if len(records) >= 64 or not re.fullmatch(r"(?:[a-f0-9]{2}){0,256}", value):
                    raise ValueError
                records.append({"bus": 0, "address": 60, "data_hex": value})
                continue
            if key in data:
                raise ValueError
            if key in {"capture_ready", "cleanup_verified", "truncated", "command_succeeded",
                       "restoration_verified", "restoration_command_succeeded"}:
                if value not in {"0", "1"}:
                    raise ValueError
                data[key] = value == "1"
            elif key == "remaining_seconds":
                if not re.fullmatch(r"[0-9]{1,2}", value):
                    raise ValueError
                data[key] = int(value)
            elif key == "outcome" and value in {"ready", "applied", "blocked", "conflict",
                                                "unknown", "capturing", "complete"}:
                data[key] = value
            elif key in {"state_sha256", "capture_id"} and rt._match(
                rt.HEX64 if key == "state_sha256" else rt.HEX32, value,
            ):
                data[key] = value
            else:
                raise ValueError
        if "capture_id" in data:
            data["records"] = records
        elif records:
            raise ValueError
        return data
    except (ValueError, TypeError):
        raise SafetyError("LED capture returned incompatible structured output") from None
