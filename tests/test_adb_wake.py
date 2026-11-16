"""Synthetic: SSH brought up over ADB after a reboot; never touches a real station or adb."""

import json
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import core
from alice_mcp.core import SafetyError, load_inventory


@pytest.fixture
def inventory(tmp_path: Path) -> Path:
    key, hosts, adb = tmp_path / "identity", tmp_path / "hosts", tmp_path / "adb"
    key.touch(mode=0o600)
    hosts.touch()
    adb.touch(mode=0o700)
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"stations": [{
        "id": "pro", "model": "station-mini3-pro", "host": "station.invalid", "port": 2222,
        "user": "root", "identity_file": str(key), "known_hosts_file": str(hosts),
        "adb_wake": {"adb": str(adb), "port": 5555, "script": "/data/opt/start-ssh.sh"},
    }]}))
    path.chmod(0o600)
    return path


def edit(inventory: Path, **changes: Any) -> None:
    data = json.loads(inventory.read_text())
    wake = data["stations"][0]["adb_wake"]
    for key, value in changes.items():
        if value is None:
            del wake[key]
        else:
            wake[key] = value
    inventory.write_text(json.dumps(data))


@pytest.mark.parametrize("changes", [
    {"adb": "adb"}, {"adb": "/nonexistent/adb"}, {"port": True}, {"port": 0},
    {"script": "/data/opt/start.sh; reboot"}, {"script": "/etc/init.d/S99quasar"},
    {"script": "/data/../etc/x"}, {"script": None}, {"extra": "x"},
])
def test_inventory_rejects_untrusted_adb_wake(inventory: Path, changes: dict) -> None:
    edit(inventory, **changes)
    with pytest.raises(SafetyError):
        load_inventory(inventory)


class Network:
    def __init__(self, states: list[type[OSError] | None]) -> None:
        self.states, self.commands = states, []

    def connect(self, address: tuple[str, int], timeout: float) -> Any:
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if state:
            raise state()
        return type("Socket", (), {"close": lambda self: None})()

    def run(self, argv: list[str], **_: Any) -> None:
        self.commands.append(argv)


def wire(monkeypatch: pytest.MonkeyPatch, network: Network) -> None:
    monkeypatch.setattr(core.socket, "create_connection", network.connect)
    monkeypatch.setattr(core.subprocess, "run", network.run)
    monkeypatch.setattr(core.time, "sleep", lambda _: None)


def test_refused_ssh_is_started_over_adb_before_connecting(
        inventory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    station = load_inventory(inventory)["pro"]
    network = Network([ConnectionRefusedError, ConnectionRefusedError, None])
    wire(monkeypatch, network)
    core.ensure_ssh(station)
    adb = station.adb_wake["adb"]
    assert network.commands == [
        [adb, "connect", "station.invalid:5555"],
        [adb, "-s", "station.invalid:5555", "shell",
         "start-stop-daemon -S -b -x /data/opt/start-ssh.sh"],
    ]


@pytest.mark.parametrize("state", [None, TimeoutError, OSError])
def test_open_or_unreachable_ssh_never_calls_adb(
        inventory: Path, monkeypatch: pytest.MonkeyPatch, state: type[OSError] | None) -> None:
    network = Network([state])
    wire(monkeypatch, network)
    core.ensure_ssh(load_inventory(inventory)["pro"])
    assert network.commands == []


def test_run_ssh_checks_before_its_only_attempt(
        inventory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order = []
    monkeypatch.setattr(core, "ensure_ssh", lambda station: order.append("ensure"))
    monkeypatch.setattr(core.subprocess, "Popen", lambda *a, **k: order.append("ssh") or 1 / 0)
    with pytest.raises(ZeroDivisionError):
        core.run_ssh(load_inventory(inventory)["pro"], "true")
    assert order == ["ensure", "ssh"]
