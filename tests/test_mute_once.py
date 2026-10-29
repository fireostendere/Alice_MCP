"""Read-only microphone-mute protocol, using only generated capability snapshots."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_asr_once import SOURCE, envelope, field


@pytest.fixture(scope="module")
def mute_client(tmp_path_factory: Any) -> Path:
    compiler = shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        pytest.skip("Existing host C compiler required")
    binary = tmp_path_factory.mktemp("mute-native") / "mute-test"
    result = subprocess.run([
        compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DASR_ONCE_TEST", "-DASR_ONCE_MUTE",
        "-Os", "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wshadow", "-Wstrict-prototypes",
        str(SOURCE), "-o", str(binary),
    ], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    return binary


def snapshot(mute_field: bytes, *, full: bool = False, cap_type: int = 17) -> bytes:
    device = field(1, b"PRIVATE_DEVICE_SENTINEL") + mute_field
    capability = field(3, field(1, device))
    holder = field(1, capability) + field(2, cap_type)
    stored = field(1, b"DeviceStateCapability") + field(6, holder)
    return envelope(field(7, b"DeviceStateCapability") + field(3, field(2, stored)),
                    full_message=full)


@pytest.mark.parametrize("muted,full", [(False, False), (True, False), (False, True), (True, True)])
def test_mute_needs_explicit_typed_snapshot_and_emits_no_private_state(
    mute_client: Path, muted: bool, full: bool,
) -> None:
    result = subprocess.run([str(mute_client), "--test-mute-stream"],
                             input=snapshot(field(26, int(muted)), full=full),
                             capture_output=True, timeout=8)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"outcome": "complete",
                                       "mute_state": "muted" if muted else "unmuted",
                                       "current_observation_only": True, "audio_captured": False}
    assert b"PRIVATE" not in result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["missing", "duplicate", "invalid", "wrong_capability"])
def test_mute_missing_or_ambiguous_is_unknown_never_unmuted(mute_client: Path, mode: str) -> None:
    mute_field = b"" if mode == "missing" else field(26, 0)
    if mode == "duplicate":
        mute_field += field(26, 1)
    elif mode == "invalid":
        mute_field = field(26, 2)
    cap_type = 1 if mode == "wrong_capability" else 17
    result = subprocess.run([str(mute_client), "--test-mute-stream"],
                             input=snapshot(mute_field, cap_type=cap_type),
                             capture_output=True, timeout=8)
    value = json.loads(result.stdout)
    assert result.returncode != 0 and value["mute_state"] == "unknown"
    assert value["audio_captured"] is False


def test_mute_subscription_never_sends_mute_or_recognition_commands(mute_client: Path) -> None:
    result = subprocess.run([str(mute_client), "--test-subscription"],
                             capture_output=True, check=True, timeout=5)
    name = b"DeviceStateCapability"
    expected = envelope(field(7, name) + field(20, field(1, name)))
    assert bytes.fromhex(result.stdout.decode()) == expected


def test_production_mute_binary_has_no_capture_or_synthetic_cli(tmp_path: Path) -> None:
    compiler = shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        pytest.skip("Existing host C compiler required")
    binary = tmp_path / "mute-production"
    subprocess.run([compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DASR_ONCE_MUTE", "-Os",
                    "-Wall", "-Wextra", "-Werror", str(SOURCE), "-o", str(binary)],
                   capture_output=True, check=True, timeout=30)
    for args in [["--listen", "5"], ["--say", "20", "a" * 32], ["--test-mute-stream"],
                 ["--test-subscription"], []]:
        result = subprocess.run([str(binary), *args], input=b"", capture_output=True, timeout=3)
        assert result.returncode == 2 and result.stdout == b""


def test_mute_watch_reports_both_states_without_exporting_capability_payload(
    mute_client: Path,
) -> None:
    stream = snapshot(field(26, 0)) + snapshot(field(26, 1)) + snapshot(field(26, 0))
    result = subprocess.run([str(mute_client), "--test-mute-watch"], input=stream,
                             capture_output=True, timeout=5)
    assert result.returncode == 0
    value = json.loads(result.stdout)
    assert value["mute_state"] == "unmuted" and value["seen_states_bitmap"] == 3
    assert value["observations"] == 3 and value["audio_captured"] is False
    assert b"PRIVATE" not in result.stdout + result.stderr
