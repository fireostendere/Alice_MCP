"""Synthetic one-shot speech orchestration; no station, microphone or real dialogue."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import controls, device_runtime, server
from alice_mcp.core import Controller, SafetyError, Station


@pytest.fixture
def speech(tmp_path: Path, monkeypatch: Any) -> tuple[Controller, list[str]]:
    from alice_mcp import speech_capture

    key, hosts = tmp_path / "identity", tmp_path / "hosts"
    key.touch(mode=0o600)
    hosts.touch()
    binary = tmp_path / "client"
    binary.write_bytes(b"synthetic reviewed client")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(speech_capture, "CLIENT_SHA256", digest)
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"stations": [{
        "id": "desk", "model": "station-mini", "host": "station.invalid", "port": 2222,
        "user": "root", "identity_file": str(key), "known_hosts_file": str(hosts),
        "preauthorize_all_actions": False,  # Exercises the explicit approval-form path.
        "controls": {"firmware_sha256": "a" * 64, "allowed_actions": ["speech.listen"],
                     "speech_client": {"path": str(binary), "sha256": digest}},
    }]}))
    inventory.chmod(0o600)
    calls: list[str] = []

    def runner(station: Any, command: Any) -> str:
        assert b"__CLIENT_OCTAL__" not in command.data  # Preview never transfers the client.
        calls.append("inspect")
        return json.dumps({"outcome": "ready", "state_sha256": "b" * 64})

    return Controller(inventory, writes=True, runner=runner), calls


def test_speech_uses_existing_single_use_native_approval(speech: Any, monkeypatch: Any) -> None:
    controller, calls = speech
    monkeypatch.setattr(server, "controller", controller)
    plan = controls.preview(controller, "desk", "speech.listen", {"duration_seconds": 60})
    assert calls == ["inspect"]
    assert "60" in plan["impact"] and "ASR" in plan["impact"]
    assert "sha256" in plan["impact"] and "no audio" in plan["impact"]

    class Decline:
        async def elicit(self, message: str, schema: Any) -> Any:
            assert "desk" in message and "temporary" in message and "Quasar" in message
            assert "station.invalid" not in message and "client\"" not in message
            return SimpleNamespace(action="decline", data=None)

    assert asyncio.run(server.apply_station_action(
        plan["plan_id"], plan["expected_sha256"], Decline(),
    ))["outcome"] == "cancelled"
    assert calls == ["inspect"] and not controller.speech_sessions
    with pytest.raises(SafetyError):
        controller.take_plan(plan["plan_id"], plan["expected_sha256"])


def test_speech_binary_or_policy_change_blocks_before_upload(speech: Any) -> None:
    controller, calls = speech
    plan = controls.preview(controller, "desk", "speech.listen", {"duration_seconds": 60})
    policy = controller.station("desk").controls
    Path(policy["speech_client"]["path"]).write_bytes(b"changed")
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert calls == ["inspect"]


@pytest.mark.parametrize("params", [{"duration_seconds": True}, {"duration_seconds": 61},
                                   {"duration_seconds": 60, "host": "other.invalid"}])
def test_no_speech_path_endpoint_or_unbounded_window(speech: Any, params: Any) -> None:
    controller, calls = speech
    with pytest.raises(SafetyError):
        controls.preview(controller, "desk", "speech.listen", params)
    assert not calls


def test_speech_cannot_use_an_arbitrary_operator_binary(speech: Any, monkeypatch: Any) -> None:
    from alice_mcp import speech_capture

    controller, calls = speech
    monkeypatch.setattr(speech_capture, "CLIENT_SHA256", "c" * 64)
    with pytest.raises(SafetyError):
        controls.preview(controller, "desk", "speech.listen", {"duration_seconds": 60})
    assert not calls


@pytest.fixture
def tts(speech: Any, monkeypatch: Any) -> Any:
    from alice_mcp import speech_capture

    controller, calls = speech
    j = json.loads(controller.inventory.read_text())
    policy = j["stations"][0]["controls"]
    policy["allowed_actions"].append("speech.say")
    policy["tts_client"] = dict(policy["speech_client"])
    monkeypatch.setattr(speech_capture, "TTS_CLIENT_SHA256", policy["tts_client"]["sha256"])
    controller.inventory.write_text(json.dumps(j))
    # Orchestration only: filesystem/SSH trust is tested separately in test_core.py.
    item = j["stations"][0]
    station = Station("desk", "station-mini", "station.invalid", 2222, "root",
                      Path(item["identity_file"]), Path(item["known_hosts_file"]), False, "",
                      "c" * 64, "d" * 64, device_runtime.validate_policy(policy, "station-mini"),
                      preauthorize_all_actions=False)
    monkeypatch.setattr(controller, "station", lambda _: station)
    return controller, calls


def test_say_is_bound_to_the_exact_text_and_native_approval(tts: Any, monkeypatch: Any) -> None:
    controller, calls = tts
    monkeypatch.setattr(server, "controller", controller)
    p = controls.preview(controller, "desk", "speech.say", {"text": "synthetic message"})
    assert p["approval_required"] and "synthetic message" in p["impact"]
    assert "Append" in p["impact"] and "current volume" in p["impact"]

    class Decline:
        async def elicit(self, message: str, schema: Any) -> Any:
            assert "synthetic message" in message and "Yandex" in message
            return SimpleNamespace(action="decline", data=None)

    assert asyncio.run(server.apply_station_action(
        p["plan_id"], p["expected_sha256"], Decline(),
    ))["outcome"] == "cancelled"
    assert calls == ["inspect"]


@pytest.mark.parametrize("text", ["", "x" * 257, "a\nline", "nul\0text", "a\u202eb", 123])
def test_invalid_say_text_never_reaches_station(tts: Any, text: Any) -> None:
    controller, calls = tts
    with pytest.raises(SafetyError):
        controls.preview(controller, "desk", "speech.say", {"text": text})
    assert not calls


def test_tts_result_requires_cleanup_and_does_not_echo_text(tts: Any) -> None:
    controller, _ = tts
    success = {"outcome": "complete", "request_sent": True, "request_may_have_been_sent": True,
               "tts_started": True, "tts_completed": True}
    cleanup = {"outcome": "cleanup", "cleanup_verified": True, "quasar_running": True}

    def transport(station: Any, command: Any) -> str:
        if " apply " not in command.command:
            return json.dumps({"outcome": "ready", "state_sha256": "b" * 64})
        assert "PRIVATE_SENTINEL" not in command.command
        assert b"PRIVATE_SENTINEL" not in command.data
        return json.dumps(success) + "\n" + json.dumps(cleanup) + "\n"

    controller.runner = transport
    p = controls.preview(controller, "desk", "speech.say", {"text": "PRIVATE_SENTINEL"})
    result = controller.apply_service(p["plan_id"], p["expected_sha256"])
    assert result["outcome"] == "applied" and result["tts_completed"]
    assert result["cleanup_verified"] and result["physical_effects_verified"] is False
    assert "PRIVATE_SENTINEL" not in str(result)


@pytest.mark.parametrize("model", ["station-mini", "station-mini3"])
def test_tts_arm32_models_use_same_pinned_client_and_no_form(
    tts: Any, monkeypatch: Any, model: str,
) -> None:
    controller, calls = tts
    previous = controller.station("desk")
    policy = device_runtime.validate_policy({
        "firmware_sha256": previous.controls["firmware_sha256"],
        "allowed_actions": ["speech.say"], "tts_client": previous.controls["tts_client"],
    }, model)
    station = replace(previous, model=model, controls=policy, preauthorize_all_actions=True)
    monkeypatch.setattr(controller, "station", lambda _: station)
    monkeypatch.setattr(server, "controller", controller)

    def transport(station: Any, command: Any) -> str:
        if " apply " not in command.command:
            assert b"127 69 76 70 1 1" in command.data  # Still checks actual ARM32 maind.
            assert b"40 0" in command.data
            return '{"outcome":"ready","state_sha256":"' + "b" * 64 + '"}'
        calls.append("apply")
        return ('{"outcome":"complete","request_sent":true,"request_may_have_been_sent":true,'
                '"tts_started":true,"tts_completed":true}\n'
                '{"outcome":"cleanup","cleanup_verified":true,"quasar_running":true}\n')

    controller.runner = transport
    preview = controls.preview(controller, "desk", "speech.say", {"text": "synthetic"})
    assert preview["approval_required"] is False
    result = asyncio.run(server.apply_station_action(
        preview["plan_id"], preview["expected_sha256"], object(),
    ))
    assert result["outcome"] == "applied" and result["cleanup_verified"]
    assert calls == ["apply"]
    again = asyncio.run(server.apply_station_action(
        preview["plan_id"], preview["expected_sha256"], object(),
    ))
    assert again["outcome"] == "blocked" and calls == ["apply"]


def test_mini3_tts_does_not_enable_asr_or_unreviewed_models(tts: Any) -> None:
    controller, _ = tts
    client = controller.station("desk").controls["tts_client"]
    with pytest.raises(ValueError):
        device_runtime.validate_policy({"firmware_sha256": "a" * 64, "allowed_actions": [],
                                        "speech_client": client}, "station-mini3")
    with pytest.raises(ValueError):
        device_runtime.validate_policy({"firmware_sha256": "a" * 64, "allowed_actions": [],
                                        "tts_client": client}, "station-lite")
    with pytest.raises(ValueError):
        device_runtime.validate_parameters("speech.say", {"text": "synthetic"},
                                           {"tts_client": client}, "station-lite")


def test_tts_cleanup_failure_cannot_claim_success(tts: Any) -> None:
    controller, _ = tts
    controller.runner = lambda station, command: (
        json.dumps({"outcome": "ready", "state_sha256": "b" * 64})
        if " apply " not in command.command else
        '{"outcome":"complete","request_sent":true,"request_may_have_been_sent":true,'
        '"tts_started":true,"tts_completed":true}\n'
        '{"outcome":"cleanup","cleanup_verified":false,"quasar_running":true}\n'
    )
    p = controls.preview(controller, "desk", "speech.say", {"text": "synthetic"})
    result = controller.apply_service(p["plan_id"], p["expected_sha256"])
    assert result["outcome"] == "unknown" and result["do_not_retry"]


def test_tts_output_cannot_export_private_fields_or_claim_uncorrelated_completion() -> None:
    from alice_mcp.speech_capture import _tts_result

    result = {"outcome": "complete", "request_sent": True, "request_may_have_been_sent": True,
              "tts_started": True, "tts_completed": True}
    cleanup = {"outcome": "cleanup", "cleanup_verified": True, "quasar_running": True}
    for change in ({"private": "PRIVATE_SENTINEL"}, {"error_code": "PRIVATE_SENTINEL"},
                   {"tts_started": False}):
        raw = json.dumps(dict(result, **change)) + "\n" + json.dumps(cleanup)
        with pytest.raises(SafetyError) as error:
            _tts_result(raw)
        assert "PRIVATE_SENTINEL" not in str(error.value)


def test_preview_reports_only_allowlisted_compatibility_failure(speech: Any) -> None:
    controller, _ = speech
    controller.runner = lambda *_: json.dumps({"outcome": "blocked", "capture_ready": False,
                                               "error_code": "runtime_tools_missing"})
    with pytest.raises(SafetyError, match="runtime_tools_missing"):
        controls.preview(controller, "desk", "speech.listen", {"duration_seconds": 60})
    controller.runner = lambda *_: json.dumps({"outcome": "blocked", "capture_ready": False,
                                               "error_code": "PRIVATE_SENTINEL"})
    with pytest.raises(SafetyError) as error:
        controls.preview(controller, "desk", "speech.listen", {"duration_seconds": 60})
    assert "PRIVATE_SENTINEL" not in str(error.value)


def test_transfer_needs_no_missing_base64_utility(speech: Any) -> None:
    from alice_mcp import speech_capture

    controller, _ = speech
    plan = controls.preview(controller, "desk", "speech.listen", {"duration_seconds": 60})
    request = dict(controller.plans[plan["plan_id"]].request,
                   mode="apply", expected_sha256="b" * 64)
    payload = speech_capture.command(request, speech_capture.load_client(request["policy"]))
    assert b"base64" not in payload.data
    assert len(payload.data) <= 120 * 1024


def test_speech_status_is_fresh_and_transcript_is_consumed_once(speech: Any,
                                                              monkeypatch: Any) -> None:
    from alice_mcp import speech_capture

    controller, _ = speech
    script = (
        "import sys,time; sys.stdin.buffer.read(); "
        "print('{\"outcome\":\"awaiting_idle\",\"capture_ready\":false,"
        "\"remaining_seconds\":59}',flush=True); "
        "print('{\"outcome\":\"ready\",\"capture_ready\":true,"
        "\"remaining_seconds\":59}',flush=True); "
        "time.sleep(0.3); "
        "print('{\"outcome\":\"complete\",\"capture_ready\":false,"
        "\"text\":\"synthetic phrase\"}',flush=True); "
        "print('{\"outcome\":\"cleanup\",\"cleanup_verified\":true,\"quasar_running\":true}',flush=True)"
    )
    monkeypatch.setattr(speech_capture, "ssh_argv", lambda *_: [sys.executable, "-c", script])
    plan = controls.preview(controller, "desk", "speech.listen", {"duration_seconds": 60})
    result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    capture_id = result["capture_id"]
    assert result["outcome"] in {"awaiting_idle", "capturing"}
    if result["capture_ready"]:
        assert 2 <= result["remaining_seconds"] <= 59
    session = controller.speech_sessions[capture_id]
    assert session.done.wait(3)
    result = controls.capture_status(controller, "desk", capture_id)
    assert result["outcome"] == "complete" and result["text"] == "synthetic phrase"
    assert result["cleanup_verified"] and result["quasar_running"]
    assert result["untrusted_text"] is True and result["capture_ready"] is False
    assert "text" not in controls.capture_status(controller, "desk", capture_id)
    assert session.text is None


@pytest.mark.parametrize("output", [
    '{"outcome":"complete","capture_ready":false,"text":"PRIVATE_SENTINEL"}\n',
    '{"outcome":"ready","capture_ready":true,"remaining_seconds":59}\n'
    '{"outcome":"complete","capture_ready":false,"text":"PRIVATE_SENTINEL"}\n',
    '{"outcome":"ready","capture_ready":true,"remaining_seconds":59}\n'
    '{"outcome":"complete","capture_ready":false,"text":"PRIVATE_SENTINEL"}\n'
    '{"outcome":"cleanup","cleanup_verified":false,"quasar_running":true}\n',
    '{"outcome":"ready","capture_ready":true,"remaining_seconds":59,"extra":"PRIVATE_SENTINEL"}\n',
    'PRIVATE_SENTINEL' * 700,
])
def test_incomplete_or_hostile_stream_never_releases_text(speech: Any, monkeypatch: Any,
                                                       output: str) -> None:
    from alice_mcp import speech_capture

    controller, _ = speech
    script = "import sys; sys.stdin.buffer.read(); sys.stdout.write(" + repr(output) + ")"
    monkeypatch.setattr(speech_capture, "ssh_argv", lambda *_: [sys.executable, "-c", script])
    plan = controls.preview(controller, "desk", "speech.listen", {"duration_seconds": 60})
    result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    session = controller.speech_sessions[result["capture_id"]]
    assert session.done.wait(3)
    result = controls.capture_status(controller, "desk", result["capture_id"])
    assert result["outcome"] != "complete" and "text" not in result
    assert "PRIVATE_SENTINEL" not in str(result) and session.text is None


def test_changed_station_blocks_speech_status(speech: Any, monkeypatch: Any) -> None:
    from alice_mcp import speech_capture

    controller, _ = speech
    monkeypatch.setattr(speech_capture, "ssh_argv", lambda *_: [sys.executable, "-c",
        "import sys; sys.stdin.buffer.read()"])
    plan = controls.preview(controller, "desk", "speech.listen", {"duration_seconds": 60})
    result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert controller.speech_sessions[result["capture_id"]].done.wait(3)
    controller.inventory.write_text(controller.inventory.read_text() + "\n")
    with pytest.raises(SafetyError):
        controls.capture_status(controller, "desk", result["capture_id"])


def test_delayed_ready_event_is_not_a_fresh_window() -> None:
    from alice_mcp.speech_capture import SETUP_SECONDS, Session

    session = Session(SimpleNamespace(id="desk"), "a" * 32, 5)
    session.started = time.monotonic() - SETUP_SECONDS - 10
    session.event({"outcome": "ready", "capture_ready": True, "remaining_seconds": 5})
    assert session.report()["capture_ready"] is False


def test_connected_warmup_is_not_ready_and_does_not_double_subtract_wait(monkeypatch: Any) -> None:
    from alice_mcp import speech_capture

    clock = [100.0]
    monkeypatch.setattr(speech_capture.time, "monotonic", lambda: clock[0])
    session = speech_capture.Session(SimpleNamespace(id="desk"), "a" * 32, 60)
    session.event({"outcome": "awaiting_idle", "capture_ready": False, "remaining_seconds": 179})
    assert session.report()["capture_ready"] is False
    assert session.report()["outcome"] == "awaiting_idle"
    clock[0] += 90
    session.event({"outcome": "ready", "capture_ready": True, "remaining_seconds": 59,
                   "elapsed_seconds": 90})
    assert session.report()["capture_ready"] is True
    assert 58 <= session.report()["remaining_seconds"] <= 59


def test_late_ready_after_warmup_cannot_refresh_an_expired_window(monkeypatch: Any) -> None:
    from alice_mcp import speech_capture

    clock = [100.0]
    monkeypatch.setattr(speech_capture.time, "monotonic", lambda: clock[0])
    session = speech_capture.Session(SimpleNamespace(id="desk"), "a" * 32, 60)
    session.event({"outcome": "awaiting_idle", "capture_ready": False, "remaining_seconds": 179})
    clock[0] += 100
    session.event({"outcome": "ready", "capture_ready": True, "remaining_seconds": 60,
                   "elapsed_seconds": 10})
    assert session.report()["capture_ready"] is False


def test_diagnostics_are_counts_not_a_raw_frame_channel() -> None:
    from alice_mcp.speech_capture import Session

    session = Session(SimpleNamespace(id="desk"), "a" * 32, 60)
    with pytest.raises(ValueError):
        session.event({"outcome": "blocked", "capture_ready": False, "error_code": "timeout",
                       "diagnostics": {"frames_received": 1, "raw": "PRIVATE_SENTINEL"}})
    assert "PRIVATE_SENTINEL" not in str(session.report())


def test_cleanup_cannot_be_revised_and_unread_text_expires() -> None:
    from alice_mcp.speech_capture import Session

    session = Session(SimpleNamespace(id="desk"), "a" * 32, 60)
    session.event({"outcome": "ready", "capture_ready": True, "remaining_seconds": 59})
    session.event({"outcome": "complete", "capture_ready": False, "text": "synthetic"})
    session.event({"outcome": "cleanup", "cleanup_verified": False, "quasar_running": True})
    with pytest.raises(ValueError):
        session.event({"outcome": "cleanup", "cleanup_verified": True, "quasar_running": True})
    session.phase = "complete"
    session.discard()
    assert session.text is None and "text" not in session.report(consume=True)


def test_remote_helper_only_installs_reviewed_client_and_removes_it(tmp_path: Path) -> None:
    from alice_mcp.speech_capture import SCRIPT

    data = tmp_path / "data"
    (data / "opt").mkdir(parents=True)
    firmware = tmp_path / "maind"
    firmware.write_bytes(bytes([127, 69, 76, 70, 1, 1]) + bytes(12) + bytes([40, 0]))
    payload = (b"#!/bin/sh\nprintf '%s\\n' "
               b"'{\"outcome\":\"blocked\",\"capture_ready\":false,\"error_code\":\"timeout\"}'\n")
    digest = hashlib.sha256(payload).hexdigest()
    source = SCRIPT.replace("OWNER_UID=0", "OWNER_UID=" + str(os.getuid()))
    source = source.replace("DATA=/data", "DATA=" + str(data))
    source = source.replace("/system/vendor/quasar/maind", str(firmware))
    source = source.replace('pidof maind >/dev/null', 'true')
    source = source.replace('ipc || blocked', 'true || blocked')
    # Only the synthetic fixture changes paths/checks; production exposes no source argument.
    args = ["sh", "-s", "--", "apply", "a" * 32, "5",
            hashlib.sha256(firmware.read_bytes()).hexdigest(), digest, "c" * 64, ""]
    inspect = subprocess.run([*args[:3], "inspect", *args[4:]], input=source.encode(),
                             capture_output=True, check=True, timeout=5)
    state = json.loads(inspect.stdout)["state_sha256"]
    assert list((data / "opt").iterdir()) == []
    args[-1] = state
    octal = "".join(f"\\{byte:03o}" for byte in payload)
    source = source.replace("__CLIENT_OCTAL__", octal)
    # A stale state cannot create anything; an altered payload is removed, never executed.
    stale = subprocess.run([*args[:-1], "d" * 64], input=source.encode(),
                           capture_output=True, check=True, timeout=5)
    assert json.loads(stale.stdout)["outcome"] == "blocked"
    assert list((data / "opt").iterdir()) == []
    changed = source.replace(octal, octal + "".join(f"\\{b:03o}" for b in b"# changed\n"))
    bad = subprocess.run(args, input=changed.encode(), capture_output=True, check=True, timeout=5)
    assert [json.loads(line)["outcome"] for line in bad.stdout.splitlines()] == ["cleanup"]
    assert list((data / "opt").iterdir()) == []
    result = subprocess.run(args, input=source.encode(), capture_output=True, check=True, timeout=8)
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]["cleanup_verified"] is True
    assert events[-1]["quasar_running"] is True
    assert list((data / "opt").iterdir()) == []
    assert not result.stderr


def test_speech_policy_requires_reviewed_digest_and_mini1_only() -> None:
    with pytest.raises(ValueError):
        device_runtime.validate_parameters("speech.listen", {"duration_seconds": 60},
                                           {}, "station-mini3")
