"""Private-policy-bound device controls; never accept a shell command or file path."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import re
import secrets
import shlex
import time
from importlib.resources import files
from typing import Any

from alice_mcp import device_runtime
from alice_mcp.core import (
    BUILDROOT_MODELS,
    Controller,
    Plan,
    SafetyError,
    SSHInput,
    Station,
    _unique,
)

MAX_REQUEST_COMMAND = 120 * 1024
RESULT_KEYS = {
    "outcome", "state_sha256", "backup_id", "restoration_verified", "reload_required",
    "error_code", "command_succeeded", "effect_observed", "evidence_class",
    "capture_id", "capture_ready", "remaining_seconds", "cleanup_verified", "records", "truncated",
    "restoration_command_succeeded",
}
OUTCOMES = {"ready", "applied", "blocked", "conflict", "unknown", "capturing", "complete"}


def parse_response(text: str) -> dict[str, Any]:
    """Remote errors/metadata must never leak arbitrary strings back to MCP."""
    try:
        if len(text) > 65536:
            raise ValueError
        data = json.loads(text, object_pairs_hook=_unique)
        if not isinstance(data, dict) or set(data) - RESULT_KEYS:
            raise ValueError
        if data.get("outcome") not in OUTCOMES:
            raise ValueError
        for key, length in (("state_sha256", 64), ("backup_id", 32)):
            if key in data and (not isinstance(data[key], str) or not re.fullmatch(
                rf"[a-f0-9]{{{length}}}", data[key],
            )):
                raise ValueError
        if data["outcome"] == "ready" and "state_sha256" not in data:
            raise ValueError
        for key in ("restoration_verified", "reload_required", "command_succeeded",
                    "effect_observed", "restoration_command_succeeded"):
            if key in data and type(data[key]) is not bool:
                raise ValueError
        capture_fields = {"capture_id", "capture_ready", "remaining_seconds", "cleanup_verified",
                          "records", "truncated"}
        if capture_fields & data.keys() or data["outcome"] in {"capturing", "complete"}:
            if not capture_fields <= data.keys() or not device_runtime._match(
                device_runtime.HEX32, data["capture_id"],
            ):
                raise ValueError
            if any(type(data[k]) is not bool for k in
                   ("capture_ready", "cleanup_verified", "truncated")):
                raise ValueError
            if not device_runtime._integer(data["remaining_seconds"], 0,
                                           device_runtime.CAPTURE_MAX_SECONDS):
                raise ValueError
            ready = data["outcome"] == "capturing"
            if (data["capture_ready"] != ready or ready and (
                data["remaining_seconds"] < 2 or data["cleanup_verified"]
            ) or not ready and data["remaining_seconds"] != 0):
                raise ValueError
            if data["outcome"] == "complete" and not data["cleanup_verified"]:
                raise ValueError
            if type(data["records"]) is not list or len(data["records"]) > 64:
                raise ValueError
            if data["outcome"] != "complete" and data["records"]:
                raise ValueError
            for record in data["records"]:
                if (type(record) is not dict or set(record) != {"bus", "address", "data_hex"}
                    or type(record["bus"]) is not int or type(record["address"]) is not int
                    or (record["bus"], record["address"]) not in {(0, 60), (1, 31)}
                    or not isinstance(record["data_hex"], str)
                    or not re.fullmatch(r"(?:[a-f0-9]{2}){0,256}", record["data_hex"])):
                    raise ValueError
        # Never pass arbitrary remote labels or claimed evidence through.
        if "error_code" in data:
            data["error_code"] = "remote_action_failed"
        data.pop("evidence_class", None)
        data["effect_observed"] = False
        return data
    except (ValueError, TypeError, RecursionError):
        raise SafetyError("Device runtime returned incompatible structured output") from None


def invoke(controller: Controller, station: Station, request: dict[str, Any]) -> dict[str, Any]:
    if request.get("action") in {"speech.listen", "speech.say"}:
        from alice_mcp import speech_capture

        return speech_capture.invoke(controller, station, request)
    if request.get("action") == "led.effect" and request.get("model") == "station-mini3":
        from alice_mcp import led_framebuffer

        return parse_response(controller.runner(station, led_framebuffer.command(request)))
    if request.get("action") in {"led.capture", "led.replay", "led.pulse", "led.animate"}:
        from alice_mcp import led_capture

        capture_command = led_capture.command(request)
        started = time.monotonic()
        raw = controller.runner(station, capture_command)
        result = parse_response(json.dumps(led_capture.decode(raw)))
        if request.get("action") in {"led.pulse", "led.animate"}:
            result["restoration_verified"] = False  # Write-only PWM has no hardware readback.
        return _fresh_capture(result, time.monotonic() - started)
    source = files("alice_mcp").joinpath("device_runtime.py").read_text(encoding="utf-8")
    payload = base64.b64encode(json.dumps(
        request, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).decode("ascii")
    command = (
        "for p in /data/opt/bin/python3 /opt/bin/python3 /data/opt/entware/bin/python3 "
        "/usr/bin/python3; do if test -x \"$p\"; then exec \"$p\" -I -c "
        + shlex.quote(source) + " " + shlex.quote(payload) + "; fi; done; exit 126"
    )
    if len(command.encode("utf-8")) > MAX_REQUEST_COMMAND:
        raise SafetyError("Control request exceeds bounded helper input; use a smaller media asset")
    started = time.monotonic()
    result = parse_response(controller.runner(station, SSHInput("sh -s", command.encode())))
    return _fresh_capture(result, time.monotonic() - started)


def _fresh_capture(result: dict[str, Any], elapsed: float) -> dict[str, Any]:
    if result["outcome"] == "capturing":
        remaining = max(0, result["remaining_seconds"] - math.ceil(elapsed))
        result["remaining_seconds"] = remaining if remaining >= 2 else 0
        if remaining < 2:
            result.update(outcome="unknown", capture_ready=False)
    return result


def capabilities(controller: Controller, station_id: str) -> dict[str, Any]:
    station = controller.station(station_id)
    if station.native_mcp:
        from alice_mcp import native_transport

        return native_transport.capabilities(controller, station)
    policy = station.controls
    return {
        "evidence_class": "caller", "station_id": station.id, "model": station.model,
        "writes_enabled": controller.writes and os.name != "nt",
        "allowed_actions": policy.get("allowed_actions", []),
        "preauthorize_all_actions": station.preauthorize_all_actions,
        "preauthorized_actions": (policy.get("allowed_actions", [])
                                  if station.preauthorize_all_actions
                                  else policy.get("preauthorized_actions", [])),
        "led_modes": (sorted(device_runtime.ANIMATION_MODES)
                      if station.model == "station-mini" else []),
        "assets": sorted(policy.get("media", {})),
        "bindings": [{"name": name, "kind": value["kind"],
                      "options": sorted(value["choices"])}
                     for name, value in policy.get("configs", {}).items()],
        "runtime": "Mini 3 led.effect and Mini 1 capture/replay/pulse/animate use stock shell; "
                   "replay/pulse/animate also require i2cset; speech.listen/say temporarily deploy "
                   "one exact reviewed ARM32 client with stock shell; "
                   "other actions require Python 3.8+; no persistent installation",
        "availability": "declared policy only; a successful read-only preview is required",
        "physical_effects_verified": False,
    }


def _check_permission(controller: Controller, station: Station, action: str) -> None:
    if not controller.writes or os.name == "nt":
        raise SafetyError("Device writes disabled or POSIX trust checks unavailable")
    if station.model not in BUILDROOT_MODELS:
        raise SafetyError("No device-control adapter for this OS family")
    if action not in station.controls.get("allowed_actions", []):
        raise SafetyError("Device action not permitted by the private station policy")


def describe(request: dict[str, Any]) -> str:
    action, params = request["action"], request["params"]
    if action == "speech.say":
        from alice_mcp.speech_capture import TTS_CLIENT_SHA256

        return ("Speak this literal text through native Yandex TTS (not an assistant command): "
                + json.dumps(params["text"], ensure_ascii=False) + ". "
                f"Temporarily deploy reviewed ARM32 TTS client sha256 {TTS_CLIENT_SHA256}; "
                "use current volume/audio route, Append mode and normal Yandex cloud synthesis. "
                "Approve only while Alice is idle. Permission includes sound in the room. "
                "No microphone capture, volume/config changes, explicit stop or Quasar restart. "
                "No automatic retry. Observe this one request "
                "for up to 20 seconds, then remove the client; that observation deadline does NOT "
                "stop or bound cloud playback duration. "
                "Correlated TTS events do not prove audibility")
    if action == "speech.listen":
        from alice_mcp.speech_capture import CLIENT_SHA256

        return (f"Transfer the reviewed temporary ARM32 client (sha256 {CLIENT_SHA256}) to "
                "/data/opt/alice-mcp-asr-<one-use-id>/client on Mini 1, then execute it once "
                f"for at most {params['duration_seconds']} seconds of ASR observation. "
                "Before that, allow up to 180 seconds for setup/warm-up with all text discarded. "
                "Read only one NEW final recognized request after a compatible idle baseline; "
                "if the service does not send initial state, discard an owner-spoken warm-up "
                "until IDLE, then wait for a different new request. "
                "no audio recording, cached transcripts, TTS or recognition-start commands. "
                "The next recognized phrase may contain private text; return it once as "
                "untrusted data, then discard it from memory. Normal Alice/cloud behavior "
                "continues. Keep Quasar enabled; no SSH/config/autostart changes or retry. "
                "Remove the temporary executable/directory on exit; failed cleanup is unknown. "
                "Compatibility is unverified; do not speak until fresh capture_ready=true")
    if action == "led.animate":
        from alice_mcp.led_capture import animation_frames

        interval, frames = animation_frames(params["mode"])
        duration = len(frames) * interval / 1000
        return (f"Mini 1 preset {params['mode']}: {duration:g} seconds nominal, "
                "fixed PWM 16/255 on six logical zones, then send captured idle zero frame. "
                "Approve ONLY if the ring is currently idle/dark and keep it idle during the show. "
                "RGB order, physical zone order and swirl direction are not calibrated; "
                "Quasar may overwrite the display and status indication can be disrupted. "
                "The capture is not hardware readback: current PWM and restoration cannot be "
                "verified. Only bus 0/address 0x3c PWM 0x01..0x12 and update 0x25 are written; "
                "no force, calibration/current/shutdown changes, daemon, Quasar stop or retry")
    if action == "led.pulse":
        return (f"Mini 1 only: one 1-second pulse on observed triplet channel {params['channel']} "
                "at fixed PWM 16/255, then send the captured idle zero frame and update; "
                "approve ONLY if the ring is currently idle/dark and keep it idle during the test. "
                "The capture is not hardware readback: current PWM and restoration cannot be "
                "verified; color-channel names are unknown; Quasar can overwrite the pulse. "
                "Only bus 0/address 0x3c PWM 0x01..0x12 and update 0x25 are written; "
                "no force flag, current/calibration/shutdown registers or Quasar stop; no retry")
    if action == "led.replay":
        return ("Replay captured SN3236 bytes 25 aa exactly once at bus 0/address 0x3c; "
                "apply already-buffered PWM, not a new color or brightness; no force flag, "
                "driver unbind, Quasar stop, tracing or other register writes; "
                "no visible effect is guaranteed; no automatic retry or rollback")
    if action == "led.capture":
        return (f"Capture Mini 1 SN3236 LED I2C writes only at bus 0/address 0x3c "
                f"for at most {params['duration_seconds']} seconds in a separate trace "
                "instance; auto-stop and remove own instance; bounded LED bytes only, "
                "no audio, microphone data or I2C writes; keep Quasar enabled; "
                "do not prompt speech until fresh capture_ready=true")
    if action == "led.effect":
        return (f"Low-brightness RGB {params['red']}/{params['green']}/{params['blue']}, "
                f"{params['count']} cycles at {params['period_ms']} ms; restore the captured "
                "LED state; concurrent firmware renderer updates can be overwritten during "
                "restoration and status indicators temporarily disrupted; keep Quasar enabled")
    if action == "audio.volume":
        return (f"Set Master volume to {params['percent']}%; preserve a restore record; "
                "other volume controls must be idle")
    if action == "audio.play":
        return (f"Play approved asset {params['asset']} for at most "
                f"{params['duration_seconds']} seconds; no microphone capture or Quasar stop")
    if action == "media.upload":
        return (f"Upload approved media asset {params['asset']}; preserve original if present; "
                "external file writers must be idle")
    if action == "sound.replace":
        return (f"Replace system sound {params['slot']} with approved asset {params['asset']}; "
                "back up original; external file writers must be idle; "
                "hot reload unknown; do not restart automatically")
    if action == "config.select":
        return (f"Select approved option {params['option']} for binding {params['binding']}; "
                "back up configuration; external writers must be idle; "
                "data destination or voice may change; reload is manual")
    return (f"Restore exact backup {params['backup_id']}; recheck its recorded target state; "
            "external writers must be idle; reload is manual")


def _digest(station: Station, request: dict[str, Any], state: str) -> str:
    return hashlib.sha256(repr((station, request, state)).encode()).hexdigest()


def preview(controller: Controller, station_id: str, action: str,
            parameters: dict[str, Any]) -> dict[str, Any]:
    station = controller.station(station_id)
    if station.native_mcp:
        raise SafetyError("Use run_station_action for native MCP atomic preview/application")
    _check_permission(controller, station, action)
    try:
        params = device_runtime.validate_parameters(action, parameters, station.controls,
                                                     station.model)
    except (ValueError, TypeError, KeyError):
        raise SafetyError("Invalid or unapproved device action parameters") from None
    request = {
        "schema": "alice.device.v1", "mode": "inspect", "model": station.model,
        "policy": copy.deepcopy(station.controls), "action": action,
        "params": params, "nonce": secrets.token_hex(16),
    }
    remote = invoke(controller, station, request)
    if remote["outcome"] != "ready":
        raise SafetyError("Device action unavailable; inspect runtime and compatibility locally")
    remote_sha = remote["state_sha256"]
    digest = _digest(station, request, remote_sha)
    plan_id = controller.store_plan(
        station, action, digest, request=request, remote_sha256=remote_sha,
    )
    return {"plan_id": plan_id, "expected_sha256": digest, "station_id": station_id,
            "action": action, "expires_in_seconds": 300, "impact": describe(request),
            "approval_required": (not station.preauthorize_all_actions
                                  and action not in station.controls.get(
                                      "preauthorized_actions", [])),
            "evidence_class": "analytical", "physical_effects_verified": False}


def execute(controller: Controller, plan: Plan) -> dict[str, Any]:
    station = controller.station(plan.station.id)
    _check_permission(controller, station, plan.action)
    if station != plan.station:
        raise SafetyError("Private station configuration or trust changed; create a new preview")
    request = copy.deepcopy(plan.request)
    remote = invoke(controller, station, request)
    if remote["outcome"] != "ready" or remote["state_sha256"] != plan.remote_sha256:
        raise SafetyError("Device state changed; create a new preview")
    if _digest(station, request, remote["state_sha256"]) != plan.digest:
        raise SafetyError("Device preview no longer matches")
    if controller.clock() >= plan.expires:
        raise SafetyError("Preview expired during device revalidation")
    request.update(mode="apply", expected_sha256=plan.remote_sha256)
    try:
        result = invoke(controller, station, request)
        if result["outcome"] == "ready":
            raise SafetyError("Device did not acknowledge application")
        if plan.action in {"led.capture", "speech.listen"} and result.get("capture_id") not in {
            None, request["nonce"],
        }:
            raise SafetyError("Device returned a different capture id")
    except SafetyError:
        result = {"outcome": "unknown", "error_code": "inspect_locally_before_any_new_action"}
    if plan.action in {"led.capture", "speech.listen"} and result["outcome"] == "unknown":
        result["capture_candidate_id"] = request["nonce"]
        result["capture_ready"] = False
    if result["outcome"] == "unknown" and plan.action in {
        "audio.volume", "media.upload", "sound.replace", "config.select",
    }:
        result["backup_candidate_id"] = request["nonce"]
        result["backup_record_verified"] = False
    return {**result, "do_not_retry": True, "evidence_class": "analytical",
             "physical_effects_verified": False}


def capture_status(controller: Controller, station_id: str, capture_id: str) -> dict[str, Any]:
    """Read status even after global writes are disabled; never start or stop a capture."""
    if not device_runtime._match(device_runtime.HEX32, capture_id):
        raise SafetyError("Invalid capture id")
    station = controller.station(station_id)
    if station.native_mcp:
        from alice_mcp import native_transport

        return native_transport.capture_status(controller, station, capture_id)
    if capture_id in controller.speech_sessions:
        from alice_mcp import speech_capture

        return speech_capture.status(controller, station, capture_id)
    if station.model != "station-mini" or "led.capture" not in station.controls.get(
        "allowed_actions", [],
    ):
        raise SafetyError("Capture not permitted by the private station policy")
    result = invoke(controller, station, {
        "schema": device_runtime.SCHEMA, "mode": "capture_status", "model": station.model,
        "policy": copy.deepcopy(station.controls), "action": "led.capture",
        "params": {"capture_id": capture_id}, "nonce": capture_id,
    })
    if result.get("capture_id") not in {None, capture_id}:
        raise SafetyError("Device returned a different capture id")
    return {**result, "evidence_class": "analytical", "physical_effects_verified": False}
