"""Fixed operator-only transaction; never accepts paths, commands or credentials."""

from alice_mcp.agent_provision import WAIT_AGENT

SCRIPT = r'''
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
OWNER_UID=0
__WAIT_AGENT__
mode=$1; nonce=$2; firmware=$3; package=$4; expected=$5; layout=$6; needed=$7; package_size=$8
shift 8
old_agent=$1; old_tts=$2; old_asr=$3; old_config=$4
old_cert=$5; old_key=$6; old_credential=$7; old_start=$8
old_pcm=$9
shift 9
new_agent=$1; new_pcm=$2; new_config=$3; new_start=$4
blocked() { printf '{"outcome":"blocked","error_code":"upgrade_precondition"}\n'; exit 0; }
case "$mode:$layout" in inspect:direct|inspect:bkupgrade|apply:direct|apply:bkupgrade) ;;
    *) blocked;; esac
[ "$(id -u)" = "$OWNER_UID" ] && [ "${#nonce}" = 32 ] || blocked
case "$nonce" in *[!0-9a-f]*) blocked;; esac
for value in "$firmware" "$package" "$old_agent" "$old_tts" "$old_asr" "$old_config" \
    "$old_cert" "$old_key" "$old_credential" "$old_start" "$new_agent" "$new_pcm" \
    "$new_config" "$new_start" "$old_pcm"; do
    [ "${#value}" = 64 ] || blocked
    case "$value" in *[!0-9a-f]*) blocked;; esac
done
case "$needed" in ''|*[!0-9]*) blocked;; esac
case "$package_size" in ''|*[!0-9]*) blocked;; esac
[ "$package_size" -gt 0 ] && [ "$package_size" -le 20971520 ] &&
    [ "$old_pcm" = "$new_pcm" ] || blocked
for command in awk sha256sum ls cut readlink pidof cp mv tar setsid env df cat sed sort dd; do
    command -v "$command" >/dev/null || blocked
done
trusted() {
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    set -- $(ls -ldn "$1")
    { [ "$3" = 0 ] || [ "$3" = "$OWNER_UID" ]; } &&
        [ "$(printf %s "$1" | cut -c6)" != w ] &&
        [ "$(printf %s "$1" | cut -c9)" != w ]
}
check() {
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    path=$1; digest=$2; secret=${3:-0}
    set -- $(ls -ldn "$path")
    [ "$3" = "$OWNER_UID" ] && [ "$(printf %s "$1" | cut -c6)" != w ] &&
        [ "$(printf %s "$1" | cut -c9)" != w ] || return 1
    if [ "$secret" = 1 ]; then
        [ "$(printf %s "$1" | cut -c5-10)" = ------ ] || return 1
    fi
    [ "$(sha256sum "$path" | awk '{print $1}')" = "$digest" ]
}
DATA=/data
if [ "$layout" = bkupgrade ]; then
    [ -L "$DATA" ] || blocked
    case "$(readlink "$DATA")" in /bkupgrade/data|bkupgrade/data) DATA=/bkupgrade/data;;
        *) blocked;; esac
    trusted /bkupgrade || blocked
else [ ! -L "$DATA" ] || blocked; fi
ROOT=$DATA/opt/alice-mcp-agent
INIT=/etc/init.d/S99zz-alice-mcp
trusted "$DATA" && trusted "$DATA/opt" && trusted "$ROOT" && trusted "${INIT%/*}" || blocked
hook_sha=$(printf '#!/bin/sh\nexec %s/start "$@"\n' "$ROOT" | sha256sum | awk '{print $1}')
check "$INIT" "$hook_sha" || blocked
check "$ROOT/pcm-player" "$old_pcm" || blocked
[ ! -e "$DATA/opt/alice-mcp-asr-active" ] &&
    [ ! -L "$DATA/opt/alice-mcp-asr-active" ] &&
    [ ! -e "$DATA/opt/alice-mcp-vqe-audit-active" ] &&
    [ ! -L "$DATA/opt/alice-mcp-vqe-audit-active" ] || blocked
LOCK=$ROOT/.agent-lock
STAGE=$ROOT/.upgrade-$nonce
[ ! -e "$LOCK" ] && [ ! -L "$LOCK" ] && [ ! -e "$STAGE" ] && [ ! -L "$STAGE" ] || blocked
starttime() { sed 's/.*) //' "/proc/$1/stat" | awk '{print $20}'; }
quasar_stamp() {
    pids=$(pidof maind) || return 1
    [ -n "$pids" ] || return 1
    identities=$(
        for qpid in $pids; do
            stamp=$(starttime "$qpid")
            case "$stamp" in ''|*[!0-9]*) exit 1;; esac
            printf '%s:%s\n' "$qpid" "$stamp"
        done
    ) || return 1
    printf '%s\n' "$identities" | sort -n | sha256sum | awk '{print $1}'
}
preserved() {
    trusted "$DATA" && trusted "$DATA/opt" && trusted "$ROOT" &&
        trusted "${INIT%/*}" && check "$INIT" "$hook_sha" || return 1
    check /system/vendor/quasar/maind "$firmware" &&
        check "$ROOT/tts-once" "$old_tts" && check "$ROOT/asr-once" "$old_asr" &&
        check "$ROOT/certificate.pem" "$old_cert" && check "$ROOT/private-key.pem" "$old_key" 1 &&
        check "$ROOT/credential" "$old_credential" 1 && check "$ROOT/pcm-player" "$old_pcm"
}
baseline() {
    preserved && check "$ROOT/agent" "$old_agent" && check "$ROOT/config.json" "$old_config" 1 &&
        check "$ROOT/start" "$old_start" || return 1
    [ -f "$ROOT/agent.pid" ] && [ ! -L "$ROOT/agent.pid" ] || return 1
    check "$ROOT/agent.pid" "$(sha256sum "$ROOT/agent.pid" | awk '{print $1}')" 1 || return 1
    read -r pid < "$ROOT/agent.pid"
    case "$pid" in ''|*[!0-9]*) return 1;; esac
    [ "$pid" -gt 1 ] && [ "$(readlink "/proc/$pid/exe")" = "$ROOT/agent" ] || return 1
    old_stamp=$(starttime "$pid")
    case "$old_stamp" in ''|*[!0-9]*) return 1;; esac
    qstamp=$(quasar_stamp) || return 1
    boot=$(sha256sum /proc/sys/kernel/random/boot_id | awk '{print $1}')
    state=$(printf '%s\n' "$boot" "$qstamp" "$pid:$old_stamp" "$nonce" "$package" \
        "$old_agent" "$old_tts" "$old_asr" "$old_config" "$old_cert" "$old_key" \
        "$old_credential" "$old_start" "$old_pcm" "$hook_sha" | sha256sum | awk '{print $1}')
}
baseline || blocked
initial_qstamp=$qstamp
free=$(df -Pk "$DATA" | awk 'NR==2 {print $4}')
case "$free" in ''|*[!0-9]*) blocked;; esac
[ "$free" -ge "$needed" ] || blocked
if [ "$mode" = inspect ]; then
    printf '{"outcome":"ready","state_sha256":"%s"}\n' "$state"; exit 0
fi
[ "$expected" = "$state" ] || blocked
mkdir "$LOCK" || blocked
trusted "$LOCK" || blocked
owner=$LOCK/owner
(set -C; printf '%s\n' "$$" > "$owner") || blocked
owned() { [ -f "$owner" ] && [ ! -L "$owner" ] && [ "$(cat "$owner")" = "$$" ]; }
stage_owned=0; stopped=0; installed=0; child=; child_stamp=; frozen=
children_absent() {
    n=0
    for status in /proc/[0-9]*/status; do
        n=$((n+1)); [ "$n" -le 4096 ] || return 1
        # A disappearing unrelated process is harmless; a visible child blocks stopping.
        if awk -v pid="$1" '$1=="PPid:" && $2==pid {found=1} END {exit !found}' "$status"; then
            return 1
        fi
    done
    return 0
}
stop_agent() {
    target=$1; target_stamp=$2; target_hash=$3
    [ "$(readlink "/proc/$target/exe")" = "$ROOT/agent" ] &&
        [ "$(starttime "$target")" = "$target_stamp" ] &&
        check "$ROOT/agent" "$target_hash" || return 1
    # Freeze only our exact agent before inspecting children, never Quasar. Once no
    # child exists, KILL avoids a resume/new-request race in the older resident.
    # Arm resume before STOP, including a handled signal at the command boundary.
    frozen=$target
    kill -STOP "$target" || return 1
    state_ready=0
    for attempt in 1 2 3; do
        if awk '$1=="State:" && $2=="T" {found=1} END {exit !found}' "/proc/$target/status"; then
            state_ready=1; break
        fi
        sleep 1
    done
    [ "$state_ready" = 1 ] || return 1
    observed_stamp=$(starttime "$target")
    case "$observed_stamp" in ''|*[!0-9]*) return 1;; esac
    # A reused PID may have received STOP. Cleanup can resume a stable stopped
    # identity, but KILL requires both original identity checks to match.
    [ "$observed_stamp" = "$target_stamp" ] &&
        [ "$(readlink "/proc/$target/exe")" = "$ROOT/agent" ] &&
        check "$ROOT/agent" "$target_hash" && children_absent "$target" && owned || return 1
    # Arm recovery before the irreversible signal, including a signal interrupting
    # this helper immediately after KILL and before the PID-file move.
    if [ "$target:$target_stamp" = "$pid:$old_stamp" ]; then stopped=1; fi
    kill -KILL "$target" || return 1
    frozen=
    for attempt in 1 2 3; do
        [ ! -e "/proc/$target/exe" ] && return 0
        sleep 1
    done
    return 1
}
wait_departure() {
    for attempt in 1 2 3; do
        [ ! -e "/proc/$1/exe" ] && return 0
        [ "$(starttime "$1")" = "$2" ] &&
            [ "$(readlink "/proc/$1/exe")" = "$ROOT/agent" ] || return 1
        sleep 1
    done
    [ ! -e "/proc/$1/exe" ]
}
resume_frozen() {
    [ -n "$frozen" ] || return 0
    [ -e "/proc/$frozen/exe" ] || return 0
    observed=$(starttime "$frozen")
    case "$observed" in ''|*[!0-9]*) return 1;; esac
    # CONT also cancels a pending STOP that has not reached observable state T
    # (for example in uninterruptible sleep). Do not wait for that state here.
    [ "$(starttime "$frozen")" = "$observed" ] && kill -CONT "$frozen"
}
listener_owned() {
    inodes=$(awk '$2 ~ /:480B$/ && $4=="0A" {print $10}' /proc/net/tcp /proc/net/tcp6)
    [ -n "$inodes" ] || return 1
    for fd in /proc/$1/fd/*; do
        link=$(readlink "$fd")
        for inode in $inodes; do [ "$link" != "socket:[$inode]" ] || return 0; done
    done
    return 1
}
launch() {
    env -i PATH=/usr/bin:/bin LANG=C LC_ALL=C HOME=/nonexistent \
        setsid "$ROOT/agent" -config "$ROOT/config.json" </dev/null >/dev/null 2>&1 &
    child=$!
    child_stamp=$(starttime "$child")
    case "$child_stamp" in ''|*[!0-9]*) return 1;; esac
    (set -C; printf '%s\n' "$child" > "$ROOT/agent.pid") && chmod 600 "$ROOT/agent.pid" || return 1
    wait_agent "$child" "$ROOT/agent" && [ "$(starttime "$child")" = "$child_stamp" ] &&
        listener_owned "$child"
}
rollback() {
    owned && [ "$stopped" = 1 ] || return 1
    # SIGKILL returning does not mean /proc and listening FDs disappeared yet.
    # Reconcile only the original identity; never signal a reused PID here.
    wait_departure "$pid" "$old_stamp" || return 1
    if [ -n "$child" ] && [ -e "/proc/$child/exe" ]; then
        stop_agent "$child" "$child_stamp" "$new_agent" || return 1
    fi
    if [ -f "$ROOT/agent.pid" ] && [ ! -L "$ROOT/agent.pid" ]; then
        read -r recorded < "$ROOT/agent.pid"
        if [ -n "$child" ] && [ "$recorded" = "$child" ]; then :
        elif [ "$recorded" = "$pid" ] && [ ! -e "/proc/$pid/exe" ]; then :
        else return 1; fi
        rm "$ROOT/agent.pid" || return 1
    fi
    for name in agent config.json start; do
        case "$name" in agent) before=$old_agent; after=$new_agent;;
            config.json) before=$old_config; after=$new_config;;
            start) before=$old_start; after=$new_start;; esac
        if check "$ROOT/$name" "$before"; then :
        elif check "$ROOT/$name" "$after" && check "$STAGE/old-$name" "$before"; then
            cp "$STAGE/old-$name" "$STAGE/restore-$name" &&
                check "$STAGE/restore-$name" "$before" &&
                chmod 600 "$STAGE/restore-$name" || return 1
            [ "$name" = config.json ] || chmod 500 "$STAGE/restore-$name" || return 1
            mv "$STAGE/restore-$name" "$ROOT/$name" || return 1
        else return 1; fi
    done
    preserved && check "$ROOT/agent" "$old_agent" &&
        check "$ROOT/config.json" "$old_config" 1 && check "$ROOT/start" "$old_start" &&
        launch || return 1
    child=
    [ "$(quasar_stamp)" = "$initial_qstamp" ]
}
cleanup() {
    trap - 0 HUP INT TERM
    clean=true
    if [ "$installed" = 0 ] && [ "$stopped" = 1 ]; then rollback || clean=false; fi
    resume_frozen || clean=false
    if [ "$stage_owned" = 1 ] && owned && [ -f "$STAGE/owner" ] &&
        [ ! -L "$STAGE/owner" ] && [ "$(cat "$STAGE/owner")" = "$$" ]; then
        for name in agent pcm-player config.json start; do
            case "$name" in agent) digest=$new_agent;; pcm-player) digest=$new_pcm;;
                config.json) digest=$new_config;; start) digest=$new_start;; esac
            if [ -e "$STAGE/$name" ] || [ -L "$STAGE/$name" ]; then
                check "$STAGE/$name" "$digest" && rm "$STAGE/$name" || clean=false
            fi
        done
        if [ -f "$STAGE/.package.tar" ] && [ ! -L "$STAGE/.package.tar" ]; then
            rm "$STAGE/.package.tar" || clean=false
        fi
    fi
    if owned; then rm "$owner" && rmdir "$LOCK" || clean=false; else clean=false; fi
    unchanged=false; [ "$(quasar_stamp)" != "$initial_qstamp" ] || unchanged=true
    if [ "$installed" = 1 ] && [ "$clean:$unchanged" = true:true ]; then
        printf '{"outcome":"installed","quasar_changed":false,"autostart_registered":true,'
        printf '"package_verified":true,"staging_cleaned":true,"do_not_retry":true}\n'
    else
        changed=true; [ "$unchanged" != true ] || changed=false
        printf '{"outcome":"unknown","quasar_changed":%s,"staging_cleaned":%s,' "$changed" "$clean"
        printf '"do_not_retry":true}\n'
    fi
}
trap cleanup 0
trap 'exit 0' HUP INT TERM
mkdir "$STAGE" && trusted "$STAGE" && chmod 700 "$STAGE" || exit 0
(set -C; printf '%s\n' "$$" > "$STAGE/owner") || exit 0
stage_owned=1
# Exact reads leave this same stdin open for the host's TLS acknowledgement.
(set -C; dd bs=1 count="$package_size" > "$STAGE/.package.tar") || exit 0
check "$STAGE/.package.tar" "$package" || exit 0
entries=$(tar tf "$STAGE/.package.tar") || exit 0
[ "$entries" = "$(printf 'agent\npcm-player\nconfig.json\nstart')" ] || exit 0
tar xf "$STAGE/.package.tar" -C "$STAGE" || exit 0
check "$STAGE/agent" "$new_agent" && check "$STAGE/pcm-player" "$new_pcm" &&
    check "$STAGE/config.json" "$new_config" && check "$STAGE/start" "$new_start" || exit 0
chmod 500 "$STAGE/agent" "$STAGE/pcm-player" "$STAGE/start" &&
    chmod 600 "$STAGE/config.json" || exit 0
rm "$STAGE/.package.tar" || exit 0
baseline && [ "$state" = "$expected" ] && owned || exit 0
for name in agent config.json start; do cp "$ROOT/$name" "$STAGE/old-$name" || exit 0; done
chmod 500 "$STAGE/old-agent" "$STAGE/old-start" && chmod 600 "$STAGE/old-config.json" || exit 0
check "$STAGE/old-agent" "$old_agent" && check "$STAGE/old-config.json" "$old_config" &&
    check "$STAGE/old-start" "$old_start" && owned || exit 0
stop_agent "$pid" "$old_stamp" "$old_agent" || exit 0
stopped=1
read -r recorded < "$ROOT/agent.pid"
[ "$recorded" = "$pid" ] && mv "$ROOT/agent.pid" "$STAGE/old-agent.pid" || exit 0
for name in agent pcm-player start; do
    owned && mv "$STAGE/$name" "$ROOT/$name" || exit 0
done
preserved && check "$ROOT/agent" "$new_agent" && check "$ROOT/pcm-player" "$new_pcm" &&
    check "$ROOT/config.json" "$old_config" 1 && check "$ROOT/start" "$new_start" &&
    launch && preserved && [ "$(quasar_stamp)" = "$initial_qstamp" ] || exit 0
printf '{"outcome":"awaiting_tls"}\n'
IFS= read -r tls_ack && [ "$tls_ack" = TLS-OK ] || exit 0
owned && preserved && check "$ROOT/config.json" "$old_config" 1 &&
    check "$ROOT/agent" "$new_agent" && [ "$(starttime "$child")" = "$child_stamp" ] &&
    listener_owned "$child" && children_absent "$child" &&
    [ "$(quasar_stamp)" = "$initial_qstamp" ] || exit 0
mv "$STAGE/config.json" "$ROOT/config.json" && check "$ROOT/config.json" "$new_config" 1 || exit 0
installed=1
child=
exit 0
'''.replace("__WAIT_AGENT__", WAIT_AGENT)
