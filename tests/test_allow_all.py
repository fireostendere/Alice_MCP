"""Owner default (2026-10-29): "all" or an absent list allows every implemented action."""

import json
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller

FIRMWARE = {"firmware_sha256": "a" * 64}


@pytest.mark.parametrize("policy", [{**FIRMWARE, "allowed_actions": "all"}, dict(FIRMWARE)])
def test_all_or_missing_list_expands_to_every_implemented_action(policy: dict[str, Any]) -> None:
    result = rt.validate_policy(policy, "station-mini")
    assert result["allowed_actions"] == sorted(rt.ACTIONS)
    assert all(rt.allows(policy, action) for action in rt.ACTIONS)
    assert not rt.allows(policy, "shell") and not rt.allows({}, "speech.say")
    assert result["max_volume"] == 30  # Value bounds are unchanged.


def test_preauthorized_all_covers_only_preauthorizable_actions() -> None:
    policy = {**FIRMWARE, "allowed_actions": ["led.effect", "speech.say"],
              "preauthorized_actions": "all"}
    assert rt.validate_policy(policy, "station-mini")["preauthorized_actions"] == ["led.effect"]
    assert rt.preauthorized_actions(policy) == ["led.effect"]


def test_explicit_lists_stay_exact_and_invalid_values_fail() -> None:
    policy = {**FIRMWARE, "allowed_actions": ["speech.say"]}
    assert rt.allowed_actions(policy) == ["speech.say"] and not rt.allows(policy, "audio.play")
    for value in ("everything", ["all", "speech.say"], ["shell"]):
        with pytest.raises(ValueError):
            rt.validate_policy({**FIRMWARE, "allowed_actions": value}, "station-mini")


def test_inventory_defaults_allow_all_actions_and_reboot(tmp_path: Path) -> None:
    key, hosts = tmp_path / "identity", tmp_path / "hosts"
    key.touch(mode=0o600)
    hosts.touch()
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"stations": [{
        "id": "desk", "model": "station-mini3", "host": "station.invalid", "port": 2222,
        "user": "root", "identity_file": str(key), "known_hosts_file": str(hosts),
        "controls": dict(FIRMWARE)}]}))
    inventory.chmod(0o600)
    station = Controller(inventory).station("desk")
    assert station.controls["allowed_actions"] == sorted(rt.ACTIONS)
    assert station.allow_reboot is True
    assert station.preauthorize_all_actions is True and station.allow_service_actions is True
    entry = json.loads(inventory.read_text())
    entry["stations"][0].update(preauthorize_all_actions=False, allow_service_actions=False,
                                allow_reboot=False)
    inventory.write_text(json.dumps(entry))
    station = Controller(inventory).station("desk")  # An explicit false stays false.
    assert not (station.preauthorize_all_actions or station.allow_service_actions
                or station.allow_reboot)
