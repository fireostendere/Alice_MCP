"""live_client.c against a local fake Realtime server over real TLS; no station or cloud."""

import base64
import ctypes.util
import hashlib
import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

SOURCE = Path(__file__).parents[1] / "src/alice_mcp/native/live_client.c"
SECRET = "synthetic_secret"


def test_fixed_firmware_identity_tracks_nanoseconds_but_not_access_time(tmp_path: Path) -> None:
    firmware = tmp_path / "maind"
    firmware.write_bytes(b"synthetic firmware")
    built = tmp_path / "identity-client"
    subprocess.run(["cc", "-O2", "-Wall", "-Wextra", "-Werror",
                    f'-DLIVE_FIRMWARE_PATH="{firmware}"', str(SOURCE), "-ldl", "-o", str(built)],
                   check=True, capture_output=True)

    def identity() -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(built), "--firmware-state"], capture_output=True,
                              text=True, timeout=5)

    before = identity()
    assert before.returncode == 0 and before.stdout.strip() and not before.stderr
    firmware.read_bytes()  # An access-time update must not invalidate cached content.
    assert identity().stdout == before.stdout
    stamp = firmware.stat()
    os.utime(firmware, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1))
    assert identity().stdout != before.stdout
    firmware.unlink()
    firmware.symlink_to(tmp_path / "missing")
    denied = identity()
    assert denied.returncode != 0 and not denied.stdout and not denied.stderr


def _recv_exact(conn: ssl.SSLSocket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise ConnectionError
        data += chunk
    return data


def _read_frame(conn: ssl.SSLSocket) -> tuple[int, bytes]:
    head = _recv_exact(conn, 2)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", _recv_exact(conn, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recv_exact(conn, 8))[0]
    assert head[1] & 0x80  # Client frames are masked.
    mask = _recv_exact(conn, 4)
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(_recv_exact(conn, length)))
    return head[0] & 0x0F, payload


def _send(conn: ssl.SSLSocket, event: Any) -> None:
    if callable(event):
        event = event()
    if event == {"ping": True}:
        conn.sendall(b"\x89\x00")  # A server keepalive between turns.
        return
    data = json.dumps(event).encode()
    header = bytes([0x81]) + (bytes([len(data)]) if len(data) < 126
                              else bytes([126]) + struct.pack(">H", len(data)))
    conn.sendall(header + data)


class Server:
    """One accepted session: scripted events after the opener, then idle."""

    def __init__(self, cert: Path, key: Path, script: list[dict[str, Any]],
                  later: list[dict[str, Any]] | None = None, *,
                  on_connect: list[dict[str, Any]] | None = None,
                  on_update: list[dict[str, Any]] | None = None) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.listener = socket.create_server(("localhost", 0))
        self.port = self.listener.getsockname()[1]
        self.context, self.script, self.later = context, script, later or []
        self.on_connect = on_connect or []
        self.on_update = on_update or []
        self.responses = 0
        self.updates = 0
        self.request = b""
        self.received: list[dict[str, Any]] = []
        self.pongs = 0
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        raw, _ = self.listener.accept()
        with self.context.wrap_socket(raw, server_side=True) as conn:
            while b"\r\n\r\n" not in self.request:
                self.request += conn.recv(4096)
            key = [line.split(b": ", 1)[1] for line in self.request.split(b"\r\n")
                   if line.lower().startswith(b"sec-websocket-key")][0]
            accept = base64.b64encode(hashlib.sha1(
                key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                         b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            _send(conn, {"type": "session.created", "session": {}})
            for item in self.on_connect:  # Server VAD answers buffered speech by itself.
                _send(conn, item)
            try:
                while True:
                    opcode, payload = _read_frame(conn)
                    if opcode == 8:
                        return
                    if opcode == 10:
                        self.pongs += 1
                        continue
                    event = json.loads(payload)
                    self.received.append(event)
                    if event["type"] == "session.update":
                        self.updates += 1
                        _send(conn, {"type": "session.updated"})
                        for item in self.on_update if self.updates == 1 else []:
                            _send(conn, item)
                    if event["type"] == "response.create":
                        self.responses += 1
                        for item in self.script if self.responses == 1 else self.later:
                            _send(conn, item)
            except (ConnectionError, ssl.SSLError, OSError):
                return


@pytest.fixture
def built(tmp_path: Path) -> Path:
    compiler, library = shutil.which("cc"), ctypes.util.find_library("ssl")
    if not compiler or not library or not shutil.which("openssl"):
        pytest.skip("Live client checks need a C compiler, OpenSSL and its CLI")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                    "-keyout", str(tmp_path / "key.pem"), "-out", str(tmp_path / "cert.pem")],
                   check=True, capture_output=True, timeout=60)
    (tmp_path / "player").write_text(
        '#!/bin/sh\ncat > "$(dirname "$0")/played.bin"\n'
        'echo \'{"outcome":"complete","frames_written":4,"flushes":1}\'\n')
    (tmp_path / "capture").write_text(
        "#!/bin/sh\ni=0; while [ $i -lt 30 ]; do head -c 3200 /dev/zero; sleep 0.1; "
        "i=$((i+1)); done\n")
    (tmp_path / "player").chmod(0o700)
    (tmp_path / "capture").chmod(0o700)
    return tmp_path


def _client(built: Path, port: int) -> Path:
    binary = built / f"live-client-{port}"
    subprocess.run([shutil.which("cc") or "cc", "-O2", "-Wall", "-Wextra", "-Werror",
                    f'-DLIVE_SSL_LIBRARY="{ctypes.util.find_library("ssl")}"',
                    '-DLIVE_HOST="localhost"', f'-DLIVE_PORT="{port}"', str(SOURCE), "-ldl",
                    "-o", str(binary)], check=True, capture_output=True, timeout=60)
    return binary


def _frames(data: bytes) -> list[tuple[str, int]]:
    out, i = [], 0
    while i < len(data):
        kind, length = chr(data[i]), struct.unpack("<I", data[i + 1:i + 5])[0]
        out.append((kind, length))
        i += 5 + length
    return out


def _run(built: Path, script: list[dict[str, Any]], *args: str,
         opening: str = 'давай "поговорим"', control: bytes = b"") -> tuple[Server, list[Any]]:
    server = Server(built / "cert.pem", built / "key.pem", script)
    client = _client(built, server.port)
    stdin = (SECRET + "\n" + opening + "\n").encode() + control
    process = subprocess.Popen([str(client), "--player", str(built / "player"),
                                "--ca", str(built / "cert.pem"), "--idle-ms", "300", *args],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert process.stdin is not None
    process.stdin.write(stdin)
    process.stdin.flush()
    output, _ = process.communicate(timeout=20) if control else _wait_end(process)
    server.thread.join(5)
    assert SECRET.encode() not in output
    return server, [json.loads(line) for line in output.splitlines()]


def _wait_end(process: subprocess.Popen[bytes]) -> tuple[bytes, None]:
    assert process.stdout is not None
    lines = b""
    watchdog = threading.Timer(15, process.kill)  # A hung client fails instead of hanging.
    watchdog.start()
    for line in process.stdout:
        lines += line
        if b'"event":"end"' in line:
            break
    watchdog.cancel()
    assert process.stdin is not None
    process.stdin.close()
    process.wait(10)
    return lines, None


def test_reply_streams_continuously_and_barge_in_flushes_the_player(built: Path) -> None:
    delta = base64.b64encode(b"\1\0" * 2400).decode()  # 100 ms at 24 kHz.
    server, events = _run(built, [
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": delta},
        {"type": "response.output_audio.delta", "delta": delta},
        {"type": "input_audio_buffer.speech_started"},
        {"type": "response.done", "response": {"status": "cancelled"}},
        {"type": "input_audio_buffer.speech_stopped"},
    ], "--capture", str(built / "capture"))
    assert b"Authorization: Bearer " + SECRET.encode() in server.request
    assert [e["type"] for e in server.received[:2]] == ["conversation.item.create",
                                                         "response.create"]
    assert server.received[0]["item"]["content"][0]["text"] == 'давай "поговорим"'
    appended = [e for e in server.received if e["type"] == "input_audio_buffer.append"]
    assert appended and abs(len(base64.b64decode(appended[0]["audio"])) - 4800) <= 4  # 24 kHz.
    kinds = [e["event"] for e in events]
    assert kinds[:3] == ["connected", "ready", "first_audio"] and "speech_started" in kinds
    end = events[-1]
    assert end["event"] == "end" and end["reason"] == "idle" and end["barge_ins"] == 1
    assert end["player"]["outcome"] == "complete"
    frames = _frames((built / "played.bin").read_bytes())
    assert frames[0][0] == "A" and ("F", 0) in frames and frames[-1] == ("E", 0)
    assert sum(n for k, n in frames if k == "A") <= 2 * 9600 + 64  # 48 kHz, never more.


def test_speak_only_session_plays_whole_reply_then_ends(built: Path) -> None:
    delta = base64.b64encode(b"\1\0" * 4800).decode()
    _, events = _run(built, [{"type": "response.output_audio.delta", "delta": delta},
                             {"type": "response.done", "response": {"status": "completed"}}])
    assert events[-1]["reason"] == "idle"
    frames = _frames((built / "played.bin").read_bytes())
    assert sum(n for k, n in frames if k == "A") == 4800 * 4  # Every sample, doubled.
    assert "F" not in [k for k, _ in frames]


def test_voice_endpoint_is_reported_separately_from_first_audio(built: Path) -> None:
    delta = base64.b64encode(b"\1\0" * 2400).decode()
    _, events = _run(built, [{"type": "input_audio_buffer.speech_stopped"},
                             {"type": "response.output_audio.delta", "delta": delta},
                             {"type": "response.done", "response": {"status": "completed"}}])
    kinds = [e["event"] for e in events]
    assert kinds.index("speech_stopped") < kinds.index("first_audio")


def test_api_error_or_failed_reply_ends_as_failure(built: Path) -> None:
    _, events = _run(built, [{"type": "error", "error": {"message": "PRIVATE"}}])
    assert events[-1]["reason"] == "api_error" and "PRIVATE" not in json.dumps(events)
    _, events = _run(built, [{"type": "response.done", "response": {"status": "failed"}}])
    assert events[-1]["reason"] == "response_failed"


def test_stop_control_drops_the_reply(built: Path) -> None:
    delta = base64.b64encode(b"\1\0" * 24000).decode()  # One second, still playing.
    _, events = _run(built, [{"type": "response.output_audio.delta", "delta": delta}],
                     control=b"stop\n")
    assert events[-1]["reason"] == "stopped"
    frames = _frames((built / "played.bin").read_bytes())
    assert frames[-2:] == [("F", 0), ("E", 0)]


def test_keepalive_ping_in_a_quiet_turn_never_blocks_the_idle_end(built: Path) -> None:
    delta = base64.b64encode(b"\1\0" * 2400).decode()
    server, events = _run(built, [{"type": "response.output_audio.delta", "delta": delta},
                                  {"type": "response.done", "response": {"status": "completed"}},
                                  {"ping": True}], "--capture", str(built / "capture"))
    assert events[-1]["event"] == "end" and events[-1]["reason"] == "idle"
    assert server.pongs == 1


def test_tool_call_waits_for_the_host_result_then_answers(built: Path) -> None:
    call = {"type": "response.function_call_arguments.done", "call_id": "call_1",
            "name": "web_search", "arguments": json.dumps({"query": 'погода "Москва"'})}
    reply = base64.b64encode(b"\1\0" * 2400).decode()
    server = Server(built / "cert.pem", built / "key.pem",
                    [call, {"type": "response.done", "response": {"status": "completed"}}],
                    [{"type": "response.output_audio.delta", "delta": reply},
                     {"type": "response.done", "response": {"status": "completed"}}])
    process = subprocess.Popen([str(_client(built, server.port)), "--player", str(built / "player"),
                                "--ca", str(built / "cert.pem"), "--idle-ms", "300"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write((SECRET + "\nкакая погода\n").encode())
    process.stdin.flush()
    watchdog = threading.Timer(15, process.kill)
    watchdog.start()
    events = []
    for line in process.stdout:
        events.append(json.loads(line))
        if events[-1]["event"] in {"tool", "end"}:
            break
    watchdog.cancel()
    assert events[-1] == {"event": "tool", "message": call}  # Raw, for the host to check.
    time.sleep(1.0)  # Longer than --idle-ms: a pending tool keeps the session open.
    output = {"type": "conversation.item.create", "item": {
        "type": "function_call_output", "call_id": "call_1", "output": "Ясно, +3 «градуса»"}}
    process.stdin.write(b"tool " + json.dumps(output, ensure_ascii=False,
                                              separators=(",", ":")).encode() + b"\n")
    process.stdin.flush()
    rest, _ = _wait_end(process)
    end = json.loads(rest.splitlines()[-1])
    server.thread.join(5)
    assert end["reason"] == "idle" and server.responses == 2
    kinds = [e["type"] for e in server.received]
    assert output in server.received
    assert kinds[server.received.index(output) + 1] == "response.create"


def test_host_lines_other_than_a_function_output_end_the_session(built: Path) -> None:
    _, events = _run(built, [], control=b'tool {"type":"session.update"}\n')
    assert events[-1]["reason"] == "invalid_control"


DETECTOR = """#!/bin/sh
echo '{"event":"ready","models":[]}'
head -c 16000 >/dev/null
echo '{"event":"wake","model":"komp","t_ms":500,"probability":0.99,"peak":0.99,'\
'"last":0.99,"trusted":false}'
cat >/dev/null
"""
NUMBERED_CAPTURE = """#!/usr/bin/env python3
import struct, sys, time
for i in range(40):
    sys.stdout.buffer.write(struct.pack("<h", BASE + 100 * (i + 1)) * 1600)
    sys.stdout.buffer.flush()
    time.sleep(0.05)
"""


def _until(process: subprocess.Popen[bytes], event: str) -> list[dict[str, Any]]:
    assert process.stdout is not None
    seen = []
    watchdog = threading.Timer(15, process.kill)
    watchdog.start()
    for line in process.stdout:
        assert SECRET.encode() not in line
        seen.append(json.loads(line))
        if seen[-1]["event"] == event:
            break
    watchdog.cancel()
    return seen


def _standby(built: Path, base: int, verb: str = "session", *,
             silent_wake: bool = False) -> tuple[Server, list[dict[str, Any]]]:
    """A standby whose capture plays numbered blocks (base + 100 * block)."""
    (built / "detector").write_text(DETECTOR)
    (built / "numbered").write_text(NUMBERED_CAPTURE.replace("BASE", str(base)))
    (built / "detector").chmod(0o700)
    (built / "numbered").chmod(0o700)
    reply = base64.b64encode(b"\1\0" * 2400).decode()
    def delayed_silence() -> dict[str, Any]:
        time.sleep(0.5)  # The question starts while the server silently finishes the wake word.
        return {"type": "response.done", "response": {"status": "completed"}}

    wake = ([{"type": "input_audio_buffer.speech_stopped"}, delayed_silence]
            if silent_wake else [])
    server = Server(built / "cert.pem", built / "key.pem", [], on_connect=[
        *wake,
        {"type": "input_audio_buffer.speech_started"},
        {"type": "input_audio_buffer.speech_stopped"},
        {"type": "response.created"},
        {"type": "response.output_audio.delta", "delta": reply},
        {"type": "response.done", "response": {"status": "completed"}}])
    process = subprocess.Popen([str(_client(built, server.port)), "--player", str(built / "player"),
                                "--ca", str(built / "cert.pem"), "--idle-ms", "300",
                                "--wake-detector", str(built / "detector"),
                                "--capture", str(built / "numbered"), "--max-seconds", "60"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert process.stdin is not None
    seen = _until(process, "detector")
    seen += _until(process, "detector")  # ready, then the wake line, both raw for the host.
    assert seen[-1]["line"]["event"] == "wake" and seen[-1]["line"]["t_ms"] == 500
    time.sleep(0.6)  # The owner keeps talking while this PC mints the secret.
    process.stdin.write(f"{verb} gpt-realtime-2.1 30 {SECRET}\n".encode())
    process.stdin.flush()
    session = _until(process, "session_end")
    process.stdin.write(b"stop\n")
    process.stdin.flush()
    assert _until(process, "end")[-1]["reason"] == "stopped"  # Standby, not a session, ends.
    process.wait(10)
    return server, session


def test_one_shot_stops_sending_microphone_at_endpoint_and_drains_reply(built: Path) -> None:
    server, session = _standby(built, 9000, "ask")
    assert session[-1]["reason"] == "idle"
    assert session[-1]["player"]["outcome"] == "complete"
    assert session[-1]["mic"]["sent_ms"] == session[-1]["mic"]["buffered_ms"]
    assert any(e["event"] == "first_audio" for e in session)
    assert not any(e["type"] == "conversation.item.create" for e in server.received)


def test_question_during_silent_wake_response_is_not_discarded(built: Path) -> None:
    server, session = _standby(built, 9000, "ask", silent_wake=True)
    mic = session[-1]["mic"]
    assert mic["sent_ms"] >= mic["buffered_ms"] + 200
    assert any(e["event"] == "first_audio" for e in session)
    assert server.received


def test_standby_detects_then_sends_the_command_from_the_wake_word_and_waits_again(
        built: Path) -> None:
    server, session = _standby(built, 9000)  # Loud enough that no gain is applied.
    assert [e["event"] for e in session][:2] == ["connected", "ready"]
    assert session[-1]["reason"] == "idle" and session[-1]["player"]["outcome"] == "complete"
    mic = session[-1]["mic"]  # Numbers only: what the model was sent, never the audio.
    assert mic["buffered_ms"] >= 500 and mic["sent_ms"] >= mic["buffered_ms"]
    assert mic["peak"] >= 9600 and mic["mean_square"] >= 9600 * 9600
    appended = [e for e in server.received if e["type"] == "input_audio_buffer.append"]
    first = struct.unpack("<h", base64.b64decode(appended[0]["audio"])[:2])[0]
    assert first == 9600  # Block 5 = 500 ms: the command right after the wake word, not before.
    assert not [e for e in server.received if e["type"] in {"conversation.item.create",
                                                             "response.create"}]


def test_quiet_station_speech_is_raised_for_the_model_but_never_beyond_sixteen_times(
        built: Path) -> None:
    # Measured 2026-10-31: the owner's voice in vqe_0 peaked near 1,000 (about -30 dBFS),
    # too quiet for server VAD. The wake detector keeps the raw level.
    server, session = _standby(built, 400)
    appended = b"".join(base64.b64decode(e["audio"]) for e in server.received
                        if e["type"] == "input_audio_buffer.append")
    samples = struct.unpack(f"<{len(appended) // 2}h", appended)
    assert 6000 <= samples[0] <= 8200  # Block 5 (900) raised toward an 8,000 speech peak.
    assert max(samples) <= 8200 and min(samples) > 0
    mic = session[-1]["mic"]
    assert mic["peak"] < 5000 and 6000 <= mic["sent_peak"] <= 8200  # Raw vs sent level.


def test_standby_speaks_text_without_the_microphone_then_keeps_waiting(built: Path) -> None:
    (built / "detector").write_text(DETECTOR)
    (built / "numbered").write_text(NUMBERED_CAPTURE.replace("BASE", "9000"))
    (built / "detector").chmod(0o700)
    (built / "numbered").chmod(0o700)
    reply = base64.b64encode(b"\1\0" * 2400).decode()
    server = Server(built / "cert.pem", built / "key.pem",
                    [{"type": "response.output_audio.delta", "delta": reply},
                     {"type": "response.done", "response": {"status": "completed"}}])
    process = subprocess.Popen([str(_client(built, server.port)), "--player", str(built / "player"),
                                "--ca", str(built / "cert.pem"), "--idle-ms", "7000",
                                "--wake-detector", str(built / "detector"),
                                "--capture", str(built / "numbered"), "--max-seconds", "60"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert process.stdin is not None
    _until(process, "detector")
    started = time.monotonic()
    process.stdin.write(f"say gpt-realtime-2.1-mini 30 {SECRET} Привет, «мир»!\n".encode())
    process.stdin.flush()
    session = _until(process, "session_end")
    assert session[-1]["reason"] == "idle" and session[-1]["player"]["outcome"] == "complete"
    assert time.monotonic() - started < 3  # Ends right after the speech, not after 7 s.
    kinds = [e["type"] for e in server.received]
    assert "input_audio_buffer.append" not in kinds  # The microphone never reaches a say.
    assert server.received[0]["item"]["content"][0]["text"] == "Привет, «мир»!"
    assert kinds[1] == "response.create"
    process.stdin.write(b"stop\n")
    process.stdin.flush()
    assert _until(process, "end")[-1]["reason"] == "stopped"
    process.wait(10)


@pytest.mark.parametrize("finish", ["speak", "ask", "ask_conversation", "expire", "cancel",
                                   "unsolicited", "disconnect"])
def test_prepared_speech_connects_without_player_or_mic_and_is_consumed_once(
        built: Path, finish: str) -> None:
    (built / "detector").write_text(DETECTOR)
    (built / "detector").chmod(0o700)
    reply = base64.b64encode(b"\1\0" * 2400).decode()
    answer = [
        {"type": "response.output_audio.delta", "delta": reply},
        {"type": "response.done", "response": {"status": "completed"}}]
    tool = {"type": "response.function_call_arguments.done", "call_id": "call_chat",
            "name": "start_conversation", "arguments": "{}"}
    server = Server(built / "cert.pem", built / "key.pem", answer,
        on_connect=([{"type": "response.output_audio.delta", "delta": reply}]
                    if finish == "unsolicited" else []),
        on_update=[{"type": "input_audio_buffer.speech_stopped"},
                   *([tool, answer[-1]] if finish == "ask_conversation" else answer)])
    process = subprocess.Popen([str(_client(built, server.port)), "--player", str(built / "player"),
                                "--ca", str(built / "cert.pem"), "--idle-ms", "200",
                                "--wake-detector", str(built / "detector"),
                                "--capture", str(built / "capture"), "--max-seconds", "30"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert process.stdin is not None

    def send(line: str) -> None:
        process.stdin.write((line + "\n").encode())
        process.stdin.flush()

    try:
        _until(process, "detector")
        send(f"prepare gpt-realtime-2.1-mini {1 if finish == 'expire' else 10} {SECRET}")
        prepared = _until(process, "prepared")
        assert prepared[-1]["event"] == "prepared"
        assert not (built / "played.bin").exists() and not server.received
        if finish.startswith("ask"):
            send('askwarm 10 {"type":"realtime","model":"gpt-realtime-2.1-mini"}')
            if finish == "ask_conversation":
                assert _until(process, "tool")[-1]["message"]["name"] == "start_conversation"
                time.sleep(0.5)  # A pending tool must outlive the fast one-shot drain window.
                assert process.poll() is None
                send('continue {"type":"realtime","model":"gpt-realtime-2.1-mini"}')
                send("continue")  # A repeated request is idempotent, never kills the listener.
                send('tool {"type":"conversation.item.create","item":'
                     '{"type":"function_call_output","call_id":"call_chat","output":"ok"}}')
            ended = _until(process, "session_end")
            assert ended[-1]["reason"] == "idle"
            assert server.received[0]["type"] == "session.update"
            assert "connected" not in [e["event"] for e in ended]
            assert ended[-1]["player"]["outcome"] == "complete"
            if finish == "ask":
                mic = ended[-1]["mic"]
                assert mic["sent_ms"] == mic["buffered_ms"]
            else:
                assert len([e for e in server.received if e["type"] == "session.update"]) == 2
        elif finish == "speak":
            send("speak 10 Привет, мир!")
            ended = _until(process, "session_end")
            assert ended[-1]["reason"] == "idle"
            assert [e["type"] for e in server.received] == ["conversation.item.create",
                                                           "response.create"]
            assert server.received[0]["item"]["content"][0]["text"] == "Привет, мир!"
            assert "connected" not in [e["event"] for e in ended]
            assert any(k == "A" for k, _ in _frames((built / "played.bin").read_bytes()))
        else:
            if finish == "cancel":
                send("cancel")
            if finish == "disconnect":
                process.stdin.close()
            ended = _until(process, "prepare_end")
            assert ended[-1]["reason"] in {"deadline", "stopped", "unexpected_warm_event"}
            assert not (built / "played.bin").exists() and not server.received
        if finish == "disconnect":
            assert _until(process, "end")[-1]["reason"] == "stopped"
            return
        send("speak 10 НЕ ПОВТОРЯТЬ")
        rejected = _until(process, "session_end")[-1]
        assert rejected["reason"] == "not_prepared" and server.responses <= 1
        send("stop")
        assert _until(process, "end")[-1]["reason"] == "stopped"
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        process.wait(10)
        server.thread.join(5)
