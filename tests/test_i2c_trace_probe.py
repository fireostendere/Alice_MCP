"""Synthetic tracepoint discovery only; never enable tracing or read trace buffers."""

import subprocess
from pathlib import Path

import pytest

from alice_mcp import core


def test_i2c_trace_probe_is_fixed_and_read_only() -> None:
    command = core.PROBES["i2c_trace"]
    subprocess.run(["sh", "-n", "-c", command], check=True)
    for forbidden in (">", "mount ", "trace_pipe", "i2cget", "i2cset", "i2cdetect", "kill"):
        assert forbidden not in command
    assert "/sys/kernel/tracing" in command
    assert "/sys/kernel/debug/tracing" in command


def test_i2c_trace_probe_on_synthetic_tracefs(tmp_path: Path) -> None:
    root = tmp_path / "sys/kernel/tracing"
    write = root / "events/i2c/i2c_write"
    write.mkdir(parents=True)
    (root / "available_events").write_text("i2c:i2c_write\ni2c:i2c_result\n")
    (root / "tracing_on").write_text("0\n")
    (root / "instances").mkdir()
    (write / "enable").write_text("0\n")
    (write / "format").write_text("field:__data_loc u8[] buf; offset:24; size:4; signed:0;\n")
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    command = core.PROBES["i2c_trace"].replace("/sys/", str(tmp_path) + "/sys/")
    completed = subprocess.run(["sh", "-c", command], capture_output=True, text=True, check=True)
    result = core.parse_probe("i2c_trace", completed.stdout + "private=SENSITIVE_SENTINEL\n")
    assert result["tracefs_write_event"] is True
    assert result["tracefs_write_payload"] is True
    assert result["tracefs_write_enable_writable"] is True
    assert result["tracefs_read_event"] is False
    assert result["debugfs_events_readable"] is False
    assert result["capture_verified"] is False
    assert "SENSITIVE_SENTINEL" not in str(result)
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("text", ["", "tracefs_directory=2\n", "x=1\nx=0\n"])
def test_i2c_trace_probe_rejects_bad_or_incomplete_output(text: str) -> None:
    with pytest.raises(core.SafetyError):
        core.parse_probe("i2c_trace", text)
