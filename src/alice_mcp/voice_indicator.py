"""Mini 1 voice indicator: Alice's own listening pulse, recoloured, while the agent works.

Owner decision (2026-10-30): when a «компьютер» command is heard, the station plays a
recoloured copy of its stock `alice_listening.led` (same frames, timing and intensity
curve; red by default) with the stock `led_util` player, until the «Голос» session of the
`alice` agent is idle again, or at most `max_seconds`. If the agent fails or runs out of
time, the ring goes dark first and the station then says Alice's stock error phrase from
a hash-pinned host WAV through the resident's local-PCM path (no synthesis).

The resident LED show cannot hold this stock animation, so the player uses explicit pinned
SSH. Its idle preparation is bound to the resident's live state. Exactly one
player at a time; the SSH session's open stdin is the hold. Its close, a dropped session
or the on-device deadline stops it: SIGTERM (KILL after 100 ms), then one dark frame,
because led_util keeps its last frame lit. Setup: docs/IO_CHANNEL.md step 7.
"""

from __future__ import annotations

import array
import base64
import hashlib
import io
import json
import logging
import os
import re
import secrets
import select
import shlex
import stat
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alice_mcp import controls, native_transport
from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller, SafetyError, SSHInput, Station, _unique, ssh_argv

LOG = logging.getLogger(__name__)
IDLE = "01" + "00" * 18
STALE_S = 15.0  # The plugin rewrites its state every 5 s; older means no opencode is there.
POLL_S = 0.25
PATTERN_LIMIT = 32768
HEADERS = frozenset({"gammacorrection", "loop"})
FRAME = re.compile(r"(?:[0-9A-Fa-f]{6}[ \t]+){6}[0-9]{1,6}")
COLORS = {"red": "ff0000"}
ERRORS = frozenset({"not_owner", "invalid_request", "runtime_tools_missing", "firmware_mismatch",
                    "led_util_missing", "pattern_missing", "tmpfs_unavailable",
                    "stale_player_left", "player_busy", "temporary_path_busy"})

# Fixed station helper. Only validated numbers/pins are arguments; the pattern is a
# host-validated heredoc (hex, digits and two header words only).
SCRIPT = r'''
OWNER_UID=0
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
mode=$1; seconds=$2; firmware=$3
blocked() { printf 'outcome=blocked\nerror_code=%s\n' "$1"; exit 0; }
[ "$(id -u)" = "$OWNER_UID" ] || blocked not_owner
case "$mode" in
read|read_verified|play|play_verified|prepare_verified|cleanup) ;; *) blocked invalid_request;; esac
case "$seconds" in [1-9]|[1-8][0-9]|90) ;; *) blocked invalid_request;; esac
case "$firmware" in *[!0-9a-f]*) blocked invalid_request;; esac
[ "${#firmware}" = 64 ] || blocked invalid_request
for c in awk cat cut grep head id kill ls mkdir mkfifo pidof rm rmdir sha256sum sleep tr; do
    command -v "$c" >/dev/null 2>&1 || blocked runtime_tools_missing
done
Q=/system/vendor/quasar
# play_verified is internal: a consumed host preview just rechecked the resident's
# firmware/boot/Quasar/policy stamp, whose hash cache checks nanosecond file metadata.
if [ "$mode" != play_verified ] && [ "$mode" != prepare_verified ] &&
        [ "$mode" != read_verified ]; then
    [ -f "$Q/maind" ] && [ "$(sha256sum "$Q/maind" | cut -d ' ' -f 1)" = "$firmware" ] ||
        blocked firmware_mismatch
fi
PLAYER=$Q/led_util
[ -x "$PLAYER" ] || blocked led_util_missing
TMPFS=
for t in /tmp /run /dev/shm /var/run; do
    if awk -v m="$t" '$2 == m && $3 == "tmpfs" { f = 1 } END { if (!f) exit 1 }' /proc/mounts
    then TMPFS=$t; break; fi
done
[ -n "$TMPFS" ] && [ -d "$TMPFS" ] && [ ! -L "$TMPFS" ] || blocked tmpfs_unavailable
DIR=$TMPFS/alice-mcp-indicator
darken=1
ours() {
    for p in $(pidof led_util); do
        [ "$p" != "${preserve:-}" ] || continue
        tr '\000' ' ' < "/proc/$p/cmdline" | grep -qF "$DIR/" && echo "$p"
    done
}
private_dir() {
    [ -d "$DIR" ] && [ ! -L "$DIR" ] || return 1
    set -- $(ls -ldn "$DIR")
    [ "$1" = drwx------ ] && [ "$3" = "$(id -u)" ]
}
quit() {
    # Background children inherit ignored SIGINT; TERM stops the stock player promptly.
    pids=$(ours)
    [ -n "$pids" ] || return 0
    kill -TERM $pids
    i=0
    while [ -n "$(ours)" ] && [ "$i" -lt 30 ]; do
        sleep 0.1; i=$((i + 1))
        if [ "$i" = 1 ]; then
            pids=$(ours)
            [ -z "$pids" ] || kill -KILL $pids
        fi
    done
    [ -z "$(ours)" ]
}
stop() {
    # Stop our players, then one dark frame (led_util keeps its last frame lit).
    if [ -n "$(ours)" ]; then
        preserve=${dark_player:-}
        quit || return 1
        preserve=
        private_dir || return 1
        if [ "$darken" = 1 ]; then
            if [ -n "${dark_player:-}" ]; then
                kill -0 "$dark_player" 2>/dev/null || return 1
                (
                    printf '%s\n' '000000 000000 000000 000000 000000 000000 100'
                ) > "$DIR/off.led" &
                feeder=$!
                (sleep 2; kill -TERM "$feeder" 2>/dev/null) &
                writer_guard=$!
                wait "$feeder" || { kill "$writer_guard"; return 1; }
                feeder=
                kill "$writer_guard"
            else
                printf '%s\n' '000000 000000 000000 000000 000000 000000 100' > "$DIR/off.led" ||
                    return 1
                "$PLAYER" "$DIR/off.led" </dev/null >/dev/null 2>&1 &
            fi
            sleep 1
            quit || return 1
        fi
    fi
    if [ -e "$DIR" ] || [ -L "$DIR" ]; then
        private_dir && rm -f "$DIR/indicator.led" "$DIR/off.led" "$DIR/wait.led" &&
            rmdir "$DIR" || return 1
    fi
    [ -z "$(ours)" ]
}
finish() {
    trap - 0 HUP INT TERM
    [ -z "${watchdog:-}" ] || kill "$watchdog"
    [ -z "${feeder:-}" ] || kill "$feeder" 2>/dev/null
    if stop; then printf 'outcome=applied\nleft_running=0\n'
    else printf 'outcome=unknown\nleft_running=1\n'; fi
}
case "$mode" in
read|read_verified)
    P=$Q/ledpatterns/alice_listening.led
    [ -f "$P" ] && [ ! -L "$P" ] || blocked pattern_missing
    printf 'outcome=ready\n'
    head -c 32769 "$P"
    ;;
cleanup)
    if stop; then left=0; else left=1; fi
    printf 'outcome=cleanup\nleft_running=%s\n' "$left"
    ;;
play|play_verified|prepare_verified)
    stop || blocked stale_player_left
    [ -z "$(pidof led_util)" ] || blocked player_busy
    mkdir "$DIR" && private_dir || blocked temporary_path_busy
    cat > "$DIR/indicator.led" <<'ALICE_LED_PATTERN' || blocked temporary_path_busy
__PATTERN__
ALICE_LED_PATTERN
    trap finish 0
    trap 'exit 0' HUP INT TERM
    if [ "$mode" = prepare_verified ]; then
        darken=0
        mkfifo "$DIR/wait.led" || blocked temporary_path_busy
        "$PLAYER" "$DIR/wait.led" </dev/null >/dev/null 2>&1 &
        player=$!
        main=$$
        (sleep 240; kill -TERM "$main") >/dev/null 2>&1 &
        watchdog=$!
        printf 'outcome=prepared\n'
        while read -r activation; do
            [ "$activation" = renew ] || break
            kill "$watchdog"
            (sleep 240; kill -TERM "$main") >/dev/null 2>&1 &
            watchdog=$!
            printf 'outcome=prepared\n'
        done
        [ "${activation:-}" = play ] || exit 0
        kill -0 "$player" 2>/dev/null || exit 0
        kill "$watchdog"
        (sleep 10; kill -TERM "$main") >/dev/null 2>&1 &
        watchdog=$!
        darken=1
        cat "$DIR/indicator.led" > "$DIR/wait.led" &
        feeder=$!
        wait "$feeder" || exit 0
        feeder=
        kill "$watchdog"
    else
        "$PLAYER" "$DIR/indicator.led" </dev/null >/dev/null 2>&1 &
    fi
    printf 'outcome=started\n'
    if [ "$mode" = prepare_verified ]; then
        mkfifo "$DIR/off.led" || exit 0
        "$PLAYER" "$DIR/off.led" </dev/null >/dev/null 2>&1 &
        dark_player=$!
    fi
    # Held while the host keeps stdin open: its close, a dropped session or the
    # on-device deadline ends the hold. Children never keep the output open.
    exec 7<&0
    cat <&7 >/dev/null &
    reader=$!
    (exec 7<&- >/dev/null; sleep "$seconds"; kill "$reader") &
    watchdog=$!
    wait "$reader"
    exit 0
    ;;
esac
'''


def recolor(text: str, color: str) -> str:
    """Same frames, timing and intensity curve in another hue; "purple" is the stock file.

    Each zone becomes the target colour scaled by max(R, G, B) / 255 of the original.
    """
    target = None
    if color != "purple":
        named = COLORS.get(color) or (color[1:] if re.fullmatch("#[0-9A-Fa-f]{6}", color)
                                      else None)
        if named is None:
            raise ValueError("colour")
        target = bytes.fromhex(named)
    if len(text) > PATTERN_LIMIT:
        raise ValueError("pattern size")
    out, frames = [], 0
    for line in text.splitlines():
        words = line.split()
        if len(words) <= 1 and (not words or words[0] in HEADERS):
            out.append(" ".join(words))
            continue
        if not FRAME.fullmatch(" ".join(words)):
            raise ValueError("unknown pattern line")
        frames += 1
        if target is not None:
            words[:6] = [bytes(round(v * max(bytes.fromhex(w)) / 255) for v in target).hex()
                         for w in words[:6]]
        out.append(" ".join(words))
    if not frames:
        raise ValueError("no frames")
    return text if target is None else "\n".join(out) + "\n"


def _command(station: Station, mode: str, seconds: int = 1, pattern: str = "") -> SSHInput:
    args = [mode, str(seconds), station.controls["firmware_sha256"]]
    return SSHInput("sh -s -- " + " ".join(shlex.quote(a) for a in args),
                    SCRIPT.replace("__PATTERN__", pattern.rstrip("\n")).encode())


def play_command(station: Station, pattern: str, seconds: int) -> SSHInput:
    recolor(pattern, "purple")  # Same format check: nothing else reaches the heredoc.
    if not 1 <= seconds <= 90:
        raise ValueError("seconds")
    return _command(station, "play", seconds, pattern)


def _result(text: str) -> dict[str, Any]:
    fields = dict(line.partition("=")[::2] for line in text.splitlines()[:3])
    if fields.get("outcome") == "blocked" and fields.get("error_code") in ERRORS:
        return {"outcome": "blocked", "error_code": fields["error_code"]}
    if (set(fields) == {"outcome", "left_running"}
            and fields["outcome"] in ("applied", "unknown", "cleanup")
            and fields["left_running"] in ("0", "1")):
        return {"outcome": fields["outcome"], "left_running": fields["left_running"] == "1"}
    return {"outcome": "unknown"}


Runner = Callable[[Station, "str | SSHInput"], str]


def read_pattern(runner: Runner, station: Station, *, verified: bool = False) -> str:
    """The stock listening pattern, format-checked."""
    raw = runner(station, _command(station, "read_verified" if verified else "read"))
    head, _, text = raw.partition("\n")
    if head != "outcome=ready":
        raise SafetyError(_result(raw).get("error_code", "pattern_unavailable"))
    recolor(text, "purple")
    return text


def cleanup(runner: Runner, station: Station) -> dict[str, Any]:
    """Stop any player of ours (dark frame after it) and verify none remains."""
    return _result(runner(station, _command(station, "cleanup")))


def stock_wav(data: bytes) -> bytes:
    """A converted stock WAV as the resident's canonical quiet PCM (peak <= 8191)."""
    with wave.open(io.BytesIO(data)) as source:
        rate = source.getframerate()
        if (source.getnchannels(), source.getsampwidth()) != (1, 2) or not (
                16000 <= rate <= 48000) or source.getnframes() > rate * 20:
            raise ValueError("unsupported WAV")
        samples = array.array("h", source.readframes(source.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max((abs(s) for s in samples), default=0)
    if peak > 8191:
        samples = array.array("h", (s * 8191 // peak for s in samples))
    if sys.byteorder != "little":
        samples.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(samples.tobytes())
    return out.getvalue()


def _spawn(station: Station, command: SSHInput) -> subprocess.Popen[bytes]:
    return subprocess.Popen(ssh_argv(station, command.command), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            start_new_session=True)


class Hold:
    """One running `play` session: the ring keeps playing while its stdin is open."""

    def __init__(self, process: subprocess.Popen[bytes], data: bytes = b"") -> None:
        self.process = process
        self.prefix = b""
        assert process.stdin is not None
        if data:
            process.stdin.write(data)  # The helper source; far below the pipe buffer.
            process.stdin.flush()

    def alive(self) -> bool:
        return self.process.poll() is None

    def wait_started(self, timeout: float = 10, *, prepared: bool = False) -> bool:
        """Wait for the remote player's launch, not just the local SSH process."""
        assert self.process.stdout is not None
        fd = self.process.stdout.fileno()
        deadline = time.monotonic() + timeout
        while len(self.prefix) < 256:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                return False
            byte = os.read(fd, 1)
            if not byte:
                return False
            self.prefix += byte
            if byte == b"\n":
                started = self.prefix == (b"outcome=prepared\n" if prepared
                                          else b"outcome=started\n")
                if started:
                    self.prefix = b""
                return started
        return False

    def start(self) -> bool:
        assert self.process.stdin is not None
        self.process.stdin.write(b"play\n")
        self.process.stdin.flush()
        return self.wait_started()

    def renew(self) -> bool:
        assert self.process.stdin is not None
        self.process.stdin.write(b"renew\n")
        self.process.stdin.flush()
        return self.wait_started(prepared=True)

    def release(self, timeout: float = 20) -> dict[str, Any]:
        """Close stdin; the helper stops the player and darkens the ring."""
        try:
            out, _ = self.process.communicate(timeout=timeout)
            return _result((self.prefix + out).decode("utf-8", "replace"))
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        except (OSError, ValueError):
            pass
        return {"outcome": "unknown"}


def _run(controller: Controller, station_id: str, action: str,
         params: dict[str, Any]) -> dict[str, Any]:
    """One fresh preview over explicit SSH, consumed once; never retried."""
    plan = controls.preview(controller, station_id, action, params, ssh=True)
    return controller.execute_plan(controller.take_plan(plan["plan_id"],
                                                        plan["expected_sha256"]))


def capture_start(controller: Controller, station_id: str, seconds: int = 30) -> dict[str, Any]:
    """Observe Quasar's own LED writes for `seconds` (evidence for raw Mini 1 LED actions).
    Once capture_ready is true the owner asks Alice something short and waits for dark."""
    return _run(controller, station_id, "led.capture", {"duration_seconds": seconds})


def _usable(records: list[dict[str, Any]]) -> bool:
    """Host copy of the helper's idle-evidence rule: whole-ring PWM frames with updates,
    something lit, ending in the observed zero frame."""
    data = [r["data_hex"] for r in records]
    frames = data[0::2]
    return (4 <= len(data) <= 64 and len(data) % 2 == 0
            and all(update == "25aa" for update in data[1::2])
            and all(len(f) == 38 and f[:2] == "01" and f[2:] == f[2:8] * 6 for f in frames)
            and any(f != IDLE for f in frames) and frames[-1] == IDLE)


def capture_status(controller: Controller, station_id: str, capture_id: str) -> dict[str, Any]:
    """`usable` says whether the evidence is good for LED writes (else capture again)."""
    result = controls.capture_status(controller, station_id, capture_id, ssh=True)
    records = result.pop("records", [])
    return dict(result, records=len(records),
                usable=result["outcome"] == "complete" and _usable(records))


def configure(inventory: Path, station_id: str, **fields: Any) -> dict[str, Any]:
    """Merge fields into the station's private controls.voice.indicator (None removes one).

    Validated before writing; a create-only 0600 backup first, then an atomic 0600
    replace that fails if the file changed meanwhile. A running MCP that predates a
    field rejects the inventory until it restarts.
    """
    try:
        path = inventory.resolve(strict=True)
        info = path.stat()
        original = path.read_bytes()
        if len(original) > 65536:
            raise ValueError("inventory too large")
        data = json.loads(original, object_pairs_hook=_unique)
        entry = next(e for e in data["stations"] if e["id"] == station_id)
        voice = entry["controls"]["voice"]
        merged = {**voice.get("indicator", {}), **fields}
        voice["indicator"] = {k: v for k, v in merged.items() if v is not None}
        rt.validate_policy(entry["controls"], entry["model"])
        updated = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        backup = path.with_name(f"{path.name}.indicator-backup-{secrets.token_hex(8)}")
        rt._atomic(backup, original, 0o600, info.st_uid, info.st_gid,
                   expected={"original": None})
        rt._atomic(path, updated, 0o600, info.st_uid, info.st_gid, expected={
            "original": base64.b64encode(original).decode(),
            "mode": info.st_mode & 0o777, "uid": info.st_uid, "gid": info.st_gid})
    except (OSError, ValueError, TypeError, KeyError, StopIteration, RecursionError):
        raise SafetyError("Indicator configuration not saved; inventory unchanged") from None
    return {"status": "saved", "indicator": voice["indicator"], "backup": backup.name,
            "next_step": "Restart opencode so the alice MCP reloads"}


def agent_state_path() -> Path:
    """Written by the opencode plugin: {session_status, ts (epoch ms), last_event_id}."""
    state = os.environ.get("XDG_STATE_HOME", "")
    base = Path(state) if state.startswith("/") else Path.home() / ".local/state"
    return base / "alice-mcp" / "voice-agent-state.json"


def read_agent(path: Path) -> dict[str, Any] | None:
    """The «Голос» session status, or None when missing, foreign or malformed."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            raw = os.read(fd, 4097)
        finally:
            os.close(fd)
        value = json.loads(raw) if len(raw) <= 4096 else None
    except (OSError, ValueError):
        return None
    if (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and type(value) is dict
            and value.get("session_status") in ("busy", "idle", "error")
            and type(value.get("ts")) is int and type(value.get("last_event_id")) is int):
        return value
    return None


class Indicator:
    """Ring and error phrase for one station while its voice listener runs; one worker.

    wake() only records the newest command and never blocks detection. The worker plays
    the pattern while the agent is busy on that command (or a newer one), or while nobody
    has picked it up yet (`pickup_s`); a stale state file counts as idle. On the agent's
    error, or still busy after `max_seconds` since the newest command, the ring goes dark
    and then the error phrase plays once. An uncertain stop latches it off.
    """

    pickup_s = 10.0

    def __init__(self, controller: Controller, station_id: str, *,
                 spawn: Callable[[Station, SSHInput], Any] = _spawn,
                 state_path: Path | None = None,
                 defer_preparation: bool = False,
                 play: Callable[[Controller, Station, bytes], dict[str, Any]] = (
                     native_transport.play_pcm)) -> None:
        self.controller, self.station_id, self.spawn, self.play = (
            controller, station_id, spawn, play)
        self.state_path = state_path or agent_state_path()
        self.lock = threading.Lock()
        self.kick, self.stopping = threading.Event(), threading.Event()
        self.wake_id, self.wake_at, self.done_id, self.max_s = 0, 0.0, 0, 90
        self.managed = False
        self.completion: str | None = None
        self.latched = self.used = False
        self.prepare_enabled = not defer_preparation
        self.pattern: str | None = None
        self.pattern_cache: tuple[Station, str, str, str] | None = None
        self.warm: Hold | None = None
        self.warm_binding: tuple[Station, str, str] | None = None
        self.prepared: tuple[str, str] | None = None
        self.started_at: float | None = None
        self.startup_ms: int | None = None
        self.sound: str | None = None
        self.clean: bool | None = None
        self.state: str = "idle"
        self.reason: str | None = None
        self._configure()
        self.thread = threading.Thread(target=self._loop, name="alice-indicator", daemon=True)
        self.thread.start()

    def _config(self) -> dict[str, Any]:
        try:
            voice = self.controller.station(self.station_id).controls.get("voice", {})
            return dict(voice.get("indicator", {"backend": "off"}))
        except SafetyError:
            return {"backend": "off"}

    def _configure(self) -> dict[str, Any]:
        config = self._config()
        self.backend, self.max_s = config["backend"], config.get("max_seconds", 90)
        if self.backend != "ssh_led":
            # ponytail: native_led waits for a resident LED action that can hold a pattern.
            self._set("off", "disabled" if self.backend == "off" else "native_led_unsupported")
        elif self.state == "off":
            self._set("idle")
        return config

    def _set(self, state: str, reason: str | None = None) -> None:
        self.state, self.reason = state, reason

    def status(self) -> dict[str, Any]:
        return {"backend": self.backend, "state": self.state, "reason": self.reason,
                "error_sound": self.sound, "clean": self.clean,
                "prepared": self.prepared is not None, "startup_ms": self.startup_ms}

    def wake(self, event_id: int, *, managed: bool = False) -> None:
        self._configure()
        with self.lock:
            self.wake_id, self.wake_at = event_id, time.time()
            self.started_at = (time.monotonic() if self.state == "lit"
                               or self.backend != "ssh_led" else None)
            self.startup_ms = None
            self.sound = None
            self.managed, self.completion = managed, None
        self.kick.set()

    def complete(self, event_id: int, *, error: bool = False) -> None:
        """Realtime ends after playback drains, independently of the OpenCode session."""
        with self.lock:
            if self.managed and event_id == self.wake_id:
                self.completion = "error" if error else "idle"
        self.kick.set()

    def listen_ready(self) -> None:
        self.prepare_enabled = True
        self.kick.set()

    def close(self, timeout: float = 30) -> None:
        """Stop the ring, then verify on the station that no player of ours remains."""
        self.stopping.set()
        self.kick.set()
        self.thread.join(timeout)
        if self.used:
            try:
                ssh = controls._station(self.controller, self.station_id, "led.show", True)
                self.clean = cleanup(self.controller.runner, ssh) == {
                    "outcome": "cleanup", "left_running": False}
            except SafetyError:
                self.clean = False

    def _verdict(self) -> str:
        """busy (keep playing), idle or error, for the newest command."""
        with self.lock:
            wake_id, wake_at = self.wake_id, self.wake_at
            managed, completion = self.managed, self.completion
        if self.stopping.is_set() or wake_id <= self.done_id:
            return "idle"
        now = time.time()
        if managed:
            return completion or ("error" if now - wake_at >= self.max_s else "busy")
        agent = read_agent(self.state_path)
        fresh = bool(agent and now * 1000 - agent["ts"] < STALE_S * 1000
                     and agent["last_event_id"] >= wake_id)
        status = agent["session_status"] if agent and fresh else None
        if status == "error":
            return "error"
        if now - wake_at >= self.max_s:
            return "error" if status == "busy" else "idle"  # Still working: timed out.
        if status is not None:
            return "busy" if status == "busy" else "idle"
        return "busy" if now - wake_at < self.pickup_s else "idle"

    def _prepare(self) -> None:
        """Prepare a FIFO-blocked stock player; activation still needs a fresh preview."""
        self.prepared = None
        if (not self.prepare_enabled or self.latched or self.stopping.is_set()
                or self._configure()["backend"] != "ssh_led"):
            return
        try:
            station = self.controller.station(self.station_id)
            if not station.native_mcp:
                return
            native_transport._permission(self.controller, station, "led.show")
            ssh = controls._station(self.controller, self.station_id, "led.show", True)
            controls._check_permission(self.controller, ssh, "led.show")
            state = native_transport._state(self.controller, station, "led.show")
            binding = (station, ssh.trust_sha256, state)
            cached = self.pattern_cache
            pattern = (cached[3] if cached is not None and cached[:3] == binding
                       else read_pattern(self.controller.runner, ssh, verified=True))
            if (native_transport._state(self.controller, station, "led.show") != state
                    or self.controller.station(self.station_id) != station
                    or controls._station(
                        self.controller, self.station_id, "led.show", True) != ssh):
                return
            self.pattern_cache = (*binding, pattern)
            digest = hashlib.sha256(
                repr((station, ssh, state, pattern, SCRIPT)).encode()).hexdigest()
            # Fresh previews rebind live state; unchanged assets need no cold process restart.
            warm_binding = (station, ssh.trust_sha256, pattern)
            if self.warm is not None and (not self.warm.alive()
                                          or self.warm_binding != warm_binding):
                old, self.warm = self.warm, None
                if not self._finish(old):
                    return
            identifier = self.controller.store_plan(station, "voice.indicator.prepare", digest,
                                                    remote_sha256=state)
            plan = self.controller.take_plan(identifier, digest)
            native_transport._permission(self.controller, station, "led.show")
            if (self.controller.clock() >= plan.expires
                    or self.controller.station(self.station_id) != station
                    or controls._station(
                        self.controller, self.station_id, "led.show", True) != ssh):
                return
            hold = self.warm
            try:
                if hold is None:
                    command = _command(ssh, "prepare_verified", self.max_s,
                                       recolor(pattern, self._config().get("color", "red")))
                    hold = Hold(self.spawn(ssh, command), command.data)
                    self.used = True
                    ready = hold.wait_started(prepared=True)
                else:
                    ready = hold.renew()
                if not ready:
                    self.warm = None
                    self._finish(hold)
                    return
            except OSError:
                self.warm = None
                if hold is not None:
                    self._finish(hold)
                self.latched = True
                self._set("unavailable", "unknown_inspect_locally_do_not_retry")
                return
            self.warm, self.warm_binding = hold, warm_binding
            identifier = self.controller.store_plan(
                station, "voice.indicator", digest,
                request={"pattern": pattern, "ssh_trust": ssh.trust_sha256},
                remote_sha256=state)
            self.prepared = identifier, digest
        except (SafetyError, ValueError):
            pass  # Cold starts retain the full SSH firmware check.

    def _start(self, *, refresh: bool = True) -> Hold | None:
        try:
            station = self.controller.station(self.station_id)
            if not (self.controller.writes and station.preauthorize_all_actions):
                raise SafetyError("Indicator needs writes and standing preauthorization")
            ssh = controls._station(self.controller, self.station_id, "led.show", True)
            controls._check_permission(self.controller, ssh, "led.show")
            prepared, self.prepared = self.prepared, None
            verified = False
            if prepared is not None:
                try:
                    plan = self.controller.take_plan(*prepared)
                    verified = (station == plan.station
                        and ssh.trust_sha256 == plan.request["ssh_trust"]
                        and native_transport._state(
                        self.controller, station, "led.show") == plan.remote_sha256
                        and self.controller.clock() < plan.expires
                        and self.controller.station(self.station_id) == station)
                    if verified:
                        self.pattern = plan.request["pattern"]
                except SafetyError:
                    pass
            if not verified and station.native_mcp and refresh:
                # Resident preview expiry can change its stamp while the host plan is fresh.
                self._prepare()
                if self.latched:
                    return None
                if self.prepared is not None:
                    return self._start(refresh=False)
            if self.pattern is None:
                self.pattern = read_pattern(self.controller.runner, ssh)
            config = self._config()
            command = play_command(ssh, recolor(self.pattern, config.get("color", "red")),
                                   self.max_s)
            if verified:
                command = _command(ssh, "play_verified", self.max_s,
                                   recolor(self.pattern, config.get("color", "red")))
        except SafetyError as error:
            self._set("unavailable", str(error) if str(error) in ERRORS | {
                "pattern_unavailable"} else "blocked_by_policy_or_station")
            return None
        except ValueError:
            self._set("unavailable", "pattern_format_unknown")
            return None
        if self._verdict() != "busy":  # The agent finished while the station was checked.
            return None
        hold, self.warm = self.warm, None
        try:
            if hold is not None and not verified:
                if not self._finish(hold):
                    return None
                hold = None
            warmed = hold is not None
            if hold is None:
                hold = Hold(self.spawn(ssh, command), command.data)
        except OSError:
            self.latched = True
            self._set("unavailable", "unknown_inspect_locally_do_not_retry")
            return None
        self.used = True
        try:
            started = hold.start() if warmed else hold.wait_started()
        except OSError:
            started = False
        if not started:
            self._finish(hold)
            return None
        self.started_at = time.monotonic()
        self.startup_ms = round((time.time() - self.wake_at) * 1000)
        self._set("lit")
        return hold

    def _finish(self, hold: Hold) -> bool:
        result = hold.release()
        if result.get("outcome") == "applied":
            self._set("idle")
            return True
        if result.get("outcome") == "blocked":  # Nothing was started.
            self._set("unavailable", result["error_code"])
        else:
            self.latched = True
            self._set("unavailable", "unknown_inspect_locally_do_not_retry")
        return False

    def _error_sound(self) -> None:
        """Alice's stock error phrase, once; skipped with a logged reason if unusable."""
        sound = self._config().get("error_sound")
        if not sound:
            return
        try:
            data = Path(sound["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != sound["sha256"]:
                raise ValueError("hash mismatch")
            audio = stock_wav(data)
        except (OSError, ValueError, EOFError, wave.Error) as error:
            LOG.warning("Voice error sound skipped: %s", type(error).__name__)
            self.sound = "skipped_sound_unavailable"
            return
        try:
            result = self.play(self.controller, self.controller.station(self.station_id),
                               audio)
            self.sound = str(result.get("outcome"))
        except SafetyError:
            self.sound = "skipped_resident_unavailable"

    def _loop(self) -> None:
        while not self.stopping.is_set():
            self._prepare()
            if not self.kick.wait(220):  # Renew before the prepared player's 240 s deadline.
                continue
            self.kick.clear()
            if self.stopping.is_set() or self._configure()["backend"] != "ssh_led":
                continue
            verdict = self._verdict()
            hold = self._start() if verdict == "busy" and not self.latched else None
            while verdict == "busy":
                if hold is not None and not hold.alive():
                    # On-device deadline while a newer command still runs: play again.
                    hold = self._start() if self._finish(hold) and not self.latched else None
                self.kick.wait(POLL_S)
                self.kick.clear()
                verdict = self._verdict()
            with self.lock:
                self.done_id = self.wake_id
            if hold is not None:
                self._finish(hold)
            if verdict == "error" and not self.stopping.is_set():
                self._error_sound()
        if self.warm is not None:
            hold, self.warm = self.warm, None
            self._finish(hold)
