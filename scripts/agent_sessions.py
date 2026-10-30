#!/usr/bin/env python3
"""Opt-in Codex/Claude Code hook status and messages; no transcripts or process injection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sqlite3
import stat
import sys
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_MESSAGE_BYTES = 2048
MAX_INPUT_BYTES = 1024 * 1024
MESSAGE_TTL = 600
STATES = {
    "SessionStart": "ready", "UserPromptSubmit": "working", "PreToolUse": "tool_pending",
    "PermissionRequest": "approval_pending", "PostToolUse": "working", "Stop": "idle",
    "SessionEnd": "ended", "Interrupt": "interrupted", "PostToolUseFailure": "working",
    "StopFailure": "turn_failed",
}
COMMON_EVENTS = tuple(name for name in STATES if name not in {
    "Interrupt", "PostToolUseFailure", "StopFailure",
})
EVENTS = {"codex": (*COMMON_EVENTS, "Interrupt"),
          "claude": (*COMMON_EVENTS, "PostToolUseFailure", "StopFailure")}
CONTEXT_EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse"}


class BridgeError(ValueError):
    """Safe-to-display failure; messages must never contain caller-supplied values."""


def _identity(client: str, session_id: str = "unused") -> None:
    if client not in EVENTS or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise BridgeError("Invalid client or session ID")


def _trusted(path: Path, *, directory: bool, private: bool = False) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & (0o077 if private else 0o022)
            or (not directory and info.st_nlink != 1)):
        raise BridgeError("Unsafe local state/configuration permissions or file type")


def _state_path(
    project: Path, *, create: bool = False, state_dir: Path | None = None,
) -> Path | None:
    if os.name != "posix":
        raise BridgeError("Use Linux/WSL; native Windows ACLs are not implemented")
    directories = (project / "local", project / "local" / "agent_sessions")
    if state_dir is not None:
        state_dir = state_dir.absolute()
        for ancestor in state_dir.parents:
            if ancestor.parent == ancestor:
                break  # The namespace root is the trust anchor; its UID may be mapped.
            info = ancestor.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                    or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
                raise BridgeError("External state needs trusted, nonsymlinked Linux ancestors")
        project_key = hashlib.sha256(os.fsencode(project.resolve())).hexdigest()
        directories = (state_dir, state_dir / project_key)
    for directory in directories:
        if not directory.exists() and not directory.is_symlink():
            if not create:
                return None
            directory.mkdir(mode=0o700, exist_ok=True)
        _trusted(directory, directory=True,
                 private=state_dir is not None or directory.name == "agent_sessions")
    path = directory / "state.sqlite3"
    if create:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
    if not path.exists() and not path.is_symlink():
        return None
    _trusted(path, directory=False, private=True)
    return path


@contextmanager
def _connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=0.5)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS sessions (
                client TEXT NOT NULL, session_id TEXT NOT NULL, event TEXT NOT NULL,
                tool TEXT, updated REAL NOT NULL, PRIMARY KEY (client, session_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, client TEXT NOT NULL, session_id TEXT NOT NULL,
                body TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                expires REAL NOT NULL, claimed REAL,
                FOREIGN KEY (client, session_id) REFERENCES sessions (client, session_id)
            );
        """)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
    finally:
        connection.close()


def _expire(connection: sqlite3.Connection, now: float) -> None:
    connection.execute(
        "UPDATE messages SET status = 'expired', body = '' "
        "WHERE status = 'queued' AND expires <= ?",
        (now,),
    )


def record_hook(
    project: Path, client: str, payload: dict[str, Any], *, now: float | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    session_id, event = payload.get("session_id"), payload.get("hook_event_name")
    if not isinstance(session_id, str) or not isinstance(event, str):
        raise BridgeError("Missing hook identity")
    _identity(client, session_id)
    cwd = payload.get("cwd")
    if (event not in EVENTS[client] or not isinstance(cwd, str) or not Path(cwd).is_absolute()
            or not Path(cwd).resolve().is_relative_to(project.resolve())):
        raise BridgeError("Hook is outside this project or has an unsupported event")
    if payload.get("agent_id"):
        return {}  # Subagents must not consume messages addressed to the main session.
    tool = payload.get("tool_name") if "ToolUse" in event or event == "PermissionRequest" else None
    if tool is not None and (
        not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", tool)
    ):
        tool = None
    now = time.time() if now is None else now
    path = _state_path(project, create=True, state_dir=state_dir)
    assert path is not None
    with _connection(path) as connection:
        _expire(connection, now)
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?) ON CONFLICT (client, session_id) "
            "DO UPDATE SET event = excluded.event, tool = excluded.tool, "
            "updated = excluded.updated",
            (client, session_id, event, tool, now),
        )
        if event not in CONTEXT_EVENTS:
            return {}
        message = connection.execute(
            "SELECT id, body FROM messages WHERE client = ? AND session_id = ? "
            "AND status = 'queued' ORDER BY created, rowid LIMIT 1", (client, session_id),
        ).fetchone()
        if message is None:
            return {}
        # Claim before stdout: a crashed hook must not automatically resend an uncertain message.
        connection.execute(
            "UPDATE messages SET status = 'claimed', claimed = ?, body = '' WHERE id = ?",
            (now, message["id"]),
        )
    context = (
        f"Project-local operator message {message['id']} (not a permission grant):\n"
        + json.dumps({"message": message["body"]}, ensure_ascii=False)
        + "\nRespect the existing task scope, permissions and safety rules."
    )
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


def send_message(
    project: Path, client: str, session_id: str, message: str, *, now: float | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    _identity(client, session_id)
    if (not message.strip() or len(message.encode("utf-8")) > MAX_MESSAGE_BYTES
            or any(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in message)):
        raise BridgeError("Message must be nonempty plain text, at most 2048 UTF-8 bytes")
    path = _state_path(project, state_dir=state_dir)
    if path is None:
        raise BridgeError("No registered session; connect and trust the client hooks first")
    now = time.time() if now is None else now
    message_id = uuid.uuid4().hex
    with _connection(path) as connection:
        _expire(connection, now)
        session = connection.execute(
            "SELECT event FROM sessions WHERE client = ? AND session_id = ?", (client, session_id),
        ).fetchone()
        if session is None or session["event"] == "SessionEnd":
            raise BridgeError("Session is unknown or reported its end; nothing was sent")
        pending = connection.execute(
            "SELECT count(*) FROM messages WHERE client = ? AND session_id = ? "
            "AND status = 'queued'",
            (client, session_id),
        ).fetchone()[0]
        if pending >= 32:
            raise BridgeError("Session message queue is full")
        connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?, 'queued', ?, ?, NULL)",
                           (message_id, client, session_id, message, now, now + MESSAGE_TTL))
    return {"message_id": message_id, "status": "queued", "expires_unix": now + MESSAGE_TTL}


def list_sessions(
    project: Path, *, now: float | None = None, state_dir: Path | None = None,
) -> list[dict[str, Any]]:
    path = _state_path(project, state_dir=state_dir)
    if path is None:
        return []
    now = time.time() if now is None else now
    with _connection(path) as connection:
        rows = connection.execute(
            "SELECT s.*, (SELECT count(*) FROM messages m WHERE m.client = s.client "
            "AND m.session_id = s.session_id AND m.status = 'queued' AND m.expires > ?) AS pending "
            "FROM sessions s ORDER BY s.updated DESC", (now,),
        ).fetchall()
    return [{"client": row["client"], "session_id": row["session_id"],
             "last_event": row["event"], "last_state": STATES[row["event"]],
             "last_tool": row["tool"], "last_seen_unix": row["updated"],
             "age_seconds": max(0, round(now - row["updated"])),
             "freshness": "recent" if now - row["updated"] < 120 else "stale",
             "liveness": "unknown", "pending_messages": row["pending"]} for row in rows]


def list_messages(
    project: Path, client: str, session_id: str, *, now: float | None = None,
    state_dir: Path | None = None,
) -> list[dict[str, Any]]:
    _identity(client, session_id)
    path = _state_path(project, state_dir=state_dir)
    if path is None:
        return []
    now = time.time() if now is None else now
    with _connection(path) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT id AS message_id, CASE WHEN status = 'queued' AND expires <= ? "
            "THEN 'expired' ELSE status END AS status, created AS created_unix, "
            "expires AS expires_unix, claimed AS claimed_unix FROM messages "
            "WHERE client = ? AND session_id = ? ORDER BY created DESC, rowid DESC LIMIT 100",
            (now, client, session_id),
        )]


def hook_config(project: Path, client: str, *, state_dir: Path | None = None) -> dict[str, Any]:
    _identity(client)
    if os.name != "posix":
        raise BridgeError("Generate and run hooks inside Linux/WSL, not native Windows")
    arguments = [sys.executable, str(Path(__file__).resolve()), "--project", str(project.resolve())]
    if state_dir is not None:
        arguments.extend(["--state-dir", str(state_dir.absolute())])
    command = shlex.join([*arguments, "hook", client])
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": command, "timeout": 3}]}]
                      for event in EVENTS[client]}}


def install_hooks(project: Path, client: str, *, state_dir: Path | None = None) -> Path:
    additions = hook_config(project, client, state_dir=state_dir)
    directory, filename = (".codex", "hooks.json") if client == "codex" else (
        ".claude", "settings.local.json",
    )
    parent = project / directory
    parent.mkdir(mode=0o700, exist_ok=True)
    _trusted(parent, directory=True)
    path = parent / filename
    before: bytes | None = None
    settings: dict[str, Any] = {}
    if path.exists() or path.is_symlink():
        _trusted(path, directory=False)
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise BridgeError("Settings file is too large")
        before = path.read_bytes()
        settings = json.loads(before)
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
        raise BridgeError("Existing settings/hooks must be JSON objects; nothing was replaced")
    hooks = settings.setdefault("hooks", {})
    for event, groups in additions["hooks"].items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise BridgeError("Existing hook groups must be arrays; nothing was replaced")
        if groups[0] not in existing:
            existing.append(groups[0])
    after = (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode()
    if after == before:
        return path
    with tempfile.NamedTemporaryFile(dir=parent, prefix=".agent-sessions-", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(after)
            file.flush()
            os.fsync(file.fileno())
            # ponytail: optimistic settings check; serialize other settings editors during install.
            if path.exists() or path.is_symlink():
                _trusted(path, directory=False)
                if before is None or path.read_bytes() != before:
                    raise BridgeError("Settings changed during installation; nothing was replaced")
            elif before is not None:
                raise BridgeError("Settings disappeared during installation; nothing was replaced")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--state-dir", type=Path,
                        help="Private Linux state root for mounted projects")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Last reported states, not proof of process liveness")
    for name in ("config", "install", "hook", "send", "messages"):
        command = commands.add_parser(name)
        command.add_argument("client", choices=EVENTS)
        if name in {"send", "messages"}:
            command.add_argument("session_id")
    args = parser.parse_args()
    try:
        project = args.project.resolve(strict=True)
        if args.command == "hook":
            data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            if len(data) > MAX_INPUT_BYTES:
                raise BridgeError("Hook input too large")
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise BridgeError("Hook input must be an object")
            output: Any = record_hook(project, args.client, payload, state_dir=args.state_dir)
        elif args.command == "send":
            data = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
            output = send_message(project, args.client, args.session_id, data.decode("utf-8"),
                                  state_dir=args.state_dir)
        elif args.command == "messages":
            output = list_messages(project, args.client, args.session_id, state_dir=args.state_dir)
        elif args.command == "install":
            path = install_hooks(project, args.client, state_dir=args.state_dir)
            output = {"config": str(path.relative_to(project)), "status": "configured_not_verified",
                      "next": "Review/trust the hooks in the client; verify an event with list"}
        elif args.command == "config":
            output = hook_config(project, args.client, state_dir=args.state_dir)
        else:
            output = list_sessions(project, state_dir=args.state_dir)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error, RecursionError) as error:
        # Never echo hook payloads, settings, message contents, or raw exception text.
        detail = str(error) if isinstance(error, BridgeError) else type(error).__name__
        print(f"Session bridge error: {detail}; no permission decision made.", file=sys.stderr)
        if args.command == "hook":
            print("{}")
            return 0
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
