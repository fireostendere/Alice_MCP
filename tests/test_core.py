"""Synthetic offline checks; these do not establish station compatibility."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from alice_mcp import core, server
from alice_mcp.core import Controller, SafetyError, load_inventory, parse_probe, ssh_argv


@pytest.fixture
def inventory(tmp_path: Path) -> Path:
    key = tmp_path / "identity"
    hosts = tmp_path / "hosts"
    key.touch(mode=0o600)
    hosts.touch()
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"stations": [{
        "id": "desk", "model": "station-mini3", "host": "station.invalid", "port": 2222,
        "user": "root", "identity_file": str(key), "known_hosts_file": str(hosts),
        "allow_service_actions": True, "quasar_script_sha256": "a" * 64,
    }]}))
    path.chmod(0o600)
    return path


def test_inventory_and_ssh_are_key_only_and_host_pinned(inventory: Path) -> None:
    station = load_inventory(inventory)["desk"]
    args = ssh_argv(station, "true")
    assert "StrictHostKeyChecking=yes" in args
    assert "PasswordAuthentication=no" in args
    assert "KbdInteractiveAuthentication=no" in args
    assert "BatchMode=yes" in args
    assert "none" in args and "-F" in args
    assert not any(arg.startswith("HostKeyAlias=") for arg in args)
    assert args[-2:] == ["root@station.invalid", "true"]


def test_parallel_probes_bound_active_transports(inventory: Path) -> None:
    lock = threading.Lock()
    release, two_started, excessive = threading.Event(), threading.Event(), threading.Event()
    active, maximum = 0, 0

    def runner(station: Any, command: Any) -> str:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                two_started.set()
            if active > 2:
                excessive.set()
        try:
            assert release.wait(3)
            return "errors=0\nwarnings=0\n"
        finally:
            with lock:
                active -= 1

    controller = Controller(inventory, runner=runner)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(controller.probe, "desk", "logs") for _ in range(6)]
        try:
            assert two_started.wait(2)
            assert not excessive.wait(0.1)
        finally:
            release.set()
        assert all(f.result()["data"] == {"errors": 0, "warnings": 0} for f in futures)
    assert maximum == 2


def test_inventory_host_key_alias_pins_ssh_without_loading_user_config(inventory: Path) -> None:
    data = json.loads(inventory.read_text())
    data["stations"][0]["host_key_alias"] = "desk-owner"
    inventory.write_text(json.dumps(data))
    station = load_inventory(inventory)["desk"]
    args = ssh_argv(station, "true")
    assert station.host_key_alias == "desk-owner"
    index = args.index("HostKeyAlias=desk-owner")
    assert args[index - 1] == "-o"
    assert args[1:3] == ["-F", "none"]
    assert "StrictHostKeyChecking=yes" in args
    assert args[-2:] == ["root@station.invalid", "true"]


@pytest.mark.parametrize("change", ["writable-file", "writable-parent", "symlink"])
def test_inventory_rejects_mutable_host_trust(inventory: Path, change: str) -> None:
    data = json.loads(inventory.read_text())
    hosts = Path(data["stations"][0]["known_hosts_file"])
    if change == "writable-file":
        hosts.chmod(0o666)
    elif change == "writable-parent":
        hosts.parent.chmod(0o777)
    else:
        target = hosts.with_name("actual-hosts")
        hosts.rename(target)
        hosts.symlink_to(target)
    try:
        with pytest.raises(SafetyError):
            load_inventory(inventory)
    finally:
        inventory.parent.chmod(0o700)


def test_ssh_host_file_with_spaces_is_one_quoted_path(inventory: Path) -> None:
    data = json.loads(inventory.read_text())
    hosts = Path(data["stations"][0]["known_hosts_file"])
    moved = hosts.with_name("trusted hosts")
    hosts.rename(moved)
    data["stations"][0]["known_hosts_file"] = str(moved)
    inventory.write_text(json.dumps(data))
    station = load_inventory(inventory)["desk"]
    assert f'UserKnownHostsFile="{moved}"' in ssh_argv(station, "true")


@pytest.mark.parametrize("field,value", [
    ("host", "-oProxyCommand=bad"), ("host", "station;id"), ("port", True),
    ("user", "root;id"), ("model", "unknown"), ("id", "../desk"),
    ("allow_service_actions", "yes"), ("password", "not-a-real-secret"),
    ("preauthorize_all_actions", "true"), ("preauthorize_all_actions", 1),
    ("preauthorize_all_actions", None),
    ("host_key_alias", True), ("host_key_alias", ""),
    ("host_key_alias", "desk\nProxyCommand=bad"), ("host_key_alias", "desk owner"),
    ("host_key_alias", "%h"), ("host_key_alias", "*.invalid"),
    ("host_key_alias", "a" * 254),
])
def test_inventory_rejects_untrusted_fields(inventory: Path, field: str, value: object) -> None:
    data = json.loads(inventory.read_text())
    data["stations"][0][field] = value
    inventory.write_text(json.dumps(data))
    with pytest.raises(SafetyError):
        load_inventory(inventory)


def test_inventory_private_errors_do_not_echo_values(inventory: Path) -> None:
    inventory.write_text("SENSITIVE_SENTINEL")
    with pytest.raises(SafetyError) as error:
        load_inventory(inventory)
    assert "SENSITIVE_SENTINEL" not in str(error.value)


def test_remote_output_is_allowlisted_not_redacted() -> None:
    output = "uptime=12.3\nmem_total=100\nmem_available=20\narch=armv7l\nSENSITIVE_SENTINEL\n"
    assert parse_probe("health", output) == {
        "uptime_seconds": 12.3, "memory_total_kib": 100,
        "memory_available_kib": 20, "kernel_arch": "armv7l",
    }
    with pytest.raises(SafetyError):
        parse_probe("health", "uptime=nan\nmem_total=1\nmem_available=0\narch=secret")
    with pytest.raises(SafetyError):
        parse_probe("health", "uptime=1\nuptime=2\n")
    assert parse_probe("logs", "errors=2\nwarnings=3\n") == {"errors": 2, "warnings": 3}


class FakeSSH:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sha = "a" * 64
        self.fail_action = False

    def __call__(self, station: object, command: str) -> str:
        self.calls.append(command)
        if command == "/etc/init.d/S99quasar restart":
            if self.fail_action:
                raise SafetyError("SSH operation failed; outcome may be unknown")
            return ""
        return f"script_sha256={self.sha}\nlauncher=1\n"


@pytest.mark.parametrize("preauthorized", [False, True])
def test_service_preview_is_read_only_and_apply_is_one_shot(
    inventory: Path, monkeypatch: Any, preauthorized: bool,
) -> None:
    data = json.loads(inventory.read_text())
    data["stations"][0]["preauthorize_all_actions"] = preauthorized
    inventory.write_text(json.dumps(data))
    ssh = FakeSSH()
    controller = Controller(inventory, writes=True, runner=ssh)
    plan = controller.preview_service("desk", "restart")
    assert plan["approval_required"] is not preauthorized
    assert all(command != "/etc/init.d/S99quasar restart" for command in ssh.calls)
    if preauthorized:
        monkeypatch.setattr(server, "controller", controller)
        context = AsyncMock()
        result = asyncio.run(server.apply_service_action(
            plan["plan_id"], plan["expected_sha256"], context,
        ))
        context.elicit.assert_not_called()
    else:
        result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert result["outcome"] == "command_succeeded" and result["launcher_running"] is True
    assert result["restart_verified"] is False  # A no-op script could return success too.
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])


def test_revoking_all_action_preauthorization_invalidates_service_preview(
    inventory: Path, monkeypatch: Any,
) -> None:
    data = json.loads(inventory.read_text())
    data["stations"][0]["preauthorize_all_actions"] = True
    inventory.write_text(json.dumps(data))
    ssh = FakeSSH()
    controller = Controller(inventory, writes=True, runner=ssh)
    monkeypatch.setattr(server, "controller", controller)
    plan = controller.preview_service("desk", "restart")
    data["stations"][0]["preauthorize_all_actions"] = False
    inventory.write_text(json.dumps(data))
    context = AsyncMock()
    result = asyncio.run(server.apply_service_action(
        plan["plan_id"], plan["expected_sha256"], context,
    ))
    assert result["outcome"] == "blocked"
    context.elicit.assert_not_called()
    assert "/etc/init.d/S99quasar restart" not in ssh.calls


@pytest.mark.parametrize("preauthorized", [False, True])
def test_write_gates_and_stale_state(inventory: Path, preauthorized: bool) -> None:
    data = json.loads(inventory.read_text())
    data["stations"][0]["preauthorize_all_actions"] = preauthorized
    inventory.write_text(json.dumps(data))
    ssh = FakeSSH()
    with pytest.raises(SafetyError):
        Controller(inventory, runner=ssh).preview_service("desk", "restart")
    controller = Controller(inventory, writes=True, runner=ssh)
    for action in ("stop", "restart;id", "reboot"):
        with pytest.raises(SafetyError):
            controller.preview_service("desk", action)
    plan = controller.preview_service("desk", "restart")
    ssh.sha = "b" * 64
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert "/etc/init.d/S99quasar restart" not in ssh.calls


def test_config_changes_and_bad_sha_consume_plans(inventory: Path) -> None:
    ssh = FakeSSH()
    controller = Controller(inventory, writes=True, runner=ssh)
    plan = controller.preview_service("desk", "restart")
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], "b" * 64)
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    plan = controller.preview_service("desk", "restart")
    inventory.write_text(inventory.read_text() + "\n")
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])


def test_host_key_alias_change_invalidates_preview(inventory: Path) -> None:
    data = json.loads(inventory.read_text())
    data["stations"][0]["host_key_alias"] = "desk-owner"
    inventory.write_text(json.dumps(data))
    ssh = FakeSSH()
    controller = Controller(inventory, writes=True, runner=ssh)
    plan = controller.preview_service("desk", "restart")
    data["stations"][0]["host_key_alias"] = "other-owner"
    inventory.write_text(json.dumps(data))
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert "/etc/init.d/S99quasar restart" not in ssh.calls


def test_key_rewrite_with_restored_mtime_invalidates_preview(inventory: Path) -> None:
    key = Path(json.loads(inventory.read_text())["stations"][0]["identity_file"])
    key.write_bytes(b"synthetic-A")
    stamp = key.stat()
    ssh = FakeSSH()
    controller = Controller(inventory, writes=True, runner=ssh)
    plan = controller.preview_service("desk", "restart")
    key.write_bytes(b"synthetic-B")
    os.utime(key, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert "/etc/init.d/S99quasar restart" not in ssh.calls


def test_expiry_and_uncertain_write_are_not_retried(inventory: Path) -> None:
    ssh = FakeSSH()
    clock = [0.0]
    controller = Controller(inventory, writes=True, runner=ssh, clock=lambda: clock[0])
    plan = controller.preview_service("desk", "restart")
    clock[0] = 301.0
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    plan = controller.preview_service("desk", "restart")
    ssh.fail_action = True
    result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert result["outcome"] == "unknown"
    assert ssh.calls.count("/etc/init.d/S99quasar restart") == 1
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])


def test_preview_expiring_during_revalidation_does_not_write(inventory: Path) -> None:
    ssh = FakeSSH()
    clock = [0.0]
    controller = Controller(inventory, writes=True, runner=ssh, clock=lambda: clock[0])
    plan = controller.preview_service("desk", "restart")

    def slow_state(station: Any, command: str) -> str:
        clock[0] = 301.0
        return ssh(station, command)

    controller.runner = slow_state
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert "/etc/init.d/S99quasar restart" not in ssh.calls


def test_non_ascii_digest_is_a_safe_consumed_rejection(inventory: Path) -> None:
    controller = Controller(inventory, writes=True, runner=FakeSSH())
    plan = controller.preview_service("desk", "start")
    with pytest.raises(SafetyError):
        controller.take_plan(plan["plan_id"], "ж" * 64)
    with pytest.raises(SafetyError):
        controller.take_plan(plan["plan_id"], plan["expected_sha256"])


def test_transport_bounds_and_no_remote_error_leaks(inventory: Path, monkeypatch: Any) -> None:
    station = load_inventory(inventory)["desk"]
    monkeypatch.setattr(core, "ssh_argv", lambda *_: [
        sys.executable, "-c", "import sys; sys.stderr.write('SENSITIVE_SENTINEL'); sys.exit(1)",
    ])
    with pytest.raises(SafetyError) as error:
        core.run_ssh(station, "not executed")
    assert "SENSITIVE_SENTINEL" not in str(error.value)
    monkeypatch.setattr(core, "MAX_OUTPUT", 32)
    monkeypatch.setattr(core, "ssh_argv", lambda *_: [
        sys.executable, "-c", "print('x' * 1024)",
    ])
    with pytest.raises(SafetyError, match="output limit"):
        core.run_ssh(station, "not executed")


def test_transport_timeout_kills_local_test_process(inventory: Path, monkeypatch: Any) -> None:
    station = load_inventory(inventory)["desk"]
    monkeypatch.setattr(core, "ssh_argv", lambda *_: [
        sys.executable, "-c", "import time; time.sleep(2)",
    ])
    wait = subprocess.Popen.wait

    def short_wait(self: Any, timeout: float | None = None) -> int:
        return wait(self, timeout=0.1)

    monkeypatch.setattr(subprocess.Popen, "wait", short_wait)
    with pytest.raises(SafetyError, match="timed out"):
        core.run_ssh(station, "not executed")


def test_bundled_helper_is_bounded_stdin_not_a_long_ssh_command(inventory: Path,
                                                             monkeypatch: Any) -> None:
    station = load_inventory(inventory)["desk"]
    payload = b"synthetic-helper\n" * 4000
    command = core.SSHInput("sh -s", payload)
    assert ssh_argv(station, command)[-1] == "sh -s"
    assert payload.decode() not in " ".join(ssh_argv(station, command))
    monkeypatch.setattr(core, "ssh_argv", lambda *_: [
        sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read()))",
    ])
    assert int(core.run_ssh(station, command)) == len(payload)
    with pytest.raises(SafetyError):
        core.run_ssh(station, core.SSHInput("sh -s", b"x" * (120 * 1024 + 1)))


def test_bundled_helper_input_failure_does_not_hang_or_leak(inventory: Path,
                                                         monkeypatch: Any) -> None:
    station = load_inventory(inventory)["desk"]
    monkeypatch.setattr(core, "ssh_argv", lambda *_: [
        sys.executable, "-c", "import sys; sys.stderr.write('SENSITIVE_SENTINEL'); sys.exit(1)",
    ])
    with pytest.raises(SafetyError) as error:
        core.run_ssh(station, core.SSHInput("sh -s", b"x" * (100 * 1024)))
    assert "SENSITIVE_SENTINEL" not in str(error.value)
