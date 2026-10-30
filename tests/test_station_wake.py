"""Synthetic native wake RPC, state binding and event boundary; no station or model."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from test_voice_service import station

from alice_mcp import station_wake as wake
from alice_mcp.core import Controller, SafetyError


def complete() -> dict[str, Any]:
    return {"outcome": "complete", "events": [{"model": "komp", "t_ms": 1000,
             "probability": 0.99, "trusted": False}], "trusted": False, "do_not_retry": True,
             "audio_exported": False, "quasar_unchanged": True, "cleanup_verified": True}


@pytest.mark.parametrize("change", [
    {"trusted": True}, {"audio_exported": True}, {"quasar_unchanged": False},
    {"cleanup_verified": False}, {"outcome": "unknown"}, {"private": "SENTINEL"},
    {"events": [{"model": "komp", "t_ms": 1, "probability": 0.99, "trusted": True}]},
    {"events": [{"model": "komp", "t_ms": 1, "probability": True, "trusted": False}]},
    {"events": [{"model": "komp", "t_ms": 1, "probability": float("nan"), "trusted": False}]},
])
def test_incompatible_or_trusted_events_are_rejected(change: dict[str, Any]) -> None:
    with pytest.raises(SafetyError):
        wake.result({**complete(), **change})


@pytest.mark.parametrize("revoke", [False, True])
def test_one_tls_window_consumes_host_preview_and_does_not_retry(
    monkeypatch: Any, tmp_path: Path, revoke: bool,
) -> None:
    mini = replace(station(tmp_path), native_mcp={"url": "synthetic"},
                   controls={"firmware_sha256": "a" * 64, "allowed_actions": "all",
                             "voice": {"source": "station"}})
    controller = Controller(None, writes=True)
    monkeypatch.setattr(controller, "station", lambda _: mini)
    calls: list[str] = []

    def call(s: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
        calls.append(name)
        if name == "get_station_controls":
            if revoke:
                controller.writes = False
            return {"station_id": mini.id, "model": mini.model,
                    "state_contract": "alice.native.state.v1", "state_sha256": "c" * 64,
                    "independent_wake_word": True, "preauthorize_all_actions": True,
                    "allowed_actions": ["wake.listen"]}
        assert args["expected_state_sha256"] == "c" * 64
        raise TimeoutError("private transport details")

    monkeypatch.setattr(wake.native_transport, "call", call)
    if revoke:
        with pytest.raises(SafetyError):
            wake.listen_once(controller, mini.id)
        assert calls == ["get_station_controls"]
    else:
        assert wake.listen_once(controller, mini.id) == {
            "outcome": "unknown", "events": [], "do_not_retry": True}
        assert calls == ["get_station_controls", "run_station_action"]
    assert not controller.plans
