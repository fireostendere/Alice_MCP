"""Private-policy-bound device controls; never accept a shell command or file path."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import secrets
import shlex
from importlib.resources import files
from typing import Any

from alice_mcp import device_runtime
from alice_mcp.core import BUILDROOT_MODELS, Controller, Plan, SafetyError, Station, _unique

MAX_REQUEST_COMMAND = 120 * 1024
RESULT_KEYS = {
    "outcome", "state_sha256", "backup_id", "restoration_verified", "reload_required",
    "error_code", "command_succeeded", "effect_observed", "evidence_class",
}
OUTCOMES = {"ready", "applied", "blocked", "conflict", "unknown"}


def parse_response(text: str) -> dict[str, Any]:
    """Remote errors/metadata must never leak arbitrary strings back to MCP."""
    try:
        if len(text) > 4096:
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
                    "effect_observed"):
            if key in data and type(data[key]) is not bool:
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
        raise SafetyError("Control request exceeds SSH argument bound; use a smaller media asset")
    return parse_response(controller.runner(station, command))


def capabilities(controller: Controller, station_id: str) -> dict[str, Any]:
    station = controller.station(station_id)
    policy = station.controls
    return {
        "evidence_class": "caller", "station_id": station.id, "model": station.model,
        "writes_enabled": controller.writes and os.name != "nt",
        "allowed_actions": policy.get("allowed_actions", []),
        "assets": sorted(policy.get("media", {})),
        "bindings": [{"name": name, "kind": value["kind"],
                      "options": sorted(value["choices"])}
                     for name, value in policy.get("configs", {}).items()],
        "runtime": "preinstalled Python 3.8+ required; no automatic installation",
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
    except SafetyError:
        result = {"outcome": "unknown", "error_code": "inspect_locally_before_any_new_action"}
    if result["outcome"] == "unknown" and plan.action in {
        "audio.volume", "media.upload", "sound.replace", "config.select",
    }:
        result["backup_candidate_id"] = request["nonce"]
        result["backup_record_verified"] = False
    return {**result, "do_not_retry": True, "evidence_class": "analytical",
            "physical_effects_verified": False}
