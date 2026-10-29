"""Owner-run continuous Mini 1 room audio to this PC for wake detection (docs/IO_CHANNEL.md).

Owner decision (2026-10-29): continuous room audio may go to this PC for wake detection;
it is processed in RAM and never written to disk here. The pinned IO-channel client is
uploaded once to a station tmpfs. Each chunk is one SSH session running one bounded
`--stream` window (<= 60 s) whose raw vqe_0 PCM (S16LE 16 kHz mono) arrives on the
session's stdout; its numbers-only report arrives on stderr. The next chunk starts
`lead_s` before the current window ends; the PC switches when the new stream has audio
and skips the part both streams delivered, so nothing is fed twice. Every chunk checks
the Quasar identity before and after its window; a change stops the session with
do_not_retry. Cleanup removes the client and is verified at the end.
"""

from __future__ import annotations

import hashlib
import os
import queue
import re
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from alice_mcp import io_channel_pilot as iop
from alice_mcp import wake_pilot as wp
from alice_mcp.core import Controller, SafetyError, Station, ssh_argv
from alice_mcp.pcm_pilot import _Transfer, _transfer
from alice_mcp.vqe_experiment import _ssh

ACTION = "wake.stream"
META_LIMIT = 8192
MAX_FAILURES = 3
ERRORS = iop.PROFILE_ERRORS | {"tmpfs_unavailable", "helper_missing"}
MATCH = 512  # One vqe_0 frame: the probe used to find the overlap of two streams.

# Fixed wrapper; only validated numbers, pins and a setup identity digest are arguments.
# Chunk metadata goes to the session's stderr, so its stdout is raw PCM only.
SCRIPT = r'''
OWNER_UID=0
LC_ALL=C; export LC_ALL
umask 077
exec 3>&2 2>/dev/null
mode=$1; port=$2; run_s=$3; firmware=$4; first_sha=$5; expected=$6; known=$7
if [ "$mode" = chunk ]; then exec 4>&3; else exec 4>&1; fi
exec 3>&-
blocked() {
    [ "$mode" != apply ] || cat >/dev/null
    printf '{"outcome":"blocked","error_code":"%s"}\n' "$1" >&4
    exit 0
}
[ "$(id -u)" = "$OWNER_UID" ] || blocked not_owner
case "$mode" in inspect|apply|chunk|cleanup) ;; *) blocked invalid_request;; esac
case "$port" in ''|0*|*[!0-9]*) blocked invalid_request;; esac
[ "${#port}" -le 5 ] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || blocked invalid_request
case "$run_s" in [1-9]|[1-5][0-9]|60) ;; *) blocked invalid_request;; esac
for pin in "$firmware" "$first_sha"; do
    [ "${#pin}" = 64 ] || blocked invalid_request
    case "$pin" in *[!0-9a-f]*) blocked invalid_request;; esac
done
if [ "$mode" = chunk ] || [ "$mode" = cleanup ]; then
    [ "${#known}" = 64 ] || blocked invalid_request
    case "$known" in *[!0-9a-f]*) blocked invalid_request;; esac
fi
for c in awk cat chmod cut grep id kill ls mkdir od pidof readlink rm rmdir sha256sum \
    sleep sort; do
    command -v "$c" >/dev/null 2>&1 || blocked runtime_tools_missing
done
identity() {
    for p in $(pidof maind); do awk '{ print $1 ":" $22 }' "/proc/$p/stat"; done |
        sort | sha256sum | cut -d ' ' -f 1
}
TMPFS=
for t in /tmp /run /dev/shm /var/run; do
    if awk -v m="$t" '$2 == m && $3 == "tmpfs" { f = 1 } END { if (!f) exit 1 }' /proc/mounts
    then TMPFS=$t; break; fi
done
[ -n "$TMPFS" ] && [ -d "$TMPFS" ] && [ ! -L "$TMPFS" ] || blocked tmpfs_unavailable
set -- $(ls -ldn "$TMPFS")
[ "$3" = "$(id -u)" ] || blocked tmpfs_unavailable
case "$1" in d????????t|d????-??-?) ;; *) blocked tmpfs_unavailable;; esac
DIR=$TMPFS/alice-mcp-stream
running() { ls -l /proc/[0-9]*/exe | grep -F " -> $DIR/client" >/dev/null; }
if [ "$mode" = cleanup ]; then
    i=0
    while running && [ "$i" -lt 5 ]; do sleep 1; i=$((i + 1)); done
    if running; then
        for q in /proc/[0-9]*; do
            [ "$(readlink "$q/exe")" != "$DIR/client" ] || kill -TERM "${q#/proc/}"
        done
        sleep 1
    fi
    clean=true
    if [ -e "$DIR" ] || [ -L "$DIR" ]; then
        [ -d "$DIR" ] && [ ! -L "$DIR" ] && rm -f "$DIR/client" && rmdir "$DIR" || clean=false
    fi
    [ ! -e "$DIR" ] && [ ! -L "$DIR" ] && ! running || clean=false
    alive=false; unchanged=false
    if pidof maind >/dev/null; then
        alive=true
        [ "$(identity)" != "$known" ] || unchanged=true
    fi
    printf '{"outcome":"cleanup","cleanup_verified":%s,"quasar_running":%s,' "$clean" "$alive" >&4
    printf '"quasar_unchanged":%s}\n' "$unchanged" >&4
    exit 0
fi
''' + iop.GUARDS + r'''before=$(identity)
if [ "$mode" = chunk ]; then
    [ "$before" = "$known" ] || { printf '{"outcome":"quasar_changed"}\n' >&4; exit 0; }
    [ -d "$DIR" ] && [ ! -L "$DIR" ] && [ -f "$DIR/client" ] && [ ! -L "$DIR/client" ] ||
        blocked helper_missing
    [ "$(sha256sum "$DIR/client" | cut -d ' ' -f 1)" = "$first_sha" ] || blocked helper_missing
    "$DIR/client" --stream "$port" "$run_s" </dev/null 2>&4 4>&-
    unchanged=false
    [ "$(identity)" != "$known" ] || unchanged=true
    printf '{"outcome":"chunk_end","quasar_unchanged":%s}\n' "$unchanged" >&4
    exit 0
fi
boot=$(sha256sum /proc/sys/kernel/random/boot_id | cut -d ' ' -f 1)
STATE=$(printf '%s\n' "$actual" "$boot" "$DATA" "$port" "$run_s" "$first_sha" "$before" \
    "$inode" "$TMPFS" | sha256sum | cut -d ' ' -f 1)
if [ "$mode" = inspect ]; then
    printf '{"outcome":"ready","state_sha256":"%s"}\n' "$STATE" >&4
    exit 0
fi
[ "$expected" = "$STATE" ] || blocked preview_state_changed
stale=false
if [ -e "$DIR" ] || [ -L "$DIR" ]; then
    # Left by a host that died: reclaimed only when no client runs from it.
    ! running || blocked temporary_path_busy
    [ -d "$DIR" ] && [ ! -L "$DIR" ] && rm -f "$DIR/client" && rmdir "$DIR" ||
        blocked temporary_path_busy
    stale=true
fi
mkdir "$DIR" || blocked temporary_path_busy
if (set -C; cat > "$DIR/client") &&
    [ "$(sha256sum "$DIR/client" | cut -d ' ' -f 1)" = "$first_sha" ] && chmod 500 "$DIR/client"
then
    printf '{"outcome":"ready","identity_sha256":"%s","stale_reclaimed":%s}\n' "$before" \
        "$stale" >&4
else
    rm -f "$DIR/client"; rmdir "$DIR"
    printf '{"outcome":"unknown","error_code":"helper_transfer_failed"}\n' >&4
fi
exit 0
'''


@dataclass(frozen=True)
class Stream:
    client: bytes = field(repr=False)
    port: int = 9850  # yio_audio on Mini 1 (docs/IO_CHANNEL.md, round 2).
    run_s: int = 60  # One chunk: one SSH session, one --stream window.
    lead_s: int = 5  # Start the next chunk this early; 0 = sequential chunks with a gap.

    def validate(self) -> None:
        if (type(self.client) is not bytes
            or hashlib.sha256(self.client).hexdigest() != iop.HELPER_SHA256
            or type(self.port) is not int or not 1024 <= self.port <= 65535
            or type(self.run_s) is not int or not 1 <= self.run_s <= 60
            or type(self.lead_s) is not int or not 0 <= self.lead_s < self.run_s):
            raise SafetyError("Wake stream input or reviewed client invalid; no transfer")

    @property
    def digest(self) -> str:
        return hashlib.sha256(repr((SCRIPT, self.port, self.run_s, self.lead_s,
                                    hashlib.sha256(self.client).hexdigest())).encode()).hexdigest()


def _command(station: Station, item: Stream, mode: str, expected: str = "",
             known: str = "") -> _Transfer:
    item.validate()
    source = SCRIPT
    if mode == "inspect":
        source = source[:source.index('[ "$expected" = "$STATE" ]')]
    args = [mode, str(item.port), str(item.run_s), station.controls["firmware_sha256"],
            iop.HELPER_SHA256, expected, known]
    return _Transfer("sh -c " + shlex.quote(source) + " sh "
                     + " ".join(shlex.quote(a) for a in args),
                     item.client if mode == "apply" else b"")


def preview(controller: Controller, station_id: str, item: Stream) -> dict[str, Any]:
    station = controller.station(station_id)
    iop._permitted(controller, station)
    item.validate()
    ssh = _ssh(station)
    state = iop.ready_state(controller.runner(ssh, _command(station, item, "inspect").command),
                            ERRORS)
    request = {"stream": item.digest, "ssh_trust": ssh.trust_sha256}
    digest = hashlib.sha256(repr((station, request, state)).encode()).hexdigest()
    plan_id = controller.store_plan(station, ACTION, digest, request=request,
                                    remote_sha256=state)
    return {"plan_id": plan_id, "expected_sha256": digest, "expires_in_seconds": 300,
            "audio_exported": True,
            "impact": f"Mini 1 room audio (Quasar vqe_0, port {item.port}) streams to this PC "
                      f"in {item.run_s} s chunks until stopped; processed in RAM only, never "
                      "written to disk. The pinned client lives in station tmpfs and is "
                      "removed at the end; no Quasar stop or configuration change"}


def parse_chunk(meta: bytes) -> dict[str, Any]:
    """One chunk's stderr: the client's numbers-only report and the identity re-check."""
    try:
        if len(meta) > META_LIMIT:
            raise ValueError
        values = [wp._json(line) for line in meta.decode("utf-8", "strict").splitlines()]
        if len(values) == 1 and type(values[0]) is dict:
            value = values[0]
            if (set(value) == {"outcome", "error_code"} and value["outcome"] == "blocked"
                and str(value["error_code"]) in ERRORS):
                return {"status": "blocked", "error_code": value["error_code"]}
            if value == {"outcome": "quasar_changed"}:
                return {"status": "quasar_changed"}
        if len(values) != 2 or type(values[1]) is not dict or set(values[1]) != {
            "outcome", "quasar_unchanged"} or values[1]["outcome"] != "chunk_end" or type(
                values[1]["quasar_unchanged"]) is not bool:
            raise ValueError
        report = wp.stream_report(values[0])
    except (ValueError, TypeError, KeyError):
        return {"status": "invalid"}
    if not values[1]["quasar_unchanged"]:
        return {"status": "quasar_changed"}
    status = "complete" if report["outcome"] == "complete" else "failed"
    return {"status": status, "error_code": report["error_code"],
            "audio_bytes": report["channels"][0]["bytes"] if report["channels"] else 0}


class _Chunk:
    """One SSH session: stdout PCM to the shared queue, stderr metadata kept bounded."""

    def __init__(self, ssh: Station, command: str, events: queue.Queue[Any],
                 deadline_s: float) -> None:
        # Own process group: a terminal Ctrl+C reaches Python, which stops sessions itself.
        self.process = subprocess.Popen(ssh_argv(ssh, command), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        start_new_session=True)
        self.deadline = time.monotonic() + deadline_s
        self.buf = bytearray()
        self.carry = b""
        self.skip = 0
        self.first_at: float | None = None
        self.stopped = False
        self.meta: list[bytes] = []
        self.reader = threading.Thread(target=self._read, args=(events,), daemon=True)
        self.errors = threading.Thread(target=self._errors, daemon=True)
        self.reader.start()
        self.errors.start()

    def _read(self, events: queue.Queue[Any]) -> None:
        assert self.process.stdout is not None
        fd = self.process.stdout.fileno()
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            events.put((self, data))
            if not data:
                return

    def _errors(self) -> None:
        assert self.process.stderr is not None
        self.meta.append(self.process.stderr.read(META_LIMIT + 1))

    def signal(self, number: int) -> None:
        try:
            os.killpg(self.process.pid, number)
        except OSError:
            pass  # Already gone.

    def stop(self) -> None:
        self.stopped = True
        self.signal(signal.SIGTERM)

    def result(self) -> dict[str, Any]:
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.signal(signal.SIGKILL)
            self.process.wait(timeout=5)
        self.errors.join(timeout=5)
        status = parse_chunk(self.meta[0] if self.meta else b"")
        if self.process.returncode and status["status"] == "complete":
            status = {"status": "failed", "error_code": "transport_uncertain"}
        return status


def _setup_result(raw: str) -> dict[str, Any]:
    value = wp._json(raw) if len(raw) <= 1024 else None
    if (type(value) is dict and set(value) == {"outcome", "identity_sha256", "stale_reclaimed"}
        and value["outcome"] == "ready" and type(value["identity_sha256"]) is str
        and re.fullmatch("[a-f0-9]{64}", value["identity_sha256"])
        and type(value["stale_reclaimed"]) is bool):
        return value
    if (type(value) is dict and set(value) == {"outcome", "error_code"} and (
            value["outcome"] == "blocked" and str(value["error_code"]) in ERRORS
            or value == {"outcome": "unknown", "error_code": "helper_transfer_failed"})):
        return value
    raise ValueError


def _cleanup(raw: str) -> dict[str, bool]:
    value = wp._json(raw) if len(raw) <= 1024 else None
    if (type(value) is not dict or set(value) != {"outcome", *wp.FLAGS}
        or value["outcome"] != "cleanup" or any(type(value[k]) is not bool for k in wp.FLAGS)):
        raise ValueError
    return {k: value[k] for k in wp.FLAGS}


def _session(ssh: Station, station: Station, item: Stream, known: str,
             sink: Callable[[bytes], None], stop: threading.Event, max_chunks: int | None,
             backoff_s: float, live: dict[str, Any] | None = None) -> dict[str, Any]:
    stats: dict[str, Any] = live if live is not None else {}  # Readable while it runs.
    stats.update(chunks=0, handovers=0, gaps=0, failures=0, audio_bytes=0, reason=None)
    events: queue.Queue[Any] = queue.Queue()
    command = _command(station, item, "chunk", known=known).command
    active: _Chunk | None = None
    pending: _Chunk | None = None
    running: list[_Chunk] = []
    history = bytearray()
    retry_at = 0.0
    failures = 0

    def feed(chunk: _Chunk, data: bytes) -> None:
        data = chunk.carry + data
        cut = len(data) - len(data) % 2
        chunk.carry, data = data[cut:], data[:cut]
        if chunk.skip:
            dropped = min(chunk.skip, len(data))
            chunk.skip -= dropped
            data = data[dropped:]
        if data:
            sink(data)
            stats["audio_bytes"] += len(data)
            history.extend(data)
            if len(history) > 131072:
                del history[:-65536]

    def switch() -> None:
        nonlocal active, pending
        assert pending is not None
        if active is not None:
            # Both streams carry the same frames: find the new stream's first frame in what
            # was already fed and skip that much of it.
            probe = bytes(pending.buf[:MATCH])
            found = history.rfind(probe) if len(probe) >= 64 else -1
            if found >= 0 and (len(history) - found) % 2 == 0:
                pending.skip = len(history) - found
            stats["handovers"] += 1
        active, pending = pending, None
        data = bytes(active.buf)
        active.buf.clear()
        feed(active, data)

    def finished(chunk: _Chunk) -> None:
        nonlocal failures, retry_at
        running.remove(chunk)
        result = chunk.result()
        if chunk.stopped:
            return
        if result["status"] in {"blocked", "quasar_changed"}:
            stats["reason"] = result["status"] if result["status"] == "quasar_changed" else (
                "blocked:" + result["error_code"])
        elif result["status"] == "complete":
            failures = 0
        else:
            failures += 1
            stats["failures"] += 1
            retry_at = time.monotonic() + backoff_s
            if failures >= MAX_FAILURES:
                stats["reason"] = "repeated_failures"

    try:
        while stats["reason"] is None:
            now = time.monotonic()
            if stop.is_set():
                stats["reason"] = "stopped"
                break
            room = max_chunks is None or stats["chunks"] < max_chunks
            due = active is None if item.lead_s == 0 else (
                active is None or (active.first_at is not None
                                   and now >= active.first_at + item.run_s - item.lead_s))
            if pending is None and room and now >= retry_at and due:
                pending = _Chunk(ssh, command, events, item.run_s + 60)
                running.append(pending)
                stats["chunks"] += 1
            if not room and active is None and pending is None and not running:
                stats["reason"] = "max_chunks"
                break
            for chunk in running:
                if now > chunk.deadline:
                    chunk.signal(signal.SIGKILL)  # Hung session: its EOF follows, counts.
            try:
                chunk, data = events.get(timeout=0.2)
            except queue.Empty:
                continue
            if data:
                if chunk.first_at is None:
                    chunk.first_at = time.monotonic()
                if chunk is active:
                    feed(chunk, data)
                elif chunk is pending:
                    chunk.buf.extend(data)
                    if active is None or len(chunk.buf) >= 2 * MATCH:
                        switch()
                continue  # A retired stream's late data duplicates the new one: dropped.
            if chunk is active:
                active = None
                if pending is not None and pending.buf:
                    switch()
                else:
                    stats["gaps"] += 1  # No successor with audio yet: the feed pauses.
            elif chunk is pending:
                pending = None
            finished(chunk)
    except KeyboardInterrupt:
        stats["reason"] = "interrupted"
    except Exception:
        stats["reason"] = "error"  # A failing sink or transport: stop, clean up, report.
    finally:
        for chunk in list(running):
            chunk.stop()
            running.remove(chunk)
            chunk.result()
    if stats["reason"] == "max_chunks" and stats["gaps"]:
        stats["gaps"] -= 1  # The last window simply ended.
    return stats


def apply(controller: Controller, plan_id: str, expected: str, item: Stream,
          sink: Callable[[bytes], None], *, stop: threading.Event | None = None,
          max_chunks: int | None = None, backoff_s: float = 5.0,
          live: dict[str, Any] | None = None) -> dict[str, Any]:
    """Runs until `stop`, Ctrl+C, `max_chunks`, a Quasar change or repeated failures.

    `live`, when given, holds the session counters while it runs."""
    base: dict[str, Any] = {"do_not_retry": True, "audio_exported": False}
    try:
        plan = controller.take_plan(plan_id, expected)
        with controller._execution_lock:
            station = controller.station(plan.station.id)
            iop._permitted(controller, station)
            ssh = _ssh(station)
            if (plan.action != ACTION or station != plan.station
                or controller.clock() >= plan.expires
                or plan.request != {"stream": item.digest, "ssh_trust": ssh.trust_sha256}):
                raise SafetyError("Wake stream preview changed or expired")
            setup = _command(station, item, "apply", plan.remote_sha256)
    except (SafetyError, ValueError):
        return dict(base, outcome="blocked", error_code="preview_changed_or_expired")
    try:
        ready = _setup_result(_transfer(ssh, setup, limit=4096))
    except Exception:
        ready = {"outcome": "unknown", "error_code": "transport_uncertain"}
    if ready["outcome"] == "blocked":
        return dict(base, outcome="blocked", error_code=ready["error_code"])
    stats: dict[str, Any] = {"reason": "setup_" + str(ready.get("error_code"))}
    known = str(ready.get("identity_sha256", "0" * 64))
    try:
        if ready["outcome"] == "ready":
            stats = dict(_session(ssh, station, item, known, sink, stop or threading.Event(),
                                  max_chunks, backoff_s, live))
    finally:
        try:
            flags = _cleanup(_transfer(ssh, _command(station, item, "cleanup", known=known),
                                       limit=4096))
        except Exception:
            flags = dict.fromkeys(wp.FLAGS, False)
    good = stats["reason"] in {"stopped", "interrupted", "max_chunks"} and all(flags.values())
    audio = stats.pop("audio_bytes", 0)
    return dict(base, **stats, **flags, outcome="complete" if good else "unknown",
                audio_exported=bool(audio), audio_s=round(audio / 32000, 2),
                stale_reclaimed=ready.get("stale_reclaimed", False))
