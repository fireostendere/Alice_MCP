"""Synthetic settings and transcript routing; no acoustic or device evidence."""

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from alice_mcp.assistant_profile import (
    load_profile,
    route_transcript,
    save_profile,
    synthesize_reply,
    validate_profile,
)
from alice_mcp.core import SafetyError


def settings() -> dict[str, Any]:
    return {
        "version": 1,
        "wake_phrases": [{"phrase": "Алёна", "route": "mcp"},
                         {"phrase": "Алиса", "route": "alice"}],
        "activation_color": "#123456",
        "tts": {"backend": "piper", "model": "/synthetic/voice.onnx",
                "model_sha256": "a" * 64, "config_sha256": "b" * 64, "volume": 0.15},
    }


def test_unicode_prefix_word_boundaries_and_untrusted_event() -> None:
    profile = settings()
    event = route_transcript(profile, "  АЛЕ\u0308НА, включи свет  ")
    assert event == {"kind": "transcript_route", "trusted": False, "route": "mcp",
                     "phrase": "алёна", "text": "включи свет",
                     "activation_color": "#123456", "tts_backend": "piper"}
    assert route_transcript(profile, "Алиса привет")["route"] == "alice"
    assert route_transcript(profile, "Алёна") is not None
    for text in ["Алёнабот привет", "Алёна_бот", "Алёна2", "не Алёна привет"]:
        assert route_transcript(profile, text) is None
    hostile = route_transcript(profile, "Алёна $(do-not-execute); <script>")
    assert hostile and hostile["trusted"] is False


def test_unicode_casefold_expansion_and_combining_phrase() -> None:
    profile = settings()
    profile["wake_phrases"] = [{"phrase": "Straße", "route": "mcp"},
                               {"phrase": "İpek", "route": "alice"}]
    assert route_transcript(profile, "STRASSE, ответ")["text"] == "ответ"
    assert route_transcript(profile, "İPEK, ответ")["route"] == "alice"


@pytest.mark.parametrize("text", ["", "   ", "Алёна\nкоманда", "Алёна\x00", "x" * 4097,
                                  True, None, "Алёна\u202eтекст", "Алёна\ud800"])
def test_reject_invalid_transcripts(text: Any) -> None:
    with pytest.raises(SafetyError):
        route_transcript(settings(), text)


@pytest.mark.parametrize("mutation", [
    {"version": True}, {"extra": 1}, {"activation_color": "red"},
    {"wake_phrases": []},
    {"wake_phrases": [{"phrase": "Алёна", "route": "shell"}]},
    {"wake_phrases": [{"phrase": "Алёна", "route": "mcp"},
                      {"phrase": "АЛЕ\u0308НА", "route": "alice"}]},
    {"wake_phrases": [{"phrase": "эй", "route": "mcp"},
                      {"phrase": "эй алиса", "route": "alice"}]},
    {"wake_phrases": [{"phrase": "$(shell)", "route": "mcp"}]},
    {"tts": {"backend": "cloud"}},
])
def test_strict_profile(mutation: dict[str, Any]) -> None:
    value = settings() | mutation
    with pytest.raises(SafetyError):
        validate_profile(value)


@pytest.mark.parametrize("key,value", [("volume", True), ("volume", float("nan")),
                                       ("model", "relative.onnx"),
                                       ("model_sha256", "not-a-pin"), ("extra", 1)])
def test_strict_tts(key: str, value: Any) -> None:
    profile = settings()
    profile["tts"][key] = value
    with pytest.raises(SafetyError):
        validate_profile(profile)


def test_validation_copies_input() -> None:
    value = settings()
    expected = copy.deepcopy(value)
    result = validate_profile(value)
    result["tts"]["volume"] = 0.2
    assert value == expected


def test_explicit_local_piper_adapter_without_playback(monkeypatch: Any) -> None:
    from alice_mcp import local_tts

    calls = []

    def synthetic(text: str, model: Path, model_pin: str, config_pin: str,
                  *, volume: float) -> bytes:
        calls.append((text, model, model_pin, config_pin, volume))
        return b"synthetic-not-audio"

    monkeypatch.setattr(local_tts, "synthesize", synthetic)
    assert synthesize_reply(settings(), "Синтетический ответ") == b"synthetic-not-audio"
    assert calls == [("Синтетический ответ", Path("/synthetic/voice.onnx"),
                      "a" * 64, "b" * 64, 0.15)]
    with pytest.raises(SafetyError):
        synthesize_reply(settings(), "bad\ntext")
    assert len(calls) == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only private persistence")
def test_private_atomic_create_does_not_replace_inventory(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    target = tmp_path / "assistant.json"
    save_profile(target, settings())
    assert target.stat().st_mode & 0o777 == 0o600
    assert load_profile(target) == validate_profile(settings())
    original = target.read_bytes()
    with pytest.raises(SafetyError):
        save_profile(target, settings())
    assert target.read_bytes() == original
    target.chmod(0o644)
    with pytest.raises(SafetyError):
        load_profile(target)
    target.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(SafetyError):
        load_profile(link)
    tmp_path.chmod(0o755)
    with pytest.raises(SafetyError):
        save_profile(tmp_path / "other.json", settings())
    tmp_path.chmod(0o700)
    target.write_text(json.dumps(settings())[:-1] + ',"version":1}')
    with pytest.raises(SafetyError):
        load_profile(target)
