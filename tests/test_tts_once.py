"""Native TTS wire checks use only synthetic stdin, never a speaker or station."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_asr_once import envelope, field

SOURCE = Path(__file__).resolve().parents[1] / "src/alice_mcp/native/asr_once.c"
NONCE = "a" * 32


@pytest.fixture(scope="module")
def tts_client(tmp_path_factory: Any) -> Path:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        pytest.skip("Native TTS checks need an existing host C compiler")
    binary = tmp_path_factory.mktemp("tts-native") / "tts-test"
    result = subprocess.run([
        compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DASR_ONCE_TEST", "-DASR_ONCE_TTS",
        "-Os", "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wshadow", "-Wstrict-prototypes",
        str(SOURCE), "-o", str(binary),
    ], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    return binary


def input_text(text: bytes) -> bytes:
    return len(text).to_bytes(4, "little") + text


def event(method: int, request_id: bytes = NONCE.encode(), *, full: bool = False,
          capability: bytes = b"AliceCapability") -> bytes:
    listener = field(1, method) + field(11, request_id)
    return envelope(field(7, capability) + field(18, listener), full_message=full)


def test_tts_frame_contains_only_literal_text_append_and_the_correlated_request(tts_client: Path):
    text = 'Тест "кавычек", \\ и $(ничего не выполнять)'.encode()
    result = subprocess.run([str(tts_client), "--test-tts-frame", NONCE],
                            input=input_text(text), capture_output=True, timeout=5)
    assert result.returncode == 0, result.stdout.decode()
    directive = field(1, text) + field(2, 1)  # Append, never Interrupt.
    method = field(1, 18) + field(3, 1) + field(10, NONCE.encode()) + field(13, directive)
    remote = field(7, b"AliceCapability") + field(8, method)
    # Native IPC consumes PackedMessage (252 payload / 253 type), not a full
    # QuasarMessage with field 210. The ASR subscription already uses this envelope.
    expected = envelope(remote)
    assert bytes.fromhex(result.stdout.decode().strip()) == expected


@pytest.mark.parametrize("full", [False, True])
def test_only_correlated_start_then_complete_confirms_tts(tts_client: Path, full: bool):
    stream = (event(1, full=full) + event(6, b"another", full=full)
              + event(1, b"another", full=full) + event(6, full=full) + event(1, full=full))
    result = subprocess.run([str(tts_client), "--test-tts-stream", NONCE],
                            input=input_text(b"synthetic") + stream, capture_output=True, timeout=5)
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data == {"outcome": "complete", "request_sent": True,
                    "request_may_have_been_sent": True, "tts_started": True, "tts_completed": True}
    assert b"synthetic" not in result.stdout and b"another" not in result.stdout


@pytest.mark.parametrize("stream", [event(1), event(6, b"other") + event(1, b"other"),
    event(6, capability=b"OtherCapability") + event(1, capability=b"OtherCapability"),
    event(6), b"\x00\x00\x00\x00"])
def test_unrelated_or_incomplete_events_cannot_confirm_playback(tts_client: Path, stream: bytes):
    result = subprocess.run([str(tts_client), "--test-tts-stream", NONCE],
                            input=input_text(b"PRIVATE_SENTINEL") + stream,
                            capture_output=True, timeout=5)
    data = json.loads(result.stdout)
    assert result.returncode != 0 and data["outcome"] == "unknown" and not data["tts_completed"]
    assert data["request_may_have_been_sent"] is True
    assert b"PRIVATE_SENTINEL" not in result.stdout


@pytest.mark.parametrize("text", [b"", b"x" * 257, b"x" * 513, b"\xc0\xaf", b"nul\0text",
                                b"line\nbreak", "a\u202eb".encode()])
def test_invalid_text_cannot_open_ipc_or_make_sound(tts_client: Path, text: bytes):
    result = subprocess.run([str(tts_client), "--say", "20", NONCE], input=input_text(text),
                            capture_output=True, timeout=5)
    data = json.loads(result.stdout)
    assert result.returncode != 0 and data["request_may_have_been_sent"] is False
    assert data["request_sent"] is False and data["tts_completed"] is False


def test_tts_binary_does_not_enable_a_listen_mode(tts_client: Path):
    result = subprocess.run([str(tts_client), "--listen", "5"], capture_output=True, timeout=5)
    assert result.returncode != 0 and not result.stdout


def test_production_build_has_no_synthetic_modes(tmp_path: Path):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("Existing host compiler required")
    binary = tmp_path / "tts-production"
    subprocess.run([compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DASR_ONCE_TTS",
                    "-Os", "-Wall", "-Wextra", "-Werror", str(SOURCE), "-o", str(binary)],
                   capture_output=True, check=True)
    content = binary.read_bytes()
    assert b"--test-tts-frame" not in content and b"--test-tts-stream" not in content
    assert b"--listen" not in content


def test_shell_tts_receives_exact_stdin_and_cleans_up(tmp_path: Path, monkeypatch: Any):
    from alice_mcp import speech_capture

    data = tmp_path / "data"
    (data / "opt").mkdir(parents=True)
    firmware = tmp_path / "maind"
    firmware.write_bytes(b"\x7fELF\x01\x01" + bytes(12) + b"\x28\x00")
    text = b'literal "quote" and $(not a command)'
    packet = input_text(text)
    packet_sha = hashlib.sha256(packet).hexdigest()
    fake = ("#!/bin/sh\n[ \"$1\" = --say ] && [ \"$2\" = 20 ] || exit 1\n"
            '[ "$(cat | sha256sum | cut -d " " -f 1)" = "' + packet_sha + '" ] || exit 1\n'
            "printf '%s\\n' '{\"outcome\":\"complete\",\"request_sent\":true,"
            '\"request_may_have_been_sent\":true,\"tts_started\":true,\"tts_completed\":true}\'\n'
            ).encode()
    digest = hashlib.sha256(fake).hexdigest()
    monkeypatch.setattr(speech_capture, "TTS_CLIENT_SHA256", digest)
    policy = {"firmware_sha256": hashlib.sha256(firmware.read_bytes()).hexdigest(),
              "allowed_actions": ["speech.say"],
              "tts_client": {"path": str(tmp_path / "fake"), "sha256": digest}}
    request = {"schema": "alice.device.v1", "mode": "inspect", "model": "station-mini",
               "policy": policy, "action": "speech.say", "params": {"text": text.decode()},
               "nonce": NONCE}

    def execute(command: Any) -> subprocess.CompletedProcess:
        source = command.data.decode().replace("OWNER_UID=0", "OWNER_UID=" + str(os.getuid()))
        source = source.replace("DATA=/data", "DATA=" + str(data))
        source = source.replace("/system/vendor/quasar/maind", str(firmware))
        source = source.replace('pidof maind >/dev/null', 'true')
        source = source.replace('ipc || blocked', 'true || blocked')
        return subprocess.run(shlex.split(command.command), input=source.encode(),
                              capture_output=True, check=True, timeout=5)

    inspected = execute(speech_capture.command(request))
    assert not list((data / "opt").iterdir())
    request.update(mode="apply", expected_sha256=json.loads(inspected.stdout)["state_sha256"])
    result = execute(speech_capture.command(request, fake))
    parsed = speech_capture._tts_result(result.stdout.decode())
    assert parsed["outcome"] == "applied" and parsed["cleanup_verified"]
    assert parsed["quasar_running"] and not list((data / "opt").iterdir())
    assert not result.stderr and text not in result.stdout
