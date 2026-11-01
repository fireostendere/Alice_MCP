"""Stock command-spotter events from generated IPC frames; never opens a station."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_asr_once import SOURCE, envelope, field

NAME = b"CommandSpotterCapability"


@pytest.fixture(scope="module")
def spotter_client(tmp_path_factory: Any) -> Path:
    compiler = shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        pytest.skip("Existing host C compiler required")
    binary = tmp_path_factory.mktemp("spotter-native") / "spotter-test"
    result = subprocess.run([
        compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DASR_ONCE_TEST",
        "-DASR_ONCE_SPOTTER", "-Os", "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wshadow",
        "-Wstrict-prototypes", str(SOURCE), "-o", str(binary),
    ], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    return binary


def spotted(phrase: bytes, method: int = 2, *, full: bool = True) -> bytes:
    return envelope(field(7, NAME) + field(15, field(1, method) + field(3, phrase)),
                    full_message=full)


def test_only_allowlisted_spotted_phrases_leave_the_station(spotter_client: Path) -> None:
    stream = (envelope(field(7, b"AliceCapability") + field(18, field(1, 0)), full_message=True)
              + spotted("хватит".encode())
              + spotted(b"PRIVATE_SENTINEL")
              + spotted("громче".encode(), method=1)
              + spotted("включи свет".encode(), full=False)
              + envelope(field(7, b"OtherCapability") + field(15, field(1, 2) + field(3, b"x")),
                         full_message=True))
    result = subprocess.run([str(spotter_client), "--test-spotter-stream"], input=stream,
                             capture_output=True, timeout=8)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode == 0
    assert lines == [
        {"event": "spotter", "phrase": "хватит", "trusted": False},
        {"event": "spotter", "phrase": "включи свет", "trusted": False},
        {"event": "end", "outcome": "complete", "reason": "stopped", "spotted": 2},
    ]
    assert b"PRIVATE" not in result.stdout + result.stderr


def test_malformed_frame_ends_as_unknown(spotter_client: Path) -> None:
    broken = b"\x03\x00\x00\x00\xff\xff\xff"
    result = subprocess.run([str(spotter_client), "--test-spotter-stream"],
                             input=spotted("стоп".encode()) + broken,
                             capture_output=True, timeout=8)
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert result.returncode != 0
    assert lines[0] == {"event": "spotter", "phrase": "стоп", "trusted": False}
    assert lines[-1]["event"] == "end" and lines[-1]["outcome"] == "unknown"


def test_spotter_subscription_is_only_add_listener(spotter_client: Path) -> None:
    result = subprocess.run([str(spotter_client), "--test-subscription"],
                             capture_output=True, check=True, timeout=5)
    assert bytes.fromhex(result.stdout.decode()) == envelope(field(7, NAME)
                                                              + field(20, field(1, NAME)))


def test_production_spotter_binary_has_only_bounded_watch(tmp_path: Path) -> None:
    compiler = shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        pytest.skip("Existing host C compiler required")
    binary = tmp_path / "spotter-production"
    subprocess.run([compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DASR_ONCE_SPOTTER",
                    "-Os", "-Wall", "-Wextra", "-Werror", str(SOURCE), "-o", str(binary)],
                   capture_output=True, check=True, timeout=30)
    for args in [["--listen", "5"], ["--watch-mute"], ["--test-spotter-stream"],
                 ["--test-subscription"], ["--watch-spotter", "0"],
                 ["--watch-spotter", "3661"], ["--watch-spotter", "5x"], []]:
        result = subprocess.run([str(binary), *args], input=b"", capture_output=True, timeout=3)
        assert result.returncode == 2 and result.stdout == b""


WORDS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
         "десять"]
TEST_ID = b"00000000-0000-4000-8000-000000000000"


def text_request(text: str) -> bytes:
    event = ('{"type":"text_input","text":"' + text + '"}').encode()
    vins = field(1, TEST_ID) + field(2, event) + field(18, 1)
    return envelope(field(7, b"AliceCapability") + field(8, field(1, 0) + field(2, vins)),
                    full_message=True)


def activation(*methods: int) -> bytes:
    name = b"ActivationSpotterCapability"
    return b"".join(envelope(field(7, name) + field(14, field(1, m)), full_message=True)
                    for m in methods)


@pytest.mark.parametrize("args,expected", [
    (["--send-volume-level", "3"], text_request("громкость " + WORDS[3])),
    (["--send-volume-level", "0"], text_request("громкость " + WORDS[0])),
    (["--send-volume-level", "10"], text_request("громкость " + WORDS[10])),
    (["--send-volume-step", "louder"], text_request("громче")),
    (["--send-volume-step", "quieter"], text_request("тише")),
    (["--send-activation", "off"], activation(5)),
    (["--send-activation", "on"], activation(4, 2)),
])
def test_control_frames_are_fixed_stock_requests_in_full_message_form(
    spotter_client: Path, args: list[str], expected: bytes,
) -> None:
    result = subprocess.run([str(spotter_client), "--test-frame", *args],
                             capture_output=True, check=True, timeout=5)
    assert bytes.fromhex(result.stdout.decode()) == expected


@pytest.mark.parametrize("args", [["--send-volume-level", "11"], ["--send-volume-level", "x"],
                                  ["--send-volume-step", "up"], ["--send-activation", "maybe"],
                                  ["--send-text", "купи слона"]])
def test_control_rejects_anything_outside_the_fixed_table(
    spotter_client: Path, args: list[str],
) -> None:
    result = subprocess.run([str(spotter_client), "--test-frame", *args],
                             capture_output=True, timeout=5)
    assert result.returncode == 2 and result.stdout == b""
