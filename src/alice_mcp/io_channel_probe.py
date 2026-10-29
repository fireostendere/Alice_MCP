"""Read-only facts for locating Quasar's IO audio-channel service (docs/IO_CHANNEL.md).

Maps quasar.cfg service ports to Quasar-owned loopback listeners and to the Quasar
roles already connected to them, plus bounded maind source-path token counts. It
reads procfs and two vendor files only: no socket connection, PCM, process attach,
library load, write or Quasar change. Only typed allowlisted values leave the parser.

Owner-run over explicit pinned key-only SSH (also for native-configured stations):
    ALICE_MCP_INVENTORY=/PRIVATE/inventory.json python -m alice_mcp.io_channel_probe --station ALIAS
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from alice_mcp.core import Controller, SSHInput
from alice_mcp.vqe_experiment import _ssh

# BusyBox 1.26 awk: never write `name (expr)`; it parses as a function call.
COMMAND = r'''
LC_ALL=C; export LC_ALL
exec 2>/dev/null
Q=/system/vendor/quasar
pids=$(pidof maind)
count=0; for p in $pids; do count=$((count+1)); done
echo "maind_processes=$count"
if [ -r /sys/fs/selinux/enforce ]; then
    echo "selinux_enforce=$(head -c 1 /sys/fs/selinux/enforce)"
else echo selinux_enforce=absent; fi
roles=$(for p in $pids; do
    printf '%s ' "$p"
    tr '\000' '\n' < "/proc/$p/cmdline" | awk 'NR == 2 {r = $0} END {
        sub(/^--/, "", r); if (r !~ /^[a-z_]+$/ || length(r) > 24) r = "unknown"; print r}'
done)
socks=$(for p in $pids; do
    ls -l "/proc/$p/fd" | awk -v pid="$p" '$(NF-1) == "->" && $NF ~ /^socket:\[[0-9]+\]$/ {
        s = $NF; gsub(/[^0-9]/, "", s); print s, pid }'
done)
services=
if [ -r "$Q/quasar.cfg" ]; then
    echo quasar_cfg_present=1
    # Heuristic token scan: "name port" for a numeric "port" directly in a top-level section.
    services=$(head -c 1048576 "$Q/quasar.cfg" | awk '
    function token(t) {
        if (t == "{" || t == "[") { depth++; sect[depth] = pend; pend = ""; return }
        if (t == "}" || t == "]") { if (depth > 0) depth--; pend = ""; return }
        if (t == ":") { pend = last; last = ""; return }
        if (t == ",") { pend = ""; last = ""; return }
        if (pend != "") {
            s = sect[2]
            if (depth == 2 && pend == "port" && t ~ /^[0-9]+$/ && length(t) <= 5 &&
                s ~ /^[A-Za-z][A-Za-z0-9_]*$/ && length(s) <= 32 && !(s in done) && n < 48) {
                done[s] = 1; n++; print s, t + 0
            }
            pend = ""; return
        }
        if (t ~ /^".*"$/) last = substr(t, 2, length(t) - 2)
    }
    {
        line = $0; gsub(/[][{},:]/, "\n&\n", line)
        m = split(line, parts, "\n")
        for (j = 1; j <= m; j++) {
            t = parts[j]; gsub(/^[ \t\r]+|[ \t\r]+$/, "", t); if (t != "") token(t)
        }
    }')
else echo quasar_cfg_present=0; fi
{
    printf '%s\n' "$socks" | sed 's/^/S /'
    printf '%s\n' "$roles" | sed 's/^/R /'
    printf '%s\n' "$services" | sed 's/^/C /'
    cat /proc/net/tcp /proc/net/tcp6 | sed 's/^/T /'
} | awk '
function hex(h,   v, k, c) {
    v = 0; h = toupper(h)
    for (k = 1; k <= length(h); k++) {
        c = index("0123456789ABCDEF", substr(h, k, 1)); if (!c) return -1; v = v * 16 + c - 1
    }
    return v
}
function scope(a) {
    if (a ~ /^0+$/) return "any"
    if (a == "0100007F" || a == "00000000000000000000000001000000" ||
        a == "0000000000000000FFFF00000100007F") return "loopback"
    return "other"
}
BEGIN { n = 0 }  # An unset counter would subscript as "", not 0.
$1 == "S" { owner[$2] = $3 }
$1 == "R" { role[$2] = $3 }
$1 == "C" && NF == 3 { name[n] = $2; port[n] = $3; idx[$3] = n; n++ }
$1 == "T" && $3 ~ /:/ && $4 ~ /:/ {
    split($3, l, ":"); split($4, r, ":"); lp = hex(l[2]); rp = hex(r[2])
    q = ""
    if ($11 in owner) { q = role[owner[$11]]; if (q == "") q = "unknown" }
    if ($5 == "0A") {
        if (lp in idx) {
            i = idx[lp]
            if (!(i in listen)) { listen[i] = scope(l[1]); lowner[i] = (q == "") ? "foreign" : q }
        } else if (q != "") unmapped++
    } else if ($5 == "01") {
        if ((lp in idx) && scope(l[1]) == "loopback" && q != "") accepted[idx[lp]]++
        if ((rp in idx) && scope(r[1]) == "loopback") {
            i = idx[rp]; key = i SUBSEP q
            if (q == "") foreign[i]++
            else if (!(key in seen)) {
                seen[key] = 1; clients[i] = (clients[i] == "") ? q : clients[i] "+" q
            }
        }
    }
}
END {
    for (i = 0; i < n; i++) {
        printf "service_%d_name=%s\nservice_%d_port=%s\n", i, name[i], i, port[i]
        printf "service_%d_listen=%s\n", i, (i in listen) ? listen[i] : "none"
        printf "service_%d_owner=%s\n", i, (i in lowner) ? lowner[i] : "-"
        printf "service_%d_accepted=%d\n", i, accepted[i] + 0
        printf "service_%d_client_roles=%s\n", i, (clients[i] == "") ? "-" : clients[i]
        printf "service_%d_foreign_clients=%d\n", i, foreign[i] + 0
    }
    printf "service_count=%d\nunmapped_quasar_listeners=%d\n", n, unmapped + 0
}'
{
    printf '%s\n' "$socks" | sed 's/^/S /'
    sed 's/^/U /' /proc/net/unix
} | awk '$1 == "S" { own[$2] = 1 }
$1 == "U" && $5 == "00010000" && $7 == "01" && ($8 in own) { n++; if (index($9, "audio")) a++ }
END { printf "unix_quasar_listeners=%d\nunix_quasar_audio_named=%d\n", n, a }'
scanned=0
if [ -r "$Q/maind" ] && [ "$(( $(wc -c < "$Q/maind") ))" -le 100663296 ]; then
    scanned=1
    # BusyBox grep stops at NUL bytes, so split the binary into C strings first.
    tr '\000' '\n' < "$Q/maind" | grep -F -e .cpp | head -c 4194304 | awk '
    BEGIN { split("audiod audio_source iohub channel", tok, " ") }
    length($0) <= 256 && $0 ~ /\.cpp$/ { for (k = 1; k <= 4; k++) if (index($0, tok[k])) c[k]++ }
    END { for (k = 1; k <= 4; k++) printf "maind_cpp_%s=%d\n", tok[k], c[k] + 0 }'
fi
echo "maind_scanned=$scanned"
'''

TOKENS = ("audiod", "audio_source", "iohub", "channel")
# Assumed server order (docs/IO_CHANNEL.md); the probe only ranks, it never connects.
PRIORITY = ("yio_audio", "audiod", "iohub_services", "iohub_clients", "audiosender", "aliced")
ROLE = r"[a-z_]{1,24}"


def parse(text: str) -> dict[str, Any]:
    """Typed allowlisted facts; ValueError on any ambiguity, never raw remote text."""
    if len(text) > 65536:
        raise ValueError
    pairs = [line.split("=", 1) for line in text.splitlines() if "=" in line]
    f = dict(pairs)
    if len(f) != len(pairs):
        raise ValueError

    def num(key: str, high: int) -> int:
        if not re.fullmatch(r"[0-9]{1,6}", f[key]) or int(f[key]) > high:
            raise ValueError
        return int(f[key])

    def match(key: str, pattern: str) -> str:
        if not re.fullmatch(pattern, f[key]):
            raise ValueError
        return f[key]

    try:
        services = []
        for i in range(num("service_count", 48)):
            p = f"service_{i}_"
            roles = [] if f[p + "client_roles"] == "-" else f[p + "client_roles"].split("+")
            if any(not re.fullmatch(ROLE, r) for r in roles) or len(set(roles)) != len(roles):
                raise ValueError
            services.append({
                "name": match(p + "name", r"[A-Za-z][A-Za-z0-9_]{0,31}"),
                "port": num(p + "port", 65535),
                "listen": match(p + "listen", "loopback|any|other|none"),
                "owner": match(p + "owner", ROLE + "|-"),
                "accepted_connections": num(p + "accepted", 4096),
                "client_roles": sorted(roles),
                "foreign_clients": num(p + "foreign_clients", 4096),
            })
        if any(s["port"] == 0 or (s["listen"] == "none") != (s["owner"] == "-")
               for s in services):
            raise ValueError
        scanned = num("maind_scanned", 1)
        facts: dict[str, Any] = {
            "maind_processes": num("maind_processes", 256),
            "selinux": {"1": "enforcing", "0": "permissive", "absent": "absent"}[
                f["selinux_enforce"]],
            "quasar_cfg_present": bool(num("quasar_cfg_present", 1)),
            "services": services,
            "unmapped_quasar_listeners": num("unmapped_quasar_listeners", 4096),
            "unix_quasar_listeners": num("unix_quasar_listeners", 4096),
            "unix_quasar_audio_named": num("unix_quasar_audio_named", 4096),
            "maind_cpp_tokens": {t: num(f"maind_cpp_{t}", 10**6) for t in TOKENS}
            if scanned else None,
            "connections_opened": False, "audio_captured": False,
        }
    except (KeyError, TypeError):
        raise ValueError from None
    owned = {s["name"]: s for s in services
             if s["listen"] in {"loopback", "any"} and s["owner"] not in {"foreign", "-"}}
    facts["io_channel_candidates"] = [
        {"service": n, "port": owned[n]["port"], "client_roles": owned[n]["client_roles"]}
        for n in PRIORITY if n in owned]
    return facts


def run(controller: Controller, station_id: str) -> dict[str, Any]:
    # Explicit pinned SSH, also for native-configured stations; never a native fallback.
    station = _ssh(controller.station(station_id))
    data = parse(controller.runner(station, SSHInput("sh -s", COMMAND.encode())))
    return {"outcome": "complete", "evidence_class": "analytical", "data": data}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only IO-channel service facts; no audio")
    parser.add_argument("--station", required=True, help="Private station alias, never an endpoint")
    args = parser.parse_args(argv)
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
    try:
        result = run(Controller(Path(inventory), writes=False), args.station)
    except Exception:
        result = {"outcome": "blocked",
                  "error": "IO-channel facts unavailable; check inventory/SSH trust locally"}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if result["outcome"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
