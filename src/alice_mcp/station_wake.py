"""Bounded native wake windows over the existing TLS transport; no SSH or model training."""

from __future__ import annotations

import hashlib
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp import io_channel_pilot, native_transport
from alice_mcp.core import Controller, SafetyError


def result(value: dict[str, Any]) -> dict[str, Any]:
    fields = {"outcome", "events", "trusted", "do_not_retry", "audio_exported",
              "quasar_unchanged", "cleanup_verified"}
    if value.get("outcome") == "blocked" and set(value) <= {"outcome", "error"}:
        return {"outcome": "blocked", "events": [], "do_not_retry": True}
    if (set(value) != fields or value["outcome"] not in ("complete", "unknown")
        or value["trusted"] is not False or value["audio_exported"] is not False
        or value["do_not_retry"] is not True
        or any(type(value[k]) is not bool for k in ("quasar_unchanged", "cleanup_verified"))
        or type(value["events"]) is not list or len(value["events"]) > 64
        or value["outcome"] == "complete" and not (
            value["cleanup_verified"] and value["quasar_unchanged"])
        or value["outcome"] == "unknown" and value["events"]):
        raise SafetyError("Native wake result unverified; do not retry")
    last = 0
    for event in value["events"]:
        if (type(event) is not dict
            or set(event) != {"model", "t_ms", "probability", "trusted"}
            or event["model"] != "komp" or event["trusted"] is not False
            or not rt._integer(event["t_ms"], last + 1, 20000)
            or type(event["probability"]) not in (float, int)
            or not 0.98 <= event["probability"] <= 1):
            raise SafetyError("Native wake event incompatible; do not retry")
        last = event["t_ms"]
    return value


def listen_once(controller: Controller, station_id: str) -> dict[str, Any]:
    """Fresh consumed-once host and resident state for one 20 s window; never retries."""
    with controller._execution_lock:
        station = controller.station(station_id)
        io_channel_pilot._permitted(controller, station)
        if (not station.native_mcp
            or station.controls.get("voice", {}).get("source") != "station"):
            raise SafetyError("Station wake source requires the resident TLS transport")
        controls = native_transport.call(station, "get_station_controls",
                                         {"station_id": station.id})
        state = controls.get("state_sha256")
        if (controls.get("station_id") != station.id or controls.get("model") != station.model
            or controls.get("state_contract") != "alice.native.state.v1"
            or controls.get("independent_wake_word") is not True
            or controls.get("preauthorize_all_actions") is not True
            or "wake.listen" not in controls.get("allowed_actions", [])
            or not rt._match(rt.HEX64, state)):
            raise SafetyError("Resident wake capability unavailable; no fallback")
        digest = hashlib.sha256(repr((station, state)).encode()).hexdigest()
        plan_id = controller.store_plan(station, "wake.native", digest)
        plan = controller.take_plan(plan_id, digest)
        io_channel_pilot._permitted(controller, station)
        if controller.station(station.id) != station or controller.clock() >= plan.expires:
            raise SafetyError("Station wake policy changed or preview expired")
        try:
            return result(native_transport.call(station, "run_station_action", {
                "station_id": station.id, "action": "wake.listen",
                "expected_state_sha256": state, "parameters": {"duration_seconds": 20}}))
        except Exception:
            return {"outcome": "unknown", "events": [], "do_not_retry": True}
