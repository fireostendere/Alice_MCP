"""MCP boundary and real stdio protocol tests without station access."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from alice_mcp import server


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
                "get_capabilities", "list_stations", "station_probe", "search_knowledge",
                "get_knowledge", "list_skills", "read_skill", "preview_service_action",
                "apply_service_action",
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
