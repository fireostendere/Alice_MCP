"""Owned host jobs (timers, declarative presets, bounded siren) and a guarded reboot.

Jobs live only in this client-launched MCP process: they end when it exits and are
never resumed or re-fired after a restart. Each step is an existing typed action with a
fresh one-use preview under standing preauthorization; nothing here accepts a command,
path or process name, and cancellation only touches job IDs this process started.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
import secrets
import struct
import sys
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alice_mcp import controls, native_typed
from alice_mcp import device_runtime as rt
from alice_mcp.core import (
    BUILDROOT_MODELS,
    Controller,
    Plan,
    SafetyError,
    Station,
    _fields,
    confirm,
)

TIMER_SECONDS = (5, 86400)
MAX_ACTIVE = 8
MAX_RETAINED = 32
MAX_STEPS = 8
MAX_PRESETS = 16
# Sessions, one-time records and bulk uploads are not repeatable button steps.
PRESET_EXCLUDED = frozenset({"led.capture", "speech.listen", "media.upload", "restore"})
UNFINISHED = frozenset({"scheduled", "running"})
SIREN_TEXT = "Внимание! Тревога!"
SIREN_PLAY_SECONDS = 2
SIREN_REPEATS = 5
SIREN_DEFAULT_VOLUME = 10
SIREN_WAV_SHA256 = "2b6527b444c68e40e611bef553adbdfa06c3a3bebaed5bd8cb507ab21acf1aca"
NATIVE_SIREN_WAV_SHA256 = "aa02430e8b1ea2f2fe1099c5f23763d3b18037f18d13d7fbc89653a8fa6e23b9"
BOOT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
REBOOT_INSPECT = (
    "LC_ALL=C; export LC_ALL; printf 'boot_id='; cat /proc/sys/kernel/random/boot_id; "
    "printf 'firmware='; sha256sum /system/vendor/quasar/maind | cut -d ' ' -f 1; "
    "if command -v reboot >/dev/null 2>&1; then echo reboot_tool=1; else echo reboot_tool=0; fi"
)
# Boot id and firmware are regex-validated before formatting; the device rechecks both
# immediately before the delayed, detached reboot so the SSH reply can still arrive.
REBOOT_APPLY = (
    "LC_ALL=C; export LC_ALL; "
    "if [ \"$(cat /proc/sys/kernel/random/boot_id)\" = {boot} ] && "
    "[ \"$(sha256sum /system/vendor/quasar/maind | cut -d ' ' -f 1)\" = {firmware} ] && "
    "command -v reboot >/dev/null 2>&1; then "
    "( trap '' HUP; sleep 2; exec reboot ) </dev/null >/dev/null 2>&1 & "
    "echo outcome=requested; else echo outcome=conflict; fi"
)
REBOOT_IMPACT = ("Reboot the station: voice, playback, captures, the resident agent and any "
                 "unsaved runtime state stop; it is unreachable for about a minute. Quasar is "
                 "not stopped separately; no files are edited; no automatic retry")
REBOOT_NEXT_STEP = ("Wait for the station, then run station_probe health and reboot once; "
                    "do not repeat the reboot")
_LOCK = threading.Lock()


def siren_wav(rate: int = 8000) -> bytes:
    """Integer-only 1 s triangle sweep 600-1400-600 Hz, peak 8191: identical on every host.

    8 kHz keeps the SSH upload small; native local-PCM playback needs at least 16 kHz.
    """
    count, peak, fade = rate, 8191, rate // 20
    phase, frames = 0, bytearray()
    for i in range(count):
        frequency = 600 + 1600 * min(i, count - i) // count
        phase = (phase + frequency * 65536 // rate) & 0xFFFF
        triangle = abs(phase * 2 - 65536) - 32768
        frames += struct.pack("<h", triangle * peak * min(i, count - 1 - i, fade)
                              // (32768 * fade))
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(bytes(frames))
    return output.getvalue()


def _validate(action: str, params: Any, policy: dict[str, Any], model: str,
              native: bool) -> dict[str, Any]:
    # Native volume/media use the resident's typed bounds, exactly as at preview time.
    if native and action in native_typed.ACTIONS:
        return native_typed.check(policy, model, action, params)
    return rt.validate_parameters(action, params, policy, model)


def _step(item: Any, policy: dict[str, Any], model: str, native: bool) -> dict[str, Any]:
    rt._require(rt._keys(item, {"action", "parameters"}) and type(item["action"]) is str
                and item["action"] in rt.ACTIONS - PRESET_EXCLUDED)
    return {"action": item["action"],
            "parameters": _validate(item["action"], item["parameters"], policy, model, native)}


def _parameter_policy(controls_policy: dict[str, Any], native: bool) -> dict[str, Any]:
    # Native clients are pinned on the device, as in native_transport.preview_action.
    return {**controls_policy, "tts_client": {}, "speech_client": {}} if native else controls_policy


def validate_policy(value: Any, controls_policy: dict[str, Any], model: str,
                    native: bool) -> dict[str, Any]:
    """Validate private job declarations; permissions are rechecked live at every use."""
    try:
        if value == {}:
            return {}
        rt._require(rt._keys(value, set(), {"presets", "siren"}))
        policy = _parameter_policy(controls_policy, native)
        presets = value.get("presets", {})
        rt._require(type(presets) is dict and len(presets) <= MAX_PRESETS)
        result: dict[str, Any] = {"presets": {}}
        for alias, steps in presets.items():
            rt._require(rt._match(rt.ALIAS, alias) and type(steps) is list
                        and 1 <= len(steps) <= MAX_STEPS)
            result["presets"][alias] = [_step(item, policy, model, native) for item in steps]
        if "siren" in value:
            siren = value["siren"]
            rt._require(rt._keys(siren, {"asset"}, {"volume", "led"}))
            ceiling = controls_policy.get("max_volume", 30)
            media = controls_policy.get("local_media" if native else "media", {}).get(
                siren["asset"])
            volume = siren.get("volume", min(SIREN_DEFAULT_VOLUME, ceiling))
            # The siren may only play the tone generated above, never an arbitrary clip.
            rt._require(type(media) is dict and rt._integer(volume, 1, ceiling) and (
                media["sha256"] == NATIVE_SIREN_WAV_SHA256 if native
                else media["path"].endswith(".wav") and media["sha256"] == SIREN_WAV_SHA256))
            result["siren"] = {"asset": siren["asset"], "volume": volume}
            if "led" in siren:
                result["siren"]["led"] = _step(siren["led"], policy, model, native)
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("invalid_jobs") from None


Steps = tuple[tuple[str, dict[str, Any]], ...]


def _compile(station: Station, kind: Any, params: Any) -> tuple[str, Steps, int, bool]:
    try:
        presets, siren = station.jobs.get("presets", {}), station.jobs.get("siren", {})
        if kind in {"timer", "preset"}:
            rt._require(rt._keys(params, {"preset"} | ({"seconds"} if kind == "timer" else set())))
            rt._require(rt._match(rt.ALIAS, params["preset"]) and params["preset"] in presets)
            delay = params.get("seconds", 0)
            rt._require(kind == "preset" or rt._integer(delay, *TIMER_SECONDS))
            steps = tuple((s["action"], s["parameters"]) for s in presets[params["preset"]])
            return params["preset"], steps, delay, False
        rt._require(kind == "siren" and bool(siren)
                    and rt._keys(params, {"mode"}, {"repeats"}))
        mode, repeats = params["mode"], params.get("repeats", 3)
        rt._require(mode in {"full", "sound_only"} and rt._integer(repeats, 1, SIREN_REPEATS)
                    or mode == "test" and "repeats" not in params)
        cycle = [("audio.play", {"asset": siren["asset"], "duration_seconds": SIREN_PLAY_SECONDS})]
        if "led" in siren and mode != "sound_only":
            cycle.append((siren["led"]["action"], siren["led"]["parameters"]))
        steps_list = [("audio.volume", {"percent": siren["volume"]})]
        if mode == "full":
            steps_list.append(("speech.say", {"text": SIREN_TEXT}))
        steps_list += cycle * (1 if mode == "test" else repeats)
        return mode, tuple(steps_list), 0, True
    except (ValueError, TypeError, KeyError):
        raise SafetyError("Invalid, undeclared or unbounded job parameters") from None


def _check(controller: Controller, station: Station, steps: Steps, restore: bool) -> None:
    """Background steps cannot show a form: every action must be allowed and delegated now."""
    if not controller.writes or os.name == "nt":
        raise SafetyError("Device writes disabled or POSIX trust checks unavailable")
    allowed = rt.allowed_actions(station.controls)
    delegated = (allowed if station.preauthorize_all_actions else
                 [] if station.native_mcp else rt.preauthorized_actions(station.controls))
    # Native volume is restored by setting the reported previous level, not a backup record.
    restore_action = {"restore"} if restore and not station.native_mcp else set()
    actions = {action for action, _ in steps} | restore_action
    if any(action not in allowed or action not in delegated for action in actions):
        raise SafetyError("Job steps need allowed actions with standing preauthorization")
    try:
        policy = _parameter_policy(station.controls, bool(station.native_mcp))
        for action, params in steps:
            _validate(action, params, policy, station.model, bool(station.native_mcp))
    except (ValueError, TypeError, KeyError):
        raise SafetyError("A job step is not supported by this station policy") from None


@dataclass
class Job:
    id: str
    station: Station
    kind: str
    name: str
    steps: Steps
    fire_at: float
    restore_volume: bool
    cancel: threading.Event = field(default_factory=threading.Event)
    state: str = "scheduled"
    outcomes: list[str] = field(default_factory=list)
    backup_id: str = ""
    previous_percent: int | None = None
    volume_restored: bool = False
    thread: threading.Thread | None = None

    def report(self, now: float) -> dict[str, Any]:
        data: dict[str, Any] = {
            "job_id": self.id, "station_id": self.station.id, "kind": self.kind,
            "name": self.name, "state": self.state, "steps_total": len(self.steps),
            "step_outcomes": list(self.outcomes), "survives_host_restart": False,
            "physical_effects_verified": False,
        }
        if self.state == "scheduled":
            data["remaining_seconds"] = math.ceil(max(0.0, self.fire_at - now))
        if self.restore_volume:
            data["volume_restored"] = self.volume_restored
            if self.backup_id:
                data["volume_backup_id"] = self.backup_id
        if self.state == "unknown":
            data["do_not_retry"] = True
        return data


def _attempt(controller: Controller, job: Job, action: str, params: dict[str, Any]) -> str:
    """One fresh preview consumed once; SafetyError always precedes any device dispatch."""
    try:
        station = controller.station(job.station.id)
        if station != job.station:
            return "blocked"
        if station.native_mcp:
            from alice_mcp import native_transport

            result = native_transport.run_action(controller, station, action, params)
        else:
            preview = controls.preview(controller, station.id, action, params)
            plan = controller.take_plan(preview["plan_id"], preview["expected_sha256"])
            if preview["approval_required"]:
                return "blocked"
            result = controller.execute_plan(plan)
    except SafetyError:
        return "blocked"
    except Exception:
        return "unknown"
    if result.get("outcome") in {"blocked", "conflict", "cancelled"}:
        return "blocked"
    if result.get("outcome") != "applied":
        return "unknown"
    if job.restore_volume and action == "audio.volume" and station.native_mcp:
        if job.previous_percent is None:
            if not rt._integer(result.get("previous_percent"), 0, 100):
                return "unknown"  # No verified level: playing would leave volume unrestorable.
            job.previous_percent = result["previous_percent"]
    elif job.restore_volume and action == "audio.volume":
        if not rt._match(rt.HEX32, result.get("backup_id")):
            return "unknown"  # No verified record: playing would leave volume unrestorable.
        job.backup_id = result["backup_id"]
    return "applied"


def _run(controller: Controller, job: Job) -> None:
    # Re-reading the injected clock lets a host suspend delay, never shorten, the wait.
    while not job.cancel.is_set() and (left := job.fire_at - controller.clock()) > 0:
        job.cancel.wait(min(left, 0.5))
    if job.cancel.is_set():
        job.state = "cancelled"
        return
    job.state = "running"
    state = "complete"
    for action, params in job.steps:
        if job.cancel.is_set():
            state = "cancelled"
            break
        outcome = _attempt(controller, job, action, params)
        job.outcomes.append(outcome)
        if outcome != "applied":
            state = outcome  # blocked or unknown; never retried, remaining steps skipped.
            break
    if job.backup_id or job.previous_percent is not None:
        # A previous level above the private ceiling is refused, never claimed as restored.
        restored = (_attempt(controller, job, "restore", {"backup_id": job.backup_id})
                    if job.backup_id else
                    _attempt(controller, job, "audio.volume", {"percent": job.previous_percent}))
        job.volume_restored = restored == "applied"
        if restored == "unknown":
            state = "unknown"
    job.state = state


def _cancel(controller: Controller, selector: Callable[[Job], bool]) -> dict[str, Any]:
    with _LOCK:
        chosen = [job for job in controller.jobs.values()
                  if job.state in UNFINISHED and selector(job)]
        for job in chosen:
            job.cancel.set()
    return {"cancel_requested": [job.id for job in chosen], "all_sound_stopped": False,
            "scope": "unfinished jobs started by this MCP process",
            "in_flight_step": "a running bounded step finishes; siren volume restore still runs",
            "evidence_class": "analytical"}


def start(controller: Controller, station_id: str, kind: Any, parameters: Any) -> dict[str, Any]:
    station = controller.station(station_id)
    if kind == "siren" and parameters == {"mode": "off"}:
        return _cancel(controller, lambda job: job.station.id == station.id and job.restore_volume)
    name, steps, delay, restore = _compile(station, kind, parameters)
    _check(controller, station, steps, restore)
    with _LOCK:
        jobs = controller.jobs
        active = [job for job in jobs.values() if job.state in UNFINISHED]
        if len(active) >= MAX_ACTIVE:
            raise SafetyError("Too many owned jobs; cancel one first")
        if restore and any(job.station.id == station.id and job.restore_volume for job in active):
            raise SafetyError("A siren job is already active on this station")
        finished = [key for key, job in jobs.items() if job.state not in UNFINISHED]
        for key in finished[:max(0, len(jobs) + 1 - MAX_RETAINED)]:
            del jobs[key]
        job = Job(secrets.token_hex(16), station, kind, name, steps,
                  controller.clock() + delay, restore)
        job.thread = threading.Thread(target=_run, args=(controller, job), daemon=True)
        jobs[job.id] = job
        report = job.report(controller.clock())
    job.thread.start()
    return {"outcome": "scheduled", **report, "evidence_class": "analytical"}


def list_jobs(controller: Controller) -> dict[str, Any]:
    now = controller.clock()
    with _LOCK:
        reports = [job.report(now) for job in controller.jobs.values()]
    return {"jobs": reports, "scope": "jobs started by this MCP process",
            "survives_host_restart": False, "evidence_class": "analytical"}


def cancel(controller: Controller, job_id: str) -> dict[str, Any]:
    if job_id == "all":
        return _cancel(controller, lambda job: True)
    if not rt._match(rt.HEX32, job_id) or job_id not in controller.jobs:
        raise SafetyError("Unknown owned job id")
    return _cancel(controller, lambda job: job.id == job_id)


def preview(controller: Controller, station_id: str, kind: str,
            parameters: dict[str, Any]) -> dict[str, Any]:
    """Read-only: compile, check delegation and preview each distinct step; consume them all."""
    station = controller.station(station_id)
    name, steps, delay, restore = _compile(station, kind, parameters)
    _check(controller, station, steps, restore)
    previews, seen = [], set()
    for action, params in steps:
        key = json.dumps([action, params], sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        try:
            if station.native_mcp:
                from alice_mcp import native_transport

                plan: dict[str, Any] = native_transport.preview_action(controller, station,
                                                                       action, params)
            else:
                plan = controls.preview(controller, station.id, action, params)
            controller.take_plan(plan["plan_id"], plan["expected_sha256"])
            previews.append({"action": action, "outcome": "ready"})
        except SafetyError:
            previews.append({"action": action, "outcome": "blocked"})
    return {"outcome": "preview", "kind": kind, "name": name, "delay_seconds": delay,
            "steps": [action for action, _ in steps] + (["restore"] if restore else []),
            "step_previews": previews, "survives_host_restart": False,
            "physical_effects_verified": False}


# Reboot: a separate, explicit disruptive action through the service preview/apply tools.


def _active_on(controller: Controller, station: Station) -> bool:
    with _LOCK:
        return any(job.station.id == station.id and job.state in UNFINISHED
                   for job in controller.jobs.values())


def _reboot_state(controller: Controller, station: Station) -> str:
    if station.native_mcp:
        raise SafetyError("Reboot is not implemented by the resident agent; no SSH fallback")
    if not controller.writes or not station.allow_reboot or os.name == "nt":
        raise SafetyError("Reboot disabled by the separate private disruptive-action policy")
    if station.model not in BUILDROOT_MODELS or "firmware_sha256" not in station.controls:
        raise SafetyError("A locally reviewed Buildroot firmware digest is required")
    if _active_on(controller, station):
        raise SafetyError("Cancel this station's owned jobs before a reboot")
    fields = _fields(controller.runner(station, REBOOT_INSPECT))
    boot = fields.get("boot_id", "")
    if not rt._match(BOOT_ID, boot) or fields.get("reboot_tool") != "1":
        raise SafetyError("Reboot state unavailable or incompatible")
    if fields.get("firmware") != station.controls["firmware_sha256"]:
        raise SafetyError("Firmware differs from the locally reviewed digest")
    return boot


def _reboot_digest(station: Station, boot: str) -> str:
    return hashlib.sha256(repr((station, "reboot", boot)).encode()).hexdigest()


def preview_reboot(controller: Controller, station_id: str) -> dict[str, Any]:
    station = controller.station(station_id)
    digest = _reboot_digest(station, _reboot_state(controller, station))
    plan_id = controller.store_plan(station, "reboot", digest)
    return {"plan_id": plan_id, "expected_sha256": digest, "station_id": station.id,
            "action": "reboot", "expires_in_seconds": 300,
            "approval_required": not station.preauthorize_all_actions, "impact": REBOOT_IMPACT}


def execute_reboot(controller: Controller, plan: Plan) -> dict[str, Any]:
    station = controller.station(plan.station.id)
    if station != plan.station:
        raise SafetyError("Station or private configuration changed; create a new preview")
    boot = _reboot_state(controller, station)
    if _reboot_digest(station, boot) != plan.digest:
        raise SafetyError("Station state changed; create a new preview")
    if controller.clock() >= plan.expires:
        raise SafetyError("Preview expired during state validation")
    command = REBOOT_APPLY.format(boot=boot, firmware=station.controls["firmware_sha256"])
    try:
        outcome = _fields(controller.runner(station, command)).get("outcome")
    except SafetyError:
        outcome = None
    result = {"outcome": "requested" if outcome == "requested" else "unknown",
              "reboot_verified": False, "do_not_retry": True, "next_step": REBOOT_NEXT_STEP,
              "evidence_class": "analytical"}
    if outcome == "conflict":
        result.update(outcome="conflict", next_step="Device state changed; nothing dispatched")
    return result


# Owner-run acceptance: preview by default; --live needs a terminal and one typed word.


def _ask(prompt: str) -> str:
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return input()


def _observed(question: str) -> bool | None:
    answer = _ask(question + " Type yes or no: ").strip().lower()
    return {"yes": True, "no": False}.get(answer)


def _wait(controller: Controller, job_id: str, seconds: float) -> dict[str, Any]:
    thread = controller.jobs[job_id].thread
    if thread is not None:
        thread.join(seconds)
    return next(r for r in list_jobs(controller)["jobs"] if r["job_id"] == job_id)


def _acceptance(controller: Controller, args: argparse.Namespace) -> dict[str, Any]:
    word = {"timer": "TIMER", "preset": "PRESET", "siren": "SIREN", "tone": "UPLOAD",
            "reboot": "REBOOT"}[args.feature]
    if args.feature == "reboot":
        plan = controller.preview_service(args.station, "reboot")
        miss = confirm(_ask(f"{plan['impact']}\nType {word} to reboot once: "),
                       word) if args.live else None
        if not args.live or miss:
            controller.take_plan(plan["plan_id"], plan["expected_sha256"])
            return {"outcome": "preview" if not args.live else "not_confirmed",
                    "action": "reboot", "impact": plan["impact"], **(miss or {})}
        started = time.monotonic()
        result = controller.execute_plan(controller.take_plan(plan["plan_id"],
                                                              plan["expected_sha256"]))
        if result["outcome"] != "requested":
            return result  # Uncertain or conflicting: never re-sent.
        _ask("When the station has booted, press Enter for one read-only check: ")
        elapsed = time.monotonic() - started
        probes: dict[str, Any] = {}
        for name in ("health", "reboot"):
            try:
                probes[name] = controller.probe(args.station, name)["data"]
            except SafetyError as error:
                probes[name] = {"error": str(error)}
        uptime = probes["health"].get("uptime_seconds")
        return {**result, "probes": probes,
                "reboot_observed": None if uptime is None else uptime < elapsed}
    if args.feature == "tone":
        station = controller.station(args.station)
        if "siren" not in station.jobs:
            raise SafetyError("No private siren declaration for this station")
        if station.native_mcp:
            # Native media is a host-side registered file; nothing is uploaded to the station.
            if not args.out or not Path(args.out).is_absolute():
                raise SafetyError("Native tone needs an absolute private --out path")
            data = siren_wav(16000)
            miss = confirm(_ask(f"Write the 16 kHz tone to {args.out}. "
                                f"Type {word} to write once: "), word) if args.live else None
            if not args.live or miss:
                return {"outcome": "preview" if not args.live else "not_confirmed",
                        "sha256": NATIVE_SIREN_WAV_SHA256, **(miss or {})}
            try:
                fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except OSError:
                raise SafetyError("Tone file exists or is unavailable; nothing replaced") from None
            with os.fdopen(fd, "wb") as target:
                target.write(data)
            return {"outcome": "written", "sha256": NATIVE_SIREN_WAV_SHA256,
                    "next_step": "Register this file and digest under private local_media"}
        upload = controls.preview(controller, args.station, "media.upload", {
            "asset": station.jobs["siren"]["asset"],
            "content_base64": base64.b64encode(siren_wav()).decode("ascii"),
        })
        miss = confirm(_ask(f"{upload['impact']}\nType {word} to upload once: "),
                       word) if args.live else None
        if not args.live or miss:
            controller.take_plan(upload["plan_id"], upload["expected_sha256"])
            return {"outcome": "preview" if not args.live else "not_confirmed",
                    "impact": upload["impact"], "tone_sha256": SIREN_WAV_SHA256, **(miss or {})}
        return controller.execute_plan(controller.take_plan(upload["plan_id"],
                                                            upload["expected_sha256"]))
    params: dict[str, Any] = {"siren": {"mode": args.mode}, "preset": {"preset": args.preset},
                              "timer": {"preset": args.preset, "seconds": args.seconds}}[
        args.feature]
    if args.repeats is not None:
        params["repeats"] = args.repeats
    planned = preview(controller, args.station, args.feature, params)
    if not args.live:
        return planned
    if miss := confirm(_ask(json.dumps(planned, ensure_ascii=False)
                            + f"\nType {word} to run once: "), word):
        return {"outcome": "not_confirmed", **miss}
    job = start(controller, args.station, args.feature, params)
    bound = planned["delay_seconds"] + 120 * (len(planned["steps"]) + 1)
    if args.feature == "timer":
        decoy = start(controller, args.station, "timer", params)
        cancel(controller, decoy["job_id"])
        report = _wait(controller, job["job_id"], bound)
        result = {"timer": report, "cancelled_timer": _wait(controller, decoy["job_id"], 5)}
        question = (f"Did exactly ONE signal occur about {args.seconds} s after confirmation "
                    "(the second timer was cancelled by its id)?")
    else:
        result = _wait(controller, job["job_id"], bound)
        question = ("Did you hear the generated siren at low volume, and is the volume back "
                    "to its previous level?" if args.feature == "siren" else
                    "Did exactly the declared preset effects occur once?")
    return {**result, "owner_observed": _observed(question), "do_not_retry": True}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Owner-run acceptance for owned jobs")
    parser.add_argument("feature", choices=["timer", "preset", "siren", "tone", "reboot"])
    parser.add_argument("--station", required=True, help="Private station alias")
    parser.add_argument("--preset", help="Declared private preset alias")
    parser.add_argument("--seconds", type=int, help="Timer delay")
    parser.add_argument("--mode", default="test", choices=["test", "sound_only", "full"])
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--out", help="Native tone only: new private file for local_media")
    parser.add_argument("--live", action="store_true", help="Run once after a typed word")
    args = parser.parse_args(argv)
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
    if args.live and not sys.stdin.isatty():
        parser.error("Run --live in a terminal beside the station, not with piped answers")
    controller = Controller(Path(inventory),
                            writes=os.environ.get("ALICE_MCP_ALLOW_WRITES") == "1")
    try:
        report = _acceptance(controller, args)
    except SafetyError as error:
        report = {"outcome": "blocked", "error": str(error)}
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


if __name__ == "__main__":
    main()
