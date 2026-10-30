"""Synthetic wake/privacy/protocol coverage; no room audio or station access."""

import base64
import io
import json
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import realtime_voice as rv
from alice_mcp.core import SafetyError


def wait(predicate: Any) -> None:
    until = time.monotonic() + 3
    while time.monotonic() < until:
        if predicate():
            return
        threading.Event().wait(0.01)
    raise AssertionError("worker did not finish")


def test_dotenv_key_stays_private_and_never_interpolates(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / ".env"
    path.write_text("REAL_TIME_API_" + "KEY='synthetic-${PRIVATE_SENTINEL}'\n")
    path.chmod(0o600)
    monkeypatch.delenv("REAL_TIME_API_KEY", raising=False)
    monkeypatch.setenv("ALICE_MCP_REALTIME_ENV", str(path))
    key = rv.api_key()
    assert key == "synthetic-${PRIVATE_SENTINEL}"
    assert "REAL_TIME_API_KEY" not in __import__("os").environ
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(SafetyError, match="Realtime configuration unavailable"):
        rv.api_key()


def test_resampling_is_continuous_and_playback_is_quiet_canonical_wav() -> None:
    pcm = struct.pack("<8h", 0, 1200, 2400, 3600, -32768, 32767, 0, 0)
    whole, chunks = rv.Resampler(), rv.Resampler()
    assert whole.feed(pcm) == chunks.feed(pcm[:6]) + chunks.feed(pcm[6:10]) + chunks.feed(pcm[10:])
    audio = rv.playback_wav(struct.pack("<3h", -32768, 0, 32767))
    with wave.open(io.BytesIO(audio)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (24000, 1, 2)
        assert max(abs(s[0]) for s in struct.iter_unpack("<h", wav.readframes(3))) <= 8191


class Ring:
    max_s = 5

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def wake(self, event_id: int, *, managed: bool = False) -> None:
        self.calls.append(("wake", event_id, managed))

    def complete(self, event_id: int, *, error: bool = False) -> None:
        self.calls.append(("complete", event_id, error))


class Socket:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.sent: list[Any] = []
        self.closed = False

    def send(self, value: str) -> None:
        self.sent.append(json.loads(value))

    def recv(self, timeout: float) -> str:
        if self.events:
            event = self.events.pop(0)
            if callable(event):
                event = event()
            return json.dumps(event)
        threading.Event().wait(min(timeout, 0.01))
        raise TimeoutError

    def close(self) -> None:
        self.closed = True


def test_no_cloud_before_wake_and_ring_covers_actual_playback(monkeypatch: Any) -> None:
    ring, playback = Ring(), threading.Event()
    raw = b"\0\1" * 12000
    socket = Socket([{"type": "session.created"}, {"type": "session.updated"},
                     {"type": "response.output_audio.delta",
                      "delta": base64.b64encode(raw).decode()},
                     {"type": "response.done", "response": {"status": "completed"}}])
    connections: list[Any] = []

    def connect(key: str, timeout: float, model: str) -> Socket:
        connections.append(key)
        return socket

    def play(audio: bytes) -> dict[str, Any]:
        assert ring.calls == [("wake", 7, True)]
        playback.wait(2)
        return {"outcome": "applied"}

    turn = rv.RealtimeTurn("synthetic", ring, play, connect=connect)
    monkeypatch.setattr(turn, "_transcribe", lambda *args: "который час")
    monkeypatch.setattr(turn, "_reason", lambda *args: "Сейчас полдень.")
    turn.feed(raw)
    assert not connections and not ring.calls
    assert turn.wake(7)
    assert not turn.wake(8)  # No echo/retrigger while a response is being played.
    wait(lambda: any(e["type"] == "session.update" for e in socket.sent))
    session = next(e["session"] for e in socket.sent if e["type"] == "session.update")
    assert session["model"] == "gpt-realtime-2.1-mini"
    assert session["audio"]["input"]["format"]["rate"] == 24000
    assert ring.calls == [("wake", 7, True)]
    playback.set()
    wait(lambda: not turn.active)
    assert ring.calls[-1] == ("complete", 7, False) and socket.closed
    assert "synthetic" not in str(turn.status())
    turn.close()


@pytest.mark.parametrize("failure", ["timeout", "api", "playback"])
def test_errors_close_once_drop_queued_audio_and_never_retry(
        failure: str, monkeypatch: Any) -> None:
    ring = Ring()
    ring.max_s = 0.05 if failure == "timeout" else 5
    events: list[Any] = [{"type": "session.created"}, {"type": "session.updated"}]
    if failure == "api":
        events.append({"type": "error", "error": {"message": "PRIVATE_SENTINEL"}})
    if failure == "playback":
        events += [{"type": "response.output_audio.delta",
                    "delta": base64.b64encode(b"\1\0" * 12000).decode()}] * 3
        events.append({"type": "response.done", "response": {"status": "completed"}})
    socket, played = Socket(events), []

    def play(audio: bytes) -> dict[str, Any]:
        played.append(audio)
        return {"outcome": "unknown", "do_not_retry": True}

    turn = rv.RealtimeTurn("synthetic", ring, play, connect=lambda *args: socket)
    monkeypatch.setattr(turn, "_transcribe", lambda *args: "который час")
    monkeypatch.setattr(turn, "_reason", lambda *args: "Полдень")
    turn.wake(1)
    wait(lambda: not turn.active)
    assert socket.closed and len(played) <= 1
    assert ring.calls[-1][0] == "complete"
    assert "PRIVATE_SENTINEL" not in str(turn.status())
    if failure == "playback":
        assert turn.status()["do_not_retry"] and not turn.wake(2)
    else:
        assert ring.calls[-1] == ("complete", 1, True)
    turn.close()


@pytest.mark.parametrize("text,route", [
    ("Компьютер, давай поговорим!", "conversation"),
    ("давай поговорим", "conversation"),
    ("давай поговорим о космосе", "conversation"),
    ("начни диктовку", "dictation"),
    ("который час", "command"),
    ("включи музыку давай поговорим", "command"),
])
def test_routes_are_explicit_prefixes(text: str, route: str) -> None:
    assert rv.route(text) == route


def test_command_transcription_commits_and_live_transcription_has_no_server_vad() -> None:
    ring = Ring()
    socket = Socket([{"type": "session.updated"},
                     {"type": "conversation.item.input_audio_transcription.completed",
                      "transcript": "который час", "item_id": "turn-1"}])
    turn = rv.RealtimeTurn("synthetic", ring, lambda _: {}, connect=lambda *args: socket)
    turn.active = True
    turn.state = "transcribing"
    turn.audio.put(b"\0\1" * 3200)
    turn.endpoint()
    assert turn._transcribe("gpt-transcribe", time.monotonic() + 2) == "который час"
    assert socket.closed
    session = socket.sent[0]["session"]
    assert session["type"] == "transcription"
    assert session["audio"]["input"]["transcription"]["model"] == "gpt-transcribe"
    assert session["audio"]["input"]["turn_detection"] is None


def test_pipeline_routes_conversation_and_dictation_without_responses_or_tts(
        monkeypatch: Any) -> None:
    for command, expected in [("давай поговорим", "conversation"),
                               ("начни диктовку", "dictation")]:
        ring, calls = Ring(), []
        turn = rv.RealtimeTurn("synthetic", ring, lambda _: {})
        monkeypatch.setattr(turn, "_transcribe", lambda *args, command=command: command)
        monkeypatch.setattr(turn, "_stream", lambda *args, calls=calls: calls.append(args))
        monkeypatch.setattr(turn, "_dictate",
                            lambda *args, calls=calls: calls.append(("dictation",)))
        turn.wake(10)
        wait(lambda turn=turn: not turn.active)
        if expected == "conversation":
            assert calls[0][0] == "gpt-realtime-2.1" and calls[0][-1] is True
        else:
            assert calls == [("dictation",)]
        assert ring.calls[-1] == ("complete", 10, False)


def test_dictation_delivers_untrusted_text_deltas_and_finals_and_stops_on_request() -> None:
    socket = Socket([{"type": "session.updated"},
                     {"type": "conversation.item.input_audio_transcription.delta",
                      "delta": "Первая", "item_id": "dictation-1"},
                     {"type": "conversation.item.input_audio_transcription.completed",
                      "transcript": "Первая фраза.", "item_id": "dictation-1"},
                     {"type": "conversation.item.input_audio_transcription.completed",
                      "transcript": "Закончи диктовку.", "item_id": "dictation-2"}])
    emitted: list[Any] = []
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {},
                           connect=lambda *args: socket, emit=emitted.append)
    turn.active = True
    assert turn._transcribe(rv.LIVE_STT_MODEL, time.monotonic() + 2) == ""
    assert emitted == [{"text": "Первая", "final": False, "item_id": "dictation-1"},
                       {"text": "Первая фраза.", "final": True, "item_id": "dictation-1"}]
    assert socket.sent[0]["session"]["audio"]["input"]["turn_detection"] is None


@pytest.mark.parametrize("prefix", ["", "Компьютер."])
def test_wake_said_alone_waits_for_the_command_instead_of_answering(prefix: str) -> None:
    socket = Socket([{"type": "session.updated"},
                     {"type": "conversation.item.input_audio_transcription.completed",
                      "transcript": prefix, "item_id": "wake-only"},
                     {"type": "conversation.item.input_audio_transcription.completed",
                      "transcript": "который час", "item_id": "command"}])
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, connect=lambda *args: socket)
    assert turn._transcribe(rv.STT_MODEL, time.monotonic() + 2) == "который час"


def test_cloud_deadline_uses_the_fresh_indicator_limit(monkeypatch: Any) -> None:
    class FreshRing(Ring):
        def wake(self, event_id: int, *, managed: bool = False) -> None:
            super().wake(event_id, managed=managed)
            self.max_s = 0.05

    socket = Socket([])
    turn = rv.RealtimeTurn("synthetic", FreshRing(), lambda _: {},
                           connect=lambda *args: socket)
    turn.wake(1)
    wait(lambda: not turn.active)
    assert turn.indicator.calls[-1] == ("complete", 1, True)


@pytest.mark.parametrize("outcome", ["applied", "unknown"])
def test_responses_tools_use_one_result_and_never_repeat_an_uncertain_action(
        outcome: str, monkeypatch: Any) -> None:
    import httpx

    requests: list[Any] = []
    actions: list[Any] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert body["store"] is False and body["model"] == rv.LLM_MODEL
        if len(requests) == 1:
            return httpx.Response(200, json={"output": [{"type": "function_call",
                "name": "station_action", "call_id": "synthetic-call",
                "arguments": json.dumps({"action": "audio.volume",
                                          "parameters": {"percent": 10}})}]})
        assert body["input"][-1]["type"] == "function_call_output"
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "Готово."}]}]})

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handle), **kwargs))
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, action=lambda args: (
        actions.append(args) or {"outcome": outcome}))
    if outcome == "unknown":
        with pytest.raises(SafetyError, match="action_unverified"):
            turn._reason("Убавь громкость", time.monotonic() + 2)
        assert len(requests) == 1 and turn.latched and not turn.wake(99)
    else:
        assert turn._reason("Убавь громкость", time.monotonic() + 2) == "Готово."
        assert len(requests) == 2
    assert actions == [{"action": "audio.volume", "parameters": {"percent": 10}}]
