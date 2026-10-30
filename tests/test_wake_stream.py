"""Synthetic station -> PC stream: a live-like fake yio_audio and a fake tmpfs, no station."""

import hashlib
import json
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from test_io_channel_pilot import LOOPBACK, SENTINEL, audio, channel, device, field, station
from test_wake_pilot import build_client, local, localize

from alice_mcp import io_channel_pilot as iop
from alice_mcp import wake_stream as ws
from alice_mcp.core import Controller, SafetyError

SUBSCRIBE, UNSUBSCRIBE = field(159, field(3, b"")), field(159, field(6, b""))


@pytest.fixture(scope="module")
def client(tmp_path_factory: Any) -> bytes:
    return build_client(tmp_path_factory)


class Live:
    """Live-like yio_audio: frame k (256 samples of value k + 1) every 10 ms to every
    connection that subscribed with the full envelope; records each connection's requests."""

    def __init__(self, send: bool = True) -> None:
        self.server = socket.socket()
        self.server.bind((LOOPBACK, 0))
        self.server.listen(8)
        self.port = self.server.getsockname()[1]
        self.subscribers: list[socket.socket] = []
        self.requests: list[list[bytes]] = []
        self.peak = 0
        self.lock, self.running, self.send = threading.Lock(), True, send
        for target in (self.accept, self.broadcast):
            threading.Thread(target=target, daemon=True).start()

    def accept(self) -> None:
        while self.running:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            log: list[bytes] = []
            self.requests.append(log)
            threading.Thread(target=self.serve, args=(connection, log), daemon=True).start()

    def serve(self, connection: socket.socket, log: list[bytes]) -> None:
        data = b""
        with connection:
            while True:
                try:
                    chunk = connection.recv(4096)
                except OSError:
                    chunk = b""
                data += chunk
                while len(data) >= 4 and len(data) >= 4 + int.from_bytes(data[:4], "little"):
                    size = int.from_bytes(data[:4], "little")
                    request, data = data[4:4 + size], data[4 + size:]
                    log.append(request)
                    with self.lock:
                        if request == SUBSCRIBE:
                            self.subscribers.append(connection)
                            self.peak = max(self.peak, len(self.subscribers))
                        elif connection in self.subscribers:
                            self.subscribers.remove(connection)
                if not chunk:
                    with self.lock:
                        if connection in self.subscribers:
                            self.subscribers.remove(connection)
                    return

    def broadcast(self) -> None:
        index = 0
        while self.running:
            frame = audio(channel(6, b"vqe_0", [index % 30000 + 1] * 256, duration=16),
                          full_message=True)
            with self.lock:
                targets = list(self.subscribers) if self.send else []
            for target in targets:
                try:
                    target.sendall(frame)
                except OSError:
                    pass
            index += 1
            time.sleep(0.01)

    def close(self) -> None:
        self.running = False
        self.server.close()


EXTRA = ("/proc/mounts", "for t in /tmp /run /dev/shm /var/run; do")


def setup(tmp_path: Path, client: bytes, port: int, monkeypatch: Any, *, run_s: int = 2,
          lead_s: int = 1, tmpfs: bool = True) -> tuple[Controller, ws.Stream, list[bytes]]:
    if not shutil.which("dash") or not shutil.which("gawk"):
        pytest.skip("Synthetic shell checks need dash and gawk")
    env = device(tmp_path, port, "gawk")
    root = env["root"]
    (Path(root) / "tmp").mkdir(mode=0o700)
    (Path(root) / "proc/mounts").write_text(
        f"rootfs / rootfs rw 0 0\n{'tmpfs' if tmpfs else 'ext4'} {root}/tmp "
        f"{'tmpfs' if tmpfs else 'ext4'} rw 0 0\n")
    extra = ((EXTRA[0], root + "/proc/mounts"), (EXTRA[1], f"for t in {root}/tmp; do"))
    uploads: list[bytes] = []

    def transfer(_: Any, request: Any, *, limit: int) -> str:
        uploads.append(request.data)
        return local(env, request.command, request.data, extra).decode()

    monkeypatch.setenv("PATH", env["PATH"])
    controller = Controller(None, writes=True,
                            runner=lambda _, command: local(env, command, b"", extra).decode())
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(ws, "_ssh", lambda item: item)
    monkeypatch.setattr(ws, "_transfer", transfer)
    monkeypatch.setattr(ws, "ssh_argv", lambda _, command: localize(env, command, extra))
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(client).hexdigest())
    return controller, ws.Stream(client, port=port, run_s=run_s, lead_s=lead_s), uploads


def indices(pcm: bytes) -> list[int]:
    assert len(pcm) % 512 == 0
    return [int.from_bytes(pcm[i:i + 2], "little", signed=True) - 1
            for i in range(0, len(pcm), 512)]


def run(controller: Controller, item: ws.Stream, **kwargs: Any) -> tuple[dict[str, Any], bytes]:
    fed: list[bytes] = []
    sink = kwargs.pop("sink", fed.append)
    preview = ws.preview(controller, "desk", item)
    assert preview["audio_exported"] is True and "RAM" in preview["impact"]
    result = ws.apply(controller, preview["plan_id"], preview["expected_sha256"], item, sink,
                      backoff_s=0, **kwargs)
    with pytest.raises(SafetyError):  # One use.
        controller.take_plan(preview["plan_id"], preview["expected_sha256"])
    return result, b"".join(fed)


def test_overlapping_chunks_hand_over_without_gaps_or_duplicates(
    tmp_path: Path, client: bytes, monkeypatch: Any,
) -> None:
    live = Live()
    controller, item, uploads = setup(tmp_path, client, live.port, monkeypatch)
    stale = tmp_path / "dev/tmp/alice-mcp-stream"
    stale.mkdir()
    (stale / "client").write_bytes(b"left by a dead session")
    result, pcm = run(controller, item, max_chunks=3)
    live.close()
    assert result["outcome"] == "complete" and result["reason"] == "max_chunks", result
    assert result["stale_reclaimed"] is True and result["chunks"] == 3
    assert result["handovers"] == 2 and result["gaps"] == 0
    found = indices(pcm)
    assert found == list(range(found[0], found[0] + len(found)))  # No gap, no duplicate.
    assert len(found) >= 300  # Three 2 s windows of up to 100 frames/s, back to back.
    assert live.peak == 2  # The handover briefly holds two subscriptions.
    assert all(r == [SUBSCRIBE, UNSUBSCRIBE] for r in live.requests) and len(live.requests) == 3
    assert result["cleanup_verified"] and result["quasar_running"] and result["quasar_unchanged"]
    assert result["do_not_retry"] is True and result["audio_exported"] is True
    assert result["audio_s"] == round(len(pcm) / 32000, 2)
    assert [u for u in uploads if u] == [client]  # Uploaded once, to tmpfs.
    assert not stale.exists() and not any((tmp_path / "dev/data/opt").iterdir())


def test_sequential_chunks_are_the_fallback(
    tmp_path: Path, client: bytes, monkeypatch: Any,
) -> None:
    live = Live()
    controller, item, _ = setup(tmp_path, client, live.port, monkeypatch, lead_s=0)
    result, pcm = run(controller, item, max_chunks=2)
    live.close()
    assert result["outcome"] == "complete" and result["gaps"] == 1 and result["handovers"] == 0
    found = indices(pcm)
    assert found == sorted(set(found)) and live.peak == 1  # Never two subscriptions.


def test_changed_quasar_identity_stops_the_session(
    tmp_path: Path, client: bytes, monkeypatch: Any,
) -> None:
    live = Live()
    controller, item, _ = setup(tmp_path, client, live.port, monkeypatch)
    stat = tmp_path / "dev/proc/123/stat"

    def sink(_: bytes) -> None:
        stat.write_text(stat.read_text().replace(" 777 ", " 778 "))  # Quasar restarted.

    result, _ = run(controller, item, sink=sink, max_chunks=5)
    live.close()
    assert result["outcome"] == "unknown" and result["reason"] == "quasar_changed", result
    assert result["chunks"] <= 2 and result["do_not_retry"] is True
    assert result["cleanup_verified"] is True and result["quasar_unchanged"] is False
    assert not (tmp_path / "dev/tmp/alice-mcp-stream").exists()


def test_stop_event_ends_the_session_and_cleans_up(
    tmp_path: Path, client: bytes, monkeypatch: Any,
) -> None:
    live = Live()
    controller, item, _ = setup(tmp_path, client, live.port, monkeypatch, run_s=10)
    stop = threading.Event()
    started = time.monotonic()
    result, pcm = run(controller, item, sink=lambda _: stop.set(), stop=stop)
    live.close()
    assert result["outcome"] == "complete" and result["reason"] == "stopped", result
    assert time.monotonic() - started < 8 and result["cleanup_verified"] is True
    assert not (tmp_path / "dev/tmp/alice-mcp-stream").exists()


def test_repeated_failures_stop_without_endless_retries(
    tmp_path: Path, client: bytes, monkeypatch: Any,
) -> None:
    live = Live(send=False)  # Accepts and subscribes, never sends audio.
    controller, item, _ = setup(tmp_path, client, live.port, monkeypatch, run_s=1, lead_s=0)
    result, pcm = run(controller, item)
    live.close()
    assert result["outcome"] == "unknown" and result["reason"] == "repeated_failures", result
    assert result["chunks"] == ws.MAX_FAILURES and pcm == b""
    assert result["cleanup_verified"] is True


def test_no_tmpfs_blocks_before_anything_is_written(
    tmp_path: Path, client: bytes, monkeypatch: Any,
) -> None:
    controller, item, uploads = setup(tmp_path, client, 9850, monkeypatch, tmpfs=False)
    with pytest.raises(SafetyError, match="tmpfs_unavailable"):
        ws.preview(controller, "desk", item)
    assert uploads == []


@pytest.mark.parametrize("change", [
    {"model": "station-mini3"}, {"preauthorize_all_actions": False},
    {"controls": {"allowed_actions": ["speech.listen"]}},
])
def test_policy_gates(tmp_path: Path, monkeypatch: Any, change: dict[str, Any]) -> None:
    controller = Controller(None, writes=True, runner=lambda *_: pytest.fail("no SSH"))
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path, **change))
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"c").hexdigest())
    with pytest.raises(SafetyError):
        ws.preview(controller, "desk", ws.Stream(b"c"))


@pytest.mark.parametrize("item", [
    ws.Stream(b"x"), ws.Stream(b"c", port=80), ws.Stream(b"c", run_s=61),
    ws.Stream(b"c", run_s=0), ws.Stream(b"c", run_s=10, lead_s=10),
    ws.Stream(b"c", lead_s=-1), ws.Stream(b"c", run_s=True),  # type: ignore[arg-type]
])
def test_stream_inputs_and_pin_are_validated(monkeypatch: Any, item: ws.Stream) -> None:
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"c").hexdigest())
    with pytest.raises(SafetyError):
        item.validate()


REPORT = {"outcome": "complete", "error_code": "none", "connected": True, "subscribe_sent": True,
          "unsubscribe_sent": True, "frames": 3, "audio_frames": 3, "other_frames": 0,
          "bytes": 1536, "first_audio_ms": 3, "elapsed_ms": 60001, "audio_exported": False,
          "other": [], "channels": [{"type": 6, "name": "vqe_0", "sample_rate": 16000,
                                     "format": 1, "frames": 3, "bytes": 1536, "duration_ms": 0,
                                     "bytes_per_sample_estimate": -1, "peak_s16": 9,
                                     "mean_square_s16": 4}]}
END = {"outcome": "chunk_end", "quasar_unchanged": True}


def lines(*values: Any) -> bytes:
    return b"".join((v if isinstance(v, str) else json.dumps(v)).encode() + b"\n"
                    for v in values)


@pytest.mark.parametrize(("meta", "status"), [
    (lines(REPORT, END), "complete"),
    (lines(dict(REPORT, outcome="unknown", error_code="output_error"), END), "failed"),
    (lines(REPORT, dict(END, quasar_unchanged=False)), "quasar_changed"),
    (lines({"outcome": "quasar_changed"}), "quasar_changed"),
    (lines({"outcome": "blocked", "error_code": "helper_missing"}), "blocked"),
    (lines({"outcome": "blocked", "error_code": SENTINEL.decode()}), "invalid"),
    (lines(dict(REPORT, extra=SENTINEL.decode()), END), "invalid"),
    (lines(REPORT), "invalid"),
    (lines(END, REPORT), "invalid"),
    (lines(SENTINEL.decode()), "invalid"),
    (b"", "invalid"),
    (b"x" * (ws.META_LIMIT + 1), "invalid"),
])
def test_chunk_metadata_is_parsed_strictly(meta: bytes, status: str) -> None:
    result = ws.parse_chunk(meta)
    assert result["status"] == status, result
    assert SENTINEL.decode() not in json.dumps(result)
