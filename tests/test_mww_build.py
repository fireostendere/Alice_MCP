"""microWakeWord helper: publication boundary, model embedding and the native contract.

Native checks run only when ALICE_MCP_MWW_HELPER names an owner-built host binary
(see docs/MICRO_WAKE_WORD.md); otherwise they skip. No station or audio device is used.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from check_public import path_rule  # noqa: E402

from alice_mcp.mww_build import model_header  # noqa: E402

HELPER = os.environ.get("ALICE_MCP_MWW_HELPER")
needs_helper = pytest.mark.skipif(not HELPER, reason="set ALICE_MCP_MWW_HELPER to a host build")


def test_reviewed_source_is_publishable_but_builds_are_not() -> None:
    assert path_rule("src/alice_mcp/native/mww/mww_detect.cc") is None
    for name in ("mww-detect-armhf", "mww_models.h", "komp.tflite", "other.cc"):
        assert path_rule(f"src/alice_mcp/native/mww/{name}") is not None


def test_model_header_embeds_digest_and_rejects_unsafe_names() -> None:
    header = model_header([("komp", b"\x01\x02\x03")])
    assert "kModelCount = 1" in header and '"komp"' in header
    assert "039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81" in header
    for name, data in (('a"b', b"\x00"), ("Komp", b"\x00"), ("komp", b""),
                       ("x" * 40, b"\x00"), ("komp", b"\x00" * (1024 * 1024 + 1))):
        with pytest.raises(ValueError):
            model_header([(name, data)])


def run(args: list[str], pcm: bytes = b"") -> tuple[int, list[dict[str, object]]]:
    assert HELPER
    done = subprocess.run([HELPER, *args], input=pcm, capture_output=True, timeout=60)
    return done.returncode, [json.loads(line) for line in done.stdout.splitlines()]


@needs_helper
@pytest.mark.parametrize("args", [[], ["--max-seconds", "0"], ["--max-seconds", "1", "--bogus"],
                                  ["--max-seconds", "1", "--threshold", "2"],
                                  ["--max-seconds", "1", "--window", "1.5"],
                                  ["--max-seconds", "1", "--threshold"]])
def test_helper_rejects_invalid_arguments(args: list[str]) -> None:
    code, lines = run(args)
    assert code == 2 and lines[-1]["event"] == "error"


@needs_helper
def test_helper_is_bounded_and_typed() -> None:
    code, lines = run(["--max-seconds", "1", "--trace"], bytes(16000 * 2 * 3))
    assert code == 0 and lines[0]["event"] == "ready" and lines[0]["rate"] == 16000
    assert lines[-1] == {"event": "end", "reason": "limit", "audio_ms": 1000,
                         "detections": [0] * len(lines[0]["models"])}  # type: ignore[arg-type]
    probabilities = [line for line in lines if line["event"] == "p"]
    assert probabilities and all(0 <= int(p["q"]) <= 255 for p in probabilities)  # type: ignore[call-overload]
    code, lines = run(["--max-seconds", "5"], bytes(321))  # partial final hop is ignored
    assert code == 0 and lines[-1]["reason"] == "eof" and lines[-1]["audio_ms"] == 10
