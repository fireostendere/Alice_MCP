"""Synthetic OpenCode voice protocol; never contacts an app, model or station."""
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import opencode_voice as ov
from alice_mcp.core import SafetyError


def test_agent_reuses_restricted_alice_session_and_sends_speech_on_stdin(
        monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[Any] = []

    def run(args: list[str], **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        if "session.create" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps({"data": {"id": "ses_test"}}))
        reply = {"mode": "conversation", "text": ""}
        event = {"type": "text", "part": {"text": json.dumps(reply)}}
        return subprocess.CompletedProcess(args, 0, json.dumps(event) + "\n")

    monkeypatch.setattr(ov.subprocess, "run", run)
    agent = ov.Agent("/PRIVATE/opencode2", "desk", tmp_path)
    for _ in range(2):
        assert agent.answer("Поболтай со мной", time.monotonic() + 5)["mode"] == "conversation"
    assert len(calls) == 3
    body = json.loads(calls[0][0][-1])
    assert body["agent"] == "alice" and body["model"]["id"] == "gpt-6-luna-fast"
    assert body["model"]["variant"] == "none"
    assert body["permissions"] == [
        {"action": "*", "resource": "*", "effect": "deny"},
        {"action": "websearch", "resource": "*", "effect": "allow"},
        {"action": "webfetch", "resource": "*", "effect": "allow"}]
    assert body["location"] == {"directory": str(tmp_path)}
    for args, options in calls[1:]:
        assert "Поболтай" not in str(args)
        assert "Поболтай со мной" in options["input"]
        assert "--session" in args and "ses_test" in args and not options.get("shell")


@pytest.mark.parametrize("reply", [
    {"mode": "command", "text": "Четыре."},
    {"mode": "dictation", "text": ""},
    {"mode": "action", "text": "Тише.", "action": "audio.volume",
     "parameters": {"step": "quieter"}},
])
def test_reply_preserves_typed_requests_for_the_station_guard(reply: dict[str, Any]) -> None:
    assert ov.parse(json.dumps(reply)) == reply


@pytest.mark.parametrize("reply", [
    "not JSON", '[]', '{"mode":"shell","text":"run"}',
    '{"mode":"command","text":""}',
    '{"mode":"conversation","text":"","permission":"allow"}',
    '{"mode":"action","text":"ok","action":"audio.volume","parameters":[]}',
    json.dumps({"mode": "command", "text": "x" * 6001}),
])
def test_malformed_reply_fails_closed_without_reflecting_model_text(reply: str) -> None:
    with pytest.raises(SafetyError, match="OpenCode voice reply unavailable"):
        ov.parse(reply)


def test_timeout_interrupts_owned_session_once_and_never_retries(monkeypatch: Any) -> None:
    calls: list[Any] = []

    def run(args: list[str], **kwargs: Any) -> Any:
        calls.append(args)
        if "run" in args:
            raise subprocess.TimeoutExpired(args, 1, output="PRIVATE_SENTINEL")
        return subprocess.CompletedProcess(args, 0, "{}")

    monkeypatch.setattr(ov.subprocess, "run", run)
    agent = ov.Agent("/PRIVATE/opencode2", "desk", Path("/PROJECT"))
    agent.session = "ses_owned"
    with pytest.raises(SafetyError, match="OpenCode voice request failed") as error:
        agent.answer("hello", time.monotonic() + 5)
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert len(calls) == 2 and "session.interrupt" in calls[1]
