"""Decision rule, corpus scoring and (optionally) native/reference parity.

Parity runs only when ALICE_MCP_MWW_HELPER and ALICE_MCP_MWW_MODEL name an owner-built
host helper and its embedded model, inside a venv with pymicro-features and
ai-edge-litert; otherwise it skips. Audio is synthetic; no device is used.
"""

import importlib.util
import math
import os
import random
import struct
from pathlib import Path
from typing import Any

import pytest

from scripts.voice import mww_eval
from scripts.voice.mww_eval import detect, parity, run_helper, score_corpus, trace_of


def test_detect_window_threshold_and_cooldown() -> None:
    trace = [(30 * i, q) for i, q in enumerate([0, 250, 250, 250, 250, 250, 0, 0, 250, 250, 250])]
    # Window 3 at 0.9: the first all-high window ends at t=90 ms; the 100 ms cooldown
    # suppresses t=180 and the window restarts, so the next needs three fresh values.
    assert [e["t_ms"] for e in detect(trace, 0.9, 3, 100)] == [90, 300]
    assert detect(trace, 0.9, 3, 10_000) == detect(trace[:6], 0.9, 3, 10_000)
    assert detect(trace, 0.99, 1, 0) == []
    assert len(detect(trace, 0.5, 1, 0)) == 8


def test_parity_selects_embedded_model_and_rejects_missing_tail(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    import hashlib

    model = tmp_path / "model.tflite"
    model.write_bytes(b"synthetic model")
    lines = [
        {"event": "ready", "models": [
            {"name": "komp", "sha256": hashlib.sha256(model.read_bytes()).hexdigest()},
            {"name": "marusya", "sha256": "b" * 64}]},
        {"event": "p", "model": "marusya", "t_ms": 50, "q": 255},
        {"event": "p", "model": "komp", "t_ms": 50, "q": 10},
        {"event": "end", "reason": "eof", "audio_ms": 100},
    ]
    monkeypatch.setattr(mww_eval, "run_helper", lambda *a: {
        "lines": lines, "exit": 0, "wall_s": 0, "cpu_s": 0,
        "cpu_per_audio_s": 0, "vm_hwm_kib": 1})
    monkeypatch.setattr(mww_eval, "reference_trace", lambda *a: ([(50, 10), (80, 20)], None))
    result = parity(tmp_path / "helper", model, bytes(3200))
    assert result["inferences"] == 1
    assert result["reference_kernels"]["time_aligned"] is False
    assert result["reference_kernels"]["same_length"] is False
    model.write_bytes(b"wrong model")
    with pytest.raises(ValueError, match="embedded"):
        parity(tmp_path / "helper", model, bytes(3200))


def test_corpus_scoring_does_not_count_other_models() -> None:
    lines = [{"event": "wake", "model": "marusya", "t_ms": 1000},
             {"event": "wake", "model": "komp", "t_ms": 4000}]
    assert mww_eval.events_of(lines, "komp") == [lines[1]]


@pytest.mark.parametrize("damage", ["missing_model", "short_input", "failed", "no_end"])
def test_benchmark_refuses_incomplete_or_wrong_model_runs(monkeypatch: Any, damage: str) -> None:
    lines = [{"event": "ready", "models": [{"name": "komp"}]},
             {"event": "end", "reason": "eof", "audio_ms": 100}]
    run: dict[str, Any] = {"exit": 0, "lines": lines}
    if damage == "missing_model":
        lines[0]["models"] = [{"name": "marusya"}]
    elif damage == "short_input":
        lines[1]["audio_ms"] = 90
    elif damage == "failed":
        run["exit"] = 1
    else:
        lines.pop()
    monkeypatch.setattr(mww_eval, "run_helper", lambda *a: run)
    with pytest.raises(ValueError):
        mww_eval.benchmark_run(Path("/PRIVATE/helper"), bytes(3200), [], "komp")


def region(case: str, kind: str, start: float, phrase: str = "") -> dict[str, object]:
    return {"case": case, "voice": "v", "kind": kind, "start_s": start, "speech_end_s": start + 1,
            "end_s": start + 3, "phrase": phrase, "route": "mcp"}


def test_score_corpus_gates_echo_and_counts_retrigger() -> None:
    meta = {"rate": 10, "speaking": [[60, 70]], "regions": [
        region("pos", "positive", 0, "компьютер"), region("neg", "negative", 3),
        region("echo", "echo", 6, "компьютер"), region("again", "retrigger", 9, "компьютер"),
        region("other", "positive", 12, "маруся")]}
    events = [{"t_ms": t} for t in (1500, 7200, 9500, 10600, 13000)]
    result = score_corpus(meta, events, "komp")
    counts = result["counts"]
    assert counts["true_accepts"] == 1 and counts["false_accepts"] == 0
    assert counts["echo_suppressed"] == 1 and counts["echo_leaks"] == 0
    assert counts["retrigger_duplicates"] == 1 and counts["other_wake_accepts"] == 1
    assert {f["error"] for f in result["failures"]} == {"retrigger_duplicate", "other_wake_accept"}
    assert score_corpus(meta, [], "komp")["false_reject_rate"] == 1.0


HELPER, MODEL = os.environ.get("ALICE_MCP_MWW_HELPER"), os.environ.get("ALICE_MCP_MWW_MODEL")


def synthetic_pcm(seconds: float = 6.0) -> bytes:
    rng = random.Random(5)
    samples = []
    for n in range(int(16000 * seconds)):
        tone = 3000 * math.sin(2 * math.pi * (300 + 200 * math.sin(n / 4000)) * n / 16000)
        burst = 6000 * rng.uniform(-1, 1) if (n // 4000) % 3 == 1 else 0
        samples.append(max(-32768, min(32767, int(tone + burst))))
    return struct.pack(f"<{len(samples)}h", *samples)


@pytest.mark.skipif(not HELPER, reason="set ALICE_MCP_MWW_HELPER to a host build")
def test_native_decisions_match_python_mirror() -> None:
    assert HELPER
    run = run_helper(Path(HELPER), synthetic_pcm(),
                     ["--trace", "--max-seconds", "60", "--threshold", "0.01", "--window", "2",
                      "--cooldown-ms", "500"])
    native = [e["t_ms"] for e in run["lines"] if e.get("event") == "wake"]
    assert run["exit"] == 0
    assert native == [e["t_ms"] for e in detect(trace_of(run["lines"]), 0.01, 2, 500)]


@pytest.mark.skipif(not HELPER, reason="set ALICE_MCP_MWW_HELPER to a host build")
def test_fixed_gain_equals_prescaled_input() -> None:
    assert HELPER
    quiet = synthetic_pcm(3.0)
    samples = struct.unpack(f"<{len(quiet) // 2}h", quiet)
    factor = 10 ** (12 / 20)
    louder = struct.pack(f"<{len(samples)}h",
                         *(max(-32768, min(32767, round(s * factor))) for s in samples))
    gained = run_helper(Path(HELPER), quiet, ["--trace", "--max-seconds", "60", "--gain-db", "12"])
    plain = run_helper(Path(HELPER), louder, ["--trace", "--max-seconds", "60"])
    assert gained["exit"] == plain["exit"] == 0
    assert trace_of(gained["lines"]) == trace_of(plain["lines"]) != []
    assert gained["lines"][0]["gain_db"] == 12


@pytest.mark.skipif(not (HELPER and MODEL and importlib.util.find_spec("ai_edge_litert")
                         and importlib.util.find_spec("pymicro_features")),
                    reason="needs host helper, its model and the isolated reference venv")
def test_native_matches_tflite_reference() -> None:
    assert HELPER and MODEL
    result = parity(Path(HELPER), Path(MODEL), synthetic_pcm())
    reference = result["reference_kernels"]
    assert reference["time_aligned"] and reference["inferences"] == result["inferences"] > 0
    assert reference["max_abs_q"] <= 1  # stated tolerance: one uint8 step (1/256); seen: 0
