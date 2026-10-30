"""Host control orchestration; all station responses are synthetic."""

import asyncio
import base64
import copy
import hashlib
import json
import shlex
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from alice_mcp import controls, server
from alice_mcp.controls import invoke as real_invoke
from alice_mcp.core import Controller, SafetyError, SSHInput


@pytest.fixture
def device(tmp_path: Path, monkeypatch: Any) -> tuple[Controller, list[dict[str, Any]]]:
    key = tmp_path / "identity"
    key.touch(mode=0o600)
    hosts = tmp_path / "hosts"
    hosts.touch()
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"stations": [{
        "id": "desk", "model": "station-lite", "host": "station.invalid", "port": 2222,
        "user": "root", "identity_file": str(key), "known_hosts_file": str(hosts),
        "preauthorize_all_actions": False,  # Exercises the explicit approval-form path.
        "controls": {
            "firmware_sha256": "a" * 64,
            "allowed_actions": ["led.effect", "config.select"],
            "configs": {"speech": {
                "path": "/system/vendor/quasar/quasar.cfg", "pointer": ["uniProxyUrl"],
                "kind": "endpoint", "choices": {"local": "https://model.invalid/uni.ws"},
            }},
        },
    }]}))
    path.chmod(0o600)
    calls: list[dict[str, Any]] = []

    def invoke(controller: Controller, station: Any, request: dict[str, Any]) -> dict[str, Any]:
        calls.append(copy.deepcopy(request))
        if request["mode"] == "inspect":
            return {"outcome": "ready", "state_sha256": "b" * 64}
        return {"outcome": "applied", "backup_id": "c" * 32, "restoration_verified": True}

    monkeypatch.setattr(controls, "invoke", invoke)
    return Controller(path, writes=True), calls


def test_preview_and_apply_share_single_use_registry(device: Any) -> None:
    controller, calls = device
    params = {"red": 0, "green": 8, "blue": 0, "count": 2, "period_ms": 500}
    plan = controls.preview(controller, "desk", "led.effect", params)
    assert [r["mode"] for r in calls] == ["inspect"]
    result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert result["outcome"] == "applied" and result["do_not_retry"] is True
    assert [r["mode"] for r in calls] == ["inspect", "inspect", "apply"]
    assert calls[-1]["expected_sha256"] == "b" * 64
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])


@pytest.mark.parametrize("change", ["none", "revoke", "expire", "state", "disabled", "unknown"])
@pytest.mark.parametrize("delegation", ["bounded", "all"])
def test_preauthorized_action_keeps_one_use_and_live_checks(device: Any, monkeypatch: Any,
                                                          change: str, delegation: str) -> None:
    controller, calls = device
    data = json.loads(controller.inventory.read_text())
    if delegation == "all":
        data["stations"][0]["preauthorize_all_actions"] = True
        action, params = "config.select", {"binding": "speech", "option": "local"}
        expected_actions = ["config.select", "led.effect"]
    else:
        data["stations"][0]["controls"]["preauthorized_actions"] = ["led.effect"]
        action = "led.effect"
        params = {"red": 0, "green": 8, "blue": 0, "count": 1, "period_ms": 500}
        expected_actions = ["led.effect"]
    controller.inventory.write_text(json.dumps(data))
    monkeypatch.setattr(server, "controller", controller)
    assert controls.capabilities(controller, "desk")["preauthorized_actions"] == expected_actions
    plan = controls.preview(controller, "desk", action, params)
    assert plan["approval_required"] is False
    assert [r["mode"] for r in calls] == ["inspect"]
    if change == "revoke":
        if delegation == "all":
            data["stations"][0]["preauthorize_all_actions"] = False
        else:
            data["stations"][0]["controls"]["preauthorized_actions"] = []
        controller.inventory.write_text(json.dumps(data))
    elif change == "expire":
        monkeypatch.setattr(controller, "clock", lambda: float("inf"))
    elif change == "disabled":
        controller.writes = False
    elif change in {"state", "unknown"}:
        original = controls.invoke

        def invoke(*args: Any) -> dict[str, Any]:
            result = original(*args)
            if change == "state":
                result["state_sha256"] = "d" * 64
            elif args[2]["mode"] == "apply":
                raise SafetyError("SENSITIVE_SENTINEL")
            return result

        monkeypatch.setattr(controls, "invoke", invoke)
    context = AsyncMock()
    result = asyncio.run(server.apply_station_action(plan["plan_id"], plan["expected_sha256"],
                                                     context))
    context.elicit.assert_not_called()
    assert "SENSITIVE_SENTINEL" not in str(result)
    if change in {"none", "unknown"}:
        assert result["outcome"] == ("applied" if change == "none" else "unknown")
        assert result["do_not_retry"] is True
        assert calls[-1]["params"] == params
        assert [r["mode"] for r in calls] == ["inspect", "inspect", "apply"]
    else:
        assert result["outcome"] == "blocked"
        assert all(r["mode"] == "inspect" for r in calls)
    again = asyncio.run(server.apply_station_action(plan["plan_id"], plan["expected_sha256"],
                                                    context))
    assert again["outcome"] == "blocked"


def test_unpreauthorized_policy_still_requires_native_approval(device: Any,
                                                              monkeypatch: Any) -> None:
    controller, calls = device
    monkeypatch.setattr(server, "controller", controller)
    assert controls.capabilities(controller, "desk")["preauthorized_actions"] == []
    plan = controls.preview(controller, "desk", "led.effect", {
        "red": 0, "green": 8, "blue": 0, "count": 1, "period_ms": 500,
    })
    assert plan["approval_required"] is True
    context = AsyncMock()
    context.elicit.side_effect = RuntimeError("Native elicitation unavailable")
    result = asyncio.run(server.apply_station_action(plan["plan_id"], plan["expected_sha256"],
                                                     context))
    assert result["outcome"] == "blocked"
    context.elicit.assert_called_once()
    assert [r["mode"] for r in calls] == ["inspect"]


def test_all_action_preauthorization_does_not_enable_forbidden_actions(device: Any) -> None:
    controller, calls = device
    data = json.loads(controller.inventory.read_text())
    data["stations"][0]["preauthorize_all_actions"] = True
    controller.inventory.write_text(json.dumps(data))
    with pytest.raises(SafetyError, match="not permitted"):
        controls.preview(controller, "desk", "audio.volume", {"percent": 10})
    assert not calls


def test_private_config_values_never_appear_in_inventory_or_preview(device: Any) -> None:
    controller, _ = device
    inventory = controls.capabilities(controller, "desk")
    plan = controls.preview(controller, "desk", "config.select", {
        "binding": "speech", "option": "local",
    })
    for value in (inventory, plan):
        assert "model.invalid" not in json.dumps(value)
        assert "quasar.cfg" not in json.dumps(value)
    assert "speech" in plan["impact"] and "local" in plan["impact"]


def test_led_impact_warns_about_firmware_renderer_race(device: Any) -> None:
    controller, _ = device
    plan = controls.preview(controller, "desk", "led.effect", {
        "red": 0, "green": 8, "blue": 0, "count": 1, "period_ms": 500,
    })
    assert "renderer" in plan["impact"] and "status" in plan["impact"]
    assert "concurrent" in plan["impact"] and "keep Quasar enabled" in plan["impact"]


@pytest.mark.parametrize("action,params", [
    ("gpio.write", {"pin": 1, "value": 1}),
    ("led.effect", {"red": 255, "green": 0, "blue": 0, "count": 2, "period_ms": 500}),
    ("config.select", {"binding": "speech", "option": "not-approved"}),
    ("audio.play", {"asset": "missing", "duration_seconds": 3}),
])
def test_unsafe_or_unapproved_actions_do_not_reach_station(device: Any, action: str,
                                                         params: dict[str, Any]) -> None:
    controller, calls = device
    with pytest.raises(SafetyError):
        controls.preview(controller, "desk", action, params)
    assert not calls


def test_readonly_default_and_intervening_private_edits(device: Any) -> None:
    controller, calls = device
    controller.writes = False
    with pytest.raises(SafetyError):
        controls.preview(controller, "desk", "config.select", {
            "binding": "speech", "option": "local",
        })
    assert not calls
    controller.writes = True
    plan = controls.preview(controller, "desk", "config.select", {
        "binding": "speech", "option": "local",
    })
    path = controller.inventory
    path.write_text(path.read_text() + "\n")
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert len(calls) == 1


def test_stale_remote_snapshot_is_rejected(device: Any, monkeypatch: Any) -> None:
    controller, calls = device
    plan = controls.preview(controller, "desk", "config.select", {
        "binding": "speech", "option": "local",
    })
    monkeypatch.setattr(controls, "invoke", lambda *_: {
        "outcome": "ready", "state_sha256": "d" * 64,
    })
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert len(calls) == 1


def test_transport_failure_after_apply_is_unknown(device: Any, monkeypatch: Any) -> None:
    controller, _ = device
    plan = controls.preview(controller, "desk", "config.select", {
        "binding": "speech", "option": "local",
    })
    old = controls.invoke

    def fail(controller: Any, station: Any, request: dict[str, Any]) -> dict[str, Any]:
        if request["mode"] == "apply":
            raise SafetyError("Synthetic transport failure")
        return old(controller, station, request)

    monkeypatch.setattr(controls, "invoke", fail)
    result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert result["outcome"] == "unknown" and result["do_not_retry"]
    assert len(result["backup_candidate_id"]) == 32


def test_remote_response_has_typed_allowlist_only() -> None:
    for text in ('{"outcome":"applied","private":"SENSITIVE_SENTINEL"}',
                 '{"outcome":"SENSITIVE_SENTINEL"}', 'SENSITIVE_SENTINEL'):
        with pytest.raises(SafetyError) as error:
            controls.parse_response(text)
        assert "SENSITIVE_SENTINEL" not in str(error.value)


def test_host_and_deployed_helper_roundtrip_without_a_device(device: Any, tmp_path: Path,
                                                            monkeypatch: Any) -> None:
    from alice_mcp import device_runtime

    controller, _ = device
    root = tmp_path / "remote"
    firmware = root / "system/vendor/quasar/maind"
    firmware.parent.mkdir(parents=True)
    firmware.write_bytes(b"synthetic")
    config = firmware.with_name("quasar.cfg")
    config.write_text('{"uniProxyUrl":"https://old.invalid/","keep":true}')
    (root / "data/opt").mkdir(parents=True)
    data = json.loads(controller.inventory.read_text())
    policy = data["stations"][0]["controls"]
    policy["firmware_sha256"] = hashlib.sha256(b"synthetic").hexdigest()
    controller.inventory.write_text(json.dumps(data))
    observed = []

    def transport(station: Any, command: str | SSHInput) -> str:
        source = command.data.decode() if isinstance(command, SSHInput) else command
        tokens = list(shlex.shlex(source, posix=True, punctuation_chars=True))
        index = tokens.index("-c")
        assert tokens[index + 1] == Path(device_runtime.__file__).read_text()
        request = json.loads(base64.b64decode(tokens[index + 2]))
        observed.append(request["mode"])
        return json.dumps(device_runtime.dispatch(request, root))

    monkeypatch.setattr(controls, "invoke", real_invoke)
    controller.runner = transport
    plan = controls.preview(controller, "desk", "config.select", {
        "binding": "speech", "option": "local",
    })
    assert observed == ["inspect"] and not (root / "data/opt/alice-mcp").exists()
    result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert result["outcome"] == "applied"
    assert observed == ["inspect", "inspect", "apply"]
    assert json.loads(config.read_text()) == {
        "uniProxyUrl": "https://model.invalid/uni.ws", "keep": True,
    }
