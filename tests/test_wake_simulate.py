"""Synthetic wake benchmark harness; real Vosk/Piper runs only when explicitly supplied."""

import importlib.util
import json
import math
import os
from array import array
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import wake_simulate as sim
from alice_mcp.core import SafetyError


def event(start: float, decided: float, decision: str, route: str = "",
          phrase: str = "", text: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {"start_s": start, "end_s": start + 0.5, "decided_s": decided,
                              "decision": decision}
    if route:
        result.update(route=route, phrase=phrase, text=text)
    return result


def region(kind: str, start: float, phrase: str = "", command: str = "") -> dict[str, Any]:
    route = "" if not phrase else "alice" if phrase == "алиса" else "mcp"
    return {"case": kind + str(start), "voice": "v", "kind": kind, "start_s": start,
            "speech_end_s": start + 1.0, "end_s": start + 4.0, "route": route,
            "phrase": phrase, "command": command}


def test_score_counts_each_error_class_without_fuzzy_credit() -> None:
    regions = [region("positive", 0, "компьютер", "включи свет"),
               region("positive", 4, "сол"), region("positive", 8, "маруся"),
               region("negative", 12), region("echo", 16, "компьютер"),
               region("retrigger", 20, "маруся")]
    events = [event(0.5, 1.6, "routed", "mcp", "компьютер", "включи свет"),
              event(4.5, 5.6, "no_activation"),  # "сок": a real reject, not near-match credit
              event(8.5, 9.5, "routed", "mcp", "компьютер"),
              event(12.5, 13.4, "routed", "mcp", "сол"),
              event(16.5, 17.5, "suppressed_echo"),
              event(20.5, 21.5, "routed", "mcp", "маруся"),
              event(22.0, 23.0, "suppressed_retrigger"),
              event(30.0, 31.0, "routed", "mcp", "сол")]
    heard = [{"start": 4.5, "end": 5.0, "text": "сок"}]
    result = sim.score(regions, events, heard)
    assert result["counts"] == {
        "positives": 3, "true_accepts": 1, "false_rejects": 1, "misroutes": 1,
        "commands_expected": 1, "commands_exact": 1, "negatives": 1, "false_accepts": 1,
        "echo_cases": 1, "echo_leaks": 0, "retrigger_cases": 1,
        "retrigger_duplicates": 0, "unattributed_routed": 1}
    assert result["endpoint_delay_s"] == [0.6]
    assert {"case": "positive4", "voice": "v", "error": "false_reject",
            "heard": ["сок"]} in result["failures"]
    assert result["per_phrase"]["сол"] == {"positives": 1, "accepted": 0}


def test_noise_is_calibrated_to_measured_speech_level(monkeypatch: Any) -> None:
    # Bounded Piper output is quiet (~-38 dBFS); fixed-dBFS noise once exceeded it.
    tone = array("h", [1000, -1000] * 8000)  # RMS 1000 at 16 kHz
    monkeypatch.setattr(sim, "_speech", lambda case, voice: (tone, 16000))
    corpus = [sim.Case("noisy", "positive", "x", "сол", snr_db=10),
              sim.Case("noise", "noise", "", noise_dbfs=-30)]
    data = sim.build_corpus([Path("/x/ru_RU-irina-medium.onnx")], corpus)
    pcm = array("h", data["pcm"])

    def rms(start: float, seconds: float) -> float:
        part = pcm[int(start * 16000):int((start + seconds) * 16000)]
        return math.sqrt(sum(s * s for s in part) / len(part))

    assert 280 < rms(0, 0.5) < 350  # lead noise = 1000 / 10 ** 0.5
    assert 900 < rms(data["regions"][1]["start_s"], 2.5) < 1150  # 32767 * 10 ** -1.5
    assert data["speech_rms_dbfs"] == [-30.3]


def test_artifacts_are_hash_pinned(tmp_path: Path) -> None:
    other = tmp_path / "libvosk.so"
    other.write_bytes(b"not the reviewed library")
    with pytest.raises(SafetyError):
        sim.read_pinned(other, sim.LIBVOSK_SHA256)
    with pytest.raises(SafetyError):
        sim.voice_pins(tmp_path / "unknown-voice.onnx")
    assert sim.voice_pins(Path("/x/ru_RU-irina-medium.onnx"))[0].startswith("8ff38212")


def test_cli_reports_unavailable_without_artifacts(capsys: Any) -> None:
    assert sim.main(["--simulate"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "unavailable"
    assert output["synthetic"] is True and output["hardware_verified"] is False
    assert {"libvosk", "vosk_zip", "voice"} <= set(output["missing"])


ARTIFACTS = [os.environ.get(k, "") for k in (
    "ALICE_MCP_WAKE_LIBVOSK", "ALICE_MCP_WAKE_VOSK_ZIP", "ALICE_MCP_WAKE_VOICE")]


@pytest.mark.skipif(not all(ARTIFACTS) or importlib.util.find_spec("piper") is None,
                    reason="optional Vosk library/model and Piper voice not supplied")
def test_real_vosk_streams_synthetic_piper_corpus() -> None:
    corpus = [sim.Case("cmd", "positive", "Компьютер, включи свет.", "компьютер", "включи свет"),
              sim.Case("none", "negative", "Включи свет на кухне."),
              sim.Case("silence", "silence", ""),
              sim.Case("echo", "echo", "Компьютер, я слушаю.", "компьютер")]
    result = sim.simulate(Path(ARTIFACTS[0]), Path(ARTIFACTS[1]), [Path(ARTIFACTS[2])], corpus)
    assert result["synthetic"] is True and result["hardware_verified"] is False
    counts = result["metrics"]["counts"]
    assert counts["true_accepts"] == 1 and counts["false_accepts"] == 0
    assert counts["echo_leaks"] == 0 and counts["commands_exact"] == 1
    assert result["adapters"]["receipts"] == {"mcp_inbox": 1}
    assert result["adapters"]["one_shot_release_verified"] is True
