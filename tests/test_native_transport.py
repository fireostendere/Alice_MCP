"""Synthetic TLS/MCP peers only; never connects to a Station or uses household audio."""

import asyncio
import base64
import copy
import hashlib
import io
import json
import shutil
import ssl
import subprocess
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlunsplit

import pytest

from alice_mcp import controls, local_tts, native_transport, server
from alice_mcp.core import Controller, SafetyError, load_inventory, ssh_argv


def synthetic_policy(station: Any) -> dict[str, Any]:
    actions = station.controls["allowed_actions"]
    return {"station_id": station.id, "model": station.model, "writes_enabled": True,
            "allowed_actions": actions, "preauthorize_all_actions": True,
            "preauthorized_actions": actions, "transport": "streamable_http_tls",
            "state_contract": "alice.native.state.v1", "state_sha256": "c" * 64,
            "tts_backend": "local_pcm" if "local_tts" in station.controls else "yandex"}


@pytest.fixture
def native(tmp_path: Path) -> Any:
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("Synthetic TLS checks require an existing OpenSSL executable")
    certificate, key = tmp_path / "certificate", tmp_path / "identity"
    subprocess.run([
        openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
        "-keyout", str(key), "-out", str(certificate),
    ], check=True, capture_output=True)
    credential = tmp_path / "credential"
    credential.write_bytes(b"a" * 64)
    credential.chmod(0o600)
    calls: list[str] = []
    result: dict[str, Any] = {
        "outcome": "unknown", "error_code": "timeout", "request_sent": True,
        "request_may_have_been_sent": True, "tts_started": False, "tts_completed": False,
        "cleanup_verified": True, "quasar_running": True, "do_not_retry": True,
        "physical_effects_verified": False, "evidence_class": "analytical",
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            if self.path != "/mcp" or self.headers.get("Authorization") != "Bearer " + "a" * 64:
                self.send_response(401)
                self.end_headers()
                return
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = request["method"]
            if method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            if method == "initialize":
                value = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                         "serverInfo": {"name": "alice-station-agent", "version": "0.1.0"}}
            else:
                assert method == "tools/call"
                name = request["params"]["name"]
                calls.append(name)
                arguments = request["params"]["arguments"]
                assert arguments["station_id"] == "desk"
                if name == "station_probe":
                    data = {"evidence_class": "analytical", "data": {
                        "uptime_seconds": 12.0, "memory_total_kib": 200000,
                        "memory_available_kib": 100000, "kernel_arch": "arm",
                        "agent_rss_kib": 9000,
                    }}
                elif name == "get_station_controls":
                    data = {"station_id": "desk", "model": "station-mini", "writes_enabled": True,
                            "allowed_actions": ["speech.say"], "preauthorize_all_actions": True,
                            "preauthorized_actions": ["speech.say"],
                            "transport": "streamable_http_tls", "independent_wake_word": False,
                            "state_contract": "alice.native.state.v1", "state_sha256": "c" * 64,
                            "tts_backend": ("local_pcm" if "local_tts" in
                                            data_inventory["stations"][0]["controls"]
                                            else "yandex"),
                            "evidence_class": "caller", "physical_effects_verified": False}
                else:
                    assert name == "run_station_action"
                    assert arguments["action"] == "speech.say"
                    assert arguments["expected_state_sha256"] == "c" * 64
                    parameters = arguments["parameters"]
                    if "wav_base64" in parameters:
                        audio = base64.b64decode(parameters["wav_base64"], validate=True)
                        assert parameters["sha256"] == hashlib.sha256(audio).hexdigest()
                        assert len(audio) > 16384 and "text" not in parameters
                    else:
                        assert parameters == {"text": "synthetic"}
                    data = copy.deepcopy(result)
                value = {"content": [{"type": "text", "text": json.dumps(data)}],
                         "structuredContent": data, "isError": False}
            response = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": value}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    peer = ThreadingHTTPServer(("localhost", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.load_cert_chain(certificate, key)
    peer.socket = tls.wrap_socket(peer.socket, server_side=True)
    worker = threading.Thread(target=peer.serve_forever, daemon=True)
    worker.start()
    inventory = tmp_path / "inventory.json"
    data = {"stations": [{
        "id": "desk", "model": "station-mini", "preauthorize_all_actions": True,
        "native_mcp": {"url": f"https://localhost:{peer.server_port}/mcp",
                       "ca_file": str(certificate), "token_file": str(credential)},
        "controls": {"firmware_sha256": "a" * 64, "allowed_actions": ["speech.say"]},
    }]}
    data_inventory = data
    inventory.write_text(json.dumps(data))
    inventory.chmod(0o600)

    def no_ssh(*args: Any) -> str:
        raise AssertionError("Native transport must never fall back to SSH")

    try:
        yield Controller(inventory, writes=True, runner=no_ssh), calls, result, data, peer
    finally:
        peer.shutdown()
        peer.server_close()
        worker.join(timeout=2)


def test_native_only_inventory_does_not_need_or_use_ssh(native: Any) -> None:
    controller, calls, _, _, _ = native
    station = controller.station("desk")
    assert station.native_mcp and station.host == ""
    with pytest.raises(SafetyError, match="SSH"):
        ssh_argv(station, "true")
    result = controller.probe("desk", "health")
    assert result["data"]["memory_available_kib"] == 100000
    assert result["data"]["agent_rss_kib"] == 9000
    assert calls == ["station_probe"]


def test_native_controls_and_one_action_preserve_unknown_without_retry(native: Any,
                                                                     monkeypatch: Any) -> None:
    controller, calls, _, _, _ = native
    assert controls.capabilities(controller, "desk")["transport"] == "native_mcp"
    monkeypatch.setattr(server, "controller", controller)
    result = asyncio.run(server.run_station_action(
        "desk", "speech.say", {"text": "synthetic"}, object(),
    ))
    assert result["outcome"] == "unknown" and result["do_not_retry"]
    assert result["request_sent"] and not result["physical_effects_verified"]
    assert calls == ["get_station_controls"] * 3 + ["run_station_action"]


@pytest.mark.parametrize("change", ["disabled", "not_preauthorized", "forbidden", "invalid_text"])
def test_native_transport_does_not_waive_host_policy(native: Any, monkeypatch: Any,
                                                  change: str) -> None:
    controller, calls, _, data, _ = native
    text = "synthetic"
    if change == "disabled":
        controller.writes = False
    elif change == "not_preauthorized":
        data["stations"][0]["preauthorize_all_actions"] = False
    elif change == "forbidden":
        data["stations"][0]["controls"]["allowed_actions"] = []
    else:
        text = "a\nline"
    controller.inventory.write_text(json.dumps(data))
    monkeypatch.setattr(server, "controller", controller)
    result = asyncio.run(server.run_station_action("desk", "speech.say", {"text": text}, object()))
    assert result["outcome"] == "blocked" and not calls


def test_native_transport_rejects_wrong_tls_name_and_revoked_token(native: Any) -> None:
    controller, calls, _, data, peer = native
    entry = data["stations"][0]
    original = entry["native_mcp"]["url"]
    entry["native_mcp"]["url"] = urlunsplit((
        "https", f"{peer.server_address[0]}:{peer.server_port}", "/mcp", "", "",
    ))
    controller.inventory.write_text(json.dumps(data))
    with pytest.raises(SafetyError) as error:
        controller.probe("desk", "health")
    assert original not in str(error.value) and not calls
    entry["native_mcp"]["url"] = original
    controller.inventory.write_text(json.dumps(data))
    Path(entry["native_mcp"]["token_file"]).write_bytes(b"b" * 64)
    with pytest.raises(SafetyError):
        controller.probe("desk", "health")
    assert not calls


@pytest.mark.parametrize("field,value", [("url", "http://station.invalid/mcp"),
                                        ("url", "https://station.invalid/mcp?token=REDACTED"),
                                        ("url", "https://station.invalid/other"),
                                        ("token_file", "relative")])
def test_native_inventory_rejects_unsafe_channel_configuration(native: Any,
                                                             field: str, value: str) -> None:
    controller, _, _, data, _ = native
    data["stations"][0]["native_mcp"][field] = value
    controller.inventory.write_text(json.dumps(data))
    with pytest.raises(SafetyError):
        load_inventory(controller.inventory)


def test_malformed_native_action_result_cannot_export_private_data(native: Any,
                                                                monkeypatch: Any) -> None:
    controller, calls, result, _, _ = native
    result["private"] = "SENSITIVE_SENTINEL"
    monkeypatch.setattr(server, "controller", controller)
    response = asyncio.run(server.run_station_action(
        "desk", "speech.say", {"text": "synthetic"}, object(),
    ))
    assert response["outcome"] == "unknown" and response["do_not_retry"]
    assert "SENSITIVE_SENTINEL" not in str(response)
    assert calls == ["get_station_controls"] * 2 + ["run_station_action"]


def test_native_preview_transport_failure_blocks_without_retry_or_private_error(
    native: Any, monkeypatch: Any,
) -> None:
    controller, _, _, _, _ = native
    calls = []

    def fail(*args: Any) -> Any:
        calls.append(1)
        raise SafetyError("SENSITIVE_SENTINEL")

    monkeypatch.setattr(native_transport, "call", fail)
    monkeypatch.setattr(server, "controller", controller)
    result = asyncio.run(server.run_station_action(
        "desk", "speech.say", {"text": "synthetic"}, object(),
    ))
    assert result["outcome"] == "blocked" and calls == [1]
    assert "SENSITIVE_SENTINEL" not in str(result)


def test_native_listen_and_once_only_status_do_not_use_ssh(native: Any, monkeypatch: Any) -> None:
    controller, _, _, data, _ = native
    data["stations"][0]["controls"]["allowed_actions"].append("speech.listen")
    controller.inventory.write_text(json.dumps(data))
    calls = []

    def peer(station: Any, tool: str, arguments: Any) -> Any:
        calls.append(tool)
        if tool == "get_station_controls":
            return synthetic_policy(station)
        return {"outcome": "capturing", "capture_id": "a" * 32, "capture_ready": True,
                "remaining_seconds": 10, "cleanup_verified": False, "quasar_running": True,
                "do_not_retry": True, "untrusted_text": True, "evidence_class": "analytical"}

    monkeypatch.setattr(native_transport, "call", peer)
    result = native_transport.run_action(controller, controller.station("desk"), "speech.listen",
                                         {"duration_seconds": 10})
    assert result["capture_ready"] and result["do_not_retry"]
    assert controls.capture_status(controller, "desk", "a" * 32)["capture_ready"]
    assert calls == ["get_station_controls"] * 2 + ["run_station_action", "get_capture_status"]


@pytest.mark.parametrize("change", ["cleanup", "quasar", "early", "field", "control", "id"])
def test_native_capture_never_releases_unverified_text(change: str) -> None:
    result = {"outcome": "complete", "capture_id": "a" * 32, "capture_ready": False,
              "remaining_seconds": 0, "cleanup_verified": True, "quasar_running": True,
              "do_not_retry": True, "untrusted_text": True, "evidence_class": "analytical",
              "text": "synthetic"}
    assert native_transport._capture_result(result, allow_text=True)["text"] == "synthetic"
    if change in {"cleanup", "quasar"}:
        result["cleanup_verified" if change == "cleanup" else "quasar_running"] = False
    elif change == "early":
        result["outcome"] = "capturing"
    elif change == "field":
        result["private"] = "SENSITIVE_SENTINEL"
    elif change == "control":
        result["text"] = "a\nline"
    else:
        result["capture_id"] = "invalid"
    with pytest.raises((SafetyError, ValueError)):
        native_transport._capture_result(result, allow_text=True)


@pytest.mark.parametrize("synthesis_ok", [True, False])
def test_local_speech_uses_only_pcm_tls_and_never_falls_back_to_cloud(
    native: Any, monkeypatch: Any, synthesis_ok: bool,
) -> None:
    controller, calls, result, data, _ = native
    data["stations"][0]["controls"]["local_tts"] = {
        "model_file": "/PRIVATE/voice.onnx", "model_sha256": "a" * 64, "config_sha256": "b" * 64,
    }
    controller.inventory.write_text(json.dumps(data))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(b"\x01\x00" * 22050)

    def synthesize(*args: Any, **kwargs: Any) -> bytes:
        if not synthesis_ok:
            raise SafetyError("SENSITIVE_SENTINEL")
        return buffer.getvalue()

    monkeypatch.setattr(local_tts, "synthesize", synthesize)
    result.clear()
    result.update(outcome="applied", playback_started=True, playback_completed=True,
                  cleanup_verified=True, frames_written=22050, sample_rate=22050,
                  input_frames=22050, stage="complete", alsa_return_code=0,
                  hardware_parameters_verified=True, playback_elapsed_ms=1000,
                  quasar_running=True, do_not_retry=True, evidence_class="analytical",
                  physical_effects_verified=False, tts_backend="local_pcm")
    response = native_transport.run_action(controller, controller.station("desk"), "speech.say",
                                           {"text": "synthetic"})
    assert "SENSITIVE_SENTINEL" not in str(response)
    if synthesis_ok:
        assert response["outcome"] == "applied" and response["tts_backend"] == "local_pcm"
        assert calls == ["get_station_controls"] * 2 + ["run_station_action"]
        assert not response["physical_effects_verified"]
    else:
        assert response["outcome"] == "blocked" and calls == ["get_station_controls"]


def test_local_pcm_results_reject_uncorrelated_or_private_fields() -> None:
    valid = {"outcome": "applied", "playback_started": True, "playback_completed": True,
             "cleanup_verified": True, "frames_written": 22050, "sample_rate": 22050,
             "quasar_running": True, "do_not_retry": True, "evidence_class": "analytical",
              "physical_effects_verified": False, "tts_backend": "local_pcm"}
    valid.update(input_frames=22050, stage="complete", alsa_return_code=0,
                 hardware_parameters_verified=True, playback_elapsed_ms=1000)
    assert native_transport._pcm_result(valid)["outcome"] == "applied"
    for key, value in [("private", "SENSITIVE_SENTINEL"), ("cleanup_verified", False),
                       ("frames_written", True), ("quasar_running", False),
                       ("tts_backend", "yandex"), ("playback_started", False),
                       ("input_frames", 22049), ("stage", "PRIVATE_SENTINEL"),
                       ("alsa_return_code", True), ("hardware_parameters_verified", False),
                       ("hardware_parameters_verified", 1), ("playback_elapsed_ms", -1),
                       ("playback_elapsed_ms", True), ("playback_elapsed_ms", 25001)]:
        with pytest.raises(SafetyError):
            native_transport._pcm_result(dict(valid, **{key: value}))


def test_local_pcm_result_is_bound_to_the_submitted_audio_not_just_plausible_counts() -> None:
    result = {"outcome": "applied", "playback_started": True, "playback_completed": True,
              "cleanup_verified": True, "frames_written": 2205, "input_frames": 2205,
              "sample_rate": 22050, "stage": "complete", "alsa_return_code": 0,
              "hardware_parameters_verified": True, "playback_elapsed_ms": 100,
              "quasar_running": True, "do_not_retry": True, "evidence_class": "analytical",
              "physical_effects_verified": False, "tts_backend": "local_pcm"}
    assert native_transport._pcm_result(result, expected_frames=2205, expected_rate=22050)
    with pytest.raises(SafetyError):
        native_transport._pcm_result(result, expected_frames=2206, expected_rate=22050)
    with pytest.raises(SafetyError):
        native_transport._pcm_result(result, expected_frames=2205, expected_rate=48000)


def test_old_agent_without_state_contract_blocks_before_dispatch(
    native: Any, monkeypatch: Any,
) -> None:
    controller, calls, _, _, _ = native
    original = native_transport.call

    def old_peer(*args: Any) -> Any:
        result = original(*args)
        result.pop("state_contract", None)
        return result

    monkeypatch.setattr(native_transport, "call", old_peer)
    with pytest.raises(SafetyError):
        native_transport.run_action(controller, controller.station("desk"), "speech.say",
                                    {"text": "synthetic"})
    assert calls == ["get_station_controls"]


@pytest.mark.parametrize("change", ["state", "request", "policy", "resident_policy", "trust",
                                    "expiry", "revalidation_expiry", "writes"])
def test_native_plan_is_bound_and_consumed_before_dispatch(
    native: Any, monkeypatch: Any, change: str,
) -> None:
    controller, _, result, data, _ = native
    station = controller.station("desk")
    policy = synthetic_policy(station)
    calls = []

    def peer(station: Any, tool: str, arguments: Any) -> Any:
        calls.append(tool)
        if tool == "get_station_controls":
            if change == "revalidation_expiry" and len(calls) > 1:
                controller.clock = lambda: 301
            return policy
        return result

    monkeypatch.setattr(native_transport, "call", peer)
    controller.clock = lambda: 0
    preview = native_transport.preview_action(controller, station, "speech.say",
                                              {"text": "synthetic"})
    plan = controller.take_plan(preview["plan_id"], preview["expected_sha256"])
    with pytest.raises(SafetyError, match="consumed"):
        controller.take_plan(preview["plan_id"], preview["expected_sha256"])
    if change == "state":
        policy["state_sha256"] = "d" * 64
    elif change == "resident_policy":
        policy["preauthorize_all_actions"] = False
    elif change == "request":
        plan.request["parameters"]["text"] = "changed"
    elif change == "policy":
        data["stations"][0]["preauthorize_all_actions"] = False
        controller.inventory.write_text(json.dumps(data))
    elif change == "trust":
        Path(station.native_mcp["token_file"]).write_bytes(b"b" * 64)
    elif change == "expiry":
        controller.clock = lambda: 300
    elif change == "writes":
        controller.writes = False
    with pytest.raises(SafetyError):
        controller.execute_plan(plan)
    assert "run_station_action" not in calls


@pytest.mark.parametrize("change", ["expiry", "policy", "trust", "state", "backend"])
def test_native_changes_during_synthesis_never_dispatch(
    native: Any, monkeypatch: Any, change: str,
) -> None:
    controller, _, _, data, _ = native
    entry = data["stations"][0]
    entry["controls"]["local_tts"] = {
        "model_file": "/PRIVATE/voice.onnx", "model_sha256": "a" * 64, "config_sha256": "b" * 64,
    }
    controller.inventory.write_text(json.dumps(data))
    station = controller.station("desk")
    policy = synthetic_policy(station)
    calls = []

    def peer(station: Any, tool: str, arguments: Any) -> Any:
        calls.append(tool)
        assert tool == "get_station_controls"
        return policy

    def synthesize(*args: Any) -> bytes:
        if change == "expiry":
            controller.clock = lambda: 300
        elif change == "policy":
            entry["preauthorize_all_actions"] = False
            controller.inventory.write_text(json.dumps(data))
        elif change == "trust":
            Path(station.native_mcp["token_file"]).write_bytes(b"b" * 64)
        elif change == "state":
            policy["state_sha256"] = "d" * 64
        else:
            policy["tts_backend"] = "yandex"
        return b"synthetic"

    controller.clock = lambda: 0
    monkeypatch.setattr(native_transport, "call", peer)
    monkeypatch.setattr(local_tts, "synthesize", synthesize)
    monkeypatch.setattr(local_tts, "wav_parameters", lambda _: {"wav_base64": "synthetic"})
    with pytest.raises(SafetyError):
        native_transport.run_action(controller, station, "speech.say", {"text": "synthetic"})
    assert "run_station_action" not in calls and not controller.plans


def test_native_expired_or_digest_mismatched_plan_cannot_be_replayed(native: Any,
                                                                  monkeypatch: Any) -> None:
    controller, _, _, _, _ = native
    station = controller.station("desk")
    monkeypatch.setattr(native_transport, "call", lambda *_: synthetic_policy(station))
    controller.clock = lambda: 0
    for expiry in (False, True):
        preview = native_transport.preview_action(controller, station, "speech.say",
                                                  {"text": "synthetic"})
        if expiry:
            controller.clock = lambda: 300
        with pytest.raises(SafetyError):
            controller.take_plan(preview["plan_id"], "d" * 64)
        with pytest.raises(SafetyError, match="consumed"):
            controller.take_plan(preview["plan_id"], preview["expected_sha256"])


def test_native_execution_is_serialized_and_unknown_is_not_retried(native: Any,
                                                                  monkeypatch: Any) -> None:
    controller, _, _, _, _ = native
    station = controller.station("desk")
    active = maximum = dispatched = 0

    def peer(station: Any, tool: str, arguments: Any) -> Any:
        nonlocal active, maximum, dispatched
        if tool == "get_station_controls":
            return synthetic_policy(station)
        active += 1
        maximum = max(maximum, active)
        dispatched += 1
        time.sleep(0.02)
        active -= 1
        raise SafetyError("SENSITIVE_SENTINEL")

    monkeypatch.setattr(native_transport, "call", peer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(native_transport.run_action, controller, station, "speech.say",
                               {"text": "synthetic"}) for _ in range(2)]
        results = [future.result() for future in futures]
    assert maximum == 1 and dispatched == 2 and not controller.plans
    assert all(r["outcome"] == "unknown" and r["do_not_retry"] for r in results)
    assert "SENSITIVE_SENTINEL" not in str(results)
