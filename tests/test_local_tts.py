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
