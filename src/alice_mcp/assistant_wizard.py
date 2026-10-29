"""Settings-only wizard and offline synthetic routing check.

Run: python -m alice_mcp.assistant_wizard --self-test (or --simulate).
No microphones, acoustic detector, real RGB, Piper model or playback are tested.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from alice_mcp.assistant_profile import route_transcript, validate_profile


def settings_only(profile: Any) -> dict[str, Any]:
    """Accept explicit settings, without opening paths or claiming hardware readiness."""
    return {"status": "settings_only", "hardware_verified": False,
            "profile": validate_profile(profile)}


def activation_status(profile: Any) -> dict[str, Any]:
    """Fail closed: this package has no qualified live activation implementation."""
    validate_profile(profile)
    return {"status": "blocked", "effects": [], "hardware_verified": False,
            "missing_verified": ["pcm_capture", "acoustic_detector", "rgb", "playback"]}


def self_test() -> dict[str, Any]:
    """Exercise routing, color sink and TTS adapter using in-memory synthetic data."""
    profile = {
        "version": 1,
        "wake_phrases": [{"phrase": "Алёна", "route": "mcp"},
                         {"phrase": "Алиса", "route": "alice"}],
        "activation_color": "#123456",
        "tts": {"backend": "piper", "model": "/synthetic/voice.onnx",
                "model_sha256": "a" * 64, "config_sha256": "b" * 64, "volume": 0.15},
    }
    settings = settings_only(profile)["profile"]
    colors: list[str] = []
    tts_calls: list[dict[str, Any]] = []

    def synthetic_tts(text: str, configuration: dict[str, Any]) -> bytes:
        tts_calls.append({"text": text, "configuration": configuration})
        return b"synthetic-not-audio"

    event = route_transcript(settings, "АЛЕ\u0308НА, синтетический ответ")
    if event is None or event["route"] != "mcp" or event["trusted"] is not False:
        raise RuntimeError("Synthetic Unicode/MCP route failed")
    colors.append(event["activation_color"])
    audio = synthetic_tts(event["text"], settings["tts"])
    alice = route_transcript(settings, "Алиса синтетический ответ")
    if (route_transcript(settings, "Алёнабот привет") is not None
            or alice is None or alice["route"] != "alice"
            or colors != ["#123456"] or audio != b"synthetic-not-audio"
            or tts_calls != [{"text": "синтетический ответ", "configuration": settings["tts"]}]
            or activation_status(settings)["status"] != "blocked"):
        raise RuntimeError("Synthetic adapter contract failed")
    return {"status": "passed", "synthetic": True, "hardware_verified": False,
            "checks": ["unicode_phrase", "word_boundary", "mcp_route", "alice_route",
                       "activation_color", "local_tts_contract", "blocked_live"],
            "limits": "Transcript prefixes only; no acoustic, RGB, Piper or playback verification"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", "--self-test", action="store_true", required=True)
    parser.parse_args(argv)
    print(json.dumps(self_test(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
