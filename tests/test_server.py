"""MCP boundary and real stdio protocol tests without station access."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from alice_mcp import server
from alice_mcp.core import Controller, Station


def test_apply_requires_native_human_approval(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(station=SimpleNamespace(id="desk"),
                                                      action="restart")
    monkeypatch.setattr(server, "controller", controller)

    class Context:
        async def elicit(self, message: str, schema: Any) -> Any:
            return SimpleNamespace(action="decline", data=None)

    result = asyncio.run(server.apply_service_action("preview", "a" * 64, Context()))
    assert result["outcome"] == "cancelled"
    controller.execute_plan.assert_not_called()


def test_missing_elicitation_is_fail_closed(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(station=SimpleNamespace(id="desk"),
                                                      action="restart")
    monkeypatch.setattr(server, "controller", controller)

    class Context:
        async def elicit(self, message: str, schema: Any) -> Any:
            raise RuntimeError("SENSITIVE_SENTINEL")

    result = asyncio.run(server.apply_service_action("preview", "a" * 64, Context()))
    assert result["outcome"] == "blocked"
    assert "SENSITIVE_SENTINEL" not in str(result)
    controller.execute_plan.assert_not_called()


def test_unanswered_approval_returns_instead_of_hanging_the_client(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(station=SimpleNamespace(id="desk"),
                                                      action="restart")
    monkeypatch.setattr(server, "controller", controller)
    monkeypatch.setattr(server, "APPROVAL_SECONDS", 0.05)

    class Context:
        async def elicit(self, message: str, schema: Any) -> Any:
            await asyncio.Event().wait()  # client never delivers the form answer

    result = asyncio.run(asyncio.wait_for(
        server.apply_service_action("preview", "a" * 64, Context()), 5,
    ))
    assert result["outcome"] == "cancelled" and result["preview_consumed"] is True
    controller.execute_plan.assert_not_called()


def test_approval_executes_consumed_plan_once(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(station=SimpleNamespace(id="desk"),
                                                      action="start")
    controller.execute_plan.return_value = {"outcome": "completed"}
    monkeypatch.setattr(server, "controller", controller)

    class Context:
        async def elicit(self, message: str, schema: Any) -> Any:
            assert "desk" in message and "start" in message
            return SimpleNamespace(action="accept", data=SimpleNamespace(approve=True))

    assert asyncio.run(server.apply_service_action("preview", "a" * 64, Context())) == {
        "outcome": "completed",
    }
    controller.take_plan.assert_called_once_with("preview", "a" * 64)
    controller.execute_plan.assert_called_once()


@pytest.mark.parametrize("approval_wait,dispatch_wait", [(301, 0), (1, 300)])
def test_expired_approval_reports_wait_without_extending_or_reusing_preview(
    monkeypatch: Any, approval_wait: int, dispatch_wait: int,
) -> None:
    now = [100.0]
    controller = Controller(None, writes=True, clock=lambda: now[0])
    station = Station("desk", "station-mini", "station.invalid", 22, "root",
                      Path("/PRIVATE/key"), Path("/PRIVATE/hosts"), False, "", "", "",
                      preauthorize_all_actions=False)
    plan_id = controller.store_plan(station, "start", "a" * 64)
    monkeypatch.setattr(server, "controller", controller)
    runner = Mock()
    controller.runner = runner
    execute_plan = controller.execute_plan

    def queued_execute(plan: Any) -> dict[str, Any]:
        now[0] += dispatch_wait
        return execute_plan(plan)

    monkeypatch.setattr(controller, "execute_plan", queued_execute)

    class Context:
        async def elicit(self, message: str, schema: Any) -> Any:
            assert plan_id not in controller.plans
            now[0] += approval_wait
            return SimpleNamespace(action="accept", data=SimpleNamespace(approve=True))

    result = asyncio.run(server.apply_service_action(plan_id, "a" * 64, Context()))
    assert result["outcome"] == "blocked" and result["error"] == "Preview expired"
    assert result["approval_timing"]["remaining_at_prompt_seconds"] == 300
    assert result["approval_timing"]["approval_wait_seconds"] == approval_wait
    assert result["approval_timing"]["remaining_at_approval_seconds"] == max(0, 300 - approval_wait)
    assert result["approval_timing"]["execution_dispatch_seconds"] == dispatch_wait
    runner.assert_not_called()
    assert not controller.plans


def test_service_endpoint_cannot_approve_a_device_plan(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(request={"action": "config.select"})
    monkeypatch.setattr(server, "controller", controller)
    result = asyncio.run(server.apply_service_action("preview", "a" * 64, Mock()))
    assert result["outcome"] == "blocked"
    controller.execute_plan.assert_not_called()


def test_device_endpoint_cannot_approve_a_service_plan(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(request={})
    monkeypatch.setattr(server, "controller", controller)
    result = asyncio.run(server.apply_station_action("preview", "a" * 64, Mock()))
    assert result["outcome"] == "blocked"
    controller.execute_plan.assert_not_called()


def test_device_approval_displays_action_not_private_values(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(
        station=SimpleNamespace(id="desk"), action="config.select", request={
            "action": "config.select", "params": {"binding": "speech", "option": "local"},
            "policy": {"private": "SENSITIVE_SENTINEL"},
        },
    )
    controller.execute_plan.return_value = {"outcome": "applied"}
    monkeypatch.setattr(server, "controller", controller)

    class Context:
        async def elicit(self, message: str, schema: Any) -> Any:
            assert "speech" in message and "local" in message and "desk" in message
            assert "SENSITIVE_SENTINEL" not in message
            return SimpleNamespace(action="accept", data=SimpleNamespace(approve=True))

    result = asyncio.run(server.apply_station_action("preview", "a" * 64, Context()))
    assert result["outcome"] == "applied"
    controller.execute_plan.assert_called_once()


@pytest.mark.parametrize("action,params", [
    ("led.effect", {"red": 0, "green": 8, "blue": 0, "count": 1, "period_ms": 500}),
    ("audio.volume", {"percent": 10}),
    ("audio.play", {"asset": "chime", "duration_seconds": 1}),
])
def test_operator_preauthorization_needs_no_elicitation(monkeypatch: Any, action: str,
                                                       params: dict[str, Any]) -> None:
    controller = Mock()
    plan = SimpleNamespace(
        station=SimpleNamespace(id="desk", controls={"preauthorized_actions": [action]}),
        action=action, request={"action": action, "params": params},
    )
    controller.take_plan.return_value = plan
    controller.execute_plan.return_value = {"outcome": "applied"}
    monkeypatch.setattr(server, "controller", controller)
    assert asyncio.run(server.apply_station_action("preview", "a" * 64, object())) == {
        "outcome": "applied",
    }
    controller.take_plan.assert_called_once_with("preview", "a" * 64)
    controller.execute_plan.assert_called_once_with(plan)


@pytest.mark.parametrize("action", sorted(server.device_runtime.ACTIONS))
def test_explicit_all_action_preauthorization_skips_every_device_form(
    monkeypatch: Any, action: str,
) -> None:
    controller = Mock()
    plan = SimpleNamespace(
        station=SimpleNamespace(id="desk", preauthorize_all_actions=True, controls={}),
        action=action, request={"action": action, "params": {}},
    )
    controller.take_plan.return_value = plan
    controller.execute_plan.return_value = {"outcome": "applied"}
    monkeypatch.setattr(server, "controller", controller)
    monkeypatch.setattr(server.controls, "describe", lambda request: "synthetic impact")
    assert asyncio.run(server.apply_station_action("preview", "a" * 64, object())) == {
        "outcome": "applied",
    }
    controller.take_plan.assert_called_once_with("preview", "a" * 64)
    controller.execute_plan.assert_called_once_with(plan)


def test_station_status_lists_every_station_even_when_one_is_offline(monkeypatch: Any) -> None:
    def probe(station_id: str, probe: str) -> dict[str, Any]:
        assert probe == "health"
        if station_id == "hall":
            raise server.SafetyError("SSH unavailable or failed; inspect locally")
        return {"evidence_class": "analytical", "data": {"uptime_seconds": 5.0}}

    controller = Mock()
    controller.probe.side_effect = probe
    monkeypatch.setattr(server, "controller", controller)
    monkeypatch.setattr(server, "load_inventory", lambda _: {
        "desk": SimpleNamespace(id="desk", model="station-mini"),
        "hall": SimpleNamespace(id="hall", model="station-lite"),
    })
    monkeypatch.setattr(server.controls, "capabilities", lambda _, station_id: {
        "host": "SENSITIVE_SENTINEL", "writes_enabled": True,
        "allowed_actions": ["speech.say"] if station_id == "desk" else [],
        "preauthorize_all_actions": station_id == "desk",
    })
    result = asyncio.run(server.station_status())
    assert result == {"evidence_class": "analytical", "stations": [
        {"id": "desk", "model": "station-mini", "online": True, "uptime_seconds": 5.0,
         "writes_enabled": True, "allowed_actions": ["speech.say"],
         "preauthorize_all_actions": True},
        {"id": "hall", "model": "station-lite", "online": False,
         "error": "SSH unavailable or failed; inspect locally", "writes_enabled": True,
         "allowed_actions": [], "preauthorize_all_actions": False},
    ]}


def test_run_station_action_does_a_preauthorized_task_in_one_call(monkeypatch: Any) -> None:
    controller = Mock()
    controller.station.return_value = SimpleNamespace(native_mcp={})
    plan = SimpleNamespace(
        station=SimpleNamespace(id="desk", preauthorize_all_actions=True, controls={}),
        action="speech.say", request={"action": "speech.say", "params": {"text": "hi"}},
    )
    controller.take_plan.return_value = plan
    controller.execute_plan.return_value = {"outcome": "applied"}
    monkeypatch.setattr(server, "controller", controller)
    preview = Mock(return_value={"plan_id": "fresh", "expected_sha256": "a" * 64})
    monkeypatch.setattr(server.controls, "preview", preview)
    assert asyncio.run(server.run_station_action(
        "desk", "speech.say", {"text": "hi"}, object(),
    )) == {"outcome": "applied"}
    preview.assert_called_once_with(controller, "desk", "speech.say", {"text": "hi"})
    controller.take_plan.assert_called_once_with("fresh", "a" * 64)
    controller.execute_plan.assert_called_once_with(plan)


def test_run_station_action_stops_at_a_blocked_preview(monkeypatch: Any) -> None:
    controller = Mock()
    controller.station.return_value = SimpleNamespace(native_mcp={})
    monkeypatch.setattr(server, "controller", controller)
    monkeypatch.setattr(server.controls, "preview", Mock(side_effect=server.SafetyError(
        "Device action not permitted by the private station policy",
    )))
    result = asyncio.run(server.run_station_action("desk", "led.effect", {}, object()))
    assert result["outcome"] == "blocked" and "not permitted" in result["error"]
    controller.take_plan.assert_not_called()
    controller.execute_plan.assert_not_called()


def test_preauthorization_does_not_cover_another_bounded_action(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(
        station=SimpleNamespace(id="desk", controls={"preauthorized_actions": ["led.effect"]}),
        action="audio.volume", request={"action": "audio.volume", "params": {"percent": 10}},
    )
    monkeypatch.setattr(server, "controller", controller)

    class Decline:
        async def elicit(self, message: str, schema: Any) -> Any:
            return SimpleNamespace(action="decline", data=None)

    result = asyncio.run(server.apply_station_action("preview", "a" * 64, Decline()))
    assert result["outcome"] == "cancelled"
    controller.execute_plan.assert_not_called()


def test_replay_still_requires_native_approval_and_never_hides_its_scope(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(
        station=SimpleNamespace(id="desk"), action="led.replay", request={
            "action": "led.replay", "params": {"capture_id": "a" * 32},
            "policy": {"private": "SENSITIVE_SENTINEL"},
        },
    )
    monkeypatch.setattr(server, "controller", controller)

    class Context:
        async def elicit(self, message: str, schema: Any) -> Any:
            assert "25 aa" in message and "exactly once" in message
            assert "not a new color" in message and "SENSITIVE_SENTINEL" not in message
            return SimpleNamespace(action="decline", data=None)

    result = asyncio.run(server.apply_station_action("preview", "a" * 64, Context()))
    assert result["outcome"] == "cancelled"
    controller.execute_plan.assert_not_called()


def test_pulse_requires_idle_confirmation_and_discloses_missing_readback(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(
        station=SimpleNamespace(id="desk"), action="led.pulse", request={
            "action": "led.pulse", "params": {"capture_id": "a" * 32, "channel": 1},
            "policy": {"private": "SENSITIVE_SENTINEL"},
        },
    )
    monkeypatch.setattr(server, "controller", controller)

    class Context:
        async def elicit(self, message: str, schema: Any) -> Any:
            assert "16/255" in message and "1-second" in message and "idle/dark" in message
            assert "not hardware readback" in message and "cannot be verified" in message
            assert "SENSITIVE_SENTINEL" not in message
            return SimpleNamespace(action="decline", data=None)

    result = asyncio.run(server.apply_station_action("preview", "a" * 64, Context()))
    assert result["outcome"] == "cancelled"
    controller.execute_plan.assert_not_called()


def test_animation_approval_discloses_preset_timing_and_mapping(monkeypatch: Any) -> None:
    controller = Mock()
    controller.take_plan.return_value = SimpleNamespace(
        station=SimpleNamespace(id="desk"), action="led.animate", request={
            "action": "led.animate", "params": {"capture_id": "a" * 32, "mode": "rainbow_swirl"},
            "policy": {"private": "SENSITIVE_SENTINEL"},
        },
    )
    monkeypatch.setattr(server, "controller", controller)

    class Context:
        async def elicit(self, message: str, schema: Any) -> Any:
            assert "rainbow_swirl" in message and "6 seconds" in message and "16/255" in message
            assert "idle/dark" in message and "not calibrated" in message
            assert "not hardware readback" in message and "SENSITIVE_SENTINEL" not in message
            return SimpleNamespace(action="decline", data=None)

    result = asyncio.run(server.apply_station_action("preview", "a" * 64, Context()))
    assert result["outcome"] == "cancelled"
    controller.execute_plan.assert_not_called()


def test_stdio_handshake_tools_and_no_private_inventory() -> None:
    async def run() -> None:
        env = dict(os.environ)
        env.pop("ALICE_MCP_INVENTORY", None)
        env.pop("ALICE_MCP_ALLOW_WRITES", None)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "alice_mcp"], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {
                "get_capabilities", "list_stations", "station_status", "station_probe",
                "search_knowledge",
                "get_knowledge", "list_skills", "read_skill", "preview_service_action",
                "apply_service_action", "get_model_interfaces", "get_station_controls",
                "preview_station_action", "apply_station_action", "run_station_action",
                "get_capture_status", "start_job", "list_jobs", "cancel_job",
                "voice_listen_start", "voice_listen_stop", "voice_status", "get_voice_events",
            }
            result = await session.call_tool("list_stations", {})
            assert not result.isError
            assert result.structuredContent == {"stations": [], "evidence_class": "caller"}
            unsafe = await session.call_tool("station_probe", {
                "station_id": "desk", "probe": "cat-devices-json",
            })
            assert unsafe.structuredContent is not None
            assert unsafe.structuredContent["outcome"] == "blocked"
    asyncio.run(run())


def test_sigterm_stops_the_voice_listener_and_exits_cleanly() -> None:
    import json
    import signal
    import subprocess

    env = {k: v for k, v in os.environ.items() if k != "ALICE_MCP_INVENTORY"}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.Popen([sys.executable, "-m", "alice_mcp"], env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"}}}).encode() + b"\n")
    process.stdin.flush()
    assert json.loads(process.stdout.readline())["id"] == 1  # Serving: handlers installed.
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=30) == 0
