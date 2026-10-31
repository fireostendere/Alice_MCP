"""Synthetic wake/privacy/protocol coverage; no room audio or station access."""

import base64
import io
import json
import queue
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import realtime_voice as rv
from alice_mcp.core import SafetyError


def test_emergency_stop_cancels_without_waiting_for_socket_or_playback(monkeypatch: Any) -> None:
    now = [11.0]
    monkeypatch.setattr(rv.time, "monotonic", lambda: now[0])
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {})
    turn.started_at = 10.0
    turn.active, turn.state = True, "speaking"
    turn.emergency_stop()
    assert turn.cancel.is_set() and turn.text_stopped.is_set()
    assert turn.error is None
    now[0] = 12.0
    turn.emergency_stop()  # A later final of the same stop must not replace its first partial.
    assert turn.timing_ms["emergency_stop"] == 1000


def test_conversation_silence_exits_after_seven_seconds_only_when_listening(
    monkeypatch: Any,
) -> None:
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {})
    turn.active, turn.mode, turn.state = True, "conversation", "conversation"
    turn.conversation_idle_at = 100.0
    monkeypatch.setattr(rv.time, "monotonic", lambda: 107.0)
    assert turn._receive(200) == {"type": "local.stop"}
    assert turn.error is None
    turn.state = "speaking"
    turn.socket = Socket([{"type": "response.done"}])
    assert turn._receive(200) == {"type": "response.done"}


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
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (48000, 1, 2)
        assert wav.getnframes() == 6  # Preserve duration at the tested hardware rate.
        assert max(abs(s[0]) for s in struct.iter_unpack("<h", wav.readframes(6))) <= 8191


def test_small_live_pcm_frames_use_the_audio_budget_and_flush_at_endpoint() -> None:
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {})
    turn.active, turn.state = True, "transcribing"
    pcm = b"\1\0" * 256
    for _ in range(201):  # 3.216 s: many more queue items than 100 without coalescing.
        turn.feed(pcm)
    assert not turn.cancel.is_set() and turn.error is None
    turn.endpoint()
    blocks = []
    while not turn.audio.empty():
        blocks.append(turn.audio.get_nowait())
    assert blocks[-1] is None
    assert b"".join(blocks[:-1]) == pcm * 201
    assert len(blocks) <= 34


def test_ready_output_chunks_are_played_together_without_waiting_for_more() -> None:
    ring = Ring()
    ring.max_s = 10
    first, generated = threading.Event(), threading.Event()
    sizes: list[int] = []

    def rest() -> dict[str, str]:
        assert first.wait(1)
        return {"type": "response.output_audio.delta",
                "delta": base64.b64encode(b"\1\0" * (rv.RATE * 11 // 2)).decode()}

    def complete() -> dict[str, Any]:
        generated.set()
        return {"type": "response.done", "response": {"status": "completed"}}

    def play(audio: bytes) -> dict[str, str]:
        with wave.open(io.BytesIO(audio)) as wav:
            sizes.append(wav.getnframes())
        if len(sizes) == 1:
            first.set()
            assert generated.wait(1)
        return {"outcome": "applied"}

    socket = Socket([{"type": "session.updated"},
                     {"type": "response.output_audio.delta",
                      "delta": base64.b64encode(b"\1\0" * (rv.RATE // 2)).decode()},
                     rest, complete])
    turn = rv.RealtimeTurn("synthetic", ring, play, connect=lambda *args: socket)
    turn._stream(rv.TTS_MODEL, "synthetic", time.monotonic() + 3, False)
    assert sizes == [24000, 192000, 72000]  # Same six seconds, no per-half-second RPC gap.


def test_completed_conversation_reply_drains_before_reading_a_peer_disconnect() -> None:
    ring = Ring()
    ring.max_s = 10
    first, generated, disconnected = (threading.Event() for _ in range(3))
    frames: list[int] = []

    def rest() -> dict[str, str]:
        assert first.wait(1)
        return {"type": "response.output_audio.delta",
                "delta": base64.b64encode(b"\1\0" * (rv.RATE * 11 // 2)).decode()}

    def complete() -> dict[str, Any]:
        generated.set()
        return {"type": "response.done", "response": {"status": "completed"}}

    def disconnect() -> dict[str, Any]:
        disconnected.set()
        raise ConnectionError("PRIVATE_SENTINEL")

    def play(audio: bytes) -> dict[str, str]:
        with wave.open(io.BytesIO(audio)) as wav:
            frames.append(wav.getnframes())
        if len(frames) == 1:
            first.set()
            assert generated.wait(1)
            disconnected.wait(0.2)  # The old receiver closes before this fragment ends.
        return {"outcome": "applied"}

    socket = Socket([{"type": "session.updated"},
                     {"type": "response.output_audio.delta",
                      "delta": base64.b64encode(b"\1\0" * (rv.RATE // 2)).decode()},
                     rest, complete, disconnect])
    turn = rv.RealtimeTurn("synthetic", ring, play, connect=lambda *args: socket,
                           local_transcripts=True)
    with pytest.raises((ConnectionError, SafetyError)):
        turn._stream(rv.MODEL, "synthetic", time.monotonic() + 3, True)
    assert sum(frames) == 6 * 48000
    assert socket.closed and disconnected.is_set()
    assert turn.status()["last_error"] == "output_transport_failed"
    assert "PRIVATE_SENTINEL" not in str(turn.status())


def test_local_text_waits_until_the_previous_reply_has_finished_playing() -> None:
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, local_transcripts=True)
    turn.active, turn.state = True, "conversation"
    assert turn.transcript("первая реплика") and turn.transcript("вторая реплика")
    second, done = threading.Event(), threading.Event()

    class Replies(Socket):
        def send(self, value: str) -> None:
            super().send(value)
            if sum(e["type"] == "response.create" for e in self.sent) == 2:
                second.set()

    turn.socket = Replies([])
    worker = threading.Thread(target=turn._send_transcripts,
                              args=(time.monotonic() + 3, done))
    worker.start()
    try:
        wait(lambda: any(e["type"] == "response.create" for e in turn.socket.sent))
        turn.state = "speaking"
        assert not second.wait(0.1), "overlapping response request during playback"
        turn.state = "conversation"
        assert second.wait(1)
        assert [e["item"]["content"][0]["text"] for e in turn.socket.sent
                if e["type"] == "conversation.item.create"] == ["первая реплика", "вторая реплика"]
    finally:
        done.set()
        worker.join(1)
    assert not worker.is_alive()


@pytest.mark.parametrize("finish", ["stop", "deadline"])
def test_draining_audio_still_honors_stop_and_deadline(finish: str, monkeypatch: Any) -> None:
    from types import SimpleNamespace

    clock = [0.0]
    monkeypatch.setattr(rv, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    first, generated, release = (threading.Event() for _ in range(3))
    frames: list[int] = []
    ring = Ring()
    ring.max_s = 10

    def rest() -> dict[str, str]:
        assert first.wait(1)
        return {"type": "response.output_audio.delta",
                "delta": base64.b64encode(b"\1\0" * rv.RATE).decode()}

    def complete() -> dict[str, Any]:
        generated.set()
        return {"type": "response.done", "response": {"status": "completed"}}

    def play(audio: bytes) -> dict[str, str]:
        with wave.open(io.BytesIO(audio)) as wav:
            frames.append(wav.getnframes())
        first.set()
        assert release.wait(2)
        return {"outcome": "applied"}

    socket = Socket([{"type": "session.updated"},
                     {"type": "response.output_audio.delta",
                      "delta": base64.b64encode(b"\1\0" * (rv.RATE // 2)).decode()},
                     rest, complete])
    turn = rv.RealtimeTurn("synthetic", ring, play, connect=lambda *args: socket,
                           local_transcripts=True)
    try:
        assert turn.wake(1) and turn.transcript("давай поговорим")
        assert generated.wait(1)
        if finish == "stop":
            turn.text_stopped.set()
        else:
            clock[0] = 11.0
        wait(lambda: socket.closed)
    finally:
        release.set()
        turn.close()
    assert frames == [24000]  # Only the write already in flight may finish.
    assert not turn.latched and not turn.active
    assert turn.error == ("deadline_exceeded" if finish == "deadline" else None)


class Ring:
    max_s = 5

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def wake(self, event_id: int, *, managed: bool = False) -> None:
        self.calls.append(("wake", event_id, managed))

    def complete(self, event_id: int, *, error: bool = False) -> None:
        self.calls.append(("complete", event_id, error))


@pytest.mark.parametrize("light_delay", [0.0, 2.0])
def test_local_wake_without_command_goes_idle_silently_three_seconds_after_light(
        monkeypatch: Any, light_delay: float) -> None:
    now = [100.0]
    monkeypatch.setattr(rv.time, "monotonic", lambda: now[0])
    ring = Ring()
    ring.started_at = None
    turn = rv.RealtimeTurn("synthetic", ring, lambda _: pytest.fail("unexpected playback"),
                           connect=lambda *args: pytest.fail("unexpected API call"),
                           local_transcripts=True)
    turn.active, turn.started_at = True, now[0]

    def empty(timeout: float) -> str:
        now[0] += timeout
        if now[0] >= 100 + light_delay:
            ring.started_at = 100 + light_delay
        raise queue.Empty

    monkeypatch.setattr(turn.text, "get", empty)
    turn._run(99, 190)
    assert now[0] == pytest.approx(103 + light_delay, abs=0.1)
    assert turn.state == "idle" and turn.error is None and not turn.latched
    assert ring.calls == [("complete", 99, False)]


def test_local_command_started_in_time_can_finish_after_three_seconds(monkeypatch: Any) -> None:
    now = [100.0]
    monkeypatch.setattr(rv.time, "monotonic", lambda: now[0])
    ring = Ring()
    ring.started_at = now[0]
    turn = rv.RealtimeTurn("synthetic", ring, lambda _: {}, local_transcripts=True)
    turn.active, turn.state, turn.started_at = True, "transcribing", now[0]

    def speech(timeout: float) -> str:
        now[0] += timeout
        if now[0] >= 102:
            turn.speech_started()
        if now[0] >= 106:
            return "сколько будет пять плюс семь"
        raise queue.Empty

    monkeypatch.setattr(turn.text, "get", speech)
    assert turn._local_text(115) == "сколько будет пять плюс семь"
    assert turn.error is None


@pytest.mark.parametrize("recognized", [False, True])
def test_local_activity_without_final_text_expires_silently(
        monkeypatch: Any, recognized: bool) -> None:
    now = [100.0]
    monkeypatch.setattr(rv.time, "monotonic", lambda: now[0])
    ring = Ring()
    ring.started_at = now[0]
    turn = rv.RealtimeTurn("synthetic", ring, lambda _: pytest.fail("unexpected playback"),
                           connect=lambda *args: pytest.fail("unexpected API call"),
                           local_transcripts=True)
    turn.active, turn.started_at = True, now[0]
    heard = False

    def empty(timeout: float) -> str:
        nonlocal heard
        now[0] += timeout
        if now[0] >= 102 and not heard:
            heard = True
            if recognized:
                turn.speech_started()
            else:
                turn.activity()
        raise queue.Empty

    monkeypatch.setattr(turn.text, "get", empty)
    turn._run(99, 190)
    assert now[0] == pytest.approx(115 if recognized else 105, abs=0.1)
    assert turn.state == "idle" and turn.error is None and not turn.latched
    assert ring.calls == [("complete", 99, False)]


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
    timing = turn.status()["timing_ms"]
    assert 0 <= timing["transcript"] <= timing["playback_dispatched"]
    assert timing["first_playback_completed"] >= timing["playback_dispatched"]
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
        return {"outcome": "unknown", "do_not_retry": True, "stage": "cleanup",
                "frames_written": 0, "alsa_return_code": -5, "error": "PRIVATE_SENTINEL"}

    turn = rv.RealtimeTurn("synthetic", ring, play, connect=lambda *args: socket)
    monkeypatch.setattr(turn, "_transcribe", lambda *args: "который час")
    monkeypatch.setattr(turn, "_reason", lambda *args: "Полдень")
    turn.wake(1)
    wait(lambda: not turn.active)
    assert socket.closed and len(played) <= 1
    assert ring.calls[-1][0] == "complete"
    assert "PRIVATE_SENTINEL" not in str(turn.status())
    assert turn.error == {"timeout": "deadline_exceeded", "api": "api_error",
                          "playback": "playback_unverified"}[failure]
    if failure == "playback":
        assert turn.status()["do_not_retry"] and not turn.wake(2)
        assert turn.status()["last_playback"] == {
            "outcome": "unknown", "stage": "cleanup", "frames_written": 0,
            "alsa_return_code": -5}
    else:
        assert ring.calls[-1] == ("complete", 1, True)
    turn.close()


def test_incomplete_response_reports_its_cause_without_speaking_or_peer_details() -> None:
    socket = Socket([{"type": "session.updated"}, {"type": "response.done", "response": {
        "status": "incomplete", "status_details": {"reason": "PRIVATE_SENTINEL"}}}])
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: pytest.fail("no complete audio"),
                           connect=lambda *args: socket, local_transcripts=True)
    assert turn.wake(1) and turn.transcript("давай поговорим")
    wait(lambda: not turn.active)
    assert turn.error == "response_failed" and not turn.latched
    assert "PRIVATE_SENTINEL" not in str(turn.status())


@pytest.mark.parametrize("text,route", [
    ("Компьютер, давай поговорим!", "conversation"),
    ("давай поговорим", "conversation"),
    ("давай поговорим о космосе", "conversation"),
    ("Компьютер, давай попиздим!", "conversation"),
    ("давай попиздим", "conversation"),
    ("давай попиздим о космосе", "conversation"),
    ("начни диктовку", "dictation"),
    ("который час", "command"),
    ("включи музыку давай поговорим", "command"),
    ("включи музыку давай попиздим", "command"),
    ("давай попиздимся", "command"),
    ("Компьютер, громче!", "volume"),
    ("тише", "volume"),
    ("громче музыку", "command"),
    ("компьютер громкость пять", "volume"),
])
def test_routes_are_explicit_prefixes(text: str, route: str) -> None:
    assert rv.route(text) == route


@pytest.mark.parametrize("text,parameters", [
    ("компьютер громкость 5", {"level": 5}),
    ("Громкость пять", {"level": 5}),
    ("громкость на десять", {"level": 10}),
    ("громкость ноль", {"level": 0}),
    ("поставь громкость три", {"level": 3}),
    ("сделай громкость 7", {"level": 7}),
    ("громкость пятьдесят процентов", {"level": 5}),
    ("громкость двадцать пять процентов", {"level": 3}),
    ("громкость 35 процентов", {"level": 4}),
    ("погромче", {"step": "louder"}),
    ("Компьютер, громче!", {"step": "louder"}),
    ("сделай потише", {"step": "quieter"}),
    ("ещё тише", {"step": "quieter"}),
    ("громкость одиннадцать", None),
    ("громкость", None),
    ("громкость сто пятьдесят процентов", None),
    ("включи громкость", None),
    ("громче музыку", None),
])
def test_volume_phrases_parse_locally(text: str, parameters: Any) -> None:
    assert rv.volume_command(text) == parameters


@pytest.mark.parametrize("outcome,error,latched", [("applied", None, False),
                                                   ("blocked", "realtime_failed", False),
                                                   ("unknown", "action_unverified", True)])
def test_volume_phrase_runs_one_action_without_answer_or_speech(
    monkeypatch: Any, outcome: str, error: str | None, latched: bool,
) -> None:
    actions: list[dict[str, Any]] = []
    ring = Ring()

    def action(arguments: dict[str, Any]) -> dict[str, Any]:
        # Our ring is already dark, so Quasar's own volume ring is not covered.
        assert ring.calls[-1] == ("complete", 1, False)
        actions.append(arguments)
        return {"outcome": outcome}

    turn = rv.RealtimeTurn("synthetic", ring, lambda _: pytest.fail("played audio"),
                           connect=lambda *a: pytest.fail("opened Realtime"),
                           action=action, local_transcripts=True)
    monkeypatch.setattr(turn, "_reason", lambda *a: pytest.fail("asked the answer agent"))
    assert turn.wake(1)
    assert turn.transcript("громкость пять")
    wait(lambda: not turn.active)
    assert actions == [{"action": "quasar.volume", "parameters": {"level": 5}}]
    assert turn.mode == "volume" and turn.error == error and turn.latched is latched


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
    def unexpected_reason(*args: Any) -> str:
        raise AssertionError("Conversation and dictation must bypass Responses")

    for command, expected in [("давай поговорим", "conversation"),
                               ("Компьютер, давай попиздим!", "conversation"),
                               ("начни диктовку", "dictation")]:
        ring, calls = Ring(), []
        turn = rv.RealtimeTurn("synthetic", ring, lambda _: {})
        monkeypatch.setattr(turn, "_transcribe", lambda *args, command=command: command)
        monkeypatch.setattr(turn, "_reason", unexpected_reason)
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


@pytest.mark.parametrize("mode,text", [("conversation", "мне скучно и хочется общения"),
                                      ("dictation", "записывай за мной"),
                                      ("command", "почему небо голубое")])
def test_agent_selects_mode_as_part_of_its_answer_without_a_second_model_request(
        mode: str, text: str, monkeypatch: Any) -> None:
    asked, streamed = [], []

    def answer(command: str, deadline: float) -> dict[str, Any]:
        asked.append(command)
        return {"mode": mode, "text": "Воздух рассеивает свет." if mode == "command" else ""}

    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, answer=answer)
    monkeypatch.setattr(turn, "_transcribe", lambda *args: text)
    monkeypatch.setattr(turn, "_stream", lambda *args: streamed.append(args))
    monkeypatch.setattr(turn, "_dictate", lambda *args: streamed.append(("dictation",)))
    turn.wake(1)
    wait(lambda: not turn.active)
    assert asked == [text] and turn.mode == mode and turn.error is None
    if mode == "dictation":
        assert streamed == [("dictation",)]
    else:
        assert streamed[0][0] == (rv.MODEL if mode == "conversation" else rv.TTS_MODEL)
        assert streamed[0][-1] is (mode == "conversation")


@pytest.mark.parametrize("outcome", ["applied", "blocked", "unknown"])
def test_agent_action_uses_host_guard_once_and_only_confirms_verified_success(
        outcome: str, monkeypatch: Any) -> None:
    actions, spoken = [], []

    def action(arguments: dict[str, Any]) -> dict[str, Any]:
        actions.append(arguments)
        return {"outcome": outcome}

    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, action=action,
                           answer=lambda *args: {"mode": "action", "text": "Сделал тише.",
                                                 "action": "audio.volume",
                                                 "parameters": {"step": "quieter"}})
    monkeypatch.setattr(turn, "_transcribe", lambda *args: "убавь звук немного")
    monkeypatch.setattr(turn, "_stream", lambda model, text, *args: spoken.append(text))
    turn.wake(1)
    wait(lambda: not turn.active)
    assert actions == [{"action": "audio.volume", "parameters": {"step": "quieter"}}]
    if outcome == "unknown":
        assert turn.latched and not spoken and not turn.wake(2)
    elif outcome == "applied":
        assert spoken == ["Сделал тише."]
    else:
        assert spoken and "Сделал" not in spoken[0]


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
    def wake_endpoint() -> dict[str, Any]:
        turn.endpoint()
        return {"type": "conversation.item.input_audio_transcription.completed",
                "transcript": prefix, "item_id": "wake-only"}

    def command_endpoint() -> dict[str, Any]:
        turn.feed(b"\1\0" * 50)
        assert bytes(turn.pending) == b"\1\0" * 50
        turn.endpoint()
        return {"type": "conversation.item.input_audio_transcription.completed",
                "transcript": "который час", "item_id": "command"}

    socket = Socket([{"type": "session.updated"}, wake_endpoint, command_endpoint])
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, connect=lambda *args: socket)
    turn.active = True
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


def test_station_transcript_uses_existing_answer_and_tts_without_cloud_stt(
    monkeypatch: Any,
) -> None:
    socket = Socket([{"type": "session.updated"},
                     {"type": "response.output_audio.delta",
                      "delta": base64.b64encode(b"\1\0" * 12000).decode()},
                     {"type": "response.done", "response": {"status": "completed"}}])
    models: list[str] = []
    heard: list[str] = []

    def connect(key: str, timeout: float, model: str) -> Socket:
        models.append(model)
        return socket

    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {"outcome": "applied"},
                           connect=connect, local_transcripts=True)
    monkeypatch.setattr(turn, "_transcribe", lambda *a: pytest.fail("cloud ASR was called"))
    monkeypatch.setattr(turn, "_reason", lambda text, deadline: heard.append(text) or "Двенадцать")
    assert not turn.transcript("случайная речь")
    assert turn.wake(1)
    assert turn.transcript("сколько будет пять плюс семь")
    wait(lambda: not turn.active)
    assert heard == ["сколько будет пять плюс семь"] and models == [rv.TTS_MODEL]
    assert turn.error is None
    assert turn.status()["stt_model"] == "station_vosk"
    assert not any(e["type"].startswith("input_audio_buffer.") for e in socket.sent)
    session = next(e["session"] for e in socket.sent if e["type"] == "session.update")
    assert session["audio"]["input"]["transcription"] is None


def test_local_conversation_sends_text_turns_and_stops_locally() -> None:
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, local_transcripts=True)
    turn.active, turn.state = True, "conversation"
    turn.socket = Socket([])
    assert turn.transcript("как дела")
    assert turn.transcript("закончи разговор")
    turn._send_transcripts(time.monotonic() + 2, threading.Event())
    assert turn.text_stopped.is_set()
    assert [e["type"] for e in turn.socket.sent] == ["conversation.item.create", "response.create"]
    assert turn.socket.sent[0]["item"]["content"] == [{"type": "input_text", "text": "как дела"}]
    turn.state = "speaking"
    assert not turn.transcript("это собственный ответ колонки")
    turn.state = "conversation"
    turn.echo_until = time.monotonic() + 1
    assert not turn.transcript("позднее эхо")
    with pytest.raises(SafetyError):
        turn.transcript("x" * 8193)


@pytest.mark.parametrize("text", ["компьютер хочу с тобой поболтать",
                                   "давай поболтаем", "давай пообщаемся",
                                   "поговори со мной", "давай по пизди",
                                   "поговорим", "поболтаем о погоде", "пообщаемся",
                                   "попиздим"])
def test_local_conversation_phrasings_take_the_fast_realtime_route(text: str) -> None:
    assert rv.route(text) == "conversation"


@pytest.mark.parametrize("continuous", [True, False])
def test_private_conversation_prompt_reaches_only_the_realtime_conversation(
        continuous: bool) -> None:
    socket = Socket([{"type": "session.updated"},
                     {"type": "response.done", "response": {"status": "completed"}}])
    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {"outcome": "applied"},
                           connect=lambda *a: socket, local_transcripts=True,
                           conversation_prompt="Начни первым с длинной истории.")
    turn.active = True
    turn.text_stopped.set() if not continuous else None
    try:
        turn._stream(rv.MODEL if continuous else rv.TTS_MODEL, "давай поговорим",
                     time.monotonic() + 2, continuous)
    except Exception:
        pass
    session = next(e["session"] for e in socket.sent if e["type"] == "session.update")
    assert ("Начни первым с длинной истории." in session["instructions"]) is continuous
