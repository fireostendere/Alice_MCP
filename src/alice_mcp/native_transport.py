"""Fixed typed MCP client for an authenticated TLS agent; never falls back to SSH."""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import math
import re
import secrets
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alice_mcp import device_runtime as rt
from alice_mcp import native_typed
from alice_mcp.core import Controller, Plan, SafetyError, Station, _check_ssh_file

PROTOCOL = "2025-11-25"
TOOLS = {"get_capabilities", "get_station_controls", "station_probe", "run_station_action",
         "get_capture_status"}
# led.show: Mini 1 adapter only.
NATIVE_ACTIONS = {"speech.say", "speech.listen", "led.show"} | native_typed.ACTIONS


def validate_config(value: Any) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"url", "ca_file", "token_file"}:
        raise ValueError("invalid native transport")
    if not all(type(item) is str for item in value.values()):
        raise ValueError("invalid native transport")
    url = urlsplit(value["url"])
    if (url.scheme != "https" or not url.hostname or url.path != "/mcp"
        or url.username is not None or url.password is not None or url.query or url.fragment
        or any(c in value["url"] for c in "%\r\n\t ")
        or not 1 <= (url.port or 443) <= 65535):
        raise ValueError("invalid native URL")
    _check_ssh_file(Path(value["ca_file"]))
    _check_ssh_file(Path(value["token_file"]), private=True)
    return dict(value)


def call(station: Station, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One MCP session and one tool invocation. No retries, redirects or raw error output."""
    if tool not in TOOLS or not station.native_mcp:
        raise SafetyError("Native tool is not supported")
    connection: http.client.HTTPSConnection | None = None
    try:
        config = validate_config(station.native_mcp)
        with Path(config["token_file"]).open("rb") as source:
            credential = source.read(129).rstrip(b"\r\n")
        if len(credential) != 64 or not re.fullmatch(b"[a-f0-9]{64}", credential):
            raise ValueError("invalid native credential")
        context = ssl.create_default_context(cafile=config["ca_file"])
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        url = urlsplit(config["url"])
        connection = http.client.HTTPSConnection(url.hostname or "", url.port or 443,
                                                 context=context, timeout=35)
        bearer = "Bearer " + credential.decode("ascii")
        headers = {"Authorization": bearer,
                   "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL}

        def exchange(method: str, params: dict[str, Any], *, notification: bool = False) -> Any:
            identifier = secrets.token_hex(16)
            request: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
            if not notification:
                request["id"] = identifier
            data = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
            limit = 2600000 if tool == "run_station_action" else 16384
            if len(data) > limit:
                raise ValueError("native request too large")
            assert connection is not None
            connection.request("POST", "/mcp", data, headers)
            response = connection.getresponse()
            body = response.read(65537)
            if notification:
                if response.status != 202 or body:
                    raise ValueError("invalid native notification acknowledgement")
                return None
            if (response.status != 200 or len(body) > 65536
                or response.getheader("Content-Type", "").split(";", 1)[0] != "application/json"):
                raise ValueError("native response unavailable")
            value = rt._json(body)
            if (type(value) is not dict or set(value) != {"jsonrpc", "id", "result"}
                or value["jsonrpc"] != "2.0" or value["id"] != identifier):
                raise ValueError("invalid native RPC response")
            return value["result"]

        initialized = exchange("initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "alice-station-mcp-router", "version": "0.2.0"},
        })
        if (type(initialized) is not dict or initialized.get("protocolVersion") != PROTOCOL
            or initialized.get("serverInfo", {}).get("name") != "alice-station-agent"):
            raise ValueError("incompatible native agent")
        exchange("notifications/initialized", {}, notification=True)
        response = exchange("tools/call", {"name": tool, "arguments": arguments})
        if (type(response) is not dict or response.get("isError") is not False
            or type(response.get("structuredContent")) is not dict):
            raise ValueError("native structured result missing")
        return dict(response["structuredContent"])
    except Exception:
        raise SafetyError(
            "Native MCP channel unavailable or incompatible; no SSH fallback",
        ) from None
    finally:
        if connection is not None:
            connection.close()


def capabilities(controller: Controller, station: Station) -> dict[str, Any]:
    result = call(station, "get_station_controls", {"station_id": station.id})
    return _capabilities(controller, station, result)


def _capabilities(controller: Controller, station: Station,
                  result: dict[str, Any]) -> dict[str, Any]:
    actions = result.get("allowed_actions")
    preauthorized = result.get("preauthorized_actions")
    if (result.get("station_id") != station.id or result.get("model") != station.model
        or result.get("transport") != "streamable_http_tls"
        or type(actions) is not list
        or any(a not in NATIVE_ACTIONS for a in actions)
        or len(actions) != len(set(actions))
        or type(preauthorized) is not list or preauthorized not in ([], actions)
        or type(result.get("preauthorize_all_actions")) is not bool
        or type(result.get("writes_enabled")) is not bool
        or result.get("tts_backend", "yandex") not in {"yandex", "local_pcm"}):
        raise SafetyError("Native policy response unavailable or incompatible")
    permitted = [a for a in actions if rt.allows(station.controls, a)]
    return {
        "station_id": station.id, "model": station.model, "evidence_class": "caller",
        "writes_enabled": controller.writes and result["writes_enabled"],
        "allowed_actions": permitted,
        "preauthorize_all_actions": (station.preauthorize_all_actions
                                     and result["preauthorize_all_actions"]),
        "preauthorized_actions": ([a for a in preauthorized if a in permitted]
                                  if station.preauthorize_all_actions else []),
        "transport": "native_mcp", "led_modes": [], "bindings": [],
        "assets": sorted(station.controls.get("local_media", {})),
        "runtime": "Resident ARM32 MCP agent; no SSH or per-call executable transfer",
        "availability": "declared local policies; physical effects are not verified",
        "independent_wake_word": False, "physical_effects_verified": False,
        "tts_backend": result.get("tts_backend", "yandex"),
        "host_tts_backend": "local_pcm" if "local_tts" in station.controls else "yandex",
    }


def probe(station: Station, name: str) -> dict[str, Any]:
    if name not in {"health", "microphone"} | native_typed.PROBES:
        raise SafetyError("Probe is not implemented by the native agent")
    result = call(station, "station_probe", {"station_id": station.id, "probe": name})
    data = result.get("data")
    if type(data) is not dict:
        raise SafetyError("Native probe unavailable or incompatible")
    if name in native_typed.PROBES:
        return {"evidence_class": "analytical", "data": native_typed.probe_data(name, data)}
    if name == "health":
        fields = {"uptime_seconds", "memory_total_kib", "memory_available_kib", "kernel_arch"}
        if not fields <= data.keys() or data.keys() - fields - {"agent_rss_kib"}:
            raise SafetyError("Native health metrics incompatible")
        if (type(data["uptime_seconds"]) not in {int, float}
            or not math.isfinite(data["uptime_seconds"])
            or not 0 <= data["uptime_seconds"] <= 1e12
            or any(type(data[k]) is not int or not 0 <= data[k] < 1 << 40
                   for k in ("memory_total_kib", "memory_available_kib", "agent_rss_kib")
                   if k in data)
            or data["memory_available_kib"] > data["memory_total_kib"]
            or data["kernel_arch"] not in {"armv7l", "armv8l", "aarch64", "arm", "unknown"}):
            raise SafetyError("Native health metrics incompatible")
    else:
        flags = {"quasar_running", "vqe_channel_declared", "vqe_output_symbol_present",
                 "matches_documented_plum_library", "independent_capture_verified",
                 "audio_captured", "quasar_changed"}
        if (set(data) != flags | {"capture_state"}
            or any(type(data[k]) is not bool for k in flags)
            or data["capture_state"] not in {"unknown", "closed", "running", "prepared", "open"}
            or any(data[k] is not False for k in
                   ("audio_captured", "quasar_changed", "independent_capture_verified"))):
            raise SafetyError("Native microphone metadata incompatible")
    return {"evidence_class": "analytical", "data": dict(data)}


def run_action(controller: Controller, station: Station, action: str,
               parameters: dict[str, Any]) -> dict[str, Any]:
    preview = preview_action(controller, station, action, parameters)
    return controller.execute_plan(controller.take_plan(
        preview["plan_id"], preview["expected_sha256"],
    ))


def _permission(controller: Controller, station: Station, action: str) -> None:
    if (not controller.writes or not station.preauthorize_all_actions
        or action not in NATIVE_ACTIONS
        or not rt.allows(station.controls, action)):
        raise SafetyError("Native action is not preauthorized by the host policy")


def _state(controller: Controller, station: Station, action: str) -> str:
    # Read-only compatibility remains available, but old residents cannot write.
    try:
        raw = call(station, "get_station_controls", {"station_id": station.id})
    except Exception:
        raise SafetyError("Native preview state unavailable; no action dispatched") from None
    result = _capabilities(controller, station, raw)
    stamp = raw.get("state_sha256")
    backend = ("local_pcm" if action == "speech.say" and "local_tts" in station.controls
               else "yandex")
    if (not result["writes_enabled"] or not result["preauthorize_all_actions"]
        or action not in result["allowed_actions"]
        or raw.get("state_contract") != "alice.native.state.v1"
        or not rt._match(rt.HEX64, stamp)
        or action == "speech.say" and raw.get("tts_backend", "yandex") != backend
        or action == "audio.play" and raw.get("tts_backend") != "local_pcm"):
        raise SafetyError("Native state-bound action contract unavailable or policy changed")
    return str(stamp)


def _digest(station: Station, request: dict[str, Any], state: str) -> str:
    return hashlib.sha256(repr((station, request, state)).encode()).hexdigest()


def preview_action(controller: Controller, station: Station, action: str,
                   parameters: dict[str, Any]) -> dict[str, str]:
    _permission(controller, station, action)
    try:
        # The binary is owned/pinned on the device; text/model validation is shared.
        parameters = (native_typed.parameters(station, action, parameters)
                      if action in native_typed.ACTIONS else rt.validate_parameters(
                          action, parameters, {"tts_client": {}, "speech_client": {}},
                          station.model))
    except (ValueError, TypeError, KeyError):
        raise SafetyError("Invalid native action parameters") from None
    state = _state(controller, station, action)
    request = {"schema": "alice.native.v1", "action": action,
               "parameters": copy.deepcopy(parameters), "nonce": secrets.token_hex(16)}
    digest = _digest(station, request, state)
    identifier = controller.store_plan(station, action, digest, request=request,
                                        remote_sha256=state)
    return {"plan_id": identifier, "expected_sha256": digest}


def execute(controller: Controller, plan: Plan) -> dict[str, Any]:
    station = controller.station(plan.station.id)
    action = plan.action
    _permission(controller, station, action)
    if station != plan.station or _digest(station, plan.request, plan.remote_sha256) != plan.digest:
        raise SafetyError("Native station configuration, trust or request changed")
    parameters = copy.deepcopy(plan.request["parameters"])
    if action == "led.show":
        from alice_mcp import led_parity

        parameters = led_parity.native_parameters(station, parameters)
    local = station.controls.get("local_tts") if action == "speech.say" else None
    audio = b""
    if local is not None:
        from alice_mcp.local_tts import synthesize, wav_parameters

        try:
            audio = synthesize(parameters["text"], Path(local["model_file"]),
                               local["model_sha256"], local["config_sha256"])
            parameters = wav_parameters(audio)
        except Exception:
            return {"outcome": "blocked", "error": "Local synthesis unavailable; no cloud fallback"}
    if action in native_typed.ACTIONS:
        try:
            parameters = native_typed.wire(station, action, parameters)
        except SafetyError as error:
            return {"outcome": "blocked", "error": str(error)}
    if controller.clock() >= plan.expires:
        raise SafetyError("Native preview expired during synthesis")
    if _state(controller, station, action) != plan.remote_sha256:
        raise SafetyError("Native state changed; create a new preview")
    fresh = controller.station(station.id)
    _permission(controller, fresh, action)
    if fresh != station:
        raise SafetyError("Native station configuration or trust changed")
    if controller.clock() >= plan.expires:
        raise SafetyError("Native preview expired during revalidation")
    try:
        result = call(station, "run_station_action", {
            "station_id": station.id, "action": action, "parameters": parameters,
            "expected_state_sha256": plan.remote_sha256,
        })
        if action in native_typed.ACTIONS:
            return native_typed.result(station, action, result, parameters)
        if action == "speech.listen":
            return _capture_result(result, allow_text=False)
        if action == "led.show":
            return led_parity.native_result(result)
        if local is not None:
            return _pcm_result(result, expected_frames=(len(audio) - 44) // 2,
                               expected_rate=int.from_bytes(audio[24:28], "little"))
        if result.get("outcome") == "blocked" and "request_may_have_been_sent" not in result:
            return {"outcome": "blocked",
                    "error": "Native station policy or runtime blocked action"}
        allowed = {"outcome", "error_code", "request_sent", "request_may_have_been_sent",
                   "tts_started", "tts_completed", "cleanup_verified", "quasar_running",
                   "do_not_retry", "evidence_class", "physical_effects_verified"}
        if set(result) - allowed:
            raise ValueError("unexpected native result fields")
        from alice_mcp.speech_capture import _tts_result

        flags = {k: v for k, v in result.items() if k in {
            "outcome", "error_code", "request_sent", "request_may_have_been_sent",
            "tts_started", "tts_completed",
        }}
        if flags.get("outcome") == "applied":
            flags["outcome"] = "complete"
        cleanup = {"outcome": "cleanup", "cleanup_verified": result["cleanup_verified"],
                   "quasar_running": result["quasar_running"]}
        value = _tts_result(json.dumps(flags) + "\n" + json.dumps(cleanup))
        return {**value, "do_not_retry": True, "evidence_class": "analytical",
                "physical_effects_verified": False}
    except Exception:
        return {"outcome": "unknown", "error_code": "native_action_unverified",
                 "do_not_retry": True, "physical_effects_verified": False}


def play_pcm(controller: Controller, station: Station, audio: bytes) -> dict[str, Any]:
    """A host-pinned canonical WAV through the resident's local-PCM speech path, without
    synthesis (e.g. Alice's stock error phrase). Bound to the resident state; once."""
    from alice_mcp.local_tts import wav_parameters

    _permission(controller, station, "speech.say")
    if "local_tts" not in station.controls:
        raise SafetyError("Resident local PCM playback is not configured")
    parameters = wav_parameters(audio)
    with controller._execution_lock:  # Never over the agent's own speech.
        state = _state(controller, station, "speech.say")
        fresh = controller.station(station.id)
        _permission(controller, fresh, "speech.say")
        if fresh != station:
            raise SafetyError("Native station configuration or trust changed")
        try:
            result = call(station, "run_station_action", {
                "station_id": station.id, "action": "speech.say", "parameters": parameters,
                "expected_state_sha256": state,
            })
            return _pcm_result(result, expected_frames=(len(audio) - 44) // 2,
                               expected_rate=int.from_bytes(audio[24:28], "little"))
        except Exception:
            return {"outcome": "unknown", "error_code": "native_action_unverified",
                    "do_not_retry": True, "physical_effects_verified": False}


def _pcm_result(result: dict[str, Any], *, expected_frames: int | None = None,
                expected_rate: int | None = None) -> dict[str, Any]:
    if result.get("outcome") == "blocked" and set(result) <= {"outcome", "error"}:
        return {"outcome": "blocked", "error": "Resident local PCM policy/runtime blocked action"}
    if (set(result) != {"outcome", "playback_started", "playback_completed", "cleanup_verified",
                        "frames_written", "sample_rate", "quasar_running", "do_not_retry",
                        "evidence_class", "physical_effects_verified", "tts_backend",
                        "input_frames", "stage", "alsa_return_code",
                        "hardware_parameters_verified", "playback_elapsed_ms"}
        or result["outcome"] not in {"applied", "blocked", "unknown"}
        or any(type(result[k]) is not bool for k in
               ("playback_started", "playback_completed", "cleanup_verified", "quasar_running",
                "hardware_parameters_verified"))
        or not rt._integer(result["playback_elapsed_ms"], -1, 25000)
        or not result["hardware_parameters_verified"] and (
            result["frames_written"] != 0 or result["playback_elapsed_ms"] != -1)
        or not rt._integer(result["frames_written"], 0, 960000)
        or not rt._integer(result["input_frames"], 1, 960000)
        or not rt._integer(result["sample_rate"], 16000, 48000)
        or not result["frames_written"] <= result["input_frames"] <= result["sample_rate"] * 20
        or expected_frames is not None and result["input_frames"] != expected_frames
        or expected_rate is not None and result["sample_rate"] != expected_rate
        or type(result["stage"]) is not str or result["stage"] not in {
            "request", "library", "symbols", "configuration", "open", "parameters", "hardware",
            "clock", "write", "wait", "blocking", "drain", "cleanup", "complete", "unverified",
        }
        or (result["stage"] == "unverified" and (
            result["outcome"] != "unknown" or result["alsa_return_code"] is not None
            or result["hardware_parameters_verified"] or result["playback_elapsed_ms"] != -1)
            or result["stage"] != "unverified"
            and not rt._integer(result["alsa_return_code"], -4095, 2**31 - 1))
        or result["do_not_retry"] is not True or result["physical_effects_verified"] is not False
        or result["evidence_class"] != "analytical" or result["tts_backend"] != "local_pcm"
        or result["playback_started"] != (result["frames_written"] > 0)
        or result["playback_completed"] != (result["outcome"] == "applied")
        or result["outcome"] == "applied" and not all(result[k] for k in
               ("playback_started", "cleanup_verified", "quasar_running"))
        or result["outcome"] == "applied" and (
             result["frames_written"] != result["input_frames"] or result["stage"] != "complete"
             or result["alsa_return_code"] < 0 or not result["hardware_parameters_verified"]
             or result["playback_elapsed_ms"] < 0)
        or result["outcome"] == "blocked" and result["playback_started"]):
        raise SafetyError("Local playback result unverified; do not retry")
    return dict(result)


def _capture_result(result: dict[str, Any], *, allow_text: bool) -> dict[str, Any]:
    from alice_mcp.speech_capture import ERRORS

    required = {"outcome", "capture_id", "capture_ready", "remaining_seconds",
                "cleanup_verified", "quasar_running", "do_not_retry", "untrusted_text",
                "evidence_class"}
    if (set(result) - required - {"error_code", "setup_remaining_seconds", "text"}
        or not required <= result.keys()
        or result["outcome"] not in {"starting", "awaiting_idle", "capturing", "finishing",
                                     "complete", "blocked", "unknown", "consumed", "expired"}
        or not rt._match(rt.HEX32, result["capture_id"])
        or not rt._integer(result["remaining_seconds"], 0, 60)
        or any(type(result[k]) is not bool for k in
               ("capture_ready", "cleanup_verified", "quasar_running"))
        or result["do_not_retry"] is not True or result["untrusted_text"] is not True
        or result["evidence_class"] != "analytical"
        or result.get("error_code", "timeout") not in ERRORS | {"speech_capture_unverified"}
        or not rt._integer(result.get("setup_remaining_seconds", 0), 0, 180)
        or result["capture_ready"] != (result["outcome"] == "capturing")
        or result["capture_ready"] and result["remaining_seconds"] < 2):
        raise SafetyError("Native capture metadata incompatible; do not retry")
    if "text" in result:
        if (not allow_text or result["outcome"] != "complete"
            or not result["cleanup_verified"] or not result["quasar_running"]):
            raise SafetyError("Native capture cleanup unverified; do not retry")
        rt.validate_parameters("speech.say", {"text": result["text"]}, {"tts_client": {}},
                               "station-mini")
    return dict(result)


def capture_status(controller: Controller, station: Station, capture_id: str) -> dict[str, Any]:
    if (not rt.allows(station.controls, "speech.listen")
        or not rt._match(rt.HEX32, capture_id)):
        raise SafetyError("Capture permission revoked or identifier invalid")
    result = _capture_result(call(station, "get_capture_status", {
        "station_id": station.id, "capture_id": capture_id,
    }), allow_text=True)
    if result["capture_id"] != capture_id or controller.station(station.id) != station:
        raise SafetyError("Native capture identity or configuration changed")
    return result
