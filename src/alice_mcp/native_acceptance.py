"""Owner-run acceptance of native typed probes, volume and registered media.

Read-only by default. ``--live`` needs a terminal beside the Station and an exact
typed word; each write uses the normal fresh one-use preview and apply path once
and is never retried. Source-tested, not live-accepted.

    python -m alice_mcp.native_acceptance health --station ALIAS
    python -m alice_mcp.native_acceptance volume --station ALIAS [--live]
    python -m alice_mcp.native_acceptance media --station ALIAS --asset ALIAS [--live]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alice_mcp import native_transport, native_typed
from alice_mcp.core import Controller, SafetyError, confirm

Ask = Callable[[str], str]


def _probe(controller: Controller, station_id: str, name: str) -> dict[str, Any]:
    try:
        return dict(controller.probe(station_id, name)["data"])
    except SafetyError:
        return {"outcome": "blocked"}


def _run(controller: Controller, station_id: str, action: str,
         parameters: dict[str, Any]) -> dict[str, Any]:
    try:
        return native_transport.run_action(controller, controller.station(station_id), action,
                                           parameters)
    except SafetyError as error:
        return {"outcome": "blocked", "error": str(error)}


def health(controller: Controller, station_id: str) -> dict[str, Any]:
    report: dict[str, Any] = {"feature": "health", "live": False,
                              "physical_effects_verified": False}
    for name in ("health", "storage", "services", "audio"):
        report[name] = _probe(controller, station_id, name)
    blocked = any("outcome" in report[n] for n in ("health", "storage", "services", "audio"))
    report["outcome"] = "blocked" if blocked else "ready"
    return report


def volume(controller: Controller, station_id: str, *, live: bool, ask: Ask) -> dict[str, Any]:
    station = controller.station(station_id)
    ceiling = station.controls.get("max_volume", 30)
    audio = _probe(controller, station_id, "audio")
    before = audio.get("volume_percent")
    report: dict[str, Any] = {"feature": "volume", "live": live, "ceiling_percent": ceiling,
                              "before_percent": before, "restored": False,
                              "owner_observed_quieter": None, "do_not_retry": True,
                              "physical_effects_verified": False}
    if not live:
        return dict(report, outcome="preview")
    if type(before) is not int or not 0 < before <= ceiling:
        # Restoration must stay within the same bounded action; never exceed the ceiling.
        return dict(report, outcome="blocked",
                    error_code="volume_unreadable_silent_or_above_ceiling")
    if miss := confirm(ask("Ask Alice a short question now and note how loud the reply is. "
                           f"Then type VOLUME to lower the playback mixer one step from "
                           f"{before}%: "), "VOLUME"):
        return dict(report, outcome="cancelled", **miss)
    report["step"] = _run(controller, station_id, "audio.volume", {"step": "quieter"})
    if report["step"].get("outcome") != "applied":
        # Unknown is inspected read-only; no restore write is stacked on an uncertain one.
        report["after_percent"] = _probe(controller, station_id, "audio").get("volume_percent")
        return dict(report, outcome=report["step"].get("outcome", "unknown"))
    try:
        answer = ask("Ask the same question again. Type QUIETER if the reply was quieter, "
                     "SAME if not: ")
        report["owner_observed_quieter"] = {"QUIETER": True, "SAME": False}.get(answer.strip())
    except (EOFError, KeyboardInterrupt):
        report["error_code"] = "operator_interrupted"
    finally:
        report["restore"] = _run(controller, station_id, "audio.volume", {"percent": before})
        report["restored"] = (report["restore"].get("outcome") == "applied"
                              and report["restore"].get("percent") == before)
        report["after_percent"] = _probe(controller, station_id, "audio").get("volume_percent")
    accepted = report["restored"] and report["owner_observed_quieter"] is True
    return dict(report, outcome="accepted" if accepted else "not_accepted")


def media(controller: Controller, station_id: str, asset: str, seconds: int, *, live: bool,
          ask: Ask) -> dict[str, Any]:
    parameters = {"asset": asset, "duration_seconds": seconds}
    report: dict[str, Any] = {"feature": "media", "live": live, "asset": asset,
                              "duration_seconds": seconds, "owner_heard": None,
                              "do_not_retry": True, "physical_effects_verified": False}
    try:
        native_typed.parameters(controller.station(station_id), "audio.play", parameters)
    except (ValueError, SafetyError):
        return dict(report, outcome="blocked", error_code="asset_not_registered_or_outside_bounds")
    if not live:
        return dict(report, outcome="preview")
    if miss := confirm(ask(f"Type PLAY to play registered asset {asset} once (at most "
                           f"{seconds} s, quiet canonical WAV at the current volume): "), "PLAY"):
        return dict(report, outcome="cancelled", **miss)
    report["play"] = _run(controller, station_id, "audio.play", parameters)
    if report["play"].get("outcome") not in {"applied", "unknown"}:
        return dict(report, outcome=report["play"].get("outcome", "blocked"))
    try:
        answer = ask("Did the clip play from the Station? Type HEARD or NOTHING: ")
        report["owner_heard"] = {"HEARD": True, "NOTHING": False}.get(answer.strip())
    except (EOFError, KeyboardInterrupt):
        report["error_code"] = "operator_interrupted"
    report["services"] = _probe(controller, station_id, "services")
    accepted = report["play"]["outcome"] == "applied" and report["owner_heard"] is True
    return dict(report, outcome="accepted" if accepted else "not_accepted")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Owner-run native typed audio/probe acceptance")
    parser.add_argument("feature", choices=["health", "volume", "media"])
    parser.add_argument("--station", required=True, help="Private station alias, never an endpoint")
    parser.add_argument("--asset", help="Registered local_media alias (media only)")
    parser.add_argument("--seconds", type=int, default=10, help="Playback bound, 1-10 seconds")
    parser.add_argument("--live", action="store_true", help="Run the confirmed write step")
    args = parser.parse_args(argv)
    if args.live and not sys.stdin.isatty():
        parser.error("--live needs a terminal beside the Station, not piped confirmations")
    if args.feature == "media" and not args.asset:
        parser.error("media needs --asset")
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
    try:
        controller = Controller(Path(inventory), writes=args.live)
        if args.feature == "health":
            report = health(controller, args.station)
        elif args.feature == "volume":
            report = volume(controller, args.station, live=args.live, ask=input)
        else:
            report = media(controller, args.station, args.asset, args.seconds, live=args.live,
                           ask=input)
    except (Exception, KeyboardInterrupt):
        # A write may have been in flight: inspect read-only, never repeat it blindly.
        report = {"outcome": "unknown", "error_code": "interrupted_or_inputs_unavailable",
                  "do_not_retry": True}
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
