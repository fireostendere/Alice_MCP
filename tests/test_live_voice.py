"""Host side of direct station Realtime sessions; synthetic station, processes and cloud."""

import hashlib
import json
import queue
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import device_runtime as rt
from alice_mcp import live_voice as lv
from alice_mcp import quasar_client as qc
from alice_mcp import realtime_voice as rv
from alice_mcp import station_assistant as sa
from alice_mcp.core import Controller, SafetyError, Station

REAL_PERMITTED = lv.iop._permitted  # The fixture below disables it.

ASR = "/bkupgrade/data/opt/alice-local-asr-0123456789ab"
FAKE_CLIENT = r"""#!/bin/sh
IFS= read -r secret; IFS= read -r opening
echo '{"event":"connected","ms":5}'
printf '{"event":"end","reason":"idle","args":"%s","first":"%s","opening":"%s"}\n' \
 "$*" "$secret" "$opening"
"""


@pytest.fixture(autouse=True)
def no_live_weather_network(monkeypatch: Any) -> None:
    monkeypatch.setattr(sa, "weather", lambda **kwargs: {"source": "unavailable"})
    monkeypatch.setattr(sa, "_weather_cache", None, raising=False)
    monkeypatch.setattr(sa, "news", lambda **kwargs: {"source": "unavailable"}, raising=False)
    monkeypatch.setattr(sa, "_news_cache", None, raising=False)


@pytest.fixture
def station_script(tmp_path: Path) -> tuple[Path, str]:
    if not shutil.which("dash"):
        pytest.skip("Station script checks use dash as a busybox-like POSIX shell")
    root = tmp_path / "dev"
    (root / "bin").mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "maind").write_bytes(b"synthetic firmware")
    for name, body in {"id": "echo 0", "pidof": "exit 0"}.items():
        (root / "bin" / name).write_text("#!/bin/sh\n" + body + "\n")
        (root / "bin" / name).chmod(0o755)
    script = (lv.SCRIPT.replace("/system/vendor/quasar/maind", str(root / "maind"))
              .replace("/tmp/alice-mcp-live", str(root / "tmp/alice-mcp-live")))
    return root, script


def _station_run(root: Path, script: str, *, check: str = "full",
                 firmware: bytes = b"synthetic firmware", client: bytes = FAKE_CLIENT.encode(),
                 client_pin: str | None = None) -> dict[str, Any]:
    player = b"#!/bin/sh\nexit 0\n"
    args = [check, hashlib.sha256(firmware).hexdigest(), str(len(player)),
            hashlib.sha256(player).hexdigest(), str(len(client)),
            client_pin or hashlib.sha256(client).hexdigest(), "-", "c" * 64,
            "gpt-realtime-2.1-mini", "60", "200"]
    out = subprocess.run(["dash", "-c", script, "sh", *args],
                         input=player + client + "ek_secret\nскажи «привет»\n".encode(),
                         capture_output=True, env={"PATH": f"{root / 'bin'}:/usr/bin:/bin"},
                         timeout=20)
    return json.loads(out.stdout.decode().strip().splitlines()[-1])


def test_station_script_pins_both_builds_and_hands_the_secret_to_the_client(
        station_script: tuple[Path, str]) -> None:
    end = _station_run(*station_script)
    assert end["reason"] == "idle" and end["first"] == "ek_secret"
    assert end["opening"] == "скажи «привет»"  # Exactly after the binaries on stdin.
    assert "--model gpt-realtime-2.1-mini --max-seconds 60 --idle-ms 200" in end["args"]
    assert "--capture" not in end["args"]  # Speak-only: no microphone.
    assert not list((station_script[0] / "tmp").iterdir())  # Temporary builds removed.


def test_standby_script_pins_the_wake_detector_and_starts_the_client_waiting(
        station_script: tuple[Path, str], tmp_path: Path) -> None:
    root, _ = station_script
    script = (lv.STANDBY_SCRIPT.replace("/system/vendor/quasar/maind", str(root / "maind"))
              .replace("/tmp/alice-mcp-live", str(root / "tmp/alice-mcp-live"))
              .replace("/bkupgrade/data/opt/alice-local-asr-*", str(tmp_path / "asr")))
    (tmp_path / "asr").mkdir()
    (tmp_path / "asr/capture").write_bytes(b"capture")
    (tmp_path / "asr/wake-detector").write_bytes(b"detector")
    player, client = b"#!/bin/sh\nexit 0\n", b'#!/bin/sh\necho "{\\"args\\":\\"$*\\"}"\n'

    def start(detector_pin: str) -> dict[str, Any]:
        args = ["full", hashlib.sha256(b"synthetic firmware").hexdigest(), str(len(player)),
                hashlib.sha256(player).hexdigest(), str(len(client)),
                hashlib.sha256(client).hexdigest(), str(tmp_path / "asr"),
                hashlib.sha256(b"capture").hexdigest(), detector_pin, "3540"]
        out = subprocess.run(["dash", "-c", script, "sh", *args], input=player + client,
                             capture_output=True, timeout=20,
                             env={"PATH": f"{root / 'bin'}:/usr/bin:/bin"})
        return json.loads(out.stdout.decode().strip().splitlines()[-1])

    started = start(hashlib.sha256(b"detector").hexdigest())["args"]
    assert "--max-seconds 3540 --idle-ms 7000" in started
    asr = tmp_path / "asr"
    assert f"--capture {asr}/capture --wake-detector {asr}/wake-detector" in started
    assert start("0" * 64) == {"event": "end", "reason": "pin_mismatch"}


def test_station_script_blocks_firmware_and_pin_mismatch_but_trusts_a_verified_stamp(
        station_script: tuple[Path, str]) -> None:
    assert _station_run(*station_script, firmware=b"other")["reason"] == "firmware_mismatch"
    assert _station_run(*station_script, firmware=b"other", check="verified")["reason"] == "idle"
    assert _station_run(*station_script, client_pin="0" * 64)["reason"] == "pin_mismatch"


class FakeProcess:
    def __init__(self, lines: list[bytes], *, wait_stop: bool = False,
                 gate: bytes = b"") -> None:
        self.stdin = self
        self.written = bytearray()
        self.lines, self.wait_stop, self.gate = lines, wait_stop, gate
        self.gated = threading.Event()
        self.stopped = threading.Event()
        self.stdout = self
        self.done = False

    def write(self, data: bytes) -> int:
        self.written += data
        if self.gate and self.gate in self.written:
            self.gated.set()
        if data == b"stop\n":
            self.stopped.set()
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def readline(self, limit: int = -1) -> bytes:
        if (self.wait_stop or self.gate) and len(self.lines) == 1:
            assert (self.gated if self.gate else self.stopped).wait(5)
        if not self.lines:
            self.done = True
            return b""
        return self.lines.pop(0)

    def poll(self) -> int | None:
        return 0 if self.done else None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass

    def __enter__(self) -> "FakeProcess":
        return self

    def __exit__(self, *args: Any) -> None:
        self.done = True


@pytest.fixture
def live_station(tmp_path: Path, monkeypatch: Any) -> tuple[Controller, Path]:
    key, hosts = tmp_path / "key", tmp_path / "hosts"
    key.write_text("synthetic key\n")
    key.chmod(0o600)
    hosts.write_text("synthetic host key\n")
    (tmp_path / "player").write_bytes(b"\x7fELF player")
    (tmp_path / "client").write_bytes(b"\x7fELF client")
    monkeypatch.setattr(lv, "PLAYER_SHA256", hashlib.sha256(b"\x7fELF player").hexdigest())
    monkeypatch.setattr(lv, "CLIENT_SHA256", hashlib.sha256(b"\x7fELF client").hexdigest())
    policy = rt.validate_policy({"firmware_sha256": "a" * 64, "voice": {
        "source": "station_asr", "asr_directory": ASR,
        "live_client": str(tmp_path / "client"), "live_player": str(tmp_path / "player")}},
        "station-mini")
    station = Station("mini1", "station-mini", "h", 22, "root", key, hosts, True, "", "c" * 64,
                      "t" * 64, policy, preauthorize_all_actions=True,
                      native_mcp={"url": "https://h/mcp"})
    controller = Controller(None, writes=True, runner=lambda *a: pytest.fail("runner"))
    monkeypatch.setattr(controller, "station", lambda _: station)
    monkeypatch.setattr(lv.native_transport, "_state", lambda *a: "d" * 64)
    monkeypatch.setattr(lv.iop, "_permitted", lambda *a: None)
    qc._verified.clear()
    return controller, tmp_path


def _live(controller: Controller, process: FakeProcess, *, listen: bool = True,
          cancel: threading.Event | None = None, minter: Any = None,
          tool: Any = None) -> tuple[
              dict[str, Any], list[str], list[Any]]:
    argv: list[Any] = []
    seen: list[str] = []

    def popen(args: list[str], **kwargs: Any) -> FakeProcess:
        argv.append(args)
        return process

    import time
    result = lv.run(controller, "mini1", "sk-key", {"model": rv.MODEL}, "давай\nпоговорим",
                    listen=listen, deadline=time.monotonic() + 30,
                    cancel=cancel or threading.Event(), on_event=lambda e: seen.append(e["event"]),
                    minter=minter or (lambda key, session, timeout: "ek_secret"), popen=popen,
                    tool=tool)
    return result, seen, argv


END = b'{"event":"end","reason":"idle","barge_ins":1,"player":{"outcome":"complete",' \
      b'"frames_written":9600,"cleanup_verified":true}}\n'


def test_run_sends_pinned_builds_then_the_secret_and_reports_the_end(
        live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station
    process = FakeProcess([b'{"event":"connected","ms":900}\n', b"noise\n",
                           b'{"event":"first_audio","ms":1900}\n', END])
    result, seen, argv = _live(controller, process)
    runtime = result.pop("speech_runtime")
    assert runtime["transport"] == "cold" and "first_audio" in runtime["timing_ms"]
    assert result == {"outcome": "complete", "reason": "idle", "barge_ins": 1, "mic": {},
                      "player": {"outcome": "complete", "frames_written": 9600,
                                 "cleanup_verified": True}}
    assert seen == ["connected", "first_audio"] and "ek_secret" not in json.dumps(result)
    args = shlex.split(argv[0][-1])
    assert args[4:] == ["full", "a" * 64, "11", lv.PLAYER_SHA256, "11", lv.CLIENT_SHA256, ASR,
                        lv.station_asr.MANIFEST["capture"], rv.MODEL, args[13], "7000"]
    assert bytes(process.written).endswith(b"\x7fELF player\x7fELF client"
                                           b"ek_secret\n\xd0\xb4\xd0\xb0\xd0\xb2\xd0\xb0\xd0\xb9 "
                                           + "поговорим".encode() + b"\n")
    # The station's full firmware check passed under this resident stamp: reuse it next time.
    _, _, argv = _live(controller, FakeProcess([END]), listen=False)
    assert shlex.split(argv[0][-1])[4] == "verified"
    assert shlex.split(argv[0][-1])[10] == "-"  # Speak-only sessions never open the mic.


@pytest.mark.parametrize(("busy", "check"), [(2, "full"), (99, "full")])
def test_busy_resident_state_is_retried_then_the_station_checks_fully(
        live_station: tuple[Controller, Path], monkeypatch: Any, busy: int, check: str) -> None:
    # The resident serves one call at a time; the ring's wake check may hold it.
    controller, _ = live_station
    calls: list[int] = []

    def state(*args: Any) -> str:
        calls.append(1)
        if len(calls) <= busy:
            raise SafetyError("Native policy response unavailable or incompatible")
        return "d" * 64

    monkeypatch.setattr(lv.native_transport, "_state", state)
    monkeypatch.setattr(lv.time, "sleep", lambda _: None)
    result, _, argv = _live(controller, FakeProcess([b'{"event":"connected"}\n', END]))
    assert result["outcome"] == "complete" and shlex.split(argv[0][-1])[4] == check
    assert (qc._verified.get("mini1") == "d" * 64) is (busy < 10)  # No stamp, no cache.


def test_cancel_sends_stop_and_a_stopped_session_is_complete(
        live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station
    cancel = threading.Event()
    process = FakeProcess([b'{"event":"connected"}\n',
                           END.replace(b'"idle"', b'"stopped"')], wait_stop=True)
    threading.Timer(0.2, cancel.set).start()
    result, _, _ = _live(controller, process, cancel=cancel)
    assert process.stopped.is_set() and result["outcome"] == "complete"


@pytest.mark.parametrize(("reason", "outcome"), [
    ("firmware_mismatch", "blocked"), ("pin_mismatch", "blocked"), ("api_error", "cloud_failed"),
    ("tls_failed", "cloud_failed"), ("failed", "unknown"), ("player_failed", "unknown"),
    ("stopped", "unknown"),  # Stopped without our cancel: the transport broke.
])
def test_end_reasons_are_classified_and_uncertain_playback_is_unknown(
        live_station: tuple[Controller, Path], reason: str, outcome: str) -> None:
    controller, _ = live_station
    line = END.replace(b'"idle"', b'"' + reason.encode() + b'"')
    assert _live(controller, FakeProcess([line]))[0]["outcome"] == outcome
    assert _live(controller, FakeProcess([]))[0]["outcome"] == "unknown"  # No end at all.


def test_mint_failure_never_starts_a_session(live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station

    def fail(*args: Any) -> str:
        raise RuntimeError("PRIVATE 401")

    with pytest.raises(SafetyError, match="api_error"):
        _live(controller, FakeProcess([END]), minter=fail)


def test_tool_call_runs_on_this_pc_and_its_output_goes_back_on_one_line(
        live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station
    call = {"type": "response.function_call_arguments.done", "call_id": "call_7",
            "name": "web_search", "arguments": '{"query":"погода в Москве"}'}
    process = FakeProcess([b'{"event":"connected"}\n',
                           json.dumps({"event": "tool", "message": call}).encode() + b"\n", END],
                          gate=b"tool {")
    calls: list[Any] = []
    result, seen, _ = _live(controller, process,
                            tool=lambda name, args: calls.append((name, args)) or 'Ясно\n"+3"')
    assert calls == [("web_search", {"query": "погода в Москве"})]
    line = bytes(process.written).split(b"tool ", 1)[1]
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert json.loads(line) == {"type": "conversation.item.create", "item": {
        "type": "function_call_output", "call_id": "call_7", "output": 'Ясно\n"+3"'}}
    assert line.startswith(b'{"type":"conversation.item.create","item":{"type":'
                           b'"function_call_output","call_id":"')  # The client's prefix.
    assert result["outcome"] == "complete" and "tool" in seen


def test_failing_or_unknown_tool_still_answers_the_call(
        live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station
    call = {"type": "response.function_call_arguments.done", "call_id": "c",
            "name": "web_search", "arguments": "{}"}

    def broken(name: str, args: Any) -> str:
        raise RuntimeError("PRIVATE")

    process = FakeProcess([json.dumps({"event": "tool", "message": call}).encode() + b"\n",
                           END], gate=b"tool {")
    _live(controller, process, tool=broken)
    output = json.loads(bytes(process.written).split(b"tool ", 1)[1])["item"]["output"]
    assert output and "PRIVATE" not in output


def test_policy_needs_both_private_live_builds() -> None:
    voice = {"source": "station_asr", "asr_directory": ASR, "live_client": "/private/c"}
    with pytest.raises(ValueError):
        rt.validate_policy({"firmware_sha256": "a" * 64, "voice": voice}, "station-mini")
    voice["live_player"] = "/private/p"
    assert rt.validate_policy({"firmware_sha256": "a" * 64, "voice": voice},
                              "station-mini")["voice"]["live_player"] == "/private/p"


class Ring:
    max_s = 5

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def wake(self, event_id: int, *, managed: bool = False) -> None:
        self.calls.append(("wake", event_id))

    def complete(self, event_id: int, *, error: bool = False) -> None:
        self.calls.append(("complete", event_id, error))


def _turn(outcome: str, sessions: list[dict[str, Any]], **kwargs: Any) -> rv.RealtimeTurn:
    """`direct` is the station session; `tool` is what it would run on this PC."""
    def direct(session: dict[str, Any], text: str, **options: Any) -> dict[str, Any]:
        sessions.append({"session": session, "text": text, **options})
        options["on_event"]({"event": "first_audio"})
        if options["listen"]:
            assert not turn.transcript("компьютер ещё вопрос")  # Live: Vosk text is ignored.
        return {"outcome": outcome, "reason": "idle", "player": {"outcome": "complete"}}

    turn = rv.RealtimeTurn("synthetic", kwargs.pop("ring", Ring()),
                           lambda _: pytest.fail("WAV playback"),
                           connect=lambda *a: pytest.fail("host Realtime socket"),
                           local_transcripts=True, direct=direct, **kwargs)
    return turn


def wait_idle(turn: rv.RealtimeTurn) -> None:
    import time
    until = time.monotonic() + 3
    while turn.active and time.monotonic() < until:
        time.sleep(0.01)
    assert not turn.active


def test_conversation_runs_on_the_station_with_barge_in_and_the_ring_held() -> None:
    sessions: list[dict[str, Any]] = []
    ring = Ring()
    turn = _turn("complete", sessions, ring=ring, conversation_prompt="Начни первым.")
    assert turn.wake(1) and turn.transcript("давай поговорим")
    wait_idle(turn)
    [call] = sessions
    audio = call["session"]["audio"]["input"]
    assert call["listen"] is True and call["text"] == "давай поговорим"
    assert call["session"]["model"] == rv.MODEL and "transcription" not in audio
    assert audio["turn_detection"]["interrupt_response"] is True
    assert "Начни первым." in call["session"]["instructions"]
    assert [t["name"] for t in call["session"]["tools"]] == [
        "web_search", "get_time", "get_weather", "get_news"]
    assert call["session"]["tool_choice"] == "auto" and callable(call["tool"])
    assert ring.calls == [("wake", 1), ("complete", 1, False)]  # Lit for the whole call.
    assert turn.error is None and "first_audio" in turn.timing_ms
    assert turn.status()["transport"] == "station_direct"


def test_answer_is_spoken_by_the_station_without_microphone_or_vad(monkeypatch: Any) -> None:
    sessions: list[dict[str, Any]] = []
    turn = _turn("complete", sessions)
    monkeypatch.setattr(turn, "_reason", lambda text, deadline: "Двенадцать")
    assert turn.wake(1) and turn.transcript("сколько будет пять плюс семь")
    wait_idle(turn)
    [call] = sessions
    assert call["listen"] is False and call["text"] == "Двенадцать"
    assert call["session"]["model"] == rv.TTS_MODEL
    assert call["session"]["audio"]["output"]["voice"] == "marin"
    assert call["session"]["audio"]["input"]["turn_detection"] is None
    assert "tools" not in call["session"] and call["tool"] is None


@pytest.mark.parametrize(("outcome", "error", "latched"), [
    ("blocked", "live_blocked", False), ("cloud_failed", "api_error", False),
    ("unknown", "playback_unverified", True)])
def test_failed_station_session_reports_and_latches_only_uncertain_playback(
        outcome: str, error: str, latched: bool) -> None:
    ring = Ring()
    turn = _turn(outcome, [], ring=ring)
    assert turn.wake(1) and turn.transcript("давай поговорим")
    wait_idle(turn)
    assert turn.error == error and turn.latched is latched
    assert ring.calls[-1] == ("complete", 1, not latched)


def test_web_search_uses_responses_search_and_returns_plain_short_text(monkeypatch: Any) -> None:
    sent: list[dict[str, Any]] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"output": [{"type": "web_search_call"}, {"type": "message", "content": [
                {"type": "output_text",
                 "text": "В Москве **+3**. ([meteo.ru](https://meteo.ru/x)) См. [сайт](https://a.b)."}]}]}

    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.append({"url": url, **kw})
                        or Response())
    text = rv.web_search("sk-key", "погода в Москве", 10)
    assert text == "В Москве +3. См. сайт."
    assert sent[0]["url"].endswith("/v1/responses")
    assert sent[0]["json"]["tools"] == [{"type": "web_search"}]
    assert sent[0]["json"]["store"] is False and sent[0]["json"]["input"] == "погода в Москве"


def test_web_search_failure_or_bad_query_is_a_spoken_fact_not_an_exception(
        monkeypatch: Any) -> None:
    import httpx

    def fail(*a: Any, **kw: Any) -> Any:
        raise httpx.ConnectError("PRIVATE")

    monkeypatch.setattr(httpx, "post", fail)
    assert "PRIVATE" not in rv.web_search("sk-key", "новости", 10)
    assert rv.web_search("sk-key", "", 10) and rv.web_search("sk-key", "x" * 501, 10)


READY = {"event": "ready", "rate": 16000, "threshold": 0.98, "window": 5, "cooldown_ms": 2500,
         "gain_db": 0, "models": [{"name": "komp", "sha256": lv.station_asr.WAKE_MODEL_SHA256,
                                   "stride": 3, "arena_bytes": 20572}]}
WAKE = {"event": "wake", "model": "komp", "t_ms": 1500, "probability": 0.99, "peak": 0.99,
        "last": 0.99, "trusted": False}


class Pipe:
    """A standby process: stdout lines appear as the test feeds them."""

    def __init__(self) -> None:
        self.lines: queue.Queue[bytes] = queue.Queue()
        self.written = bytearray()
        self.done = False
        self.stdin, self.stdout = self, self

    def say(self, event: dict[str, Any]) -> None:
        self.lines.put(json.dumps(event).encode() + b"\n")

    def write(self, data: bytes) -> int:
        self.written += data
        if data == b"stop\n":
            self.say({"event": "end", "reason": "stopped"})
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def readline(self, limit: int = -1) -> bytes:
        line = self.lines.get(timeout=5)
        if not line:
            self.done = True
        return line

    def poll(self) -> int | None:
        return 0 if self.done else None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass

    def __enter__(self) -> "Pipe":
        return self

    def __exit__(self, *args: Any) -> None:
        self.done = True


def test_standby_reports_wakes_and_runs_one_session_per_secret(
        live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station
    pipe, sunk, argv = Pipe(), [], []
    stop = threading.Event()

    def popen(args: list[str], **kwargs: Any) -> Pipe:
        argv.append(args)
        return pipe

    standby = lv.Standby(controller, "mini1", "sk-key", sunk.append, stop, popen=popen,
                         minter=lambda key, session, timeout: "ek_secret")
    runner = threading.Thread(target=lambda: sunk.append(standby.run()))
    runner.start()
    pipe.say({"event": "detector", "line": READY})
    pipe.say({"event": "detector", "line": WAKE})
    import time
    until = time.monotonic() + 3
    while len(sunk) < 2 and time.monotonic() < until:
        time.sleep(0.01)
    assert sunk[:2] == [{"event": "ready", "wake_source": "station_mww"},
                        {"event": "wake", "audio_ms": 1500, "trusted": False}]
    args = shlex.split(argv[0][-1])
    assert args[4:] == ["full", "a" * 64, "11", lv.PLAYER_SHA256, "11", lv.CLIENT_SHA256, ASR,
                        lv.station_asr.MANIFEST["capture"], lv.wp.FT_DETECTOR_SHA256, "3540"]

    seen: list[str] = []

    def station_side() -> None:
        while b"session " not in pipe.written:
            time.sleep(0.01)
        pipe.say({"event": "connected", "ms": 900})
        pipe.say({"event": "tool", "message": {
            "type": "response.function_call_arguments.done", "call_id": "c1",
            "name": "web_search", "arguments": '{"query":"погода"}'}})
        while b"tool {" not in pipe.written:
            time.sleep(0.01)
        pipe.say({"event": "session_end", "reason": "idle", "barge_ins": 0,
                  "player": {"outcome": "complete", "cleanup_verified": True}})

    threading.Thread(target=station_side).start()
    result = standby.session({"model": rv.MODEL}, "", listen=True,
                             deadline=time.monotonic() + 30, cancel=threading.Event(),
                             on_event=lambda e: seen.append(e["event"]),
                             tool=lambda name, args: "ясно")
    assert result["outcome"] == "complete" and seen == ["connected", "tool"]
    line = bytes(pipe.written).split(b"session ", 1)[1].split(b"\n", 1)[0]
    model, seconds, secret = line.split()
    assert model == rv.MODEL.encode() and 25 <= int(seconds) <= 30 and secret == b"ek_secret"
    assert b'"call_id":"c1","output":"\xd1\x8f\xd1\x81\xd0\xbd\xd0\xbe"' in pipe.written
    stop.set()
    runner.join(5)
    assert sunk[-1]["outcome"] == "complete"


@pytest.mark.parametrize("player_outcome", ["complete", "unknown"])
def test_standby_session_cancel_and_a_dead_standby(
        live_station: tuple[Controller, Path], player_outcome: str) -> None:
    controller, _ = live_station
    pipe = Pipe()
    stop = threading.Event()
    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, stop,
                         popen=lambda *a, **k: pipe, minter=lambda *a: "ek_secret")
    import time
    with pytest.raises(SafetyError, match="live_unavailable"):
        standby.session({"model": rv.MODEL}, "", listen=True, deadline=time.monotonic() + 30,
                        cancel=threading.Event())
    runner = threading.Thread(target=standby.run)
    runner.start()
    pipe.say({"event": "detector", "line": READY})
    while standby.process is None:
        time.sleep(0.01)
    cancel = threading.Event()

    def station_side() -> None:
        while b"session " not in pipe.written:
            time.sleep(0.01)
        cancel.set()
        while b"cancel\n" not in pipe.written:
            time.sleep(0.01)
        pipe.say({"event": "session_end", "reason": "stopped", "barge_ins": 0,
                  "player": {"outcome": player_outcome}})

    threading.Thread(target=station_side).start()
    result = standby.session({"model": rv.MODEL}, "", listen=True,
                             deadline=time.monotonic() + 30, cancel=cancel)
    assert result["outcome"] == player_outcome and result["reason"] == "stopped"
    assert result["interrupted"] is (player_outcome == "complete")
    stop.set()
    runner.join(5)


def test_policy_station_live_needs_the_wake_directory_and_both_live_builds() -> None:
    voice = {"source": "station_live", "asr_directory": ASR, "live_client": "/private/c"}
    with pytest.raises(ValueError):
        rt.validate_policy({"firmware_sha256": "a" * 64, "voice": voice}, "station-mini")
    voice["live_player"] = "/private/p"
    assert rt.validate_policy({"firmware_sha256": "a" * 64, "voice": voice},
                              "station-mini")["voice"]["source"] == "station_live"


def test_wake_opens_a_listening_session_at_once_with_search_and_volume(monkeypatch: Any) -> None:
    sessions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    outputs: list[str] = []

    def direct(session: dict[str, Any], text: str, **options: Any) -> dict[str, Any]:
        sessions.append({"session": session, "text": text, **options})
        outputs.append(options["tool"]("set_volume", {"level": 5}))
        outputs.append(options["tool"]("set_volume", {"level": 5, "step": "louder"}))
        return {"outcome": "complete", "reason": "idle", "player": {"outcome": "complete"}}

    def action(arguments: dict[str, Any]) -> dict[str, Any]:
        actions.append(arguments)
        return {"outcome": "applied"}

    ring = Ring()
    weather = {"city": "Белград", "current": {"temperature_c": 9.6,
               "time": "UNRELATED_WEATHER_OBSERVATION_TIME"}, "forecast": [],
               "sources": ["https://example.com/weather"], "retrieved_at": "unused"}
    news = {"publisher": "BBC", "headlines": [{"title": "Свежий заголовок", "url":
             "https://example.com/news", "published": "unused"}], "sources": []}
    monkeypatch.setattr(sa, "weather_snapshot", lambda: weather)
    monkeypatch.setattr(sa, "news_snapshot", lambda: news)
    turn = rv.RealtimeTurn("synthetic", ring, lambda _: pytest.fail("WAV playback"),
                           connect=lambda *a: pytest.fail("host Realtime socket"),
                           action=action, direct=direct, listen_on_wake=True,
                           conversation_prompt="CONVERSATION_ONLY_SENTINEL")
    assert turn.wake(1)  # No transcript: the model hears the command itself.
    wait_idle(turn)
    [call] = sessions
    assert call["listen"] is True and call["text"] == ""
    assert call["one_shot"] is True and turn.mode == "command"
    assert call["session"]["model"] == rv.TTS_MODEL
    assert call["session"]["audio"]["output"]["voice"] == "coral"
    assert "Белград" in call["session"]["instructions"]
    assert '"temperature_c":9.6' in call["session"]["instructions"]
    assert "Свежий заголовок" in call["session"]["instructions"]
    assert "https://example.com" not in call["session"]["instructions"]
    assert "unused" not in call["session"]["instructions"]
    assert "UNRELATED_WEATHER_OBSERVATION_TIME" not in call["session"]["instructions"]
    assert weather["current"]["time"] == "UNRELATED_WEATHER_OBSERVATION_TIME"
    assert weather["sources"] and news["headlines"][0]["url"]  # Do not mutate cached evidence.
    assert call["session"]["audio"]["input"]["turn_detection"]["silence_duration_ms"] == 200
    assert call["session"]["audio"]["input"]["noise_reduction"] is None
    assert "CONVERSATION_ONLY_SENTINEL" not in call["session"]["instructions"]
    assert [t["name"] for t in call["session"]["tools"]] == [
        "web_search", "set_volume", "start_conversation"]
    assert "перед вызовом коротко скажи" not in call["session"]["instructions"]
    assert "get_time" not in call["session"]["instructions"]
    assert "get_weather" not in call["session"]["instructions"]
    assert "get_news" not in call["session"]["instructions"]
    assert "по-русски" in call["session"]["instructions"]
    assert actions == [{"action": "quasar.volume", "parameters": {"level": 5}}]
    assert outputs[0] and outputs[1] and "компьютер" in call["session"]["instructions"]
    assert ring.calls == [("wake", 1), ("complete", 1, False)] and turn.error is None


@pytest.mark.parametrize("busy", [False, True])
def test_default_weather_refresh_runs_only_in_idle_preparation(
        live_station: tuple[Controller, Path], monkeypatch: Any, busy: bool) -> None:
    controller, _ = live_station
    stop, pipe, calls = threading.Event(), Pipe(), []
    standby = lv.Standby(controller, "mini1", "synthetic", lambda _: None, stop)
    standby.process = pipe
    standby.command_supported = standby.warm_supported = True
    monkeypatch.setattr(sa, "weather", lambda **kw: calls.append(kw) or {"source": "open_meteo"})
    monkeypatch.setattr(sa, "news", lambda **kw: calls.append(kw) or {"source": "bbc_rss"})
    monkeypatch.setattr(standby, "_prepare_speech", stop.set)
    if busy:
        standby.busy.acquire()
    standby._keep_spare(pipe)
    assert calls == ([] if busy else [{"timeout": 5, "refresh": True}] * 2)
    if busy:
        standby.busy.release()


def test_late_tool_result_never_writes_into_a_new_standby_session(
        live_station: tuple[Controller, Path]) -> None:
    import time

    controller, _ = live_station
    standby = lv.Standby(controller, "mini1", "synthetic", lambda _: None, threading.Event())
    pipe = Pipe()
    standby.process = pipe
    started, release = threading.Event(), threading.Event()
    results: list[dict[str, Any]] = []

    def tool(name: str, arguments: dict[str, Any]) -> str:
        started.set()
        assert release.wait(3)
        return "old result"

    def run(cancel: threading.Event, callback: Any = None) -> None:
        results.append(standby._run_session("session", {"model": rv.MODEL}, "",
            time.monotonic() + 5, cancel, lambda _: None, callback))

    standby.minter = lambda *args: "ek_synthetic"
    first_cancel = threading.Event()
    first = threading.Thread(target=run, args=(first_cancel, tool))
    first.start()
    until = time.monotonic() + 2
    while standby.events is None and time.monotonic() < until:
        time.sleep(0.01)
    assert standby.events is not None
    standby.events.put({"event": "tool", "message": {
        "call_id": "call_old", "name": "web_search", "arguments": "{}"}})
    assert started.wait(2)
    first_cancel.set()
    standby.events.put({"event": "session_end", "reason": "stopped",
                        "player": {"outcome": "complete"}})
    first.join(2)
    assert not first.is_alive()
    second = threading.Thread(target=run, args=(threading.Event(),))
    second.start()
    until = time.monotonic() + 2
    while standby.events is None and time.monotonic() < until:
        time.sleep(0.01)
    assert standby.events is not None
    release.set()
    time.sleep(0.1)
    standby.events.put({"event": "session_end", "reason": "idle",
                        "player": {"outcome": "complete"}})
    second.join(2)
    assert not second.is_alive() and len(results) == 2
    assert b"call_old" not in pipe.written


@pytest.mark.parametrize(("command_ms", "conversation_ms"), [(200, 350), (250, 500)])
def test_only_explicit_conversation_tool_switches_the_one_shot_mode(
        command_ms: int, conversation_ms: int, monkeypatch: Any) -> None:
    monkeypatch.setattr(sa, "weather_snapshot", lambda: {
        "city": "Белград", "current": {}, "forecast": []})
    monkeypatch.setattr(sa, "news_snapshot", lambda: {"publisher": "BBC", "headlines": []})

    def direct(session: dict[str, Any], text: str, **options: Any) -> dict[str, Any]:
        assert turn.mode == "command" and options["one_shot"]
        detection = session["audio"]["input"]["turn_detection"]
        assert detection["silence_duration_ms"] == command_ms
        assert session["audio"]["input"]["noise_reduction"] is None
        options["tool"]("start_conversation", {"permission": "allow"})
        assert turn.mode == "command"
        options["tool"]("start_conversation", {})
        assert turn.mode == "conversation"
        assert detection["silence_duration_ms"] == conversation_ms
        assert session["audio"]["input"]["noise_reduction"] == {"type": "far_field"}
        assert "get_time" in [t["name"] for t in session["tools"]]
        assert "get_weather" in [t["name"] for t in session["tools"]]
        assert "get_news" in [t["name"] for t in session["tools"]]
        assert "Режим рации" not in session["instructions"]
        return {"outcome": "complete", "reason": "idle", "player": {}}

    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, direct=direct, listen_on_wake=True,
                           command_vad_silence_ms=command_ms, vad_silence_ms=conversation_ms)
    assert turn.wake(1)
    wait_idle(turn)
    assert turn.error is None and turn.status()["command_backend"] == "station_oneshot"
    assert turn.status()["command_vad_silence_ms"] == command_ms
    assert turn.status()["vad_silence_ms"] == conversation_ms


@pytest.mark.parametrize("value", [True, 199, 1001, "200"])
def test_direct_command_endpointer_rejects_invalid_values(value: Any) -> None:
    with pytest.raises(SafetyError, match="endpointer"):
        rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, command_vad_silence_ms=value)


def test_one_shot_never_falls_back_to_continuous_on_an_old_client(
        live_station: tuple[Controller, Path]) -> None:
    import time

    controller, _ = live_station
    standby = lv.Standby(controller, "mini1", "synthetic", lambda _: None, threading.Event())
    with pytest.raises(SafetyError, match="live_unavailable"):
        standby.session({"model": rv.TTS_MODEL}, "", listen=True, one_shot=True,
                        deadline=time.monotonic() + 5, cancel=threading.Event())


def test_direct_clock_uses_current_host_time_and_configured_endpoint() -> None:
    sessions: list[dict[str, Any]] = []
    answers: list[str] = []

    def direct(session: dict[str, Any], text: str, **options: Any) -> dict[str, Any]:
        sessions.append(session)
        options["on_event"]({"event": "speech_stopped"})
        answers.append(options["tool"]("get_time", {}))
        return {"outcome": "complete", "reason": "idle", "player": {"outcome": "complete"}}

    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, direct=direct,
                           listen_on_wake=True, vad_silence_ms=500,
                           command_vad_silence_ms=250)
    assert turn.wake(1)
    wait_idle(turn)
    assert json.loads(answers[0])["source"] == "host_clock"
    assert sessions[0]["audio"]["input"]["turn_detection"]["silence_duration_ms"] == 250
    assert "get_time" not in [t["name"] for t in sessions[0]["tools"]]
    assert {"get_weather", "get_news"} <= {t["name"] for t in sessions[0]["tools"]}
    assert "command_endpoint" in turn.timing_ms


def test_each_turn_reports_a_sanitized_summary_when_it_ends() -> None:
    reports: list[dict[str, Any]] = []

    def direct(session: dict[str, Any], text: str, **options: Any) -> dict[str, Any]:
        options["on_event"]({"event": "first_audio"})
        return {"outcome": "cloud_failed", "reason": "api_error", "player": {},
                "mic": {"peak": 900, "sent_ms": 4000}}

    turn = rv.RealtimeTurn("synthetic", Ring(), lambda _: {}, direct=direct,
                           listen_on_wake=True, done=reports.append)
    assert turn.wake(1)
    wait_idle(turn)
    [report] = reports
    assert report["mode"] == "command" and report["error"] == "api_error"
    assert "first_audio" in report["timing_ms"] and set(report) == {
        "mode", "error", "timing_ms", "playback", "do_not_retry", "mic"}
    assert report["mic"] == {"peak": 900, "sent_ms": 4000}


def test_speech_openai_text_is_bounded_plain_text_and_needs_the_live_builds() -> None:
    policy = rt.validate_policy({"firmware_sha256": "a" * 64, "voice": {
        "source": "station_live", "asr_directory": ASR, "live_client": "/private/c",
        "live_player": "/private/p"}}, "station-mini")
    assert rt.validate_parameters("speech.openai", {"text": "Привет!\nКак дела?"}, policy,
                                  "station-mini") == {"text": "Привет!\nКак дела?"}
    for bad in ("", "   ", "x" * 1001, "a\u0000b", 5):
        with pytest.raises(ValueError):
            rt.validate_parameters("speech.openai", {"text": bad}, policy, "station-mini")
    with pytest.raises(ValueError):
        rt.validate_parameters("speech.openai", {"text": "привет"},
                               {"firmware_sha256": "a" * 64}, "station-mini")


@pytest.mark.parametrize(("outcome", "expected"), [
    ("complete", "applied"), ("blocked", "blocked"), ("cloud_failed", "unknown"),
    ("unknown", "unknown")])
def test_speech_openai_previews_then_speaks_once_through_the_station_without_the_resident(
        live_station: tuple[Controller, Path], monkeypatch: Any, outcome: str,
        expected: str) -> None:
    from alice_mcp import controls

    controller, _ = live_station
    spoken: list[Any] = []

    def run(controller: Any, station_id: str, key: str, session: dict[str, Any], text: str,
            **options: Any) -> dict[str, Any]:
        spoken.append((station_id, key, session["model"], text, options["listen"]))
        return {"outcome": outcome, "reason": "idle", "barge_ins": 0, "mic": {},
                "player": {"outcome": "complete", "frames_written": 48000}}

    monkeypatch.setattr(lv, "run", run)
    monkeypatch.setattr(rv, "api_key", lambda: "sk-synthetic")
    monkeypatch.setattr(controls, "_check_permission", lambda *a: None)
    monkeypatch.setattr(lv.native_transport, "run_action",
                        lambda *a: pytest.fail("speech.openai must not need the resident"))
    plan = controls.preview(controller, "mini1", "speech.openai", {"text": "Добрый вечер!"})
    assert "OpenAI" in plan["impact"] and not spoken  # A preview never speaks.
    result = controller.execute_plan(controller.take_plan(plan["plan_id"],
                                                          plan["expected_sha256"]))
    assert spoken == [("mini1", "sk-synthetic", rv.TTS_MODEL, "Добрый вечер!", False)]
    assert result["outcome"] == expected and result["do_not_retry"] is True
    assert "sk-synthetic" not in json.dumps(result)


def test_owner_can_bind_speech_say_to_the_openai_voice_explicitly(
        live_station: tuple[Controller, Path], monkeypatch: Any) -> None:
    import asyncio

    from alice_mcp import controls, server

    controller, path = live_station
    station = controller.station("mini1")
    voice = {**station.controls["voice"], "say_backend": "openai"}
    policy = rt.validate_policy({"firmware_sha256": "a" * 64, "voice": voice}, "station-mini")
    bound = Station(**{**station.__dict__, "controls": policy})
    monkeypatch.setattr(controller, "station", lambda _: bound)
    monkeypatch.setattr(server, "controller", controller)
    monkeypatch.setattr(controls, "_check_permission", lambda *a: None)
    monkeypatch.setattr(rv, "api_key", lambda: "sk-synthetic")
    monkeypatch.setattr(lv.native_transport, "run_action",
                        lambda *a: pytest.fail("speech.say went to the stopped resident"))
    spoken: list[str] = []
    monkeypatch.setattr(lv, "run", lambda controller, station_id, key, session, text, **o: (
        spoken.append(text) or {"outcome": "complete", "reason": "idle", "barge_ins": 0,
                                "mic": {}, "player": {"outcome": "complete"}}))
    result = asyncio.run(server.run_station_action("mini1", "speech.say", {"text": "Привет"},
                                                   object()))
    assert spoken == ["Привет"] and result["outcome"] == "applied"
    with pytest.raises(ValueError):  # Only with the live builds, only the value "openai".
        rt.validate_policy({"firmware_sha256": "a" * 64, "voice": {
            **voice, "say_backend": "yandex"}}, "station-mini")
    with pytest.raises(ValueError):
        rt.validate_policy({"firmware_sha256": "a" * 64, "voice": {
            "source": "PC", "client": "/p/c", "mww": "/p/m", "libvosk": "/p/l",
            "vosk_zip": "/p/z", "say_backend": "openai"}}, "station-mini")


def test_running_standby_speaks_text_without_a_new_ssh_launch_and_one_session_at_a_time(
        live_station: tuple[Controller, Path], monkeypatch: Any) -> None:
    from alice_mcp import controls

    controller, _ = live_station
    pipe, launches = Pipe(), []
    stop = threading.Event()
    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, stop,
                         popen=lambda *a, **k: launches.append(a) or pipe,
                         minter=lambda *a: "ek_secret")
    runner = threading.Thread(target=standby.run)
    runner.start()
    pipe.say({"event": "detector", "line": READY})
    import time
    while lv.ACTIVE.get("mini1") is not standby:
        time.sleep(0.01)

    def station_side() -> None:
        while b"say " not in pipe.written:
            time.sleep(0.01)
        pipe.say({"event": "connected", "ms": 400})
        pipe.say({"event": "session_end", "reason": "idle", "barge_ins": 0,
                  "player": {"outcome": "complete", "cleanup_verified": True}})

    threading.Thread(target=station_side).start()
    monkeypatch.setattr(rv, "api_key", lambda: "sk-synthetic")
    monkeypatch.setattr(controls, "_check_permission", lambda *a: None)
    monkeypatch.setattr(lv, "run", lambda *a, **k: pytest.fail("launched a second SSH session"))
    plan = controls.preview(controller, "mini1", "speech.openai", {"text": "Привет,\nмир"})
    result = controller.execute_plan(controller.take_plan(plan["plan_id"],
                                                          plan["expected_sha256"]))
    assert result["outcome"] == "applied" and len(launches) == 1
    assert result["speech_runtime"]["transport"] == "standby"
    assert result["speech_runtime"]["timing_ms"]["connected"] >= 0
    assert "ek_secret" not in json.dumps(result)
    line = bytes(pipe.written).split(b"say ", 1)[1].split(b"\n", 1)[0].decode()
    model, seconds, secret, text = line.split(" ", 3)
    assert (model, secret, text) == (rv.TTS_MODEL, "ek_secret", "Привет, мир")
    standby.busy.acquire()  # A wake session is running: speech must not overlap it.
    with pytest.raises(SafetyError, match="live_busy"):
        standby.say("ещё", deadline=time.monotonic() + 30, cancel=threading.Event())
    standby.busy.release()
    stop.set()
    runner.join(5)
    assert "mini1" not in lv.ACTIVE


def test_voice_action_respects_the_same_speech_backend_binding_as_mcp(
        live_station: tuple[Controller, Path], monkeypatch: Any) -> None:
    from alice_mcp import controls, voice_service

    controller, _ = live_station
    station = controller.station("mini1")
    bound = Station(**{**station.__dict__, "controls": {**station.controls, "voice": {
        **station.controls["voice"], "say_backend": "openai"}}})
    monkeypatch.setattr(controller, "station", lambda _: bound)
    monkeypatch.setattr(controls, "_check_permission", lambda *a: None)
    monkeypatch.setattr(rv, "api_key", lambda: "sk-synthetic")
    monkeypatch.setattr(lv.native_transport, "run_action",
                        lambda *a: pytest.fail("ignored private speech binding"))
    spoken: list[str] = []
    monkeypatch.setattr(lv, "run", lambda c, sid, key, session, text, **kw: (
        spoken.append(text) or {"outcome": "complete", "reason": "idle", "player": {}}))
    service = voice_service.VoiceService(controller)
    service.station = "mini1"
    result = service._action({"action": "speech.say", "parameters": {"text": "Привет"}})
    assert result["outcome"] == "applied" and spoken == ["Привет"]


def test_live_timings_allowlist_events_without_echoing_station_data(
        live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station
    process = FakeProcess([
        b'{"event":"connected","ms":3,"extra":"PRIVATE_SENTINEL"}\n',
        b'{"event":"first_audio","text":"PRIVATE_SENTINEL"}\n', END])
    result, _, _ = _live(controller, process, listen=False)
    runtime = result["speech_runtime"]
    assert runtime["transport"] == "cold" and runtime["secret_source"] == "minted"
    assert 0 <= runtime["timing_ms"]["connected"] <= runtime["timing_ms"]["first_audio"]
    assert runtime["timing_ms"]["total"] >= runtime["timing_ms"]["first_audio"]
    assert "PRIVATE_SENTINEL" not in json.dumps(result)


def test_a_stopped_resident_is_not_waited_for(live_station: tuple[Controller, Path],
                                              monkeypatch: Any) -> None:
    controller, _ = live_station
    calls: list[int] = []

    def state(*args: Any) -> str:
        calls.append(1)
        raise SafetyError("Native preview state unavailable; no action dispatched")

    monkeypatch.setattr(lv.native_transport, "_state", state)
    monkeypatch.setattr(lv.time, "sleep", lambda _: pytest.fail("waited for a stopped resident"))
    assert lv._stamp(controller, controller.station("mini1")) == "-" and calls == [1]


def test_a_spare_speech_secret_is_minted_ahead_and_used_once(
        live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station
    pipe = Pipe()
    stop = threading.Event()
    minted: list[int] = []

    def minter(key: str, session: dict[str, Any], timeout: float, ttl: int = 60) -> str:
        minted.append(ttl)
        return f"ek_{len(minted)}"

    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, stop,
                         popen=lambda *a, **k: pipe, minter=minter)
    runner = threading.Thread(target=standby.run)
    runner.start()
    pipe.say({"event": "detector", "line": READY})
    import time
    until = time.monotonic() + 3
    while not minted and time.monotonic() < until:
        time.sleep(0.01)
    assert minted == [600]  # Ready before anyone asks to speak.

    def station_side() -> None:
        while b"say " not in pipe.written:
            time.sleep(0.01)
        pipe.say({"event": "session_end", "reason": "idle", "barge_ins": 0,
                  "player": {"outcome": "complete"}})

    threading.Thread(target=station_side).start()
    standby.say("привет", deadline=time.monotonic() + 30, cancel=threading.Event())
    assert b" ek_1 " in pipe.written  # The spare, not a fresh mint on the critical path.
    until = time.monotonic() + 3
    while len(minted) < 2 and time.monotonic() < until:
        time.sleep(0.01)
    assert minted == [600, 600]  # Consumed once, replaced in the background.
    stop.set()
    runner.join(5)


@pytest.mark.parametrize("outcome", ["idle", "failed", "not_prepared"])
@pytest.mark.parametrize("verb", ["say", "ask"])
def test_preconnected_speech_consumes_connection_without_mint_or_reconnect(
        live_station: tuple[Controller, Path], monkeypatch: Any, outcome: str, verb: str) -> None:
    import time

    controller, _ = live_station
    monkeypatch.setattr(lv, "ONESHOT_CLIENT_SHA256", lv.CLIENT_SHA256)
    pipe, stop, minted = Pipe(), threading.Event(), []

    def minter(*args: Any) -> str:
        minted.append(1)
        return "ek_synthetic"

    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, stop,
                         popen=lambda *a, **k: pipe, minter=minter)
    runner = threading.Thread(target=standby.run)
    runner.start()
    try:
        pipe.say({"event": "detector", "line": READY})
        until = time.monotonic() + 3
        while b"prepare " not in pipe.written and time.monotonic() < until:
            time.sleep(0.01)
        assert b"prepare gpt-realtime-2.1-mini 120 ek_synthetic\n" in pipe.written
        assert b"say " not in pipe.written and b"speak " not in pipe.written
        pipe.say({"event": "prepared"})
        until = time.monotonic() + 3
        while getattr(standby, "warm_until", 0) == 0 and time.monotonic() < until:
            time.sleep(0.01)
        assert standby.warm_until > time.monotonic()

        def station_side() -> None:
            until = time.monotonic() + 3
            command = b"askwarm " if verb == "ask" else b"speak "
            while command not in pipe.written and time.monotonic() < until:
                time.sleep(0.01)
            pipe.say({"event": "first_audio"})
            pipe.say({"event": "session_end", "reason": outcome,
                      "player": {"outcome": "complete"}})
            stop.set()  # Do not prepare a second connection in this test.

        threading.Thread(target=station_side).start()
        result = (standby.session({"model": rv.TTS_MODEL}, "", listen=True, one_shot=True,
                                  deadline=time.monotonic() + 30, cancel=threading.Event())
                  if verb == "ask" else
                  standby.say("Привет", deadline=time.monotonic() + 30, cancel=threading.Event()))
        assert result["outcome"] == {"idle": "complete", "failed": "unknown",
                                     "not_prepared": "blocked"}[outcome]
        assert result["speech_runtime"]["secret_source"] == "preconnected"
        assert result["speech_runtime"]["transport"] == "standby"
        assert bytes(pipe.written).count(b"askwarm " if verb == "ask" else b"speak ") == 1
        assert b"say " not in pipe.written and len(minted) == 1
        assert standby.warm_until == 0
    finally:
        stop.set()
        runner.join(5)


def test_unreviewed_client_cannot_enable_preconnection(tmp_path: Path) -> None:
    path = tmp_path / "client"
    path.write_bytes(b"unreviewed client")
    with pytest.raises(SafetyError):
        lv.load_client(path)


def test_standby_interrupt_targets_only_its_current_speech(
        live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station
    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, threading.Event())
    standby.interrupt()  # Idle/preparing: no audio to cancel, never write cancel blindly.
    cancel = threading.Event()
    standby.session_cancel = cancel
    standby.interrupt()
    assert cancel.is_set()


def test_preparation_rechecks_permission_and_cannot_cancel_active_speech(
        live_station: tuple[Controller, Path], monkeypatch: Any) -> None:
    import time

    from alice_mcp import controls

    controller, _ = live_station
    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, threading.Event(),
                         minter=lambda *a: pytest.fail("minted under revoked policy"))
    standby.launched_station = controller.station("mini1")
    standby.process = Pipe()
    monkeypatch.setattr(controls, "_check_permission",
                        lambda *a: (_ for _ in ()).throw(SafetyError("revoked")))
    standby._prepare_speech()
    assert not standby.process.written and standby.warm_retry_at > time.monotonic()
    standby.warm_retry_at = 0
    standby.preparing_at = time.monotonic() - 20
    standby.busy.acquire()
    standby._prepare_speech()
    assert not standby.process.written
    standby.busy.release()
    standby._prepare_speech()
    assert standby.process.written == b"cancel\n" and standby.preparing_at is None


def test_normal_preconnection_expiry_renews_without_a_failure_backoff(
        live_station: tuple[Controller, Path], monkeypatch: Any) -> None:
    import time

    controller, _ = live_station
    monkeypatch.setattr(lv, "WARM_CLIENT_SHA256", lv.CLIENT_SHA256)
    pipe, stop = Pipe(), threading.Event()
    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, stop,
                         popen=lambda *a, **k: pipe, minter=lambda *a: "ek_synthetic")
    runner = threading.Thread(target=standby.run)
    runner.start()
    try:
        until = time.monotonic() + 3
        while b"prepare " not in pipe.written and time.monotonic() < until:
            time.sleep(0.01)
        assert b"prepare " in pipe.written
        pipe.say({"event": "prepared"})
        pipe.say({"event": "prepare_end", "reason": "deadline"})
        until = time.monotonic() + 3
        while bytes(pipe.written).count(b"prepare ") < 2 and time.monotonic() < until:
            time.sleep(0.01)
        assert bytes(pipe.written).count(b"prepare ") == 2
        assert b"speak " not in pipe.written and b"say " not in pipe.written
    finally:
        stop.set()
        runner.join(5)


def test_station_supplied_end_fields_are_allowlisted_and_type_checked() -> None:
    end = {"event": "end", "reason": "/private/path token=SECRET", "barge_ins": "PRIVATE",
           "player": {"outcome": "PRIVATE", "frames_written": "PRIVATE", "flushes": -1,
                      "underruns_recovered": True, "cleanup_verified": "yes", "extra": 1},
           "mic": {"peak": "PRIVATE"}}
    result = lv._outcome(end, threading.Event())
    assert result == {"outcome": "unknown", "reason": "unknown", "barge_ins": 0, "mic": {},
                      "player": {}}
    end = {"reason": "idle", "barge_ins": 2, "player": {
        "outcome": "complete", "frames_written": 9600, "flushes": 1, "underruns_recovered": 0,
        "cleanup_verified": True, "stage": "complete"}}
    assert lv._outcome(end, threading.Event()) == {
        "outcome": "complete", "reason": "idle", "barge_ins": 2, "mic": {}, "player": {
            "outcome": "complete", "frames_written": 9600, "flushes": 1,
            "underruns_recovered": 0, "cleanup_verified": True}}
    for reason in lv.BLOCKED | lv.CLOUD | {"stopped", "deadline", "failed", "player_failed",
                                          "output_limit", "invalid_control", "wake_failed"}:
        assert lv._outcome({"reason": reason}, threading.Event())["reason"] == reason
    assert lv._outcome({"reason": ["idle"], "player": {"outcome": ["complete"]}},
                       threading.Event())["reason"] == "unknown"


def test_standby_listener_reports_only_fixed_reasons(
        live_station: tuple[Controller, Path]) -> None:
    controller, _ = live_station
    pipe = Pipe()
    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, threading.Event(),
                         popen=lambda *a, **k: pipe, minter=lambda *a: "ek_secret")
    pipe.say({"event": "end", "reason": "/private/path token=SECRET"})
    assert standby.run() == {"outcome": "unknown", "reason": "unknown"}


def _speak_only(controller: Controller, monkeypatch: Any, **changes: Any) -> None:
    import dataclasses

    station = dataclasses.replace(controller.station("mini1"), **changes)
    monkeypatch.setattr(controller, "station", lambda _: station)
    monkeypatch.setattr(lv.iop, "_permitted", REAL_PERMITTED)


def test_speak_only_session_needs_only_the_previewed_speech_permission(
        live_station: tuple[Controller, Path], monkeypatch: Any) -> None:
    # The owner approves the form; no standing preauthorization or speech.listen needed.
    controller, _ = live_station
    policy = {**controller.station("mini1").controls, "allowed_actions": ["speech.openai"]}
    _speak_only(controller, monkeypatch, preauthorize_all_actions=False, controls=policy)
    assert _live(controller, FakeProcess([END]), listen=False)[0]["outcome"] == "complete"
    with pytest.raises(SafetyError):  # The microphone keeps the stricter check.
        _live(controller, FakeProcess([END]), listen=True)
    _speak_only(controller, monkeypatch, controls={**policy, "allowed_actions": ["speech.say"]})
    with pytest.raises(SafetyError):
        _live(controller, FakeProcess([END]), listen=False)


class Stuck(FakeProcess):
    """A live SSH transport whose remote never answers; only a kill ends the read."""

    def __init__(self) -> None:
        super().__init__([])
        self.killed = threading.Event()

    def readline(self, limit: int = -1) -> bytes:
        self.killed.wait(5)
        return super().readline(limit)

    def kill(self) -> None:
        self.killed.set()


def test_previewed_speech_applies_without_preauthorization_and_a_stuck_remote_is_unknown(
        live_station: tuple[Controller, Path], monkeypatch: Any) -> None:
    import functools
    import time

    from alice_mcp import controls

    controller, _ = live_station
    _speak_only(controller, monkeypatch, preauthorize_all_actions=False)
    monkeypatch.setattr(rv, "api_key", lambda: "sk-key")
    processes: list[FakeProcess] = [FakeProcess([END]), Stuck()]
    monkeypatch.setattr(lv, "run", functools.partial(
        lv.run, popen=lambda *a, **k: processes.pop(0), minter=lambda *a: "ek_secret"))

    def apply() -> dict[str, Any]:
        plan = controls.preview(controller, "mini1", "speech.openai", {"text": "Привет"})
        assert plan["approval_required"] is True
        return controller.execute_plan(controller.take_plan(plan["plan_id"],
                                                            plan["expected_sha256"]))

    assert apply()["outcome"] == "applied"
    # speech.openai sessions are bound to 179 s; shrink the host margin instead of waiting.
    monkeypatch.setattr(lv, "KILL_MARGIN", -178.5, raising=False)
    started = time.monotonic()
    result = apply()
    assert time.monotonic() - started < 4 and not processes
    assert result["outcome"] == "unknown" and result["do_not_retry"] is True
    assert result["reason"] == "unknown"


def test_standby_transport_is_killed_after_its_own_bound(
        live_station: tuple[Controller, Path], monkeypatch: Any) -> None:
    controller, _ = live_station
    pipe = Pipe()
    pipe.kill = lambda: pipe.lines.put(b"")  # type: ignore[method-assign]
    monkeypatch.setattr(lv, "STANDBY_SECONDS", 0, raising=False)
    monkeypatch.setattr(lv, "KILL_MARGIN", 0.2, raising=False)
    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, threading.Event(),
                         popen=lambda *a, **k: pipe, minter=lambda *a: "ek_secret")
    assert standby.run() == {"outcome": "unknown", "reason": "unknown"}


def test_standby_session_stops_waiting_at_its_deadline_and_is_unknown(
        live_station: tuple[Controller, Path], monkeypatch: Any) -> None:
    import time

    controller, _ = live_station
    pipe = Pipe()
    killed = threading.Event()
    pipe.kill = lambda: (killed.set(), pipe.lines.put(b""))  # type: ignore[method-assign]
    monkeypatch.setattr(lv, "KILL_MARGIN", 0, raising=False)
    standby = lv.Standby(controller, "mini1", "sk-key", lambda e: None, threading.Event(),
                         popen=lambda *a, **k: pipe, minter=lambda *a: "ek_secret")
    runner = threading.Thread(target=standby.run)
    runner.start()
    while standby.process is None:
        time.sleep(0.01)
    # Without a host deadline this late end would be awaited and reported as complete.
    late = threading.Timer(4, pipe.say, [{"event": "session_end", "reason": "idle",
                                          "player": {"outcome": "complete"}}])
    late.start()
    started = time.monotonic()
    result = standby.say("Привет", deadline=started + 1.5, cancel=threading.Event())
    late.cancel()
    assert time.monotonic() - started < 3
    assert result["outcome"] == "unknown" and result["reason"] == "unknown"
    # The stuck transport is ended, so no late end can reach the next session.
    assert killed.is_set()
    runner.join(5)
    assert not runner.is_alive() and standby.process is None
