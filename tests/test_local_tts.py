"""Local synthesis uses synthetic model/chunks, never household speech."""

import hashlib
import io
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import local_tts
from alice_mcp.core import SafetyError


def test_local_tts_is_pinned_bounded_pcm_without_text_files(tmp_path: Path,
                                                          monkeypatch: Any) -> None:
    model, config = tmp_path / "voice.onnx", tmp_path / "voice.onnx.json"
    model.write_bytes(b"synthetic model")
    config.write_text('{"audio":{"sample_rate":22050},"espeak":{"voice":"ru"}}')
    calls = []

    class Voice:
        def synthesize(self, text: str, options: Any) -> Any:
            calls.append((text, options.volume))
            yield SimpleNamespace(sample_rate=22050, sample_width=2, sample_channels=1,
                                  audio_int16_bytes=b"\x01\x00" * 2205)

    def load(*args: Any, **kwargs: Any) -> Voice:
        return Voice()

    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(
        PiperVoice=SimpleNamespace(load=load),
        SynthesisConfig=lambda **kwargs: SimpleNamespace(**kwargs),
    ))
    pin = hashlib.sha256(model.read_bytes()).hexdigest()
    config_pin = hashlib.sha256(config.read_bytes()).hexdigest()
    audio = local_tts.synthesize("synthetic", model, pin, config_pin)
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getframerate() == 22050 and wav.getnchannels() == 1
        assert wav.getsampwidth() == 2 and wav.getnframes() == 2205
    assert calls == [("synthetic", 0.15)]
    assert {p.name for p in tmp_path.iterdir()} == {model.name, config.name}
    model.write_bytes(b"changed")
    with pytest.raises(SafetyError):
        local_tts.synthesize("synthetic", model, pin, config_pin)
    assert len(calls) == 1


def test_local_tts_rejects_text_and_non_russian_or_unpinned_input(tmp_path: Path) -> None:
    with pytest.raises(SafetyError):
        local_tts.synthesize("a\nline", tmp_path / "missing", "a" * 64, "b" * 64)


def test_local_wav_transport_rejects_trailers_loud_audio_and_wrong_duration() -> None:
    def wav(samples: bytes) -> bytes:
        stream = io.BytesIO()
        with wave.open(stream, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(22050)
            audio.writeframes(samples)
        return stream.getvalue()

    valid = wav(b"\x01\x00" * 2205)
    assert set(local_tts.wav_parameters(valid)) == {"wav_base64", "sha256"}
    for data in [valid[:-1], valid + b"extra", wav(b"\x00\x20"),
                 wav(b"\x01\x00" * 22050 * 21)]:
        with pytest.raises(SafetyError):
            local_tts.wav_parameters(data)


FAKE_TTS = """
import io, sys, time, wave
text = sys.stdin.buffer.read().decode("utf-8")
mode = sys.argv[1]
if mode == "exit":
    sys.exit(3)
if mode == "text":
    sys.stdout.write("not a wav")
    sys.exit(0)
if mode == "slow":
    time.sleep(30)
out = io.BytesIO()
with wave.open(out, "wb") as wav:
    wav.setnchannels(2 if mode == "stereo" else 1)
    wav.setsampwidth(2)
    wav.setframerate(24000)
    wav.writeframes(b"\\x30\\x75\\xd0\\x8a" * (24000 * 11 if mode == "long" else 1000 * len(text)))
sys.stdout.buffer.write(out.getvalue())
"""


def _command(tmp_path: Path, mode: str) -> list[str]:
    script = tmp_path / "tts.py"
    script.write_text(FAKE_TTS)
    return [sys.executable, str(script), mode]


def test_any_synthesizer_command_becomes_quiet_canonical_pcm(tmp_path: Path) -> None:
    audio = local_tts.command_wav("привет", _command(tmp_path, "ok"))
    assert set(local_tts.wav_parameters(audio)) == {"wav_base64", "sha256"}
    with wave.open(io.BytesIO(audio), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        assert wav.getframerate() == 24000 and wav.getnframes() == 2 * 1000 * len("привет")
    # ±30000 from the engine is attenuated to the player's ceiling, shape kept.
    assert frames[:4] == (8191).to_bytes(2, "little", signed=True) + (-8191).to_bytes(
        2, "little", signed=True)


@pytest.mark.parametrize("mode,reason", [
    ("exit", "failed"), ("text", "not a WAV"), ("stereo", "mono 16-bit"),
    ("long", "longer than 20 s"), ("slow", "timed out")])
def test_synthesizer_command_failures_have_fixed_reasons(
    tmp_path: Path, monkeypatch: Any, mode: str, reason: str,
) -> None:
    monkeypatch.setattr(local_tts, "COMMAND_SECONDS", 2.0)
    with pytest.raises(local_tts.SpeechCommandError, match=reason):
        local_tts.command_wav("synthetic", _command(tmp_path, mode))
    with pytest.raises(local_tts.SpeechCommandError, match="could not be run"):
        local_tts.command_wav("synthetic", [str(tmp_path / "missing")])


def test_policy_accepts_a_synthesizer_command_only_as_an_absolute_argv() -> None:
    from alice_mcp import device_runtime as rt

    base = {"firmware_sha256": "a" * 64}
    command = ["/opt/tts/say", "--voice", "ru_RU", "голос"]
    policy = rt.validate_policy({**base, "local_tts": {"command": command}}, "station-mini")
    assert policy["local_tts"] == {"command": command}
    for bad in [[], ["say"], ["/opt/tts/say", 1], ["/opt/tts/say", ""], ["/opt/tts/say", "a\0b"],
                ["/opt/tts/say"] * 17, "/opt/tts/say"]:
        with pytest.raises(ValueError):
            rt.validate_policy({**base, "local_tts": {"command": bad}}, "station-mini")
    with pytest.raises(ValueError):
        rt.validate_policy({**base, "local_tts": {
            "command": command, "model_file": "/x", "model_sha256": "a" * 64,
            "config_sha256": "b" * 64}}, "station-mini")
    # Host synthesis is not tied to a model: the station only has to play the result.
    assert rt.validate_policy({**base, "local_tts": {"command": command}}, "station-lite")
