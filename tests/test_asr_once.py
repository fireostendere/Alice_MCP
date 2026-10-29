"""Synthetic native IPC streams only; never opens a station or records audio."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src/alice_mcp/native/asr_once.c"


def varint(value: int) -> bytes:
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    return bytes((*out, value))


def field(number: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return varint(number * 8) + varint(value)
    return varint(number * 8 + 2) + varint(len(value)) + value


def envelope(remote: bytes, kind: int = 210, full_message: bool = False) -> bytes:
    data = field(kind, remote) if full_message else field(252, remote) + field(253, kind)
    return len(data).to_bytes(4, "little") + data


def state(value: int, request_id: bytes = b"old", text: bytes | None = None,
          final: bool = False, response: bytes | None = None, direct: bool = False,
          full_message: bool = False) -> bytes:
    body = field(1, value) + field(7, request_id)
    if text is not None:
        body += field(15, field(1, text) + field(2, int(final)))
    if response is not None:
        body += field(14, field(1, response) + field(3, 1))
    if direct:
        return envelope(body, kind=3, full_message=full_message)
    return envelope(field(7, b"AliceCapability") + field(18, field(1, 0) + field(2, body)),
                    full_message=full_message)


@pytest.fixture(scope="module")
def client(tmp_path_factory: Any) -> Path:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        pytest.skip("Native ASR parser checks require a host C compiler; no automatic install")
    assert SOURCE.is_file(), "The one-shot text client must exist before it can be exercised"
    binary = tmp_path_factory.mktemp("asr-native") / "asr-once-test"
    subprocess.run([compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DASR_ONCE_TEST",
                    "-O2", "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wshadow",
                    "-Wstrict-prototypes", "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2",
                    str(SOURCE), "-o", str(binary)], check=True, capture_output=True)
    return binary


def run(client: Path, data: bytes) -> tuple[int, list[dict[str, Any]], str]:
    result = subprocess.run([str(client), "--test-stream", "5"], input=data,
                            capture_output=True, timeout=8)
    text = result.stdout.decode("utf-8")
    events = [json.loads(line) for line in text.splitlines()]
    return result.returncode, [e for e in events if e["outcome"] != "awaiting_idle"], text


@pytest.mark.parametrize("direct,full_message", [(False, False), (True, False),
                                               (False, True), (True, True)])
def test_only_new_final_request_text_is_emitted(client: Path, direct: bool,
                                              full_message: bool) -> None:
    expected = "контрольная фраза".encode()
    options = {"direct": direct, "full_message": full_message}
    data = (state(0, text=b"OLD_PRIVATE_SENTINEL", final=True, **options)
            + state(1, b"new", **options)
            + state(3, b"new", text=b"PARTIAL_SENTINEL", **options)
            + state(2, b"new", response=b"RESPONSE_SENTINEL", **options)
            + state(2, b"new", text=expected, final=True, **options))
    code, events, raw = run(client, data)
    assert code == 0
    assert events[0]["outcome"] == "ready" and events[0]["capture_ready"] is True
    assert 0 < events[0]["remaining_seconds"] <= 5
    assert events[-1] == {"outcome": "complete", "capture_ready": False,
                          "text": expected.decode()}
    assert not any(word in raw for word in ["OLD_PRIVATE", "PARTIAL_SENTINEL",
                                            "RESPONSE_SENTINEL", '"new"'])


def test_request_id_may_arrive_after_fresh_listening(client: Path) -> None:
    code, events, _ = run(client, state(0) + state(1, b"")
                          + state(3, b"new", text=b"synthetic", final=True))
    assert code == 0 and events[-1]["text"] == "synthetic"


def test_direct_baseline_and_remoting_final_are_the_same_correlated_request(client: Path) -> None:
    data = (state(0, direct=True) + state(1, b"fresh", direct=True)
            + state(3, b"fresh", text=b"synthetic", final=True))
    code, events, _ = run(client, data)
    assert code == 0 and events[-1]["text"] == "synthetic"


def test_other_capability_cannot_supply_alice_text(client: Path) -> None:
    body = (field(1, 3) + field(7, b"fresh")
            + field(15, field(1, b"OTHER_PRIVATE_SENTINEL") + field(2, 1)))
    other = envelope(field(7, b"OtherCapability") + field(18, field(1, 0) + field(2, body)))
    code, events, raw = run(client, state(0) + state(1, b"fresh") + other)
    assert code != 0 and "OTHER_PRIVATE_SENTINEL" not in raw
    assert not any("text" in event for event in events)


def test_first_final_closes_capture_before_any_later_phrase(client: Path) -> None:
    data = (state(0) + state(1, b"first") + state(3, b"first", text=b"first input", final=True)
            + state(1, b"second") + state(3, b"second", text=b"LATER_PRIVATE_SENTINEL", final=True))
    code, events, raw = run(client, data)
    assert code == 0 and len(events) == 2 and events[-1]["text"] == "first input"
    assert "LATER_PRIVATE_SENTINEL" not in raw


@pytest.mark.parametrize("tail", [
    state(2, b"old", text=b"OLD_PRIVATE_SENTINEL", final=True),
    state(3, b"new", text=b"NO_LISTENING_SENTINEL", final=True),
    state(1, b"new") + state(3, b"new", text=b"PARTIAL_SENTINEL"),
    state(1, b"new") + state(2, b"new", response=b"RESPONSE_SENTINEL"),
])
def test_cached_partial_and_response_text_never_count_as_asr(client: Path, tail: bytes) -> None:
    code, events, raw = run(client, state(0) + tail)
    assert code != 0 and not any("text" in event for event in events)
    assert "SENTINEL" not in raw


def test_warmup_is_discarded_until_idle_then_only_next_phrase_counts(client: Path) -> None:
    data = (state(1, b"warmup", text=b"PRIVATE_SENTINEL", final=True)
            + state(2, b"warmup", response=b"PRIVATE_SENTINEL")
            + state(0, b"warmup", text=b"PRIVATE_SENTINEL", final=True)
            + state(1, b"fresh") + state(3, b"fresh", text=b"new input", final=True))
    code, events, raw = run(client, data)
    assert code == 0 and events[0]["outcome"] == "ready"
    assert events[-1]["text"] == "new input"
    assert "PRIVATE_SENTINEL" not in raw


def test_waiting_for_initial_idle_is_explicitly_not_capture_readiness(client: Path) -> None:
    _, _, raw = run(client, state(1, b"warmup", text=b"PRIVATE_SENTINEL", final=True))
    events = [json.loads(line) for line in raw.splitlines()]
    assert events[0]["outcome"] == "awaiting_idle" and events[0]["capture_ready"] is False
    assert 0 < events[0]["remaining_seconds"] <= 5
    assert not any(event.get("capture_ready") for event in events)
    assert events[-1]["diagnostics"] == {
        "frames_received": 1, "direct_states": 0, "listener_states": 1,
    }


def test_request_change_stops_instead_of_capturing_another_conversation(client: Path) -> None:
    data = state(0) + state(1, b"first") + state(3, b"second", text=b"OTHER_SENTINEL", final=True)
    code, events, raw = run(client, data)
    assert code != 0 and events[-1]["error_code"] == "request_changed"
    assert "OTHER_SENTINEL" not in raw and not any("text" in event for event in events)


def test_return_to_idle_without_final_stops_the_window(client: Path) -> None:
    data = (state(0) + state(1, b"first") + state(0, b"first")
            + state(1, b"second") + state(2, b"second", text=b"OTHER_SENTINEL", final=True))
    code, events, raw = run(client, data)
    assert code != 0 and events[-1]["error_code"] == "no_final_text"
    assert "OTHER_SENTINEL" not in raw


@pytest.mark.parametrize("text", [b"", b"x" * 513, b"x" * 257, b"private\x00text",
                                b"line\nbreak", b"\xc0\xaf", b"\xed\xa0\x80",
                                b"\xf4\x90\x80\x80", "a\u202eb".encode()])
def test_invalid_or_excessive_text_is_not_exported(client: Path, text: bytes) -> None:
    code, events, _ = run(client, state(0) + state(1, b"new")
                          + state(3, b"new", text=text, final=True))
    assert code != 0 and events[-1]["error_code"] == "invalid_text"
    assert not any("text" in event for event in events)


def test_json_escaping_and_unicode_are_lossless(client: Path) -> None:
    text = 'слово "тест" \\'.encode()
    code, events, _ = run(client, state(0) + state(1, b"new")
                          + state(3, b"new", text=text, final=True))
    assert code == 0 and events[-1]["text"] == text.decode()


@pytest.mark.parametrize("data", [b"\0\0\0\0", (65537).to_bytes(4, "little"), b"\x01",
                                b"\x01\0\0\0\x00", b"\x01\0\0\0\xff"])
def test_invalid_frames_fail_closed_without_dumping_bytes(client: Path, data: bytes) -> None:
    code, events, _ = run(client, data)
    assert code != 0 and events[-1]["outcome"] == "blocked"
    assert not any("text" in event for event in events)


def test_ambiguous_duplicate_envelope_type_fails_closed(client: Path) -> None:
    payload = field(253, 3) + field(253, 210) + field(252, b"PRIVATE_SENTINEL")
    code, events, raw = run(client, len(payload).to_bytes(4, "little") + payload)
    assert code != 0 and events[-1]["error_code"] == "protocol_error"
    assert "PRIVATE_SENTINEL" not in raw


def test_silent_stream_expires_without_claiming_readiness(client: Path) -> None:
    process = subprocess.Popen([str(client), "--test-stream", "5"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.wait(timeout=7) != 0
        assert process.stdout is not None
        events = [json.loads(line) for line in process.stdout.read().decode().splitlines()]
        assert events[0]["outcome"] == "awaiting_idle" and not events[0]["capture_ready"]
        assert events[-1] == {"outcome": "blocked", "capture_ready": False,
                              "error_code": "timeout", "diagnostics": {
                                  "frames_received": 0, "direct_states": 0, "listener_states": 0,
                              }}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        for stream in [process.stdin, process.stdout, process.stderr]:
            if stream is not None:
                stream.close()


def test_subscription_has_no_voice_start_tts_or_record_request(client: Path) -> None:
    result = subprocess.run([str(client), "--test-subscription"], capture_output=True,
                            text=True, check=True, timeout=5)
    remote = field(7, b"AliceCapability") + field(20, field(1, b"AliceCapability"))
    assert bytes.fromhex(result.stdout.strip()) == envelope(remote, kind=210)


@pytest.mark.parametrize("seconds", ["0", "4", "61", "-1", "forever", "5;id"])
def test_invalid_window_is_rejected_before_connecting(client: Path, seconds: str) -> None:
    result = subprocess.run([str(client), "--listen", seconds], capture_output=True,
                            text=True, timeout=5)
    assert result.returncode != 0 and "ready" not in result.stdout
