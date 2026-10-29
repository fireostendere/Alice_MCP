"""Owner-run physical-mute evidence from read-only snapshots (docs/VOICE_CAPTURE.md).

Each owner-confirmed phase takes one snapshot over pinned key-only SSH: interrupt
counters, debugfs GPIO values, LED brightness, regulator state and capture-stream
progress. No audio capture, file write, Quasar change, GPIO/LED/mixer write or
debugfs mount. Correlation is evidence of an observable signal, never proof of an
electrical microphone power cut.

    ALICE_MCP_INVENTORY=/PRIVATE/inventory.json python -m alice_mcp.mute_evidence --station ALIAS
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alice_mcp.core import SafetyError, confirm

SNAPSHOT = r'''
LC_ALL=C; export LC_ALL
exec 2>/dev/null
printf 'maind_pids=%s\n' "$(pidof maind | tr ' ' '\n' | sort -n | tr '\n' ',')"
head -c 131072 /proc/interrupts | awk 'NR > 1 && n < 256 {
    id = $1; sub(/:$/, "", id); total = 0; k = 2
    while (k <= NF && $k ~ /^[0-9]+$/) { total += $k; k++ }
    label = ""; for (; k <= NF; k++) label = label (label == "" ? "" : "_") $k
    if (id !~ /^[A-Za-z0-9_]+$/ || length(id) > 16) next
    if (label !~ /^[A-Za-z0-9_.:,@+-]+$/ || length(label) > 64) label = "unknown"
    printf "irq_%d=%s:%.0f:%s\n", n, id, total, label; n++
}'
if [ -r /sys/kernel/debug/gpio ]; then
    echo gpio_readable=1
    head -c 131072 /sys/kernel/debug/gpio | awk '/^[ \t]*gpio-[0-9]+[ \t]*\(/ && n < 256 {
        id = $1; rest = $0; sub(/^[^(]*\(/, "", rest)
        label = rest; sub(/\).*$/, "", label); sub(/^[^|]*\|/, "", label)
        gsub(/^[ \t]+|[ \t]+$/, "", label)
        after = rest; sub(/^[^)]*\)/, "", after); split(after, w, " ")
        if (label !~ /^[A-Za-z0-9_.-]+$/ || length(label) > 32) label = "unknown"
        if (w[1] != "in" && w[1] != "out") w[1] = "unknown"
        if (w[2] != "hi" && w[2] != "lo") w[2] = "unknown"
        printf "gpio_%d=%s:%s:%s:%s\n", n, id, label, w[1], w[2]; n++
    }'
else echo gpio_readable=0; fi
n=0
for l in /sys/class/leds/*; do
    [ -r "$l/brightness" ] && [ "$n" -lt 64 ] || continue
    name=${l##*/}; value=$(head -c 16 "$l/brightness")
    case "$name" in ''|*[!A-Za-z0-9_.:-]*) continue;; esac
    case "$value" in ''|*[!0-9]*) continue;; esac
    echo "led_$n=$value:$name"; n=$((n+1))
done
n=0
for r in /sys/class/regulator/regulator.*; do
    [ -r "$r/name" ] && [ "$n" -lt 64 ] || continue
    name=$(head -c 64 "$r/name"); state=$(head -c 16 "$r/state")
    case "$name" in ''|*[!A-Za-z0-9_.:-]*) continue;; esac
    case "$state" in enabled|disabled) ;; *) state=unknown;; esac
    echo "regulator_$n=$state:$name"; n=$((n+1))
done
sample() {
    for d in /proc/asound/card[0-9]*/pcm[0-9]*c; do
        [ -r "$d/sub0/status" ] || continue
        card=${d%/pcm*}; card=${card##*card}; dev=${d##*/pcm}; dev=${dev%c}
        head -c 4096 "$d/sub0/status" | awk -v id="c${card}d${dev}" '
            $1 == "closed" { s = "closed" } $1 == "state:" { s = tolower($2) }
            $1 == "hw_ptr" && $2 == ":" { p = $3 } $1 == "hw_ptr:" { p = $2 }
            END { if (s == "") s = "unknown"; if (p !~ /^[0-9]+$/) p = "-"; print id, s, p }'
    done
}
first=$(sample)
sleep 1
second=$(sample)
{ printf '%s\n' "$first" | sed 's/^/A /'; printf '%s\n' "$second" | sed 's/^/B /'; } | awk '
BEGIN { n = 0 }
$1 == "A" && NF == 4 { s[$2] = $3; p[$2] = $4; order[n] = $2; n++ }
$1 == "B" && NF == 4 { q[$2] = $4 }
END {
    for (k = 0; k < n && k < 8; k++) {
        id = order[k]; moved = (p[id] != "-" && (id in q) && q[id] != "-" && q[id] != p[id])
        printf "pcm_%d=%s:%s:%d\n", k, id, s[id], moved
    }
}'
'''

STATES = {"closed", "open", "setup", "prepared", "running", "xrun", "draining", "paused",
          "suspended", "disconnected", "unknown"}
ORDER = ("unmuted_a", "control", "muted", "unmuted_b")
CONTROL_SECONDS = 10
PROMPTS = {
    "unmuted_a": "Read-only mute evidence: no audio capture, writes or Quasar change. Stand by "
                 "the station; microphones ON (red indicator OFF). Type UNMUTED: ",
    "muted": "Short-press the microphone button and RELEASE it; red must stay ON. "
             "Type MUTED: ",
    "unmuted_b": "Short-press it again and RELEASE; red must turn OFF. Type UNMUTED: ",
}
WORDS = {"unmuted_a": "UNMUTED", "muted": "MUTED", "unmuted_b": "UNMUTED"}


def parse_snapshot(text: str) -> dict[str, Any]:
    """Typed allowlisted values only; SafetyError on any ambiguity."""
    try:
        if len(text) > 65536:
            raise ValueError
        pairs = [line.split("=", 1) for line in text.splitlines() if "=" in line]
        f = dict(pairs)
        if len(f) != len(pairs) or f["gpio_readable"] not in {"0", "1"}:
            raise ValueError
        pids = [p for p in f["maind_pids"].split(",") if p]
        if len(pids) > 256 or any(not re.fullmatch(r"[0-9]{1,10}", p) for p in pids):
            raise ValueError
        result: dict[str, Any] = {"maind_pids": sorted(int(p) for p in pids),
                                  "gpio_readable": f["gpio_readable"] == "1",
                                  "irqs": {}, "gpios": {}, "leds": {}, "regulators": {},
                                  "pcms": {}}

        def put(group: str, key: str, value: Any) -> None:
            if key in result[group] or len(result[group]) >= 256:
                raise ValueError
            result[group][key] = value

        for key, value in f.items():
            kind = re.fullmatch(r"(irq|gpio|led|regulator|pcm)_[0-9]{1,3}", key)
            if kind is None:
                continue  # Unknown keys are ignored, never returned.
            group = kind.group(1)
            if group == "irq":
                irq, total, label = value.split(":", 2)
                if (not re.fullmatch(r"[A-Za-z0-9_]{1,16}", irq)
                        or not re.fullmatch(r"[0-9]{1,20}", total)
                        or not re.fullmatch(r"[A-Za-z0-9_.:,@+-]{1,64}", label)):
                    raise ValueError
                put("irqs", irq, {"name": label, "total": int(total)})
            elif group == "gpio":
                line, label, direction, level = value.split(":")
                if (not re.fullmatch(r"gpio-[0-9]{1,5}", line)
                        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", label)
                        or direction not in {"in", "out", "unknown"}
                        or level not in {"hi", "lo", "unknown"}):
                    raise ValueError
                put("gpios", line, {"label": label, "direction": direction, "value": level})
            elif group in {"led", "regulator"}:
                state, name = value.split(":", 1)
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", name):
                    raise ValueError
                if group == "led":
                    if not re.fullmatch(r"[0-9]{1,7}", state):
                        raise ValueError
                    put("leds", name, int(state))
                else:
                    if state not in {"enabled", "disabled", "unknown"}:
                        raise ValueError
                    put("regulators", name, state)
            else:
                pcm, state, moved = value.split(":")
                if (not re.fullmatch(r"c[0-9]{1,2}d[0-9]{1,2}", pcm) or state not in STATES
                        or moved not in {"0", "1"}):
                    raise ValueError
                put("pcms", pcm, {"state": state, "advancing": moved == "1"})
        return result
    except (KeyError, ValueError):
        raise SafetyError("Mute snapshot unavailable or incompatible") from None


def _stable_flip(values: list[Any]) -> bool:
    """Same in both unmuted phases and the quiet control, different while muted."""
    return (None not in values and "unknown" not in values
            and values[0] == values[1] == values[3] != values[2])


def analyze(phases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    a, c, m, b = (phases[name] for name in ORDER)
    snaps = (a, c, m, b)
    irqs = []
    for irq, info in a["irqs"].items():
        totals = [s["irqs"].get(irq, {}).get("total") for s in snaps]
        if None in totals:
            continue
        deltas = [totals[1] - totals[0], totals[2] - totals[1], totals[3] - totals[2]]
        # ponytail: press/release heuristic; a rare unrelated IRQ can still coincide.
        if deltas[0] == 0 and 1 <= deltas[1] <= 8 and 1 <= deltas[2] <= 8:
            irqs.append({"irq": irq, "name": info["name"], "deltas": deltas})
    gpios = []
    for line, info in a["gpios"].items():
        values = [s["gpios"].get(line, {}).get("value") for s in snaps]
        if _stable_flip(values):
            gpios.append({"line": line, "label": info["label"],
                          "unmuted": values[0], "muted": values[2]})
    leds: list[dict[str, Any]] = []
    regulators: list[dict[str, Any]] = []
    for group, out, key in (("leds", leds, "led"), ("regulators", regulators, "regulator")):
        for name in a[group]:
            values = [s[group].get(name) for s in snaps]
            if _stable_flip(values):
                out.append({key: name, "unmuted": values[0], "muted": values[2]})
    muted_pcms = m["pcms"].values()
    stream = None if not muted_pcms else any(
        p["state"] == "running" and p["advancing"] for p in muted_pcms)
    return {
        "button_irq_candidates": irqs, "gpio_mute_candidates": gpios,
        "led_mute_candidates": leds, "regulator_mute_candidates": regulators,
        "mute_signal_observable": bool(gpios or leds or regulators),
        "gpio_readable": all(s["gpio_readable"] for s in snaps),
        "capture_stream_continues_while_muted": stream,
        "quasar_unchanged": bool(a["maind_pids"]) and all(
            s["maind_pids"] == a["maind_pids"] for s in snaps),
        "audio_captured": False, "electrical_power_cut_verified": False,
        "physical_effects_verified": False,
    }


def run(snapshot: Callable[[], dict[str, Any]], *, ask: Callable[[str], str] = input,
        wait: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Phases advance only on exact typed confirmations; read-only, so nothing to undo."""
    base = {"audio_captured": False, "writes": False, "quasar_changed_by_runner": False}
    phases: dict[str, dict[str, Any]] = {}
    for name in ORDER:
        try:
            if name == "control":
                wait(CONTROL_SECONDS)  # Quiet interval: do not touch the station.
            elif miss := confirm(ask(PROMPTS[name]), WORDS[name]):
                return dict(base, outcome="cancelled", phase=name, **miss)
        except (EOFError, KeyboardInterrupt):
            return dict(base, outcome="cancelled", phase=name)
        try:
            phases[name] = snapshot()
        except (SafetyError, OSError, ValueError):
            return dict(base, outcome="unknown", phase=name,
                        error="Snapshot unavailable; inspect SSH trust locally")
    return dict(base, outcome="complete", analysis=analyze(phases))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only physical-mute evidence runner")
    parser.add_argument("--station", required=True, help="Private station alias, never an endpoint")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error("Run in a terminal beside the station; do not pipe confirmations")
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
    from alice_mcp import vqe_experiment
    from alice_mcp.core import Controller, SSHInput

    controller = Controller(Path(inventory), writes=False)

    def snapshot() -> dict[str, Any]:
        # Fresh pinned SSH trust per phase; explicit operator transport, not a native fallback.
        station = vqe_experiment._ssh(controller.station(args.station))
        return parse_snapshot(controller.runner(station, SSHInput("sh -s", SNAPSHOT.encode())))

    result = run(snapshot)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if result["outcome"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
