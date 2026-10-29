"""Synthetic IO-channel level pilot: fake loopback IPC only, never a station or real audio."""

import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import io_channel_pilot as pilot
from alice_mcp.core import Controller, SafetyError, SSHInput

SOURCE = Path(__file__).resolve().parents[1] / "src/alice_mcp/native/io_channel_levels.c"
LOOPBACK = socket.inet_ntoa(socket.INADDR_LOOPBACK.to_bytes(4, "big"))
SENTINEL = b"SENSITIVE_SENTINEL"


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


def frame(payload: bytes, kind: int, full_message: bool = False) -> bytes:
    data = field(kind, payload) if full_message else field(252, payload) + field(253, kind)
    return len(data).to_bytes(4, "little") + data


def channel(kind: int, name: bytes, samples: list[int], rate: int = 16000,
            duration: int | None = None) -> bytes:
    body = field(1, kind) + field(2, name) + field(3, struct.pack(f"<{len(samples)}h", *samples))
    body += field(4, 1) + field(7, rate) + field(8, 1)
    if duration is not None:
        body += field(10, duration)
    return body


def audio(*channels: bytes, full_message: bool = False) -> bytes:
    return frame(b"".join(field(1, c) for c in channels), 158, full_message)


class FakeIPC:
    """Loopback-only synthetic Quasar service: scripted frames, records what it receives."""

    def __init__(self, frames: list[bytes], *, close_early: bool = False) -> None:
        self.frames, self.close_early, self.received = frames, close_early, b""
        self.server = socket.socket()
        self.server.bind((LOOPBACK, 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        connection, _ = self.server.accept()
        with connection:
            connection.settimeout(8)
            try:
                for item in self.frames:
                    connection.sendall(item)
                if self.close_early:
                    return
                while chunk := connection.recv(4096):
                    self.received += chunk
            except OSError:
                pass

    def close(self) -> list[bytes]:
        self.thread.join(timeout=10)
        self.server.close()
        out, data = [], self.received
        while len(data) >= 4:
            size = int.from_bytes(data[:4], "little")
            out.append(data[4:4 + size])
            data = data[4 + size:]
        return out


@pytest.fixture(scope="module")
def helper(tmp_path_factory: Any) -> Path:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        pytest.skip("Native pilot checks require a host C compiler; no automatic install")
    binary = tmp_path_factory.mktemp("io-channel") / "io-channel-levels"
    subprocess.run([compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-O2", "-Wall",
                    "-Wextra", "-Werror", "-Wconversion", "-Wshadow", "-Wstrict-prototypes",
                    "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2", "-s", str(SOURCE), "-o",
                    str(binary)], check=True, capture_output=True)
    return binary


def run(helper: Path, port: int, channel: str = "vqe",
        preamble: str = "none") -> tuple[int, dict[str, Any]]:
    args = ["--levels", str(port), channel, "1", preamble]
    result = subprocess.run([str(helper), *args], capture_output=True, timeout=15)
    lines = result.stdout.decode().splitlines()
    assert len(lines) <= 1 and SENTINEL not in result.stdout
    return result.returncode, json.loads(lines[0]) if lines else {}


def subscribe_body(request_field: int) -> bytes:
    return field(252, field(request_field, b"")) + field(253, 159)


def test_vqe_levels_subscribe_unsubscribe_and_never_export_audio(helper: Path) -> None:
    loud = [12000, -16000, 8000, -4000] * 40
    server = FakeIPC([
        frame(field(1, 0), 3),  # Unrelated Alice state is counted, not parsed for audio.
        audio(channel(6, b"vqe_0", loud, duration=10)),
        audio(channel(6, b"vqe_0", [0] * 160, duration=10), full_message=True),
    ])
    code, result = run(helper, server.port)
    received = server.close()
    assert code == 0
    assert received == [subscribe_body(3), subscribe_body(6)]  # request_vqe, then unsubscribe.
    assert result["outcome"] == "complete" and result["error_code"] == "none"
    assert result["connected"] and result["subscribe_sent"] and result["unsubscribe_sent"]
    assert result["frames"] == 3 and result["audio_frames"] == 2 and result["other_frames"] == 1
    assert result["audio_exported"] is False and 900 <= result["elapsed_ms"] <= 2500
    squares = sum(s * s for s in loud) // 320
    assert result["channels"] == [{
        "type": 6, "name": "vqe_0", "sample_rate": 16000, "format": 1, "frames": 2,
        "bytes": 640, "duration_ms": 20, "bytes_per_sample_estimate": 2,
        "peak_s16": 16000, "mean_square_s16": squares,
    }]


@pytest.mark.parametrize(("name", "number"), [("raw", 4), ("main", 1)])
def test_other_requests_use_their_schema_field(helper: Path, name: str, number: int) -> None:
    server = FakeIPC([audio(channel(1, b"raw_mic_0", [5, -7], rate=48000),
                            channel(1, b"raw_mic_1", [1, 1], rate=48000))])
    code, result = run(helper, server.port, name)
    assert server.close() == [subscribe_body(number), subscribe_body(6)]
    assert code == 0 and [c["name"] for c in result["channels"]] == ["raw_mic_0", "raw_mic_1"]
    assert result["channels"][0]["peak_s16"] == 7
    assert result["channels"][0]["bytes_per_sample_estimate"] == -1  # No duration: unknown.


def test_no_audio_is_unknown_not_success(helper: Path) -> None:
    server = FakeIPC([frame(field(1, 0), 3)])
    code, result = run(helper, server.port)
    assert server.close()[-1] == subscribe_body(6)
    assert code == 1 and result["outcome"] == "unknown"
    assert result["error_code"] == "no_audio_frames" and result["channels"] == []


def raw(body: bytes) -> bytes:
    return len(body).to_bytes(4, "little") + body


@pytest.mark.parametrize(("frames", "code"), [
    ([(1 << 20).to_bytes(4, "little")], "frame_limit"),
    ([raw(field(158, b"") + field(252, b"") + field(253, 158))], "protocol_error"),
    ([frame(b"\x0a\x05ab", 158)], "protocol_error"),
    ([audio(*[channel(6, b"ch_%d" % i, [1]) for i in range(17)])], "channel_limit"),
])
def test_malformed_stream_is_unknown_without_raw_output(
    helper: Path, frames: list[bytes], code: str,
) -> None:
    server = FakeIPC(frames)
    status, result = run(helper, server.port)
    server.close()
    assert status == 1 and result["outcome"] == "unknown" and result["error_code"] == code
    assert result["subscribe_sent"] is True and result["unsubscribe_sent"] is True


def test_server_close_is_unknown_and_refused_connection_is_blocked(helper: Path) -> None:
    server = FakeIPC([audio(channel(6, b"vqe_0", [3]))], close_early=True)
    status, result = run(helper, server.port)
    server.close()
    assert status == 1 and result["outcome"] == "unknown" and result["error_code"] == "ipc_closed"
    with socket.socket() as closed:  # Bound, not listening: refused, and no self-connect.
        closed.bind((LOOPBACK, 0))
        status, result = run(helper, closed.getsockname()[1])
    assert status == 1 and result["outcome"] == "blocked"
    assert result["error_code"] == "ipc_unavailable" and result["subscribe_sent"] is False


@pytest.mark.parametrize("args", [
    [], ["--levels", "80", "vqe", "1", "none"], ["--levels", "9875", "all", "1", "none"],
    ["--levels", "9875", "vqe", "6", "none"], ["--levels", "9875", "vqe", "0", "none"],
    ["--levels", "09875", "vqe", "1", "none"], ["--listen", "9875", "vqe", "1", "none"],
    ["--levels", "9875", "vqe", "1"], ["--levels", "9875", "vqe", "1", "custom"],
    ["--levels", "9875", "vqe", "1", "none", "extra"],
    ["--stream", "9850"], ["--stream", "9850", "0"], ["--stream", "9850", "61"],
    ["--stream", "80", "5"], ["--stream", "9850", "5", "full_envelope"],
])
def test_invalid_arguments_never_connect(helper: Path, args: list[str]) -> None:
    result = subprocess.run([str(helper), *args], capture_output=True, timeout=5)
    assert result.returncode == 2 and result.stdout == b""


def test_non_audio_frames_return_numeric_structure_only(helper: Path) -> None:
    frames = [
        frame(field(1, 0) + field(7, b"req-" + SENTINEL), 3),  # Packed AliceState-like.
        raw(field(210, field(7, b"AliceCapability" + SENTINEL)
                  + field(18, field(1, 0) + field(2, field(1, 1))))),  # Full-form Remoting.
        frame(b"service_" + SENTINEL, 157),  # String payload: never parsed or returned.
        frame(field(1, b""), 159),  # A pushed IOChannelRequest{request_main}.
        frame(b"", 183),
    ]
    server = FakeIPC(frames)
    code, result = run(helper, server.port, preamble="listen_only")
    assert server.close() == []  # listen_only writes nothing at all.
    assert code == 1 and result["error_code"] == "no_audio_frames"
    assert result["subscribe_sent"] is False and result["unsubscribe_sent"] is False
    packed = [[252, 2], [253, 0]]
    assert result["other"] == [
        {"length": len(frames[0]) - 4, "kind": 3, "top": packed, "payload": "message",
         "paths": [[1], [7]]},
        {"length": len(frames[1]) - 4, "kind": -1, "top": [[210, 2]], "payload": "message",
         "paths": [[7], [18], [18, 1], [18, 2]]},
        {"length": len(frames[2]) - 4, "kind": 157, "top": packed, "payload": "string_like",
         "paths": []},
        {"length": len(frames[3]) - 4, "kind": 159, "top": packed, "payload": "message",
         "paths": [[1]]},
        {"length": len(frames[4]) - 4, "kind": 183, "top": packed, "payload": "empty",
         "paths": []},
    ]


def test_frame_metadata_is_capped(helper: Path) -> None:
    server = FakeIPC([frame(field(1, 0), 3)] * 20)
    _, result = run(helper, server.port)
    server.close()
    assert result["other_frames"] == 20 and len(result["other"]) == 16


@pytest.mark.parametrize(("preamble", "expected"), [
    ("none", [subscribe_body(3), subscribe_body(6)]),
    ("full_envelope", [field(159, field(3, b"")), field(159, field(6, b""))]),
    ("hub_retrieve_state", [field(252, field(1, b"")) + field(253, 183), subscribe_body(3),
                            subscribe_body(6)]),
    ("listen_only", []),
])
def test_preambles_send_only_their_fixed_schema_frames(
    helper: Path, preamble: str, expected: list[bytes],
) -> None:
    server = FakeIPC([audio(channel(6, b"vqe_0", [9] * 160, duration=10))])
    code, result = run(helper, server.port, preamble=preamble)
    assert server.close() == expected
    assert code == 0 and result["outcome"] == "complete"
    assert result["subscribe_sent"] is result["unsubscribe_sent"] is (preamble != "listen_only")


def test_continuous_stream_is_bounded_by_the_window(helper: Path) -> None:
    server = FakeIPC([audio(channel(6, b"vqe_0", [100] * 160, duration=10))] * 2000)
    code, result = run(helper, server.port)
    server.close()
    assert result["elapsed_ms"] <= 2500 and result["audio_frames"] <= 2000
    assert code == 0 and result["outcome"] == "complete"


# --stream: the wake pilot's PCM source.

FULL_VQE = [field(159, field(3, b"")), field(159, field(6, b""))]


def stream(helper: Path, port: int, stdout: Any = subprocess.PIPE) -> tuple[int, bytes, Any]:
    result = subprocess.run([str(helper), "--stream", str(port), "1"], stdout=stdout,
                            stderr=subprocess.PIPE, timeout=15)
    lines = result.stderr.decode().splitlines()
    assert len(lines) == 1 and SENTINEL not in result.stderr
    return result.returncode, result.stdout or b"", json.loads(lines[0])


def test_stream_pipes_vqe_pcm_unchanged_and_reports_numbers_on_stderr(helper: Path) -> None:
    chunks = [[300, -300] * 128, [7, -32768] * 128, [1] * 256]
    server = FakeIPC([audio(channel(6, b"vqe_0", c, duration=16), full_message=True)
                      for c in chunks] + [frame(field(7, b"x-" + SENTINEL), 3)])
    code, pcm, result = stream(helper, server.port)
    assert server.close() == FULL_VQE  # Only the live-proven full_envelope request_vqe.
    assert code == 0 and pcm == b"".join(struct.pack("<256h", *c) for c in chunks)
    assert result["outcome"] == "complete" and result["error_code"] == "none"
    assert result["other_frames"] == 1 and result["other"] == []  # No metadata in streams.
    assert result["channels"][0]["name"] == "vqe_0" and result["channels"][0]["frames"] == 3
    assert result["channels"][0]["peak_s16"] == 32768
    assert result["audio_exported"] is False


def odd(data: bytes) -> bytes:
    return field(1, 6) + field(2, b"vqe_0") + field(3, data) + field(7, 16000) + field(8, 1)


@pytest.mark.parametrize("bad", [
    channel(6, b"raw_mic_0", [1, 2]), channel(7, b"vqe_0", [1, 2]),
    channel(6, b"vqe_0", [1, 2], rate=48000), odd(b"\x01\x02\x03"),
])
def test_stream_stops_on_any_format_change(helper: Path, bad: bytes) -> None:
    good = [5, -5] * 128
    server = FakeIPC([audio(channel(6, b"vqe_0", good), full_message=True),
                      audio(bad, full_message=True),
                      audio(channel(6, b"vqe_0", good), full_message=True)])
    code, pcm, result = stream(helper, server.port)
    assert server.close() == FULL_VQE
    assert code == 1 and pcm == struct.pack("<256h", *good)
    assert result["outcome"] == "unknown" and result["error_code"] == "format_changed"
    assert result["unsubscribe_sent"] is True


def test_stream_stops_when_the_detector_pipe_closes(helper: Path) -> None:
    server = FakeIPC([audio(channel(6, b"vqe_0", [9] * 256), full_message=True)] * 4)
    read, write = os.pipe()
    os.close(read)
    try:
        code, _, result = stream(helper, server.port, stdout=write)
    finally:
        os.close(write)
    assert server.close() == FULL_VQE
    assert code == 1 and result["error_code"] == "output_error"
    assert result["outcome"] == "unknown" and result["unsubscribe_sent"] is True


# Host wrapper and fixed on-device shell script.

FIRMWARE = b"\x7fELF\x01\x01" + b"\0" * 12 + b"\x28\x00" + SENTINEL


def station(tmp_path: Path, **changes: Any) -> Any:
    values = dict(id="desk", model="station-mini", preauthorize_all_actions=True,
                  controls={"firmware_sha256": hashlib.sha256(FIRMWARE).hexdigest(),
                            "allowed_actions": ["speech.listen"]}, native_mcp={},
                  trust_sha256="t" * 64)
    values.update(changes)
    return SimpleNamespace(**values)


def device(tmp_path: Path, port: int, awk: str, inode: str = "4242") -> dict[str, str]:
    root = tmp_path / "dev"
    for path, data in {
        "system/vendor/quasar/maind": FIRMWARE,
        "proc/sys/kernel/random/boot_id": b"boot\n",
        "proc/123/stat": " ".join(["123", "(maind)", "S", *map(str, range(1, 19)), "777", "0",
                                    "\n"]).encode(),
        "proc/net/tcp": (
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
            "   uid  timeout inode\n"
            f"   0: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 "
            f"00000000     0        0 {inode} 1 0 100 0 0 10 0\n").encode(),
    }.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    (root / "data/opt").mkdir(parents=True)
    (root / "proc/123/fd").mkdir()
    os.symlink(f"socket:[{inode}]", root / "proc/123/fd/7")
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    (shims / "pidof").write_text('#!/bin/sh\n[ "$1" = maind ] && echo 123\n')
    (shims / "pidof").chmod(0o700)
    os.symlink(shutil.which(awk) or "missing", shims / "awk")
    return {"root": str(root), "PATH": f"{shims}:{os.environ['PATH']}"}


def device_runner(env: dict[str, str], calls: list[SSHInput]) -> Any:
    def runner(_: Any, command: SSHInput) -> str:
        calls.append(command)
        assert len(command.command.encode()) < 1024 and command.command.startswith("sh -s -- ")
        source = command.data.decode().replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
        for prefix in ("/proc/", "/system/", "DATA=/data"):
            target = env["root"] + prefix if prefix[0] == "/" else "DATA=" + env["root"] + "/data"
            source = source.replace(prefix, target)
        args = command.command.split(" ", 3)[3]
        return subprocess.run(f"dash -s -- {args}", shell=True, input=source.encode(),
                              capture_output=True, timeout=25, env={**os.environ, **env},
                              check=True).stdout.decode()
    return runner


def plan(tmp_path: Path, helper: Path, port: int, monkeypatch: Any,
         awk: str = "gawk") -> tuple[Controller, Any, list[SSHInput]]:
    if not shutil.which("dash") or not shutil.which(awk):
        pytest.skip("Synthetic shell checks need dash and the requested awk")
    calls: list[SSHInput] = []
    env = device(tmp_path, port, awk)
    controller = Controller(None, writes=True, runner=device_runner(env, calls))
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(pilot, "_ssh", lambda item: item)
    monkeypatch.setattr(pilot, "HELPER_SHA256", hashlib.sha256(helper.read_bytes()).hexdigest())
    item = pilot.Pilot(port, "vqe", 1, helper.read_bytes())
    return controller, item, calls


@pytest.mark.parametrize(("awk", "preamble"), [("gawk", "none"), ("mawk", "listen_only")])
def test_end_to_end_synthetic_pilot_cleans_up_and_checks_quasar_identity(
    tmp_path: Path, helper: Path, monkeypatch: Any, awk: str, preamble: str,
) -> None:
    server = FakeIPC([audio(channel(6, b"vqe_0", [300, -300] * 80, duration=10))])
    controller, item, calls = plan(tmp_path, helper, server.port, monkeypatch, awk)
    item = replace(item, preamble=preamble)
    preview = pilot.preview(controller, "desk", item)
    assert b"__HELPER_OCTAL__" not in calls[0].data and b"\\177" not in calls[0].data
    assert preview["expires_in_seconds"] == 300 and preview["audio_exported"] is False
    result = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert server.close() == ([] if preamble == "listen_only" else
                              [subscribe_body(3), subscribe_body(6)])
    assert result["outcome"] == "complete", result
    assert result["other"] == [] and result["subscribe_sent"] is (preamble == "none")
    assert result["cleanup_verified"] and result["quasar_running"] and result["quasar_unchanged"]
    assert result["do_not_retry"] is True and result["audio_exported"] is False
    assert result["channels"][0]["peak_s16"] == 300
    assert result["channels"][0]["rms_dbfs"] == pytest.approx(-40.77, abs=0.01)
    assert not any((tmp_path / "dev/data/opt").iterdir())
    assert SENTINEL.decode() not in json.dumps(result)
    with pytest.raises(SafetyError):  # One use: the consumed preview cannot be replayed.
        controller.take_plan(preview["plan_id"], preview["expected_sha256"])


def test_changed_quasar_identity_blocks_apply_and_is_reported(
    tmp_path: Path, helper: Path, monkeypatch: Any,
) -> None:
    server = FakeIPC([audio(channel(6, b"vqe_0", [1] * 160, duration=10))])
    controller, item, _ = plan(tmp_path, helper, server.port, monkeypatch)
    preview = pilot.preview(controller, "desk", item)
    stat = tmp_path / "dev/proc/123/stat"
    stat.write_text(stat.read_text().replace(" 777 ", " 778 "))  # Quasar restarted.
    result = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert result == {"outcome": "blocked", "error_code": "preview_state_changed",
                      "do_not_retry": True, "audio_exported": False}
    assert not any((tmp_path / "dev/data/opt").iterdir())
    server.server.close()


def test_listener_must_be_owned_by_quasar(tmp_path: Path, helper: Path, monkeypatch: Any) -> None:
    controller, item, _ = plan(tmp_path, helper, 9875, monkeypatch)
    os.unlink(tmp_path / "dev/proc/123/fd/7")
    with pytest.raises(SafetyError, match="ipc_unavailable"):
        pilot.preview(controller, "desk", item)


@pytest.mark.parametrize("change", [
    {"model": "station-mini3"}, {"preauthorize_all_actions": False},
    {"controls": {"firmware_sha256": "a" * 64, "allowed_actions": ["speech.say"]}},
])
def test_policy_gates(tmp_path: Path, monkeypatch: Any, change: dict[str, Any]) -> None:
    controller = Controller(None, writes=True, runner=lambda *_: pytest.fail("no SSH"))
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path, **change))
    monkeypatch.setattr(pilot, "HELPER_SHA256", hashlib.sha256(b"x").hexdigest())
    with pytest.raises(SafetyError):
        pilot.preview(controller, "desk", pilot.Pilot(9875, "vqe", 5, b"x"))
    controller.writes = False
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    with pytest.raises(SafetyError):
        pilot.preview(controller, "desk", pilot.Pilot(9875, "vqe", 5, b"x"))


@pytest.mark.parametrize("item", [
    pilot.Pilot(80, "vqe", 5, b"x"), pilot.Pilot(9875, "all", 5, b"x"),
    pilot.Pilot(9875, "vqe", 6, b"x"), pilot.Pilot(9875, "vqe", 5, b"y"),
    pilot.Pilot(True, "vqe", 5, b"x"),  # type: ignore[arg-type]
    pilot.Pilot(9875, "vqe", 5, b"x", "custom_bytes"),
])
def test_pilot_inputs_and_pin_are_validated(monkeypatch: Any, item: Any) -> None:
    monkeypatch.setattr(pilot, "HELPER_SHA256", hashlib.sha256(b"x").hexdigest())
    with pytest.raises(SafetyError):
        item.validate()


def result_line(**changes: Any) -> str:
    value: dict[str, Any] = {
        "outcome": "complete", "error_code": "none", "connected": True, "subscribe_sent": True,
        "unsubscribe_sent": True, "frames": 2, "audio_frames": 2, "other_frames": 0,
        "bytes": 640, "first_audio_ms": 3, "elapsed_ms": 1001, "audio_exported": False,
        "other": [],
        "channels": [{"type": 6, "name": "vqe_0", "sample_rate": 16000, "format": 1,
                      "frames": 2, "bytes": 640, "duration_ms": 20,
                      "bytes_per_sample_estimate": 2, "peak_s16": 0, "mean_square_s16": 0}],
    }
    value.update(changes)
    return json.dumps(value)


TRAILER = '{"outcome":"cleanup","cleanup_verified":true,"quasar_running":true,' \
          '"quasar_unchanged":true}'


@pytest.mark.parametrize(("raw", "outcome", "code"), [
    (result_line() + "\n" + TRAILER, "complete", "none"),
    (result_line() + "\n" + TRAILER.replace("unchanged\":true", "unchanged\":false"),
     "unknown", "quasar_changed_or_cleanup_unverified"),
    (TRAILER, "unknown", "helper_output_missing"),
    (result_line(channels=[{"name": SENTINEL.decode()}]) + "\n" + TRAILER, "unknown",
     "helper_output_invalid"),
    (result_line(extra=1) + "\n" + TRAILER, "unknown", "helper_output_invalid"),
    (result_line(audio_exported=True) + "\n" + TRAILER, "unknown", "helper_output_invalid"),
    (result_line(outcome="complete", unsubscribe_sent=False) + "\n" + TRAILER, "unknown",
     "helper_output_invalid"),
    ('{"outcome":"blocked","error_code":"temporary_path_busy"}', "blocked",
     "temporary_path_busy"),
    ('{"outcome":"blocked","error_code":"' + SENTINEL.decode() + '"}', "unknown",
     "helper_output_invalid"),
    (result_line(other=[{"length": 9, "kind": 3, "top": [[252, 2]], "payload": "message",
                         "paths": [["x"]]}]) + "\n" + TRAILER, "unknown",
     "helper_output_invalid"),
    (result_line(other=[{"length": 9, "kind": 3, "top": [[252, 2]], "payload": SENTINEL.decode(),
                         "paths": []}]) + "\n" + TRAILER, "unknown", "helper_output_invalid"),
    (result_line(other=[{"length": 9, "kind": 3, "top": [[252, 2]], "payload": "message",
                         "paths": [[1], [1, 2]]}] * 17) + "\n" + TRAILER, "unknown",
     "helper_output_invalid"),
])
def test_result_parser_is_strict_and_never_echoes_raw_output(
    raw: str, outcome: str, code: str,
) -> None:
    result = pilot.parse_result(raw)
    assert result["outcome"] == outcome and result["error_code"] == code
    assert result["do_not_retry"] is True and result["audio_exported"] is False
    assert SENTINEL.decode() not in json.dumps(result)
    if outcome == "complete":
        assert result["channels"][0]["rms_dbfs"] is None  # Digital silence has no dBFS.


def test_operator_flow_requires_typed_word_after_state_bound_preview(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(pilot, "preview", lambda *a: calls.append("preview") or {
        "plan_id": "p", "expected_sha256": "e" * 64, "impact": "x", "audio_exported": False,
        "expires_in_seconds": 300})
    monkeypatch.setattr(pilot, "apply", lambda *a: calls.append("apply") or {"outcome": "x"})
    controller: Any = SimpleNamespace(take_plan=lambda *a: calls.append("consumed"))
    item = pilot.Pilot(9875, "vqe", 5, b"x")
    prompts: list[str] = []
    cancelled = pilot.operator_levels(controller, "desk", item,
                                      ask=lambda p: prompts.append(p) or "levels")
    assert cancelled["outcome"] == "cancelled" and cancelled["do_not_retry"] is True
    assert calls == ["preview", "consumed"] and "no audio" in prompts[0].lower()
    assert pilot.operator_levels(controller, "desk", item, ask=lambda p: "LEVELS") == \
        {"outcome": "x"}
    assert calls == ["preview", "consumed", "preview", "apply"]
    # Terminal line endings or stray spaces around the exact word must not cancel.
    assert pilot.operator_levels(controller, "desk", item, ask=lambda p: " LEVELS\r") == \
        {"outcome": "x"}
    mismatch = pilot.operator_levels(controller, "desk", item, ask=lambda p: "LEVEL")
    assert mismatch["outcome"] == "cancelled" and mismatch["received"] == "'LEVEL'"


def test_apply_uncertainty_is_never_retried(tmp_path: Path, monkeypatch: Any) -> None:
    attempts: list[int] = []

    def runner(_: Any, command: SSHInput) -> str:
        attempts.append(1)
        if " apply " in command.command:
            raise SafetyError("SSH timed out; do not automatically retry writes")
        return '{"outcome":"ready","state_sha256":"' + "c" * 64 + '"}\n'

    controller = Controller(None, writes=True, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(pilot, "_ssh", lambda item: item)
    monkeypatch.setattr(pilot, "HELPER_SHA256", hashlib.sha256(b"x").hexdigest())
    item = pilot.Pilot(9875, "vqe", 5, b"x")
    preview = pilot.preview(controller, "desk", item)
    result = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert result["outcome"] == "unknown" and result["do_not_retry"] is True
    assert len(attempts) == 2


def test_pinned_helper_size_fits_the_bounded_ssh_input(monkeypatch: Any, tmp_path: Path) -> None:
    helper = b"\0" * 19664  # Size of the pinned ARM32 build (docs/IO_CHANNEL.md).
    monkeypatch.setattr(pilot, "HELPER_SHA256", hashlib.sha256(helper).hexdigest())
    request = pilot._request(station(tmp_path), pilot.Pilot(9875, "vqe", 5, helper), "apply",
                             "e" * 64)
    assert len(request.data) <= 120 * 1024 and b"__HELPER_OCTAL__" not in request.data


def test_listen_only_result_must_not_claim_a_request() -> None:
    line = result_line(subscribe_sent=False, unsubscribe_sent=False)
    assert pilot.parse_result(line + "\n" + TRAILER, "listen_only")["outcome"] == "complete"
    assert pilot.parse_result(result_line() + "\n" + TRAILER, "listen_only")["error_code"] \
        == "helper_output_invalid"
    assert pilot.parse_result(line + "\n" + TRAILER, "none")["error_code"] == \
        "helper_output_invalid"
