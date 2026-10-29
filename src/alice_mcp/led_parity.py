"""Owner-run LED parity acceptance, channel calibration and a read-only display probe.

Every write goes through the normal `led.show` action: private policy, a fresh
300-second preview consumed once, live state checks, no retry. The terminal's
exact typed word is the human approval. Nothing here edits private inventory:
the calibration report prints a `led_map` for the owner to paste.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alice_mcp import controls, native_transport
from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller, SafetyError, Station, _fields, _number, confirm

EFFECTS = ("rainbow", "breathe", "spin", "spin_color", "static", "off", "notify", "police")
# Read-only presence checks: no device opens, values, writes, mounts or settings contents.
DISPLAY_PROBE = (
    "LC_ALL=C; export LC_ALL; "
    "if tr '\\000' '\\n' < /proc/device-tree/compatible | grep -qx 'yandex,bergamot'; "
    "then echo bergamot=1; else echo bergamot=0; fi; "
    "n=0; for p in /sys/class/leds/*clock*; do test -e \"$p\" && n=$((n+1)); done; "
    "echo clock_led_entries=$n; "
    "if test -e /data/quasar/data/clock_display_settings.dat; "
    "then echo clock_settings_file=1; else echo clock_settings_file=0; fi"
)
Ask = Callable[[str], str]


def native_parameters(station: Station, params: dict[str, Any]) -> dict[str, Any]:
    """Host maps the effect; the resident gets only bounded PWM frames, never code."""
    if station.model != "station-mini":
        raise SafetyError("Native LED adapter exists only for Mini 1")
    interval, frames = rt.show_frames(params, station.model, station.controls.get("led_map"))
    return {"capture_id": params["capture_id"], "interval_ms": interval,
            "frames": [bytes(frame).hex() for frame in frames]}


def native_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("outcome") == "blocked" and set(result) <= {"outcome", "error"}:
        return {"outcome": "blocked", "error": "Native LED policy, evidence or runtime blocked"}
    flags = ("command_succeeded", "restoration_command_succeeded", "quasar_running")
    if (set(result) != {"outcome", "restoration_verified", "do_not_retry", "evidence_class",
                        "physical_effects_verified", *flags}
        or result["outcome"] not in {"applied", "unknown"}
        or any(type(result[k]) is not bool for k in flags)
        or result["restoration_verified"] is not False or result["do_not_retry"] is not True
        or result["physical_effects_verified"] is not False
        or result["evidence_class"] != "analytical"
        or result["outcome"] == "applied" and not (
            result["command_succeeded"] and result["restoration_command_succeeded"])):
        raise SafetyError("Native LED result unverified; do not retry")
    return dict(result, effect_observed=False)


def _once(controller: Controller, station: Station, params: dict[str, Any]) -> dict[str, Any]:
    """One fresh state-bound preview, consumed once by one execution; never retried."""
    if station.native_mcp:
        return native_transport.run_action(controller, station, "led.show", params)
    plan = controls.preview(controller, station.id, "led.show", params)
    return controller.execute_plan(controller.take_plan(plan["plan_id"],
                                                        plan["expected_sha256"]))


def _choice(ask: Ask, prompt: str, options: dict[str, Any]) -> Any:
    for _ in range(3):
        answer = ask(prompt).strip().lower()
        if answer in options:
            return options[answer]
    raise ValueError("no valid answer")


def derive_map(model: str, answers: list[tuple[str, int]]) -> dict[str, Any] | None:
    """Owner reports (colour, clock position 1-12, 0 = unseen) per raw channel.

    Logical zone 0 is the first lit position clockwise from 12 o'clock. Missing,
    extra or duplicated zone/colour reports yield no map rather than a guess.
    """
    if len(answers) != len(rt.led_layout(model)):
        return None
    positions = sorted({p for c, p in answers if c != "none" and p}, key=lambda p: p % 12)
    if len(positions) != rt.LED_ZONES[model]:
        return None
    channels = [None if c == "none" or not p else [positions.index(p), c] for c, p in answers]
    used = [tuple(e) for e in channels if e]
    if len(used) != len(set(used)):
        return None
    return {"channels": channels}


def calibrate(controller: Controller, station_id: str, capture_id: str = "", *,
              ask: Ask = input) -> dict[str, Any]:
    report: dict[str, Any] = {"outcome": "cancelled", "do_not_retry": True,
                              "physical_effects_verified": False, "observations": []}
    station = controller.station(station_id)
    if station.model not in rt.LED_ZONES:
        return dict(report, outcome="blocked", error_code="no_led_profile")
    count = len(rt.led_layout(station.model))
    if miss := confirm(ask(
            f"{count} LED channels will light one at a time at 16/255 for 2 s each, then the "
            "captured idle state. Keep the ring idle; pick 12 o'clock as the power-socket side. "
            "Type CALIBRATE to begin: "), "CALIBRATE"):
        return dict(report, **miss)
    answers: list[tuple[str, int]] = []
    try:
        for channel in range(count):
            params: dict[str, Any] = {"effect": "calibrate", "channel": channel,
                                      "duration_seconds": 2}
            if station.model == "station-mini":
                params["capture_id"] = capture_id
            result = _once(controller, station, params)
            if result.get("outcome") != "applied" or result.get("yielded"):
                return dict(report, outcome="unknown" if result.get("outcome") == "unknown"
                            else "interrupted", error_code="step_not_applied_inspect_locally")
            colour = _choice(ask, f"Channel {channel}: colour seen? [r/g/b/n=none]: ",
                             {"r": "red", "g": "green", "b": "blue", "n": "none"})
            position = 0 if colour == "none" else _choice(
                ask, "Clock position 1-12: ", {str(i): i for i in range(1, 13)})
            answers.append((colour, position))
            report["observations"].append({"channel": channel, "colour": colour,
                                           "position": position})
    except (EOFError, KeyboardInterrupt, ValueError, SafetyError):
        return dict(report, outcome="interrupted", error_code="operator_interrupted_or_blocked")
    led_map = derive_map(station.model, answers)
    if led_map is None:
        return dict(report, outcome="inconsistent")
    rt.validate_led_map(station.model, led_map)
    return dict(report, outcome="complete", led_map=led_map,
                next_step="Paste led_map into this station's private controls; reload MCP")


def show(controller: Controller, station_id: str, effects: list[str], *, speed: int = 3,
         seconds: int = 4, color: tuple[int, int, int] = (8, 0, 8), capture_id: str = "",
         live: bool = False, ask: Ask = input) -> dict[str, Any]:
    """Preview by default; with live=True, one typed word then each effect once."""
    station = controller.station(station_id)
    report: dict[str, Any] = {"outcome": "previewed", "do_not_retry": True,
                              "physical_effects_verified": False, "effects": []}
    requests = []
    for effect in effects:
        params: dict[str, Any] = {"effect": effect, "duration_seconds": seconds, "speed": speed}
        if effect in rt.SHOW_COLOR_EFFECTS:
            params.update(zip(rt.COLORS, color, strict=True))
        if station.model == "station-mini":
            params["capture_id"] = capture_id
        checked = rt.validate_parameters("led.show", params, station.controls, station.model)
        requests.append(params)
        report["effects"].append({"effect": effect, "impact": controls.describe({
            "action": "led.show", "model": station.model, "policy": station.controls,
            "params": checked})})
    if not live:
        for params in requests:  # Read-only previews; they expire unused.
            if station.native_mcp:
                native_transport.preview_action(controller, station, "led.show", params)
            else:
                controls.preview(controller, station.id, "led.show", params)
        return report
    if miss := confirm(ask(f"Run {len(requests)} LED show(s) now, each fresh and once. "
                           "Type SHOW: "), "SHOW"):
        return dict(report, outcome="cancelled", **miss)
    report["outcome"] = "applied"
    try:
        for item, params in zip(report["effects"], requests, strict=True):
            item.pop("impact")
            result = _once(controller, station, params)
            item.update(outcome=result.get("outcome"), yielded=result.get("yielded", False))
            if result.get("outcome") != "applied":
                report["outcome"] = "unknown" if result.get("outcome") == "unknown" else "blocked"
                break
            item["owner_observed"] = _choice(ask, f"Did you see {params['effect']} and then "
                                                  "the normal idle ring? [yes/no]: ",
                                             {"yes": True, "no": False})
    except (EOFError, KeyboardInterrupt, ValueError, SafetyError):
        report.update(outcome="interrupted", error_code="operator_interrupted_or_blocked")
    return report


def display_probe(controller: Controller, station_id: str) -> dict[str, Any]:
    station = controller.station(station_id)
    fields = _fields(controller.runner(station, DISPLAY_PROBE))
    flags = {key: _number(fields, key) for key in ("bergamot", "clock_settings_file")}
    entries = _number(fields, "clock_led_entries")
    if any(v not in {0, 1} for v in flags.values()) or entries > 256:
        raise SafetyError("Display probe unavailable or incompatible")
    return {"model": station.model, "bergamot_compatible": bool(flags["bergamot"]),
            "clock_led_entries": entries, "clock_settings_file": bool(flags["clock_settings_file"]),
            "display_writes_supported": False, "evidence_class": "analytical"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Owner-run LED parity checks; no retries")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("display-probe", "show", "calibrate"):
        sub = commands.add_parser(name)
        sub.add_argument("--station", required=True, help="Private station alias only")
        if name != "display-probe":
            sub.add_argument("--capture-id", default="", help="Mini 1 recent idle capture id")
    show_args = commands.choices["show"]
    show_args.add_argument("--effect", choices=[*EFFECTS, "all"], default="all")
    show_args.add_argument("--speed", type=int, default=3)
    show_args.add_argument("--seconds", type=int, default=4)
    show_args.add_argument("--color", default="8,0,8", help="R,G,B each 0-16")
    show_args.add_argument("--live", action="store_true", help="Ask for SHOW, then write")
    args = parser.parse_args()
    if args.command != "display-probe" and not sys.stdin.isatty():
        parser.error("Use a terminal beside the station, not an agent or piped answers")
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
    controller = Controller(Path(inventory), writes=args.command != "display-probe")
    try:
        if args.command == "display-probe":
            report = display_probe(controller, args.station)
        elif args.command == "calibrate":
            report = calibrate(controller, args.station, args.capture_id)
        else:
            color = tuple(int(v) for v in args.color.split(","))
            if len(color) != 3:
                raise ValueError
            report = show(controller, args.station,
                          list(EFFECTS) if args.effect == "all" else [args.effect],
                          speed=args.speed, seconds=args.seconds,
                          color=(color[0], color[1], color[2]),
                          capture_id=args.capture_id, live=args.live)
    except SafetyError as error:
        report = {"outcome": "blocked", "error": str(error)}
    except Exception:
        report = {"outcome": "blocked", "error_code": "operator_inputs_unavailable"}
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
