"""Disposable synthetic inventories; no device, credentials or network access."""

import copy
import json
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import device_runtime as rt
from alice_mcp import local_voice_binding as binding
from alice_mcp import native_transport
from alice_mcp.core import Controller, SafetyError

PROFILE = {"model_file": "/synthetic/model.onnx", "model_sha256": "b" * 64,
           "config_sha256": "c" * 64}


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: Any) -> Any:
    ca, token = tmp_path / "ca", tmp_path / "token"
    for path in (ca, token):
        path.write_text("synthetic")
        path.chmod(0o600)
    entry = {"id": "desk", "model": "station-mini", "preauthorize_all_actions": True,
             "native_mcp": {"url": "https://synthetic.invalid/mcp", "ca_file": str(ca),
                            "token_file": str(token)},
             "host": "synthetic.invalid", "port": 22, "user": "root",
             "identity_file": "/synthetic/key", "known_hosts_file": "/synthetic/hosts",
             "controls": {"firmware_sha256": "a" * 64, "allowed_actions": ["speech.say"]}}
    other = dict(copy.deepcopy(entry), id="other")
    data = {"stations": [entry, other]}
    inventory = tmp_path / "inventory"
    inventory.write_text(json.dumps(data))
    inventory.chmod(0o600)

    def no_ssh(*args: Any) -> str:
        raise AssertionError("No SSH fallback")

    controller = Controller(inventory, writes=True, runner=no_ssh)
    remote = {"station_id": "desk", "model": "station-mini", "tts_backend": "local_pcm",
              "state_contract": "alice.native.state.v1", "state_sha256": "d" * 64}
    calls = []

    def call(station: Any, tool: str, arguments: Any) -> Any:
        assert tool == "get_station_controls" and arguments == {"station_id": "desk"}
        calls.append(tool)
        return dict(remote)

    monkeypatch.setattr(native_transport, "call", call)
    return controller, controller.station("desk"), data, remote, calls


def test_binding_preserves_inventory_and_is_idempotent(setup: Any) -> None:
    controller, station, data, _, calls = setup
    path = controller.inventory
    original = path.read_bytes()
    result = binding.bind(controller, station, PROFILE)
    assert result == {"status": "bound", "backup_id": result["backup_id"]}
    backup = path.with_name(path.name + ".local-pcm-backup-" + result["backup_id"])
    assert backup.read_bytes() == original and backup.stat().st_mode & 0o777 == 0o600
    data["stations"][0]["controls"]["local_tts"] = PROFILE
    assert json.loads(path.read_bytes()) == data
    current = path.read_bytes()
    assert binding.bind(controller, controller.station("desk"), PROFILE) == {"status": "unchanged"}
    assert path.read_bytes() == current
    assert len(list(path.parent.glob("*.local-pcm-backup-*"))) == 1
    assert calls and set(calls) == {"get_station_controls"}


@pytest.mark.parametrize("field,value", [("station_id", "other"), ("model", "station-mini3"),
    ("tts_backend", "yandex"), ("state_contract", "old"), ("state_sha256", "invalid")])
def test_remote_fails_closed(setup: Any, field: str, value: str) -> None:
    controller, station, _, remote, _ = setup
    before = controller.inventory.read_bytes()
    remote[field] = value
    with pytest.raises(SafetyError):
        binding.bind(controller, station, PROFILE)
    assert controller.inventory.read_bytes() == before
    assert not list(controller.inventory.parent.glob("*.local-pcm-backup-*"))


@pytest.mark.parametrize("change", ["writes", "preauth", "action", "trust", "permissions",
                                   "symlink", "profile", "conflict", "oversized", "hash",
                                   "native", "model", "ancestor"])
def test_host_gates(setup: Any, change: str) -> None:
    controller, station, data, _, calls = setup
    path = controller.inventory
    profile = dict(PROFILE)
    if change == "writes":
        controller.writes = False
    elif change == "hash":
        station = replace(station, config_sha256="f" * 64)
    elif change == "native":
        station = replace(station, native_mcp={})
    elif change == "model":
        station = replace(station, model="station-mini3")
    elif change == "ancestor":
        path.parent.chmod(0o777)
    elif change == "trust":
        Path(station.native_mcp["ca_file"]).write_text("changed")
    elif change == "permissions":
        path.chmod(0o644)
    elif change == "symlink":
        target = path.with_name("target")
        path.rename(target)
        path.symlink_to(target)
    elif change == "profile":
        profile["provider"] = "cloud"
    elif change == "oversized":
        path.write_bytes(b" " * 65537)
    else:
        entry = data["stations"][0]
        if change == "preauth":
            entry["preauthorize_all_actions"] = False
        elif change == "action":
            entry["controls"]["allowed_actions"] = []
        else:
            entry["controls"]["local_tts"] = dict(PROFILE, model_sha256="e" * 64)
        path.write_text(json.dumps(data))
        station = controller.station("desk")
    before = path.read_bytes()
    with pytest.raises(SafetyError):
        binding.bind(controller, station, profile)
    assert path.read_bytes() == before and not calls


@pytest.mark.parametrize("change", ["inventory", "trust", "state", "backup_failure", "collision",
                                   "commit_race", "backup_symlink", "policy"])
def test_changes_during_binding_fail_closed(setup: Any, monkeypatch: Any, change: str) -> None:
    controller, station, _, remote, _ = setup
    path = controller.inventory
    original = path.read_bytes()
    atomic = rt._atomic
    concurrent = original + b"\n"
    collision = path.with_name(path.name + ".local-pcm-backup-" + "f" * 32)
    if change in {"collision", "backup_symlink"}:
        monkeypatch.setattr(secrets, "token_hex", lambda n: "f" * 32)
        if change == "backup_symlink":
            collision.symlink_to(path)
        else:
            collision.write_bytes(b"keep")
            collision.chmod(0o600)

    def write(target: Path, *args: Any, **kwargs: Any) -> None:
        if target != path:
            if change == "backup_failure":
                raise OSError("private detail")
            atomic(target, *args, **kwargs)
            if change == "inventory":
                path.write_bytes(concurrent)
            elif change == "trust":
                Path(station.native_mcp["token_file"]).write_text("changed")
            elif change == "state":
                remote["state_sha256"] = "e" * 64
            elif change == "policy":
                controller.writes = False
        else:
            if change == "commit_race":
                path.write_bytes(concurrent)
            atomic(target, *args, **kwargs)

    monkeypatch.setattr(rt, "_atomic", write)
    message = "^Local voice binding failed; inspect locally before retrying$"
    with pytest.raises(SafetyError, match=message):
        binding.bind(controller, station, PROFILE)
    assert path.read_bytes() == (concurrent if change in {"inventory", "commit_race"} else original)
    if change == "collision":
        assert collision.read_bytes() == b"keep"
    if change == "backup_symlink":
        assert collision.is_symlink()


def test_transport_failure_is_sanitized_without_fallback_or_writes(setup: Any,
                                                                 monkeypatch: Any) -> None:
    controller, station, _, _, _ = setup
    calls = []

    def fail(*args: Any) -> Any:
        calls.append(True)
        raise SafetyError("synthetic private detail")

    monkeypatch.setattr(native_transport, "call", fail)
    with pytest.raises(SafetyError, match="^Local voice binding failed;"):
        binding.bind(controller, station, PROFILE)
    assert calls == [True]
    assert not list(controller.inventory.parent.glob("*.local-pcm-backup-*"))


def test_exactly_two_atomic_writes(setup: Any, monkeypatch: Any) -> None:
    controller, station, _, _, _ = setup
    atomic = rt._atomic
    calls = []

    def write(path: Path, *args: Any, **kwargs: Any) -> None:
        calls.append(path)
        atomic(path, *args, **kwargs)

    monkeypatch.setattr(rt, "_atomic", write)
    result = binding.bind(controller, station, PROFILE)
    assert calls == [controller.inventory.with_name(
        controller.inventory.name + ".local-pcm-backup-" + result["backup_id"]),
        controller.inventory]
    binding.bind(controller, controller.station("desk"), PROFILE)
    assert len(calls) == 2
