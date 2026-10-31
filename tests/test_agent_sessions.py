"""Project-local hooks use synthetic sessions, never existing client histories."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from scripts import agent_sessions as bridge

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux/WSL hook bridge")


def event(project: Path, name: str = "PreToolUse", **extra: Any) -> dict[str, Any]:
    return {"session_id": "synthetic-session", "cwd": str(project),
            "hook_event_name": name, "tool_name": "Bash", **extra}


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_status_and_messages_do_not_copy_private_hook_fields(
    tmp_path: Path, client: str,
) -> None:
    assert bridge.list_sessions(tmp_path, now=100) == []
    assert not (tmp_path / "local").exists()
    payload = event(tmp_path, "SessionStart", prompt="synthetic private prompt",
                    transcript_path="/PRIVATE/do-not-read", tool_input={"command": "private input"},
                    tool_response="private result", last_assistant_message="private answer")
    assert bridge.record_hook(tmp_path, client, payload, now=100) == {}
    receipt = bridge.send_message(
        tmp_path, client, "synthetic-session", "Run focused tests", now=101,
    )
    assert receipt["status"] == "queued"
    assert bridge.list_sessions(tmp_path, now=102)[0]["pending_messages"] == 1
    output = bridge.record_hook(tmp_path, client, event(tmp_path), now=103)
    assert output.keys() == {"hookSpecificOutput"}
    context = output["hookSpecificOutput"]
    assert context["hookEventName"] == "PreToolUse"
    assert "Run focused tests" in context["additionalContext"]
    assert "not a permission grant" in context["additionalContext"]
    assert bridge.record_hook(tmp_path, client, event(tmp_path), now=104) == {}
    receipts = bridge.list_messages(tmp_path, client, "synthetic-session", now=104)
    assert receipts[0]["status"] == "claimed"
    assert "body" not in receipts[0]
    serialized = json.dumps(bridge.list_sessions(tmp_path, now=105))
    assert "liveness" in serialized and "unknown" in serialized
    for path in (tmp_path / "local" / "agent_sessions").iterdir():
        data = path.read_bytes()
        for value in (b"synthetic private prompt", b"do-not-read", b"private input",
                      b"private result", b"private answer"):
            assert value not in data


def test_expiry_staleness_and_idle_events_do_not_wake_or_claim(tmp_path: Path) -> None:
    bridge.record_hook(tmp_path, "codex", event(tmp_path, "UserPromptSubmit"), now=100)
    bridge.send_message(tmp_path, "codex", "synthetic-session", "Do not lose this", now=101)
    assert bridge.record_hook(tmp_path, "codex", event(tmp_path, "Stop"), now=102) == {}
    assert bridge.list_messages(tmp_path, "codex", "synthetic-session", now=103)[0]["status"] \
        == "queued"
    stale = bridge.list_sessions(tmp_path, now=1000)[0]
    assert stale["freshness"] == "stale" and stale["last_state"] == "idle"
    assert stale["liveness"] == "unknown"
    assert bridge.record_hook(tmp_path, "codex", event(tmp_path), now=1000) == {}
    assert bridge.list_messages(tmp_path, "codex", "synthetic-session", now=1000)[0]["status"] \
        == "expired"
    bridge.record_hook(tmp_path, "codex", event(tmp_path, "SessionEnd"), now=1001)
    with pytest.raises(ValueError):
        bridge.send_message(tmp_path, "codex", "synthetic-session", "No restart", now=1002)


def test_concurrent_hooks_claim_once_and_clients_are_isolated(tmp_path: Path) -> None:
    for client in ("codex", "claude"):
        bridge.record_hook(tmp_path, client, event(tmp_path, "SessionStart"), now=100)
    bridge.send_message(tmp_path, "codex", "synthetic-session", "One copy", now=101)
    assert bridge.record_hook(tmp_path, "claude", event(tmp_path), now=102) == {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(
            lambda _: bridge.record_hook(tmp_path, "codex", event(tmp_path), now=103), range(4),
        ))
    assert sum(bool(output) for output in outputs) == 1


@pytest.mark.parametrize("damage", ["other_project", "escape", "bad_id", "bad_event"])
def test_hook_input_boundary_is_validated_before_writes(tmp_path: Path, damage: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    payload = event(project)
    if damage == "other_project":
        payload["cwd"] = str(tmp_path)
    elif damage == "escape":
        (project / "escape").symlink_to(tmp_path, target_is_directory=True)
        payload["cwd"] = str(project / "escape")
    elif damage == "bad_id":
        payload["session_id"] = "../other"
    else:
        payload["hook_event_name"] = "Unrecognized"
    with pytest.raises(ValueError):
        bridge.record_hook(project, "codex", payload)
    assert not (project / "local").exists()


def test_subagent_hooks_never_take_a_main_session_message(tmp_path: Path) -> None:
    bridge.record_hook(tmp_path, "claude", event(tmp_path, "SessionStart"))
    bridge.send_message(tmp_path, "claude", "synthetic-session", "Main session only")
    assert bridge.record_hook(tmp_path, "claude", event(tmp_path, agent_id="child")) == {}
    assert bridge.list_sessions(tmp_path)[0]["pending_messages"] == 1


@pytest.mark.parametrize("message", [
    "", " \n", "x" * 2049, "я" * 1025, "bad\x1b[31m", "bad\u009b31m",
])
def test_messages_are_bounded_plain_text(tmp_path: Path, message: str) -> None:
    bridge.record_hook(tmp_path, "codex", event(tmp_path, "SessionStart"))
    with pytest.raises(ValueError):
        bridge.send_message(tmp_path, "codex", "synthetic-session", message)


def test_missing_session_is_not_created_by_send(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        bridge.send_message(tmp_path, "codex", "missing", "No hidden session launch")


def test_private_state_rejects_symlinks_and_has_owner_only_permissions(tmp_path: Path) -> None:
    (tmp_path / "local").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        bridge.record_hook(tmp_path, "codex", event(tmp_path))
    (tmp_path / "local").unlink()
    bridge.record_hook(tmp_path, "codex", event(tmp_path))
    state = tmp_path / "local" / "agent_sessions"
    assert state.stat().st_mode & 0o077 == 0
    assert (state / "state.sqlite3").stat().st_mode & 0o077 == 0


def test_external_private_state_supports_mounted_projects_without_mixing_them(
    tmp_path: Path,
) -> None:
    state = tmp_path / "runtime"
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(mode=0o777)
    second.mkdir()
    bridge.record_hook(first, "codex", event(first), state_dir=state)
    assert bridge.list_sessions(first, state_dir=state)[0]["session_id"] == "synthetic-session"
    assert bridge.list_sessions(second, state_dir=state) == []
    assert not (first / "local").exists()
    config = bridge.hook_config(first, "codex", state_dir=state)
    assert "--state-dir" in config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_external_state_rejects_untrusted_ancestors(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    unsafe.chmod(0o777)
    with pytest.raises(ValueError):
        bridge.record_hook(tmp_path, "codex", event(tmp_path), state_dir=unsafe / "runtime")
    assert not (unsafe / "runtime").exists()


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_install_merges_idempotently_without_permission_changes(
    tmp_path: Path, client: str,
) -> None:
    directory, filename = (".codex", "hooks.json") if client == "codex" else (
        ".claude", "settings.local.json",
    )
    path = tmp_path / directory / filename
    path.parent.mkdir()
    original_hook = {"hooks": [{"type": "command", "command": "synthetic-existing-hook"}]}
    original = {"permissions": {"deny": ["Read(.env)"]},
                "hooks": {"PreToolUse": [original_hook]}, "unrelated": True}
    path.write_text(json.dumps(original))
    bridge.install_hooks(tmp_path, client)
    first = path.read_bytes()
    bridge.install_hooks(tmp_path, client)
    assert path.read_bytes() == first
    configured = json.loads(first)
    assert configured["permissions"] == original["permissions"]
    assert configured["unrelated"] is True
    assert configured["hooks"]["PreToolUse"][0] == original_hook
    assert len(configured["hooks"]["PreToolUse"]) == 2
    assert "Interrupt" in configured["hooks"] if client == "codex" else (
        "StopFailure" in configured["hooks"]
    )


@pytest.mark.parametrize("damage", ["unsafe_directory", "symlink_file", "wrong_shape"])
def test_installer_refuses_unsafe_config_without_replacing(tmp_path: Path, damage: str) -> None:
    directory = tmp_path / ".codex"
    directory.mkdir()
    path = directory / "hooks.json"
    original = b'{"unrelated":true}'
    if damage == "unsafe_directory":
        directory.chmod(0o777)
        path.write_bytes(original)
    elif damage == "symlink_file":
        other = tmp_path / "original.json"
        other.write_bytes(original)
        path.symlink_to(other)
    else:
        original = b'{"hooks":{"PreToolUse":null}}'
        path.write_bytes(original)
    with pytest.raises(ValueError):
        bridge.install_hooks(tmp_path, "codex")
    assert path.read_bytes() == original


def test_installer_preserves_detected_concurrent_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / ".codex"
    directory.mkdir()
    path = directory / "hooks.json"
    path.write_text('{}')
    fsync = os.fsync

    def changed(descriptor: int) -> None:
        fsync(descriptor)
        path.write_text('{"concurrent":true}')

    monkeypatch.setattr(bridge.os, "fsync", changed)
    with pytest.raises(ValueError, match="changed during"):
        bridge.install_hooks(tmp_path, "codex")
    assert json.loads(path.read_text()) == {"concurrent": True}
    assert list(directory.glob(".agent-sessions-*")) == []


def test_cli_hook_errors_are_nonblocking_and_do_not_echo_input(tmp_path: Path) -> None:
    script = Path(bridge.__file__)
    result = subprocess.run(
        [sys.executable, str(script), "--project", str(tmp_path), "hook", "codex"],
        input=b'{"prompt":"synthetic-private",', capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"{}"
    assert b"synthetic-private" not in result.stderr
    assert not (tmp_path / "local").exists()
