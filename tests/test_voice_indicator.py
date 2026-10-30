"""Mini 1 voice indicator: fake led_util, fake SSH, fake resident and inventory; no device."""

import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import pytest
from test_capture_busybox import (  # noqa: F401  (fixtures)
    pulse_device,
    replay_device,
    request,
    run_device,
    shell_device,
)

from alice_mcp import controls, native_transport, wake_stream
from alice_mcp import device_runtime as rt
from alice_mcp import io_channel_pilot as iop
from alice_mcp import voice_indicator as vi
from alice_mcp.core import Controller, SafetyError, Station

CAPTURE = "a" * 32
FIRMWARE = hashlib.sha256(b"synthetic").hexdigest()
# Shaped like the stock alice_listening.led: header words, then 6 zones + milliseconds.
LISTENING = ("gammacorrection\nloop\n"
             "6b0fff 6b0fff 6b0fff 6b0fff 6b0fff 6b0fff 40\n"
             "35087f 35087f 35087f 35087f 35087f 35087f 40\n"
             "000000 000000 000000 000000 000000 000000 80\n")


def native_station(root: Path, *actions: str, **voice: Any) -> Station:
    key, hosts = root / "station-key", root / "known-hosts"
    key.write_text("synthetic key\n")
    key.chmod(0o600)
    hosts.write_text("synthetic host key\n")
    policy: dict[str, Any] = {"firmware_sha256": FIRMWARE, "allowed_actions": list(actions)}
    if voice:
        policy["voice"] = {k: str(root / k) for k in ("client", "mww", "libvosk", "vosk_zip")}
        policy["voice"].update(voice)
    return Station("mini1", "station-mini", "h", 22, "root", key, hosts, True, "", "c" * 64,
                   "t" * 64, rt.validate_policy(policy, "station-mini"),
                   preauthorize_all_actions=True, native_mcp={"url": "https://h/mcp"})


def ssh_only(station: Station) -> Station:
    return Station(**{**station.__dict__, "native_mcp": {}})


# Recoloured copy of Alice's listening pulse.

def test_recolour_keeps_frames_timing_and_intensity() -> None:
    red = vi.recolor(LISTENING, "red").splitlines()
    assert red[:2] == ["gammacorrection", "loop"]
    assert red[2] == " ".join(["ff0000"] * 6 + ["40"])
    assert red[3] == " ".join(["7f0000"] * 6 + ["40"])  # max(35,08,7f) = 127/255.
    assert red[4] == " ".join(["000000"] * 6 + ["80"])
    assert vi.recolor(LISTENING, "#00ff80").splitlines()[3].startswith("007f40 ")
    assert vi.recolor(LISTENING, "purple") == LISTENING  # The stock file, unchanged.
    for bad in [LISTENING + "rm -rf /\n", LISTENING.replace(" 40\n", "\n", 1),
                LISTENING.replace("6b0fff", "6b0fffff", 1), "loop\n", "",
                LISTENING.replace("loop", "ALICE_LED_PATTERN"), LISTENING * 2000]:
        with pytest.raises(ValueError):
            vi.recolor(bad, "red")
    with pytest.raises(ValueError):
        vi.recolor(LISTENING, "#12345")


def test_indicator_policy_defaults_off_and_is_bounded(tmp_path: Path) -> None:
    def policy(**indicator: Any) -> dict[str, Any]:
        voice = {k: str(tmp_path / k) for k in ("client", "mww", "libvosk", "vosk_zip")}
        return {"firmware_sha256": "a" * 64, "voice": {**voice, "indicator": indicator}}

    result = rt.validate_policy(policy(), "station-mini")["voice"]["indicator"]
    assert result == {"backend": "off", "max_seconds": 90, "color": "red"}
    sound = {"path": "/PRIVATE/stock/vins_error.wav", "sha256": "b" * 64}
    full = {"backend": "ssh_led", "max_seconds": 60, "color": "#FF8000", "error_sound": sound}
    assert rt.validate_policy(policy(**full), "station-mini")["voice"]["indicator"] == full
    legacy = dict(full, palette_index=4, capture_id=CAPTURE)  # Earlier fields: unused.
    assert rt.validate_policy(policy(**legacy), "station-mini")["voice"]["indicator"] == legacy
    for bad in [{"backend": "led"}, {"max_seconds": 91}, {"max_seconds": True},
                {"color": "crimson"}, {"color": "#12345g"}, {"error_sound": {"path": "x"}},
                {"error_sound": {"path": "relative.wav", "sha256": "b" * 64}},
                {"palette_index": 7}, {"capture_id": "x"}, {"colour": "red"}]:
        with pytest.raises(ValueError):
            rt.validate_policy(policy(**bad), "station-mini")


# The station helper with a fake led_util (a maind symlink on the device).

@pytest.fixture
def station(tmp_path: Path, monkeypatch: Any) -> Any:
    if not shutil.which("dash"):
        pytest.skip("Synthetic shell checks need dash")
    root = tmp_path / "dev"
    quasar = root / "system/vendor/quasar"
    (quasar / "ledpatterns").mkdir(parents=True)
    (quasar / "maind").write_bytes(b"synthetic")
    (quasar / "ledpatterns/alice_listening.led").write_text(LISTENING)
    log = tmp_path / "led-util.log"
    (quasar / "led_util").write_text(f"""#!{sys.executable}
import os, signal, sys, time
def note(text):
    with open({str(log)!r}, "a") as out:
        out.write(text + "\\n")
def stop(number, frame):
    note(signal.Signals(number).name + " " + os.path.basename(sys.argv[1]))
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
note("start " + os.path.basename(sys.argv[1]) + " " + open(sys.argv[1]).read().splitlines()[-1])
while True:
    time.sleep(0.02)
""")
    (quasar / "led_util").chmod(0o700)
    (root / "tmp").mkdir()
    (root / "mounts").write_text(f"tmpfs {root}/tmp tmpfs rw 0 0\n")
    shims = tmp_path / "shims"
    shims.mkdir()
    # Match BusyBox pidof without spawning cat for every process on a busy CI host.
    (shims / "pidof").write_text(f"""#!{sys.executable}
import os, sys
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        with open("/proc/" + pid + "/comm") as comm:
            if comm.read().strip() == sys.argv[1]:
                print(pid, end=" ")
    except OSError:
        pass
print()
""")
    (shims / "pidof").chmod(0o700)
    env = {**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"}
    script = (vi.SCRIPT.replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
              .replace("/system/", f"{root}/system/").replace("/proc/mounts", f"{root}/mounts")
              .replace("for t in /tmp /run /dev/shm /var/run", f"for t in {root}/tmp"))
    monkeypatch.setattr(vi, "SCRIPT", script)

    def runner(target: Station, command: Any) -> str:
        assert target.native_mcp == {} and command.command.startswith("sh -s -- ")
        return subprocess.run(command.command.replace("sh -s", "dash -s", 1), shell=True,
                              input=command.data, env=env, capture_output=True, timeout=30,
                              check=True).stdout.decode()

    def spawn(target: Station, command: Any) -> Any:
        assert target.native_mcp == {}
        return subprocess.Popen(command.command.replace("sh -s", "dash -s", 1), shell=True,
                                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)

    yield {"root": root, "log": log, "runner": runner, "spawn": spawn, "env": env}
    subprocess.run(["pkill", "-f", str(root)], check=False)


def players(device: dict[str, Any]) -> list[str]:
    out = subprocess.run(["pgrep", "-f", str(device["root"] / "tmp")], capture_output=True,
                         text=True, check=False).stdout
    return out.split()


def lines(device: dict[str, Any]) -> list[str]:
    log: Path = device["log"]
    return log.read_text().splitlines() if log.exists() else []


def wait(predicate: Any, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def test_helper_reads_plays_until_released_then_darkens_and_cleans(station: Any) -> None:
    ssh = ssh_only(native_station(station["root"], "led.show"))
    assert vi.read_pattern(station["runner"], ssh) == LISTENING
    pattern = vi.recolor(LISTENING, "red")
    command = vi.play_command(ssh, pattern, 90)
    hold = vi.Hold(station["spawn"](ssh, command), command.data)
    wait(lambda: lines(station))
    time.sleep(0.3)
    assert hold.alive() and lines(station) == ["start indicator.led " + "000000 " * 6 + "80"]
    played = station["root"] / "tmp/alice-mcp-indicator/indicator.led"
    assert played.read_text() == pattern
    assert hold.release() == {"outcome": "applied", "left_running": False}
    assert lines(station) == ["start indicator.led " + "000000 " * 6 + "80",
                              "SIGINT indicator.led",
                              "start off.led " + "000000 " * 6 + "100", "SIGINT off.led"]
    assert not (station["root"] / "tmp/alice-mcp-indicator").exists() and not players(station)


def test_on_device_deadline_and_dropped_session_never_leave_a_player(station: Any) -> None:
    ssh = ssh_only(native_station(station["root"], "led.show"))
    command = vi.play_command(ssh, vi.recolor(LISTENING, "red"), 1)
    hold = vi.Hold(station["spawn"](ssh, command), command.data)
    started = time.monotonic()
    wait(lambda: not hold.alive())  # stdin still open: the 1 s deadline ends it.
    assert time.monotonic() - started < 6
    assert hold.release()["outcome"] == "applied" and not players(station)
    stale = station["root"] / "tmp/alice-mcp-indicator"  # A helper killed outright.
    stale.mkdir(mode=0o700)
    (stale / "indicator.led").write_text(LISTENING)
    subprocess.Popen([str(station["root"] / "system/vendor/quasar/led_util"),
                      str(stale / "indicator.led")], env=station["env"], start_new_session=True)
    wait(lambda: players(station))
    assert vi.cleanup(station["runner"], ssh) == {"outcome": "cleanup", "left_running": False}
    assert not players(station) and lines(station)[-1] == "SIGINT off.led"


def test_a_foreign_player_blocks_without_writing(station: Any) -> None:
    ssh = ssh_only(native_station(station["root"], "led.show"))
    other = station["root"] / "other.led"
    other.write_text("000000 " * 6 + "100\n")
    foreign = subprocess.Popen([str(station["root"] / "system/vendor/quasar/led_util"),
                                str(other)], env=station["env"])
    try:
        wait(lambda: lines(station))
        command = vi.play_command(ssh, vi.recolor(LISTENING, "red"), 90)
        result = vi.Hold(station["spawn"](ssh, command), command.data).release()
        assert result == {"outcome": "blocked", "error_code": "player_busy"}
        assert lines(station) == ["start other.led " + "000000 " * 6 + "100"]
    finally:
        foreign.kill()
        foreign.wait()


def test_quasar_identity_ignores_led_util_players(tmp_path: Path) -> None:
    """led_util is a maind symlink, so BusyBox `pidof maind` also lists the player."""
    if not shutil.which("dash"):
        pytest.skip("Synthetic shell checks need dash")
    for pid, name in (("123", "maind"), ("456", "led_util")):
        stat = tmp_path / "proc" / pid / "stat"
        stat.parent.mkdir(parents=True)
        stat.write_text(" ".join([pid, f"({name})", "S", *map(str, range(1, 19)), "777\n"]))
    shims = tmp_path / "shims"
    shims.mkdir()
    for source in (wake_stream.SCRIPT, iop.GUARDS):
        start = source.index("identity() {")
        function = source[start:source.index("\n}\n", start) + 3].replace(
            "/proc/", f"{tmp_path}/proc/")
        digests = []
        for pids in ("123", "123 456"):
            (shims / "pidof").write_text(f"#!/bin/sh\necho {pids}\n")
            (shims / "pidof").chmod(0o700)
            digests.append(subprocess.run(
                ["dash", "-c", function + "identity"], capture_output=True, text=True,
                env={**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"}, check=True).stdout)
        assert digests[0] == digests[1] and len(digests[0].strip()) == 64


# The indicator: lit while the «Голос» session is busy, then dark; error phrase on failure.

def agent(path: Path, status: str, last_event_id: int, age_s: float = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_status": status, "last_event_id": last_event_id,
                                "ts": int((time.time() - age_s) * 1000)}))


def canonical(samples: list[int], rate: int = 22050, extra: bytes = b"") -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    data = buffer.getvalue()
    if extra:  # A converter's LIST chunk before the samples.
        chunk = b"LIST" + struct.pack("<I", len(extra)) + extra
        data = (data[:4] + struct.pack("<I", len(data) - 8 + len(chunk)) + data[8:36] + chunk
                + data[36:])
    return data


LOUD = [20000, -20000, 100] * 100


def indicator(station: Any, monkeypatch: Any, tmp_path: Path, **config: Any) -> Any:
    sound = tmp_path / "vins_error.wav"
    sound.write_bytes(canonical(LOUD, extra=b"INFOISFT\x04\x00\x00\x00x\x00\x00\x00"))
    voice = {"backend": "ssh_led", "max_seconds": 90,
             "error_sound": {"path": str(sound),
                             "sha256": hashlib.sha256(sound.read_bytes()).hexdigest()}}
    voice.update(config)
    mini = native_station(station["root"], "led.show", "speech.say", indicator=voice)
    controller = Controller(None, writes=True, runner=station["runner"])
    monkeypatch.setattr(controller, "station", lambda _: mini)
    played: list[Any] = []

    def play(target: Any, station_arg: Station, audio: bytes) -> dict[str, Any]:
        played.append((lines(station)[-1] if lines(station) else None, audio))
        return {"outcome": "applied"}

    state = tmp_path / "agent.json"
    item = vi.Indicator(controller, "mini1", spawn=station["spawn"], state_path=state,
                        play=play)
    item.pickup_s = 3.0
    return item, state, played


def test_lit_while_busy_then_dark_when_the_agent_answers(
        station: Any, monkeypatch: Any, tmp_path: Path) -> None:
    item, state, played = indicator(station, monkeypatch, tmp_path)
    assert item.status()["state"] == "idle"
    agent(state, "idle", 1)
    item.wake(5)
    wait(lambda: item.status()["state"] == "lit")
    agent(state, "busy", 5)
    time.sleep(3.5)  # Past the pickup window: busy keeps it lit.
    item.wake(6)  # A new command while lit extends; never a second player.
    agent(state, "busy", 6)
    assert item.status()["state"] == "lit" and len(players(station)) == 1
    agent(state, "idle", 6)
    wait(lambda: item.status()["state"] == "idle")
    assert [x.split()[0] for x in lines(station)] == ["start", "SIGINT", "start", "SIGINT"]
    assert not players(station) and not played
    item.close()
    assert item.status()["clean"] is True


def test_managed_wake_stays_lit_without_agent_until_playback_completes(
        station: Any, monkeypatch: Any, tmp_path: Path) -> None:
    item, state, played = indicator(station, monkeypatch, tmp_path)
    item.pickup_s = 0.1
    item.wake(42, managed=True)
    wait(lambda: item.status()["state"] == "lit")
    agent(state, "idle", 42)
    time.sleep(0.3)
    assert item.status()["state"] == "lit"
    item.complete(41)  # An earlier turn cannot turn this one off.
    assert item._verdict() == "busy"
    item.complete(42)
    wait(lambda: item.status()["state"] == "idle")
    assert not played
    item.close()


def test_wake_refreshes_the_limit_before_the_cloud_deadline_is_bound(
        station: Any, monkeypatch: Any, tmp_path: Path) -> None:
    item, _, _ = indicator(station, monkeypatch, tmp_path)
    config = item._config()
    monkeypatch.setattr(item, "_config", lambda: {**config, "max_seconds": 5})
    item.wake(43, managed=True)
    assert item.max_s == 5
    item.complete(43)
    item.close()


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_error_or_timeout_darkens_first_then_plays_the_stock_phrase_once(
        station: Any, monkeypatch: Any, tmp_path: Path, failure: str) -> None:
    item, state, played = indicator(station, monkeypatch, tmp_path, max_seconds=4)
    item.wake(7)
    agent(state, "busy", 7)
    wait(lambda: item.status()["state"] == "lit")
    if failure == "error":
        agent(state, "error", 7)
    wait(lambda: played, 12)
    time.sleep(0.5)
    assert len(played) == 1 and played[0][0] == "SIGINT off.led"  # Dark before speaking.
    audio = played[0][1]
    assert audio[:4] == b"RIFF" and audio[36:40] == b"data" and len(audio) == 44 + 600
    assert max(abs(s) for (s,) in struct.iter_unpack("<h", audio[44:])) <= 8191
    assert item.status()["error_sound"] == "applied" and not players(station)
    item.close()


def test_error_sound_is_skipped_on_a_hash_mismatch(
        station: Any, monkeypatch: Any, tmp_path: Path) -> None:
    item, state, played = indicator(station, monkeypatch, tmp_path)
    (tmp_path / "vins_error.wav").write_bytes(canonical([1, 2, 3]))
    item.wake(8)
    agent(state, "error", 8)
    wait(lambda: item.status()["error_sound"] is not None)
    assert not played and item.status()["error_sound"] == "skipped_sound_unavailable"
    item.close()


def test_off_backend_does_nothing_and_unknown_is_never_retried(
        station: Any, monkeypatch: Any, tmp_path: Path) -> None:
    item, state, played = indicator(station, monkeypatch, tmp_path, backend="off")
    item.wake(1)
    agent(state, "error", 1)
    time.sleep(0.5)
    assert item.status()["state"] == "off" and not lines(station) and not played
    item.close()
    item, state, _ = indicator(station, monkeypatch, tmp_path)
    monkeypatch.setattr(vi.Hold, "release", lambda self, timeout=20: {"outcome": "unknown"})
    item.wake(2)
    agent(state, "busy", 2)
    wait(lambda: item.status()["state"] == "lit" and lines(station))
    agent(state, "idle", 2)
    wait(lambda: item.status()["state"] == "unavailable")
    assert item.status()["reason"] == "unknown_inspect_locally_do_not_retry"
    starts = len([x for x in lines(station) if x.startswith("start")])
    item.wake(3)
    agent(state, "busy", 3)
    time.sleep(1)
    assert len([x for x in lines(station) if x.startswith("start")]) == starts  # Latched.
    item.close()  # The stop still cleans up and verifies no player remains.
    assert not players(station) and item.status()["clean"] is True


def test_play_pcm_sends_the_pinned_canonical_wav_through_the_resident(
        tmp_path: Path, monkeypatch: Any) -> None:
    mini = native_station(tmp_path, "speech.say")
    mini = Station(**{**mini.__dict__, "controls": {**mini.controls, "local_tts": {
        "model_file": "/PRIVATE/voice.onnx", "model_sha256": "a" * 64,
        "config_sha256": "b" * 64}}})
    controller = Controller(None, writes=True)
    monkeypatch.setattr(controller, "station", lambda _: mini)
    monkeypatch.setattr(native_transport, "_state", lambda *args: "s" * 64)
    sent: list[Any] = []
    raw = canonical(LOUD, extra=b"INFOISFT\x04\x00\x00\x00x\x00\x00\x00")
    audio = vi.stock_wav(raw)
    assert audio[:4] == b"RIFF" and audio[36:40] == b"data" and len(audio) == 44 + 600
    assert max(abs(s) for (s,) in struct.iter_unpack("<h", audio[44:])) == 8191

    def call(station: Station, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        sent.append((tool, arguments))
        return {"outcome": "applied", "playback_started": True, "playback_completed": True,
                "cleanup_verified": True, "frames_written": 300, "input_frames": 300,
                "sample_rate": 22050, "stage": "complete", "alsa_return_code": 0,
                "hardware_parameters_verified": True, "playback_elapsed_ms": 14,
                "quasar_running": True, "do_not_retry": True, "evidence_class": "analytical",
                "physical_effects_verified": False, "tts_backend": "local_pcm"}

    monkeypatch.setattr(native_transport, "call", call)
    assert native_transport.play_pcm(controller, mini, audio)["outcome"] == "applied"
    (tool, arguments), = sent
    assert tool == "run_station_action" and arguments["action"] == "speech.say"
    assert arguments["expected_state_sha256"] == "s" * 64
    assert arguments["parameters"]["sha256"] == hashlib.sha256(audio).hexdigest()

    def broken(*args: Any) -> Any:
        raise OSError("SENSITIVE_SENTINEL")

    monkeypatch.setattr(native_transport, "call", broken)
    result = native_transport.play_pcm(controller, mini, audio)
    assert result["outcome"] == "unknown" and "SENSITIVE" not in str(result)
    with pytest.raises(SafetyError):  # Only where the resident expects host PCM.
        native_transport.play_pcm(controller, native_station(tmp_path, "speech.say"), audio)


# Capture over explicit SSH (still used by other Mini 1 LED actions) and its pruning.

def complete(records: list[str]) -> str:
    return ("outcome=complete\ncapture_id=" + CAPTURE + "\ncapture_ready=0\n"
            "remaining_seconds=0\ncleanup_verified=1\ntruncated=0\n"
            + "".join(f"record={r}\n" for r in records))


@pytest.mark.parametrize("records,usable", [
    (["01" + "0c0804" * 6, "25aa", "01" + "00" * 18, "25aa"], True),
    (["01" + "00" * 18, "25aa", "01" + "00" * 18, "25aa"], False),  # Alice never lit.
    (["01" + "0c0804" * 6, "25aa"], False),  # No idle frame at the end.
    (["01" + "0c0804" * 5 + "000000", "25aa", "01" + "00" * 18, "25aa"], False),
    ([], False),
])
def test_capture_start_and_status_over_ssh_report_usable_evidence(
        tmp_path: Path, monkeypatch: Any, records: list[str], usable: bool) -> None:
    mini = native_station(tmp_path, "led.capture")
    commands: list[str] = []

    def runner(target: Station, command: Any) -> str:
        assert target.native_mcp == {}
        commands.append(command.command.split()[3])
        if command.command.split()[3] == "inspect":
            return "outcome=ready\nstate_sha256=" + "c" * 64 + "\n"
        if command.command.split()[3] == "apply":
            nonce = command.command.split()[4]
            return (f"outcome=capturing\ncapture_id={nonce}\ncapture_ready=1\n"
                    "remaining_seconds=29\ncleanup_verified=0\ntruncated=0\n")
        return complete(records)

    controller = Controller(None, writes=True, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: mini)
    with pytest.raises(SafetyError, match="native"):  # No silent fallback without the flag.
        controls.preview(controller, "mini1", "led.capture", {"duration_seconds": 30})
    with pytest.raises(SafetyError, match="Explicit SSH"):
        controls.preview(controller, "mini1", "audio.volume", {"percent": 5}, ssh=True)
    started = vi.capture_start(controller, "mini1", 30)
    assert started["outcome"] == "capturing" and started["capture_ready"] is True
    status = vi.capture_status(controller, "mini1", CAPTURE)
    assert status["outcome"] == "complete" and status["usable"] is usable
    assert status["records"] == len(records)
    assert commands == ["inspect", "inspect", "apply", "capture_status"]


def results(root: Path, phases: list[str]) -> list[str]:
    """Capture result directories, oldest first, with the given phases."""
    captures = root / "data/opt/alice-mcp/led-captures"
    captures.mkdir(parents=True, exist_ok=True)
    for path in (captures, captures.parent):
        path.chmod(0o700)
    names = []
    for i, phase in enumerate(phases, 1):
        name = f"{i:032x}"
        directory = captures / name
        directory.mkdir(mode=0o700)
        (directory / "cleaning").mkdir()
        (directory / "phase").write_text(phase + "\n")
        (directory / "cleanup").write_text(("1" if phase == "complete" else "0") + "\n")
        os.utime(directory, (1_000_000 + i, 1_000_000 + i))
        names.append(name)
    return names


def test_new_capture_prunes_old_completed_results(shell_device: Any) -> None:  # noqa: F811
    root, _, _ = shell_device
    names = results(root, ["unknown"] + ["complete"] * 15)
    captures = root / "data/opt/alice-mcp/led-captures"
    req = request()
    preview = run_device(shell_device, req)  # 16 results, but old ones can go: not blocked.
    assert preview["outcome"] == "ready" and len(list(captures.iterdir())) == 16
    started = run_device(shell_device, dict(req, mode="apply",
                                           expected_sha256=preview["state_sha256"]))
    assert started["outcome"] == "capturing"
    kept = {names[0], *names[-7:], req["nonce"]}  # Needs inspection; the newest 7.
    assert {p.name for p in captures.iterdir()} == kept
    time.sleep(5.2)  # Let the detached worker finish inside the temporary tree.


def test_capture_still_blocks_when_results_need_inspection(shell_device: Any) -> None:  # noqa: F811
    root, _, _ = shell_device
    results(root, ["unknown"] * 16)
    assert run_device(shell_device, request())["outcome"] == "blocked"


# Private configuration: voice.indicator inside the voice block.

def inventory(tmp_path: Path, **indicator: Any) -> Path:
    voice: dict[str, Any] = {k: f"/private/{k}" for k in ("client", "mww", "libvosk",
                                                          "vosk_zip")}
    if indicator:
        voice["indicator"] = indicator
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"stations": [
        {"id": "other", "model": "station-lite", "host": "h", "port": 22},
        {"id": "mini1", "model": "station-mini", "native_mcp": {"url": "u"},
         "controls": {"firmware_sha256": "a" * 64, "voice": voice}}]}, indent=1))
    path.chmod(0o600)
    return path


def test_configure_saves_indicator_with_private_backup(tmp_path: Path) -> None:
    path = inventory(tmp_path, palette_index=4)
    original = path.read_bytes()
    saved = vi.configure(path, "mini1", backend="ssh_led", palette_index=None)
    assert saved["indicator"] == {"backend": "ssh_led"}  # None removes a field.
    backup = tmp_path / saved["backup"]
    assert backup.read_bytes() == original and backup.stat().st_mode & 0o777 == 0o600
    data = json.loads(path.read_text())
    assert data["stations"][0] == json.loads(original)["stations"][0]
    assert data["stations"][1]["controls"]["voice"]["indicator"] == saved["indicator"]
    assert path.stat().st_mode & 0o777 == 0o600
    sound = {"path": "/PRIVATE/vins_error.wav", "sha256": "b" * 64}
    vi.configure(path, "mini1", error_sound=sound)
    indicator = json.loads(path.read_text())["stations"][1]["controls"]["voice"]["indicator"]
    assert indicator == {"backend": "ssh_led", "error_sound": sound}
    before = path.read_bytes()
    for station_id, fields in [("mini1", {"color": "crimson"}), ("mini1", {"colour": 1}),
                               ("ghost", {"backend": "off"}), ("other", {"backend": "off"})]:
        with pytest.raises(SafetyError):
            vi.configure(path, station_id, **fields)
    assert path.read_bytes() == before
