"""Read-only facts for capturing audio alongside Quasar (docs/VOICE_CAPTURE.md).

Reads procfs/sysfs metadata, ALSA/Quasar configuration text and descriptor link
targets only. It never opens a PCM/control node, captures audio, loads a library,
attaches to a process, edits files or interrupts Quasar. Only typed allowlisted
values leave the parser; configuration secrets and raw text are never returned.

Owner-run over explicit pinned key-only SSH (also for native-configured stations):
    ALICE_MCP_INVENTORY=/PRIVATE/inventory.json python -m alice_mcp.capture_probe --station ALIAS
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

COMMAND = r'''
LC_ALL=C; export LC_ALL
exec 2>/dev/null
Q=/system/vendor/quasar
cfg=$Q/quasar.cfg
pids=$(pidof maind)
count=0; for p in $pids; do count=$((count+1)); done
echo "maind_processes=$count"
if pidof quasar_launcher2 >/dev/null; then echo launcher_running=1; else echo launcher_running=0; fi
if [ -r /sys/fs/selinux/enforce ]; then
    echo "selinux_enforce=$(head -c 1 /sys/fs/selinux/enforce)"
else echo selinux_enforce=absent; fi
cards=0
for c in /proc/asound/card[0-9]*; do [ -d "$c" ] && cards=$((cards+1)); done
echo "sound_cards=$cards"
if [ -d /sys/module/snd_aloop ]; then echo snd_aloop_loaded=1; else echo snd_aloop_loaded=0; fi
# "pid target" for sound-node and socket descriptors; ls only reads link targets.
fds=$(for p in /proc/[0-9]*; do
    ls -l "$p/fd" | awk -v pid="${p##*/}" \
        '$(NF-1) == "->" && $NF ~ /^(\/dev\/snd\/|socket:)/ {print pid, $NF}'
done)
roles=$(for p in $pids; do
    printf '%s ' "$p"
    tr '\000' '\n' < "/proc/$p/cmdline" | awk 'NR == 2 {r = $0} END {
        sub(/^--/, "", r); if (r !~ /^[a-z_]+$/ || length(r) > 24) r = "unknown"; print r}'
done)
i=0
for d in /proc/asound/card[0-9]*/pcm[0-9]*c; do
    [ -d "$d" ] && [ "$i" -lt 8 ] || continue
    card=${d%/pcm*}; card=${card##*card}
    dev=${d##*/pcm}; dev=${dev%c}
    node="/dev/snd/pcmC${card}D${dev}c"
    {
        echo "C $card $dev"
        head -c 4096 "$d/info" | sed 's/^/I /'
        head -c 4096 "$d/sub0/status" | sed 's/^/S /'
        head -c 4096 "$d/sub0/hw_params" | sed 's/^/H /'
        printf '%s\n' "$fds" | awk -v n="$node" '$2 == n {print "F", $1}'
        for p in $pids; do echo "Q $p"; done
        printf '%s\n' "$roles" | sed 's/^/R /'
    } | awk -v i="$i" '
    function num(v) { return (v ~ /^[0-9]+$/ && length(v) <= 9) ? v : "-" }
    $1 == "C" { card = $2; dev = $3 }
    $1 == "I" && $2 == "id:" {
        id = $0; sub(/^I id:[ \t]*/, "", id); gsub(/ /, "_", id)
        if (id !~ /^[A-Za-z0-9_.()-]+$/ || length(id) > 32) id = "unknown"
    }
    $1 == "I" && $2 == "subdevices_count:" { subs = $3 }
    $1 == "I" && $2 == "subdevices_avail:" { avail = $3 }
    $1 == "S" && $2 == "closed" { state = "closed" }
    $1 == "S" && $2 == "state:" { state = tolower($3) }
    $1 == "H" && $2 == "format:" { fmt = $3 }
    $1 == "H" && $2 == "channels:" { ch = $3 }
    $1 == "H" && $2 == "rate:" { rate = $3 }
    $1 == "H" && $2 == "period_size:" { period = $3 }
    $1 == "H" && $2 == "buffer_size:" { buffer = $3 }
    $1 == "F" && !($2 in held) { held[$2] = 1; holders++ }
    $1 == "Q" { quasar[$2] = 1 }
    $1 == "R" { role[$2] = $3 }
    END {
        for (p in held) if (p in quasar) {
            qh++; r = (p in role) ? role[p] : "unknown"
            roles = (roles == "" ? r : roles "+" r)
        }
        if (id == "") id = "unknown"
        ok = "^(closed|open|setup|prepared|running|xrun|draining|paused|suspended|disconnected)$"
        if (state !~ ok) state = "unknown"
        if (fmt !~ /^[A-Z0-9_]+$/ || length(fmt) > 16) fmt = "-"
        printf "capture_%d_card=%s\ncapture_%d_device=%s\n", i, num(card), i, num(dev)
        printf "capture_%d_id=%s\ncapture_%d_state=%s\n", i, id, i, state
        printf "capture_%d_subdevices=%s\n", i, num(subs)
        printf "capture_%d_subdevices_available=%s\n", i, num(avail)
        printf "capture_%d_format=%s\ncapture_%d_channels=%s\n", i, fmt, i, num(ch)
        printf "capture_%d_rate=%s\ncapture_%d_period_frames=%s\n", i, num(rate), i, num(period)
        printf "capture_%d_buffer_frames=%s\ncapture_%d_holders=%d\n", i, num(buffer), i, holders
        printf "capture_%d_quasar_holders=%d\n", i, qh
        printf "capture_%d_quasar_roles=%s\n", i, (roles == "" ? "-" : roles)
    }'
    i=$((i+1))
done
echo "capture_count=$i"
{
    printf '%s\n' "$fds" |
        awk '$2 ~ /^socket:\[[0-9]+\]$/ {s = $2; gsub(/[^0-9]/, "", s); print "S", s, $1}'
    for p in $pids; do echo "Q $p"; done
    printf '%s\n' "$roles" | sed 's/^/R /'
    cat /proc/net/tcp /proc/net/tcp6 | sed 's/^/T /'
} | awk '
function hex(h,   v, k, c) {
    v = 0; h = toupper(h)
    for (k = 1; k <= length(h); k++) {
        c = index("0123456789ABCDEF", substr(h, k, 1)); if (!c) return -1; v = v * 16 + c - 1
    }
    return v
}
$1 == "S" { owner[$2] = $3 }
$1 == "Q" { quasar[$2] = 1 }
$1 == "R" { role[$2] = $3 }
$1 == "T" && $5 == "0A" && n < 32 {
    split($3, a, ":"); inode = $11
    if (!(inode in owner) || !(owner[inode] in quasar)) next
    port = hex(a[2]); if (port < 1 || port > 65535) next
    if (a[1] ~ /^0+$/) scope = "any"
    else if (a[1] == "0100007F" || a[1] == "00000000000000000000000001000000") scope = "loopback"
    else scope = "other"
    p = owner[inode]; r = (p in role) ? role[p] : "unknown"
    printf "listener_%d_role=%s\nlistener_%d_port=%d\n", n, r, n, port
    printf "listener_%d_scope=%s\n", n, scope
    n++
}
END { printf "listener_count=%d\n", n }'
readable=0; override=0
for p in $pids; do
    [ -r "/proc/$p/environ" ] || continue
    readable=1
    if tr '\000' '\n' < "/proc/$p/environ" | grep -q '^ALSA_CONFIG_PATH='; then override=1; fi
done
if [ "$readable" = 1 ]; then echo "maind_alsa_config_override=$override"
else echo maind_alsa_config_override=-1; fi
names=-1
if [ -r "$Q/maind" ] && [ "$(( $(wc -c < "$Q/maind") ))" -le 100663296 ]; then
    # BusyBox grep stops at NUL bytes, so split the binary into C strings first.
    names=$(tr '\000' '\n' < "$Q/maind" |
        grep -F -e quasar.proto.IOChannelRequest -e quasar.proto.IOAudioChannelsData |
        awk 'index($0, "IOChannelRequest") {a = 1} index($0, "IOAudioChannelsData") {b = 1}
            END {print (a && b) ? 1 : 0}')
fi
echo "maind_io_channel_type_names=$names"
keys=
conf=/etc/asound.conf
if [ -r "$conf" ]; then
    echo alsa_conf_present=1
    echo "alsa_conf_sha256=$(sha256sum "$conf" | cut -d ' ' -f 1)"
    keys=$(head -c 262144 "$conf" | awk '/ipc_key[ \t]+[0-9]+/ {
        k = $0; sub(/.*ipc_key[ \t]+/, "", k); sub(/[^0-9].*/, "", k); print k}' | head -n 8)
    printf 'alsa_ipc_key_count=%d\n' "$(printf '%s\n' "$keys" | grep -c '^[0-9]')"
    head -c 262144 "$conf" | awk '
    BEGIN { n = 0; cur = -1 }
    /^[ \t]*(ctl|pcm_slave|defaults)\./ { cur = -1 }
    match($0, /^[ \t]*pcm\.!?[A-Za-z0-9_]+/) {
        name = substr($0, RSTART, RLENGTH); sub(/^[ \t]*pcm\.!?/, "", name)
        if (n < 16 && length(name) <= 32) { names[n] = name; types[n] = "-"; cur = n; n++ }
        else cur = -1
    }
    cur >= 0 && types[cur] == "-" && match($0, /(^|[ \t{])type[ \t]+[A-Za-z0-9_]+/) {
        t = substr($0, RSTART, RLENGTH); sub(/.*type[ \t]+/, "", t)
        if (length(t) <= 16) types[cur] = t
    }
    END { for (k = 0; k < n; k++) print names[k], types[k] }' | {
        n=0
        while read -r name type; do
            used=0
            if [ -r "$cfg" ] && grep -q -F "\"$name\"" "$cfg"; then used=1; fi
            printf 'alsa_pcm_%d_name=%s\nalsa_pcm_%d_type=%s\n' "$n" "$name" "$n" "$type"
            printf 'alsa_pcm_%d_in_quasar_cfg=%d\n' "$n" "$used"
            n=$((n+1))
        done
        echo "alsa_pcm_count=$n"
    }
else echo alsa_conf_present=0; fi
if [ -r /proc/sysvipc/shm ]; then
    echo sysv_shm_readable=1
    {
        for k in $keys; do echo "K $k"; done
        for p in $pids; do echo "Q $p"; done
        sed 's/^/M /' /proc/sysvipc/shm
    } | awk '$1 == "K" { key[$2] = 1 } $1 == "Q" { q[$2] = 1 }
    $1 == "M" && $2 ~ /^-?[0-9]+$/ {
        seg++; if ($8 + 0 > 0) att++; if ($2 in key) hit++; if ($6 in q) qc++
    }
    END {
        printf "sysv_shm_segments=%d\nsysv_shm_attached=%d\n", seg, att
        printf "sysv_shm_alsa_key_matches=%d\nsysv_shm_created_by_quasar=%d\n", hit, qc
    }'
else echo sysv_shm_readable=0; fi
if [ -r "$cfg" ]; then
    echo quasar_cfg_present=1
    # Heuristic structural token scan: a brace inside a string value can confuse it.
    head -c 1048576 "$cfg" | awk '
    function record(k, v,   o) {
        o = sect[depth]
        if (o ~ /^(aliced|audiod|audiosender|glagold)$/ && !((o "|" k) in val) && v ~ /^[0-9]+$/ &&
            length(v) <= 9) val[o "|" k] = v
    }
    function token(t) {
        if (t == "{" || t == "[") {
            depth++; sect[depth] = pend; if (t == "{") seen[pend] = 1; pend = ""; return
        }
        if (t == "}" || t == "]") { if (depth > 0) depth--; pend = ""; return }
        if (t == ":") { pend = last; last = ""; return }
        if (t == ",") { pend = ""; last = ""; return }
        if (pend != "") { record(pend, t); pend = ""; return }
        if (t ~ /^".*"$/) last = substr(t, 2, length(t) - 2)
    }
    function show(o, k,   v) { v = ((o "|" k) in val) ? val[o "|" k] : "-"; return v }
    {
        s = s $0 "\n"; line = $0; gsub(/[][{},:]/, "\n&\n", line)
        m = split(line, parts, "\n")
        for (j = 1; j <= m; j++) {
            t = parts[j]; gsub(/^[ \t\r]+|[ \t\r]+$/, "", t); if (t != "") token(t)
        }
    }
    END {
        split("aliced audiod audiosender glagold", svc, " ")
        for (j = 1; j <= 4; j++) {
            printf "cfg_%s_section=%d\n", svc[j], (svc[j] in seen)
            printf "cfg_%s_port=%s\n", svc[j], show(svc[j], "port")
        }
        printf "cfg_audiod_in_rate=%s\n", show("audiod", "inRate")
        printf "cfg_audiod_out_rate=%s\n", show("audiod", "outRate")
        printf "cfg_audiod_mic_channels=%s\n", show("audiod", "micChannels")
        printf "cfg_audiod_spk_channels=%s\n", show("audiod", "spkChannels")
        printf "cfg_vqe_0=%d\n", (index(s, "\"vqe_0\"") > 0)
        printf "cfg_raw_mic_0=%d\n", (index(s, "\"raw_mic_0\"") > 0)
        printf "cfg_raw_spk_0=%d\n", (index(s, "\"raw_spk_0\"") > 0)
        printf "cfg_available_channels=%d\n", (index(s, "\"availableChannels\"") > 0)
    }'
else echo quasar_cfg_present=0; fi
'''

STATES = {"closed", "open", "setup", "prepared", "running", "xrun", "draining", "paused",
          "suspended", "disconnected", "unknown"}
SERVICES = ("aliced", "audiod", "audiosender", "glagold")


def parse(text: str) -> dict[str, Any]:
    """Typed allowlisted facts; ValueError on any ambiguity, never raw remote text."""
    if len(text) > 65536:
        raise ValueError
    pairs = [line.split("=", 1) for line in text.splitlines() if "=" in line]
    f = dict(pairs)
    if len(f) != len(pairs):
        raise ValueError

    def num(key: str, limit: int, *, optional: bool = False) -> int | None:
        value = f[key]
        if optional and value == "-":
            return None
        if not re.fullmatch(r"[0-9]{1,10}", value) or int(value) > limit:
            raise ValueError
        return int(value)

    def flag(key: str) -> bool:
        return bool(num(key, 1))

    def tri(key: str) -> bool | None:
        return None if f[key] == "-1" else flag(key)

    def match(key: str, pattern: str) -> str:
        if not re.fullmatch(pattern, f[key]):
            raise ValueError
        return f[key]

    try:
        pcms = []
        for i in range(int(num("capture_count", 8) or 0)):
            p = f"capture_{i}_"
            roles = [] if f[p + "quasar_roles"] == "-" else f[p + "quasar_roles"].split("+")
            pcm = {
                "card": num(p + "card", 31), "device": num(p + "device", 63),
                "id": match(p + "id", r"[A-Za-z0-9_.()-]{1,32}"),
                "state": match(p + "state", "|".join(sorted(STATES))),
                "subdevices": num(p + "subdevices", 64, optional=True),
                "subdevices_available": num(p + "subdevices_available", 64, optional=True),
                "format": None if f[p + "format"] == "-" else match(p + "format",
                                                                    r"[A-Z0-9_]{1,16}"),
                "channels": num(p + "channels", 64, optional=True),
                "rate": num(p + "rate", 768000, optional=True),
                "period_frames": num(p + "period_frames", 10**8, optional=True),
                "buffer_frames": num(p + "buffer_frames", 10**8, optional=True),
                "holders": num(p + "holders", 4096),
                "quasar_holders": num(p + "quasar_holders", 4096),
                "quasar_roles": roles,
            }
            if (pcm["quasar_holders"] > pcm["holders"]  # type: ignore[operator]
                    or len(roles) != pcm["quasar_holders"]
                    or any(not re.fullmatch(r"[a-z_]{1,24}", r) for r in roles)):
                raise ValueError
            pcms.append(pcm)
        listeners = [{"role": match(f"listener_{i}_role", r"[a-z_]{1,24}"),
                      "port": num(f"listener_{i}_port", 65535),
                      "scope": match(f"listener_{i}_scope", "loopback|any|other")}
                     for i in range(int(num("listener_count", 32) or 0))]
        if any(item["port"] == 0 for item in listeners):
            raise ValueError
        alsa = None
        if flag("alsa_conf_present"):
            alsa = {"sha256": match("alsa_conf_sha256", "[0-9a-f]{64}"),
                    "ipc_keys": num("alsa_ipc_key_count", 8),
                    "pcms": [{"name": match(f"alsa_pcm_{i}_name", r"[A-Za-z0-9_]{1,32}"),
                              "type": match(f"alsa_pcm_{i}_type", r"[A-Za-z0-9_]{1,16}|-"),
                              "in_quasar_cfg": flag(f"alsa_pcm_{i}_in_quasar_cfg")}
                             for i in range(int(num("alsa_pcm_count", 16) or 0))]}
        shm = None
        if flag("sysv_shm_readable"):
            shm = {key: num("sysv_shm_" + key, 10**6) for key in
                   ("segments", "attached", "alsa_key_matches", "created_by_quasar")}
        cfg = None
        if flag("quasar_cfg_present"):
            cfg = {"sections": {s: flag(f"cfg_{s}_section") for s in SERVICES},
                   "ports": {s: num(f"cfg_{s}_port", 65535, optional=True) for s in SERVICES},
                   "audiod": {key: num(f"cfg_audiod_{key}", 768000, optional=True)
                              for key in ("in_rate", "out_rate", "mic_channels",
                                          "spk_channels")},
                   "channels": {"vqe_0": flag("cfg_vqe_0"), "raw_mic_0": flag("cfg_raw_mic_0"),
                                "raw_spk_0": flag("cfg_raw_spk_0"),
                                "available_channels_key": flag("cfg_available_channels")}}
        facts: dict[str, Any] = {
            "maind_processes": num("maind_processes", 256),
            "launcher_running": flag("launcher_running"),
            "selinux": {"1": "enforcing", "0": "permissive", "absent": "absent"}[
                f["selinux_enforce"]],
            "sound_cards": num("sound_cards", 32), "snd_aloop_loaded": flag("snd_aloop_loaded"),
            "capture_pcms": pcms, "quasar_listeners": listeners, "alsa_config": alsa,
            "sysv_shm": shm, "quasar_config": cfg,
            "maind_alsa_config_override": tri("maind_alsa_config_override"),
            "io_channel_type_names": tri("maind_io_channel_type_names"),
            "audio_captured": False, "quasar_changed": False, "capture_verified": False,
        }
    except (KeyError, TypeError):
        raise ValueError from None
    facts["capture_paths"] = assess(facts)
    return facts


def assess(facts: dict[str, Any]) -> dict[str, str]:
    """Map facts to the decision tree; every positive branch still needs a live test."""
    held = [c for c in facts["capture_pcms"] if c["quasar_holders"]]
    pcms = (facts["alsa_config"] or {}).get("pcms", [])
    shm = facts["sysv_shm"]
    names = facts["io_channel_type_names"]
    if any(p["type"] == "dsnoop" and p["in_quasar_cfg"] for p in pcms) and shm and \
            shm["alsa_key_matches"]:
        dsnoop = "candidate_untested"
    elif (held and not any(p["type"] == "dsnoop" for p in pcms) and shm is not None
          and not shm["created_by_quasar"] and facts["maind_alsa_config_override"] is False):
        dsnoop = "excluded_without_quasar_reconfiguration"
    else:
        dsnoop = "unknown"
    available = [c["subdevices_available"] for c in held]
    return {
        "quasar_io_channel_subscription": "unknown" if names is None else (
            "schema_names_present_untested" if names else "schema_names_absent_from_maind"),
        "alsa_dsnoop_shared": dsnoop,
        "second_hw_substream": "unknown" if not held or None in available else (
            "available_untested" if any(available) else "excluded"),
        "vqe_preload_hook": "excluded_requires_quasar_restart",
        "kernel_loopback": "present_requires_quasar_routing_change"
        if facts["snd_aloop_loaded"] else "excluded_requires_module_load",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only capture-coexistence facts; no audio")
    parser.add_argument("--station", required=True, help="Private station alias, never an endpoint")
    args = parser.parse_args(argv)
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
    from alice_mcp import core, vqe_experiment

    try:
        controller = core.Controller(Path(inventory), writes=False)
        # Explicit pinned SSH, also for native-configured stations; never a native fallback.
        station = vqe_experiment._ssh(controller.station(args.station))
        data = core.parse_probe("capture", controller.runner(
            station, core.SSHInput("sh -s", COMMAND.encode())))
        result: dict[str, Any] = {"outcome": "complete", "evidence_class": "analytical",
                                  "data": data}
    except Exception:
        result = {"outcome": "blocked",
                  "error": "Capture facts unavailable; check inventory/SSH trust locally"}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if result["outcome"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
