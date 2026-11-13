"""Host-synthesized speech streamed over SSH to the firmware player; synthetic only."""

import hashlib
import io
import json
import os
import subprocess
import wave
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import controls, local_tts, models, speech_stream
from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller, SafetyError, SSHInput, SSHStream

READY = '{"outcome":"ready","state_sha256":"' + "b" * 64 + '"}\n'
SAY = {"command": ["/PRIVATE/say"]}


def _wav() -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(b"\x01\x00" * 22050)
    return stream.getvalue()


def _controller(tmp_path: Path, runner: Any, model: str = "station-lite",
                **extra: Any) -> Controller:
    key, hosts = tmp_path / "identity", tmp_path / "hosts"
    key.touch(mode=0o600)
    hosts.touch()
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"stations": [{
        "id": "desk", "model": model, "host": "station.invalid", "port": 2222, "user": "root",
        "identity_file": str(key), "known_hosts_file": str(hosts),
        "preauthorize_all_actions": True,
        "controls": {"firmware_sha256": "a" * 64, "local_tts": SAY, **extra},
    }]}))
    path.chmod(0o600)
    return Controller(path, writes=True, runner=runner)


def _say(controller: Controller) -> dict[str, Any]:
    plan = controls.preview(controller, "desk", "speech.say", {"text": "synthetic words"})
    assert "own synthesizer" in plan["impact"] and "Yandex" not in plan["impact"]
    return controller.execute_plan(controller.take_plan(plan["plan_id"], plan["expected_sha256"]))


@pytest.mark.parametrize("played,outcome", [
    ('{"outcome":"played","player_exit":0,"quasar_running":true}', "applied"),
    ('{"outcome":"played","player_exit":137,"quasar_running":true}', "unknown"),
    ('{"outcome":"blocked","error_code":"preview_state_changed"}', "blocked"),
    ('{"outcome":"played","player_exit":0,"quasar_running":true,"x":"PRIVATE"}', "unknown"),
    ("PRIVATE noise", "unknown")])
def test_speech_say_without_an_agent_streams_host_audio_to_the_firmware_player(
    tmp_path: Path, monkeypatch: Any, played: str, outcome: str,
) -> None:
    audio = _wav()
    monkeypatch.setattr(local_tts, "command_wav", lambda text, command: audio)
    seen: list[Any] = []

    def runner(station: Any, command: Any) -> str:
        seen.append(command)
        return played + "\n" if command.data else READY

    result = _say(_controller(tmp_path, runner))
    assert result["outcome"] == outcome and result["do_not_retry"]
    assert "PRIVATE" not in str(result)
    assert result.get("playback_completed", False) == (outcome == "applied")
    # Preview and revalidation carry no audio; only the apply call streams it.
    assert [type(c) for c in seen] == [SSHStream] * 3
    assert [c.data for c in seen] == [b"", b"", audio]
    assert all("synthetic words" not in c.command and len(c.command) <= 4096 for c in seen)


def test_failed_synthesis_sends_nothing_and_names_only_the_fixed_reason(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    def fail(text: str, command: list[str]) -> bytes:
        raise local_tts.SpeechCommandError("TTS command failed")

    monkeypatch.setattr(local_tts, "command_wav", fail)
    seen: list[Any] = []

    def runner(station: Any, command: Any) -> str:
        seen.append(command)
        return READY

    result = _say(_controller(tmp_path, runner))
    assert result["outcome"] == "blocked" and "TTS command failed" in result["error"]
    assert [c.data for c in seen] == [b"", b""]


def test_stream_bound_is_one_canonical_utterance_and_helpers_keep_theirs() -> None:
    SSHStream("true", bytes(1920044))
    for kind, size in ((SSHStream, 1920045), (SSHInput, 192 * 1024 + 1)):
        with pytest.raises(SafetyError):
            kind("true", bytes(size))


def test_any_buildroot_speaker_takes_local_tts_and_the_matrix_says_generic(
    tmp_path: Path,
) -> None:
    policy = {"firmware_sha256": "a" * 64, "local_tts": SAY}
    for model in ("station-lite", "station-mini2", "station-2", "station-street"):
        assert rt.validate_parameters("speech.say", {"text": "x"}, policy, model)
        assert models.actions(model)["speech.say"] == "generic"
        with pytest.raises(ValueError):  # Without host synthesis there is nothing to call.
            rt.validate_parameters("speech.say", {"text": "x"}, {"tts_client": {}}, model)
    assert models.actions("station-mini")["speech.say"] == "adapter"
    assert models.actions("yandex-hub")["speech.say"] == "none"
    assert models.actions("station-max")["speech.say"] == "none"
    with pytest.raises(SafetyError):  # Android: no write path at all.
        _say(_controller(tmp_path, lambda station, command: READY, "station-max"))


def test_local_media_still_needs_the_resident_agent(tmp_path: Path) -> None:
    controller = _controller(tmp_path, lambda station, command: READY, local_media={
        "chime": {"file": "/PRIVATE/chime.wav", "sha256": "c" * 64}})
    with pytest.raises(SafetyError):
        controller.station("desk")


def test_station_script_binds_firmware_and_state_and_feeds_only_the_player(
    tmp_path: Path,
) -> None:
    quasar = tmp_path / "quasar"
    quasar.mkdir()
    player = quasar / "maind"
    player.write_text('#!/bin/sh\necho "$@" > "$0.args"\ncat > "$0.heard"\n'
                      'exit "$(cat "$0.code")"\n')
    player.chmod(0o700)
    (quasar / "maind.code").write_text("0")
    source = speech_stream.SCRIPT.replace("OWNER_UID=0", "OWNER_UID=" + str(os.getuid()))
    # Only the synthetic fixture changes paths/checks; production exposes no source argument.
    source = source.replace("Q=/system/vendor/quasar", "Q=" + str(quasar))
    source = source.replace("pidof maind >/dev/null", "true")
    firmware = hashlib.sha256(player.read_bytes()).hexdigest()

    def run(mode: str, expected: str = "", digest: str = firmware,
            audio: bytes = b"") -> dict[str, Any]:
        done = subprocess.run(["sh", "-c", source, "sh", mode, digest, "c" * 64, expected],
                              input=audio, capture_output=True, check=True, timeout=10)
        return dict(json.loads(done.stdout))

    assert run("inspect", digest="d" * 64) == {
        "outcome": "blocked", "error_code": "firmware_mismatch"}
    state = run("inspect")["state_sha256"]
    assert run("apply", "e" * 64, audio=_wav())["error_code"] == "preview_state_changed"
    assert not (quasar / "maind.heard").exists()
    assert run("apply", state, audio=_wav()) == {
        "outcome": "played", "player_exit": 0, "quasar_running": True}
    assert (quasar / "maind.heard").read_bytes() == _wav()
    assert (quasar / "maind.args").read_text().startswith("--gst-launch fdsrc fd=0 ! decodebin")
    (quasar / "maind.code").write_text("3")
    assert run("apply", state, audio=_wav())["player_exit"] == 3
