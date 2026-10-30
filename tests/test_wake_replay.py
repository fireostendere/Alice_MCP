"""Synthetic wake replay: a host clip into the on-station detector with --trace, no station."""

import hashlib
import io
import json
import os
import random
import shutil
import struct
import wave
from pathlib import Path
from typing import Any

import pytest
from test_io_channel_pilot import SENTINEL, device, station
from test_wake_pilot import READY, TRAILER, local, raw

from alice_mcp import wake_pilot as pilot
from alice_mcp.core import Controller, SafetyError

CLIP = struct.pack("<4800h", *(random.Random(5).randint(-900, 900) for _ in range(4800)))
KOMP = [0, 10, 250, 255, 255, 255, 255, 251, 3]  # 0.3 s: (30 - 2) // 3 = 9 inferences.
OKEY = [1] * 9
TWO = dict(READY, models=[READY["models"][0], dict(READY["models"][0], name="okey_dom")])


def trace(model: str, step: int, q: int) -> dict[str, Any]:
    return {"event": "p", "model": model, "t_ms": 50 + 30 * step, "q": q}


def trace_lines(komp: list[int] = KOMP, okey: list[int] = OKEY) -> list[dict[str, Any]]:
    return [line for step, pair in enumerate(zip(komp, okey, strict=False))
            for line in (trace("komp", step, pair[0]), trace("okey_dom", step, pair[1]))]


END = {"event": "end", "reason": "eof", "audio_ms": 300, "detections": [0, 0]}


def replay_stand_in(clip_sha256: str) -> bytes:
    """Detector stand-in: requires --trace and the exact clip on stdin, then traces two models."""
    lines = "\n".join(json.dumps(v) for v in [TWO, *trace_lines(), END])
    return f'''#!/bin/sh
[ "$*" = "--trace --max-seconds 90 --gain-db 0" ] || exit 9
sum=$(sha256sum | cut -d ' ' -f 1)
[ "$sum" = {clip_sha256} ] || exit 8
cat <<'EOF'
{lines}
EOF
'''.encode()


def setup(tmp_path: Path, monkeypatch: Any, detector: bytes, clip: bytes = CLIP,
          ) -> tuple[Controller, pilot.Replay, list[str]]:
    if not shutil.which("dash") or not shutil.which("gawk"):
        pytest.skip("Synthetic shell checks need dash and gawk")
    env = device(tmp_path, 9850, "gawk")
    calls: list[str] = []

    def runner(_: Any, command: str) -> str:
        calls.append(command)
        return local(env, command, b"").decode()

    def transfer(_: Any, request: Any, *, limit: int) -> str:
        assert request.data == clip + detector and limit == pilot.REPLAY_LIMIT
        return local(env, request.command, request.data).decode()

    controller = Controller(None, writes=True, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(pilot, "_ssh", lambda item: item)
    monkeypatch.setattr(pilot, "_transfer", transfer)
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(detector).hexdigest())
    return controller, pilot.Replay(clip, detector), calls


def test_replay_traces_one_clip_and_returns_per_model_numbers_only(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    detector = replay_stand_in(hashlib.sha256(CLIP).hexdigest())
    controller, item, calls = setup(tmp_path, monkeypatch, detector)
    preview = pilot.preview(controller, "desk", item)
    assert "dd bs=1" not in calls[0] and preview["audio_exported"] is False
    assert hashlib.sha256(CLIP).hexdigest()[:12] in preview["impact"]
    result = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert result["outcome"] == "complete", result
    assert result["models"] == {
        "komp": {"max_q": 255, "max_mean5": 0.993, "q_ge_250": 6, "inferences": 9,
                 "trace_sha256": hashlib.sha256(bytes(KOMP)).hexdigest()},
        "okey_dom": {"max_q": 1, "max_mean5": 0.0039, "q_ge_250": 0, "inferences": 9,
                     "trace_sha256": hashlib.sha256(bytes(OKEY)).hexdigest()},
    }
    detector_result = result["detector"]
    assert detector_result["audio_ms"] == 300 and detector_result["exit"] == 0
    assert detector_result["vmhwm_kib"] > 0 and detector_result["cpu_ms"] >= 0
    assert result["cleanup_verified"] and result["quasar_unchanged"] and result["do_not_retry"]
    assert result["audio_exported"] is False
    assert not any((tmp_path / "dev/data/opt").iterdir())  # Clip and detector removed.
    with pytest.raises(SafetyError):
        controller.take_plan(preview["plan_id"], preview["expected_sha256"])


@pytest.mark.skipif(not os.environ.get("ALICE_MCP_MWW_HELPER"),
                    reason="set ALICE_MCP_MWW_HELPER to an owner-built host detector")
def test_replay_parses_the_real_detector_trace(tmp_path: Path, monkeypatch: Any) -> None:
    detector = Path(os.environ["ALICE_MCP_MWW_HELPER"]).read_bytes()
    clip = CLIP * 3 + CLIP[:3200]  # 1.0 s: (100 - 2) // 3 = 32 inferences per model.
    controller, item, _ = setup(tmp_path, monkeypatch, detector, clip)
    preview = pilot.preview(controller, "desk", item)
    result = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert result["outcome"] == "complete", result
    assert result["models"] and all(m["inferences"] == 32 for m in result["models"].values())
    assert result["detector"]["audio_ms"] == 1000 and result["detector"]["vmhwm_kib"] > 0


def replay_raw(*lines: Any, final: dict[str, Any] | None = None) -> str:
    return raw(*lines, final or dict(TRAILER, detector_vmhwm_kib=700, detector_cpu_ticks=3))


@pytest.mark.parametrize(("lines", "code"), [
    ((TWO, *trace_lines(), END), "none"),
    ((TWO, *trace_lines(KOMP[:8], OKEY[:8]), END), "trace_incomplete"),
    ((TWO, *trace_lines(), dict(END, audio_ms=270)), "pcm_count_mismatch"),
    ((TWO, *trace_lines(), dict(END, reason="limit")), "detector_incomplete"),
    ((TWO, *trace_lines()), "detector_incomplete"),
    ((TWO, {"event": "error", "error": "invoke"}), "detector_error"),
    ((trace("komp", 0, 1), TWO, END), "helper_output_invalid"),
    ((TWO, trace("other", 0, 1), END), "helper_output_invalid"),
    ((TWO, trace("komp", 0, 256), END), "helper_output_invalid"),
    ((TWO, trace("komp", 0, True), END), "helper_output_invalid"),
    ((TWO, trace("komp", 1, 1), trace("komp", 0, 1), END), "helper_output_invalid"),
    ((TWO, dict(trace("komp", 0, 1), x=SENTINEL.decode()), END), "helper_output_invalid"),
    ((TWO, *trace_lines(), END, {"outcome": "complete"}), "helper_output_invalid"),
    ((TWO, *trace_lines(), SENTINEL.decode(), END), "helper_output_invalid"),
])
def test_replay_parser_is_strict_and_never_echoes_raw_output(
    lines: tuple[Any, ...], code: str,
) -> None:
    result = pilot.parse_replay(replay_raw(*lines), gain_db=0, clip_bytes=len(CLIP))
    assert result["error_code"] == code, result
    assert result["outcome"] == ("complete" if code == "none" else "unknown")
    assert result["do_not_retry"] is True and result["audio_exported"] is False
    assert SENTINEL.decode() not in json.dumps(result)
    if code == "none":
        assert result["models"]["komp"]["q_ge_250"] == 6
        assert result["detector"]["cpu_ms"] == 30 and result["detector"]["vmhwm_kib"] == 700


def test_replay_parser_flags_and_short_outputs() -> None:
    changed = dict(TRAILER, quasar_unchanged=False)
    lines = (TWO, *trace_lines(), END)
    assert pilot.parse_replay(replay_raw(*lines, final=changed), 0, len(CLIP))["error_code"] \
        == "quasar_changed_or_cleanup_unverified"
    assert pilot.parse_replay(raw(TRAILER), 0, len(CLIP))["error_code"] == \
        "helper_output_missing"
    blocked = pilot.parse_replay('{"outcome":"blocked","error_code":"ipc_unavailable"}\n', 0, 9)
    assert blocked["outcome"] == "blocked"
    assert pilot.parse_replay("x\n" * 5000, 0, len(CLIP))["error_code"] == \
        "helper_output_invalid"


def test_replay_rejects_shifted_inference_clock_and_fingerprints_probabilities() -> None:
    lines = trace_lines()
    result = pilot.parse_replay(replay_raw(TWO, *lines, END), 0, len(CLIP))
    digest = hashlib.sha256(bytes(KOMP)).hexdigest()
    assert result["models"]["komp"]["trace_sha256"] == digest
    assert result["model_sha256"] == {m["name"]: m["sha256"] for m in TWO["models"]}
    shifted = [dict(v, t_ms=v["t_ms"] + 10) for v in lines]
    assert pilot.parse_replay(replay_raw(TWO, *shifted, END), 0, len(CLIP))[
        "outcome"] == "unknown"


def wav(rate: int = 16000, channels: int = 1, width: int = 2, frames: int = 1600) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(b"\1" * frames * channels * width)
    return buffer.getvalue()


def test_clip_must_be_a_short_16k_mono_s16_wav(tmp_path: Path) -> None:
    path = tmp_path / "clip.wav"
    path.write_bytes(wav())
    assert pilot.pcm_from_wav(path) == b"\1" * 3200
    for data in (wav(rate=48000), wav(channels=2), wav(width=1), wav(frames=160001),
                 wav(frames=0), b"RIFF" + SENTINEL):
        path.write_bytes(data)
        with pytest.raises(SafetyError):
            pilot.pcm_from_wav(path)


@pytest.mark.parametrize("item", [
    pilot.Replay(b"", b"d"), pilot.Replay(b"\0" * 3, b"d"), pilot.Replay(b"\0" * 320002, b"d"),
    pilot.Replay(b"\0" * 4, b"x"), pilot.Replay(b"\0" * 4, b"d", gain_db=31),
    pilot.Replay(b"\0" * 4, b"d", nonce="x"),
])
def test_replay_inputs_and_detector_pin_are_validated(monkeypatch: Any, item: Any) -> None:
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(b"d").hexdigest())
    with pytest.raises(SafetyError):
        item.validate()


def test_replay_never_sends_the_client_and_bounds_the_clip(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(b"d").hexdigest())
    item = pilot.Replay(b"\0" * 320000, b"d")
    request = pilot._command(station(tmp_path), item, "apply", "e" * 64)
    assert request.data == item.pcm + b"d" and request.command.endswith(" replay")
    assert " 10 0 " in request.command and " 320000 " in request.command  # seconds, gain, size
    assert item.digest != pilot.Replay(b"\1" * 320000, b"d", nonce=item.nonce).digest


def test_replay_cli_previews_by_default_and_applies_only_with_live(
    monkeypatch: Any, tmp_path: Path, capsys: Any,
) -> None:
    clip, detector = tmp_path / "clip.wav", tmp_path / "detector"
    clip.write_bytes(wav())
    detector.write_bytes(b"d")
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(b"d").hexdigest())
    monkeypatch.setenv("ALICE_MCP_INVENTORY", "/PRIVATE/inventory.json")
    monkeypatch.setattr(pilot, "Controller", lambda *a, **kw: object())
    monkeypatch.setattr(pilot, "preview", lambda *a: {
        "plan_id": "p", "expected_sha256": "a" * 64, "audio_exported": False})
    calls: list[Any] = []
    monkeypatch.setattr(pilot, "apply", lambda *a: calls.append(a) or {"outcome": "complete"})
    args = ["--station", "desk", "--detector", str(detector), "--replay", str(clip)]
    assert pilot.main(args) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "preview" and not calls
    assert pilot.main([*args, "--live"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "complete" and len(calls) == 1
