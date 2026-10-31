"""Voice service in the MCP process: fake engines and stream, no station, model or audio."""

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import device_runtime as rt
from alice_mcp import io_channel_pilot as iop
from alice_mcp import server
from alice_mcp import voice_service as vs
from alice_mcp import wake_stream as ws
from alice_mcp.core import SafetyError, Station

CLIENT = b"\x7fELF synthetic client"
FIRMWARE = {"firmware_sha256": "a" * 64}


@pytest.fixture(autouse=True)
def no_private_cloud_key(monkeypatch: Any) -> None:
    monkeypatch.setattr(vs.rv, "api_key", lambda: None)


def voice(tmp_path: Path, **extra: Any) -> dict[str, Any]:
    paths = {k: str(tmp_path / k) for k in ("client", "mww", "libvosk", "vosk_zip")}
    return {**paths, **extra}


def test_policy_accepts_voice_with_defaults_and_refuses_anything_else(tmp_path: Path) -> None:
    result = rt.validate_policy({**FIRMWARE, "voice": voice(tmp_path)}, "station-mini")
    assert result["voice"] == {**voice(tmp_path), "mww_model": "ft", "mode": "either",
                               "autostart": False, "source": "PC"}
    for bad, model in [(voice(tmp_path, autostart="yes"), "station-mini"),
                       (voice(tmp_path, mode="any"), "station-mini"),
                       (voice(tmp_path, mww_model="new"), "station-mini"),
                       (voice(tmp_path, extra="x"), "station-mini"),
                       (voice(tmp_path, source="cloud"), "station-mini"),
                       ({**voice(tmp_path), "client": "relative/client"}, "station-mini"),
                       ({**voice(tmp_path), "mww": str(tmp_path / ".." / "x")}, "station-mini"),
                       ({"client": "/a"}, "station-mini"),
                       (voice(tmp_path), "station-mini3")]:
        with pytest.raises(ValueError):
            rt.validate_policy({**FIRMWARE, "voice": bad}, model)


def test_station_source_needs_no_host_models_and_never_uses_ssh(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    controls = rt.validate_policy({**FIRMWARE, "voice": {"source": "station"}}, "station-mini")
    mini = station(tmp_path)
    from dataclasses import replace
    mini = replace(mini, controls=controls, native_mcp={"url": "synthetic"})
    voice_service = service(tmp_path, monkeypatch, mini)
    seen = threading.Event()

    def once(*args: Any) -> dict[str, Any]:
        voice_service.stop_event.set()
        seen.set()
        return {"outcome": "complete", "cleanup_verified": True, "quasar_unchanged": True,
                "events": [{"model": "komp", "t_ms": 1000, "probability": 0.99,
                            "trusted": False}]}

    monkeypatch.setattr(vs.station_wake, "listen_once", once)
    monkeypatch.setattr(vs.rv, "api_key", lambda: pytest.fail("station wake loaded cloud key"))
    monkeypatch.setattr(ws, "preview", lambda *a: pytest.fail("station source used SSH"))
    voice_service.start("mini1")
    assert seen.wait(5)
    wait(lambda: voice_service.status()["state"] == "stopped")
    event = voice_service.get_events()["events"][0]
    assert event["command"] == "" and event["trusted"] is False
    assert event["sources"] == ["station_mww"]


def test_switching_source_preserves_optional_pc_settings(tmp_path: Path) -> None:
    pc = voice(tmp_path)
    result = rt.validate_policy({**FIRMWARE, "voice": {**pc, "source": "station"}},
                                 "station-mini")
    assert all(result["voice"][key] == value for key, value in pc.items())


@pytest.mark.parametrize("source", ["PC", "station"])
def test_source_and_indicator_policies_compose_without_dropping_pc_settings(
        source: str, tmp_path: Path) -> None:
    pc = voice(tmp_path)
    indicator = {"backend": "ssh_led", "max_seconds": 5, "color": "red"}
    policy = {**FIRMWARE, "voice": {**pc, "source": source, "indicator": indicator}}
    result = rt.validate_policy(policy, "station-mini")["voice"]
    assert result["source"] == source and result["indicator"] == indicator
    assert all(result[k] == v for k, v in pc.items())


def test_sink_is_private_append_only_and_rotates(tmp_path: Path) -> None:
    sink = vs.Sink(tmp_path / "state" / "voice-events.jsonl", limit=120)
    for i in range(4):
        sink.write({"id": i, "command": "включи свет"})
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700
    assert sink.path.stat().st_mode & 0o777 == 0o600
    rotated = sink.path.with_name("voice-events.jsonl.1")
    ids = [json.loads(line)["id"] for f in (rotated, sink.path)
           for line in f.read_text(encoding="utf-8").splitlines()]
    assert ids == [0, 1, 2, 3] and sink.path.stat().st_size <= 120
    sink.path.unlink()
    sink.path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        sink.write({"id": 4})
    assert not (tmp_path / "elsewhere").exists()


def test_events_path_follows_xdg_state_home(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert vs.events_path() == tmp_path / "alice-mcp" / "voice-events.jsonl"
    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    assert vs.events_path() == Path.home() / ".local/state/alice-mcp/voice-events.jsonl"


def station(tmp_path: Path, **extra: Any) -> Station:
    controls = rt.validate_policy({**FIRMWARE, "voice": voice(tmp_path, **extra)},
                                  "station-mini")
    return Station("mini1", "station-mini", "h", 22, "root", tmp_path, tmp_path, True, "",
                   "c" * 64, "t" * 64, controls)


class Recognizer:
    """Hears «компьютер включи свет» once, at 1.0-2.6 s of stream time."""

    def __init__(self) -> None:
        self.samples = 0
        self.done = False

    def accept(self, pcm: bytes) -> dict[str, Any] | None:
        self.samples += len(pcm) // 2
        if not self.done and self.samples >= 3.2 * 16000:
            self.done = True
            return {"text": "компьютер включи свет", "start": 1.0, "end": 2.6}
        return None

    def flush(self) -> dict[str, Any] | None:
        return None


@contextmanager
def engines(*args: Any) -> Iterator[Any]:
    yield Recognizer, None


def fake_stream(monkeypatch: Any, summary: dict[str, Any]) -> list[Any]:
    calls: list[Any] = []

    def preview(controller: Any, station_id: str, item: ws.Stream) -> dict[str, Any]:
        calls.append(("preview", station_id, item.client))
        return {"plan_id": "p", "expected_sha256": "e" * 64}

    def apply(controller: Any, plan_id: str, expected: str, item: ws.Stream, sink: Any, *,
              stop: threading.Event, live: dict[str, Any]) -> dict[str, Any]:
        live.update(chunks=1, handovers=0, gaps=0, failures=0, audio_bytes=0)
        for _ in range(40):  # 4 s of audio in 0.1 s blocks.
            sink(b"\0\1" * 1600)
            live["audio_bytes"] += 3200
        stop.wait(10)
        calls.append(("cleanup", stop.is_set()))
        return {"do_not_retry": True, **summary}

    monkeypatch.setattr(ws, "preview", preview)
    monkeypatch.setattr(ws, "apply", apply)
    return calls


def service(tmp_path: Path, monkeypatch: Any, mini: Station) -> vs.VoiceService:
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(CLIENT).hexdigest())
    (tmp_path / "client").write_bytes(CLIENT)
    controller = SimpleNamespace(writes=True, inventory=None,
                                 station=lambda station_id: mini)
    return vs.VoiceService(controller, vs.Sink(tmp_path / "state" / "events.jsonl"),
                           engines=engines)


def wait(predicate: Any) -> None:
    for _ in range(200):
        if predicate():
            return
        threading.Event().wait(0.02)
    raise AssertionError("timed out")


def test_listener_lifecycle_events_and_verified_cleanup(tmp_path: Path, monkeypatch: Any) -> None:
    calls = fake_stream(monkeypatch, {"outcome": "complete", "reason": "stopped",
                                      "cleanup_verified": True, "quasar_unchanged": True,
                                      "quasar_running": True, "audio_s": 4.0})
    voice_service = service(tmp_path, monkeypatch, station(tmp_path))
    started = voice_service.start("mini1")
    assert started["outcome"] == "started" and started["station"] == "mini1"
    with pytest.raises(SafetyError, match="already running"):
        voice_service.start("mini1")
    wait(lambda: voice_service.get_events()["events"])
    event = voice_service.get_events()["events"][0]
    assert {k: event[k] for k in ("station", "word", "command", "sources", "trusted")} == {
        "station": "mini1", "word": "компьютер", "command": "включи свет",
        "sources": ["vosk"], "trusted": False}
    assert voice_service.get_events(event["id"])["events"] == []
    status = voice_service.status()
    assert status["state"] == "listening" and status["chunks"] == 1 and status["running"]
    stopped = voice_service.stop()
    assert stopped["outcome"] == "stopped" and stopped["state"] == "stopped"
    assert stopped["last_session"]["cleanup_verified"] and stopped["last_error"] is None
    assert calls == [("preview", "mini1", CLIENT), ("cleanup", True)]
    lines = [json.loads(x) for x in voice_service.sink.path.read_text("utf-8").splitlines()]
    assert [x.get("state") or x["command"] for x in lines] == [
        "starting", "listening", "включи свет", "stopped"]
    assert [x["id"] for x in lines] == sorted({x["id"] for x in lines})
    assert str(tmp_path) not in json.dumps([stopped, lines, voice_service.get_events()])
    assert voice_service.stop()["outcome"] == "not_running"


def test_unverified_cleanup_is_reported(tmp_path: Path, monkeypatch: Any) -> None:
    fake_stream(monkeypatch, {"outcome": "unknown", "reason": "quasar_changed",
                              "cleanup_verified": False})
    voice_service = service(tmp_path, monkeypatch, station(tmp_path))
    voice_service.start("mini1")
    wait(lambda: voice_service.status()["state"] == "listening")
    result = voice_service.stop()
    assert result["last_error"] == "Voice stream ended: quasar_changed"
    assert result["last_session"]["cleanup_verified"] is False


def test_start_fails_closed_without_policy_or_artifacts(tmp_path: Path, monkeypatch: Any) -> None:
    fake_stream(monkeypatch, {"outcome": "complete"})
    plain = Station("mini1", "station-mini", "h", 22, "root", tmp_path, tmp_path, True, "",
                    "c" * 64, "t" * 64, rt.validate_policy(FIRMWARE, "station-mini"))
    with pytest.raises(SafetyError, match="not configured"):
        service(tmp_path, monkeypatch, plain).start("mini1")
    voice_service = service(tmp_path, monkeypatch, station(tmp_path))
    voice_service.controller.writes = False
    with pytest.raises(SafetyError):
        voice_service.start("mini1")
    voice_service.controller.writes = True
    (tmp_path / "client").write_bytes(b"tampered")
    voice_service.start("mini1")
    wait(lambda: voice_service.status()["state"] == "stopped")
    status = voice_service.status()
    assert "pinned" in status["last_error"] and str(tmp_path) not in json.dumps(status)


def test_autostart_starts_the_declared_station(tmp_path: Path, monkeypatch: Any) -> None:
    fake_stream(monkeypatch, {"outcome": "complete"})
    mini = station(tmp_path, autostart=True)
    voice_service = service(tmp_path, monkeypatch, mini)
    monkeypatch.setattr(vs, "load_inventory", lambda path: {"mini1": mini})
    voice_service.autostart()
    assert voice_service.status()["running"]
    voice_service.stop()


def test_start_reserves_listener_before_starting_worker(tmp_path: Path, monkeypatch: Any) -> None:
    voice_service = service(tmp_path, monkeypatch, station(tmp_path))
    real_state = voice_service._state

    def concurrent_start(state: str) -> None:
        if state == "starting":
            with pytest.raises(SafetyError, match="already running"):
                voice_service.start("mini1")
        real_state(state)

    monkeypatch.setattr(voice_service, "_state", concurrent_start)
    fake_stream(monkeypatch, {"outcome": "complete"})
    voice_service.start("mini1")
    voice_service.stop()


def test_tools_wrap_the_service_and_main_stops_it_on_exit(monkeypatch: Any) -> None:
    calls: list[Any] = []
    fake = SimpleNamespace(
        start=lambda station_id: calls.append(("start", station_id)) or {"outcome": "started"},
        stop=lambda: calls.append("stop") or {"outcome": "stopped"},
        status=lambda: {"state": "stopped"},
        get_events=lambda after_id: {"events": [], "after": after_id},
        autostart=lambda: calls.append("autostart"))
    monkeypatch.setattr(server, "voice", fake)
    assert asyncio.run(server.voice_listen_start("mini1")) == {"outcome": "started"}
    assert asyncio.run(server.voice_listen_stop()) == {"outcome": "stopped"}
    assert server.voice_status() == {"state": "stopped"}
    assert server.get_voice_events(7) == {"events": [], "after": 7}

    def run(transport: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server.mcp, "run", run)
    monkeypatch.setattr(server.signal, "signal", lambda *args: None)
    with pytest.raises(KeyboardInterrupt):
        server.main()
    assert calls == [("start", "mini1"), "stop", "autostart", "stop"]


def test_heard_commands_drive_the_ring_indicator(tmp_path: Path, monkeypatch: Any) -> None:
    fake_stream(monkeypatch, {"outcome": "complete", "reason": "stopped",
                              "cleanup_verified": True})
    made: list[Any] = []

    class Indicator:
        def __init__(self, controller: Any, station_id: str, *,
                     defer_preparation: bool = False) -> None:
            self.station_id, self.woken, self.closed = station_id, [], False
            made.append(self)

        def wake(self, event_id: int) -> None:
            self.woken.append(event_id)

        def status(self) -> dict[str, Any]:
            return {"backend": "ssh_led", "state": "red", "reason": None}

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(vs.vi, "Indicator", Indicator)
    voice_service = service(tmp_path, monkeypatch, station(tmp_path))
    assert voice_service.status()["indicator"]["state"] == "off"
    voice_service.start("mini1")
    wait(lambda: voice_service.get_events()["events"])
    event = voice_service.get_events()["events"][0]
    voice_service._wake("mini1")({"word": "компьютер", "command": "", "sources": ["vosk"]})
    assert made[0].station_id == "mini1" and made[0].woken == [event["id"]]
    assert voice_service.status()["indicator"]["state"] == "red"
    voice_service.stop()
    assert made[0].closed


@pytest.mark.parametrize("opencode", [False, True])
def test_realtime_uses_early_wake_and_never_emits_a_second_agent_command(
        tmp_path: Path, monkeypatch: Any, opencode: bool) -> None:
    fake_stream(monkeypatch, {"outcome": "complete", "cleanup_verified": True})
    turns: list[Any] = []
    monkeypatch.setattr(vs.rv, "api_key", lambda: "synthetic")
    answer = lambda *a: {"mode": "command", "text": "Ответ"}  # noqa: E731
    bindings: list[Any] = []

    def agent(executable: str, station_id: str, directory: Path) -> Any:
        bindings.append((executable, station_id, directory))
        return SimpleNamespace(answer=answer)

    monkeypatch.setattr(vs.ov, "Agent", agent)
    monkeypatch.delenv("ALICE_MCP_OPENCODE", raising=False)
    if opencode:
        monkeypatch.setenv("ALICE_MCP_OPENCODE", "/PRIVATE/opencode2")

    class Turn:
        active = False

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            assert kwargs["answer"] is (answer if opencode else None)
            self.closed, self.frames, self.wakes = False, 0, []
            turns.append(self)

        def wake(self, event_id: int) -> bool:
            self.wakes.append(event_id)
            self.active = True
            return True

        def feed(self, pcm: bytes) -> None:
            if self.active:
                self.frames += len(pcm)

        def endpoint(self) -> None:
            pass

        def status(self) -> dict[str, Any]:
            return {"model": "gpt-realtime-2.1"}

        def close(self) -> None:
            self.closed = True

    class Early(Recognizer):
        def partial(self) -> str:
            return "компьютер" if self.samples >= 1600 else ""

    @contextmanager
    def early_engines(*args: Any) -> Iterator[Any]:
        yield Early, None

    monkeypatch.setattr(vs.rv, "RealtimeTurn", Turn)
    voice_service = service(tmp_path, monkeypatch, station(tmp_path))
    voice_service.engines = early_engines
    voice_service.start("mini1")
    wait(lambda: turns and turns[0].frames > 0)
    voice_service.stop()
    assert len(turns[0].wakes) == 1 and turns[0].closed
    assert voice_service.get_events()["events"] == []  # No OpenCode/Piper duplicate.
    assert voice_service.status()["realtime"]["model"] == "gpt-realtime-2.1"
    assert bindings == ([("/PRIVATE/opencode2", "mini1", Path.cwd())] if opencode else [])


def test_voice_model_cannot_switch_station_or_bypass_live_authorization(
        tmp_path: Path, monkeypatch: Any) -> None:
    configured = station(tmp_path)
    mini = Station(**{**configured.__dict__, "preauthorize_all_actions": False})
    voice_service = service(tmp_path, monkeypatch, mini)
    voice_service.station = "mini1"
    sent: list[Any] = []
    monkeypatch.setattr(vs.native_transport, "run_action",
                        lambda *args: sent.append(args) or {"outcome": "applied"})
    assert voice_service._action({"action": "audio.volume", "parameters": {"percent": 10},
                                  "station_id": "other"})["outcome"] == "blocked"
    assert voice_service._action({"action": "audio.volume", "parameters": {"percent": 10}
                                  })["outcome"] == "blocked"
    assert not sent


def test_dictation_events_are_ram_only(tmp_path: Path, monkeypatch: Any) -> None:
    voice_service = service(tmp_path, monkeypatch, station(tmp_path))
    voice_service._record({"kind": "dictation", "station": "mini1", "text": "synthetic",
                            "trusted": False}, persist=False)
    assert voice_service.get_events()["events"][0]["text"] == "synthetic"
    assert not voice_service.sink.path.exists()


def test_on_station_asr_wakes_from_partial_then_delivers_local_finals(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    voice_service = service(tmp_path, monkeypatch, station(tmp_path))
    heard: list[str] = []
    wakes: list[int] = []
    speech: list[bool] = []

    class LocalTurn:
        active = False
        state = "idle"
        echo_until = 0.0

        def wake(self, event_id: int) -> bool:
            self.active, self.state = True, "transcribing"
            wakes.append(event_id)
            return True

        def transcript(self, text: str) -> bool:
            heard.append(text)
            return True

        def speech_started(self) -> None:
            speech.append(True)

    turn = LocalTurn()
    voice_service.realtime = turn  # type: ignore[assignment]
    voice_service.station = "mini1"
    voice_service._station_event({"event": "ready", "load_ms": 100})
    assert voice_service.state == "listening"
    voice_service._station_event({"event": "partial", "result": {"partial": "компьютер"}})
    assert not speech
    voice_service._station_event({"event": "partial", "result": {"partial": "компьютер давай"}})
    assert speech == [True]
    voice_service._station_event({"event": "final", "result": {"text": "компьютер"}})
    voice_service._station_event({"event": "final", "result": {"text": "давай попиздим"}})
    assert len(wakes) == 1 and heard == ["давай попиздим"]
    assert voice_service.get_events()["events"] == []
    turn.active, turn.state = False, "idle"
    voice_service._station_event({"event": "final", "result": {"text": "просто шум"}})
    assert len(wakes) == 1
    voice_service._station_event({"event": "partial", "result": {"partial": "компьютер"}})
    assert len(wakes) == 2
    turn.active, turn.state = False, "idle"  # The empty command timed out quietly.
    voice_service._station_event({"event": "final", "result": {"text": "компьютер"}})
    assert len(wakes) == 2  # Its delayed final must not wake the ring a second time.
    voice_service._station_event({"event": "partial", "result": {"partial": "компьютер"}})
    assert len(wakes) == 3


def test_station_asr_status_reports_actual_audio_progress(tmp_path: Path, monkeypatch: Any) -> None:
    voice_service = service(tmp_path, monkeypatch, station(tmp_path))
    voice_service.source = "station_asr"
    voice_service.live.update(audio_ms=3200, elapsed_ms=3400, load_ms=10000)
    status = voice_service.status()
    assert status["audio_s"] == 3.2
    assert status["asr"] == {"load_ms": 10000, "lag_ms": 200, "wake_source": "station_vosk"}


@pytest.mark.parametrize("ready", [False, True])
def test_station_asr_only_owner_can_enable_indicator_preparation(
        tmp_path: Path, monkeypatch: Any, ready: bool) -> None:
    made: list[Any] = []

    class Indicator:
        def __init__(self, *args: Any, defer_preparation: bool = False) -> None:
            self.deferred = defer_preparation
            self.armed = not defer_preparation
            self.closed = False
            made.append(self)

        def listen_ready(self) -> None:
            self.armed = True

        def close(self) -> None:
            self.closed = True

        def status(self) -> dict[str, str]:
            return {"state": "idle"}

    def listen(controller: Any, station_id: str, sink: Any, *, stop: Any,
               **kwargs: Any) -> dict[str, Any]:
        assert not made[0].armed
        if not ready:
            raise SafetyError("source already owned")
        sink({"event": "ready", "wake_source": "station_mww"})
        assert made[0].armed
        stop.set()
        return {"outcome": "complete", "cleanup_verified": True}

    monkeypatch.setattr(vs.vi, "Indicator", Indicator)
    monkeypatch.setattr(vs.station_asr, "listen", listen)
    voice_service = service(tmp_path, monkeypatch, station(
        tmp_path, source="station_asr",
        asr_directory="/bkupgrade/data/opt/alice-local-asr-aaaaaaaaaaaa"))
    voice_service.start("mini1")
    wait(lambda: voice_service.state == "stopped")
    assert made[0].deferred and made[0].closed and made[0].armed == ready
    assert (voice_service.last_error is None) == ready


def test_fast_station_wake_starts_before_vosk_and_late_text_cannot_reactivate(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    voice = service(tmp_path, monkeypatch, station(tmp_path))
    heard = []
    wakes = []

    class Turn:
        active = False
        echo_until = 0.0

        def wake(self, event_id: int) -> bool:
            self.active = True
            wakes.append(event_id)
            return True

        def transcript(self, text: str) -> bool:
            heard.append(text)
            return True

        def speech_started(self) -> None:
            pass

    turn = Turn()
    voice.realtime = turn  # type: ignore[assignment]
    voice.station = "mini1"
    voice._station_event({"event": "ready", "wake_source": "station_mww"})
    voice._station_event({"event": "wake", "audio_ms": 500, "trusted": False})
    assert len(wakes) == 1
    voice._station_event({"event": "final", "result": {"text": "компьютер давай попиздим"}})
    assert heard == ["давай попиздим"]
    turn.active = False
    for kind in ("partial", "final"):
        voice._station_event({"event": kind, "result": {
            "partial" if kind == "partial" else "text": "компьютер"}})
    assert len(wakes) == 1
    turn.echo_until = time.monotonic() + 10
    voice._station_event({"event": "wake", "audio_ms": 4000, "trusted": False})
    assert len(wakes) == 1
    turn.echo_until = 0
    voice._station_event({"event": "wake", "audio_ms": 7000, "trusted": False})
    assert len(wakes) == 2


@pytest.mark.parametrize("text", ["стоп", "хватит", "замолчи", "компьютер стоп",
                                  "прекрати", "остановись", "хватит говорить"])
def test_emergency_words_interrupt_partials_during_playback_without_wake_or_echo_gate(
    tmp_path: Path, monkeypatch: Any, text: str,
) -> None:
    voice = service(tmp_path, monkeypatch, station(tmp_path))
    turn = vs.rv.RealtimeTurn("synthetic", None, lambda _: {}, local_transcripts=True)
    turn.active, turn.state = True, "speaking"
    turn.echo_until = time.monotonic() + 20
    voice.realtime = turn
    voice._station_event({"event": "partial", "result": {"partial": text}})
    assert turn.cancel.is_set() and turn.text_stopped.is_set() and turn.error is None


def test_station_activity_keeps_a_lagging_command_open_but_ignores_wake_tail(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    voice = service(tmp_path, monkeypatch, station(tmp_path))
    turn = vs.rv.RealtimeTurn('synthetic', None, lambda _: {}, local_transcripts=True)
    voice.realtime = turn
    voice._station_event({'event': 'ready', 'wake_source': 'station_mww'})
    monkeypatch.setattr(voice, '_detected', lambda: None)
    voice._station_event({'event': 'wake', 'audio_ms': 1000, 'trusted': False})
    turn.active, turn.state = True, 'transcribing'
    voice._station_event({'event': 'activity', 'audio_ms': 1500, 'trusted': False})
    assert not turn.command_started.is_set()
    assert turn.last_input_at == 0
    voice._station_event({'event': 'activity', 'audio_ms': 1800, 'trusted': False})
    assert turn.last_input_at > 0 and not turn.command_started.is_set()
    last_input = turn.last_input_at
    turn.state = 'speaking'
    voice._station_event({'event': 'activity', 'audio_ms': 2000, 'trusted': False})
    assert turn.last_input_at == last_input and not turn.command_started.is_set()


@pytest.mark.parametrize('kind,key', [('interrupt_partial','partial'), ('interrupt_final','text')])
def test_stop_grammar_output_can_only_interrupt_not_execute_commands(
    tmp_path: Path, monkeypatch: Any, kind: str, key: str,
) -> None:
    voice = service(tmp_path, monkeypatch, station(tmp_path))
    turn = vs.rv.RealtimeTurn('synthetic', None, lambda _: {}, local_transcripts=True)
    turn.active, turn.state = True, 'speaking'
    turn.echo_until = time.monotonic() + 10
    voice.realtime = turn
    voice._station_event({'event':kind, 'result':{key:'[unk] включи свет'}})
    assert not turn.cancel.is_set() and turn.text.empty()
    voice._station_event({'event':kind, 'result':{key:'[unk] стоп [unk]'}})
    assert turn.cancel.is_set() and turn.text_stopped.is_set()


def test_fast_wake_drops_a_vosk_word_fragment_until_post_wake_command_activity(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    voice = service(tmp_path, monkeypatch, station(tmp_path))
    turn = vs.rv.RealtimeTurn('synthetic', None, lambda _: {}, local_transcripts=True)
    voice.realtime = turn
    voice._station_event({'event':'ready','wake_source':'station_mww'})
    monkeypatch.setattr(voice, '_detected', lambda: None)
    voice._station_event({'event':'wake','audio_ms':1000,'trusted':False})
    turn.active,turn.state = True,'transcribing'
    # A decoder rotation during the wake word can decode only its last syllables.
    voice._station_event({'event':'partial','result':{'partial':'питер'}})
    voice._station_event({'event':'final','result':{'text':'питер'}})
    assert turn.text.empty() and not turn.command_started.is_set()
    voice._station_event({'event':'activity','audio_ms':2200,'trusted':False})
    voice._station_event({'event':'partial','result':{'partial':'давай поговорим'}})
    voice._station_event({'event':'final','result':{'text':'давай поговорим'}})
    assert turn.text.get_nowait() == 'давай поговорим'


def test_activity_from_the_other_channel_can_arrive_before_its_wake(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    voice = service(tmp_path, monkeypatch, station(tmp_path))
    turn = vs.rv.RealtimeTurn('synthetic', None, lambda _: {}, local_transcripts=True)
    voice.realtime = turn
    voice._station_event({'event':'ready','wake_source':'station_mww'})
    def detected():
        turn.active,turn.state = True,'transcribing'
    monkeypatch.setattr(voice, '_detected', detected)
    voice._station_event({'event':'activity','audio_ms':2000,'trusted':False})
    voice._station_event({'event':'wake','audio_ms':1000,'trusted':False})
    assert turn.last_input_at > 0 and not turn.command_started.is_set()
    voice._station_event({'event':'final','result':{'text':'давай поговорим'}})
    assert turn.text.get_nowait() == 'давай поговорим'


def test_speech_activity_prevents_false_seven_second_conversation_timeout(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    voice = service(tmp_path, monkeypatch, station(tmp_path))
    turn = vs.rv.RealtimeTurn('synthetic', None, lambda _: {}, local_transcripts=True)
    voice.realtime = turn
    voice._station_event({'event':'ready','wake_source':'station_mww'})
    turn.active,turn.mode,turn.state = True,'conversation','conversation'
    turn.conversation_idle_at = 100.0
    now = [106.5]
    monkeypatch.setattr(vs.rv.time,'monotonic',lambda:now[0])
    turn.socket = SimpleNamespace(recv=lambda **kw:json.dumps({'type':'response.created'}))
    voice._station_event({'event':'activity','audio_ms':20000,'trusted':False})
    now[0] = 107.0
    assert turn._receive(200) == {'type':'response.created'}
