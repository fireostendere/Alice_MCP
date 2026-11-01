"""Direct Realtime sessions from the station, the way a browser talks to the model.

The host mints a one-session client secret (60 s to connect, settings bound server-side), so
the API key never leaves this PC. A pinned station client (native/live_client.c) connects to
OpenAI itself, streams the echo-cancelled `vqe_0` microphone and plays the reply through one
continuous `pcm_player --stream`. Server VAD barge-in drops queued reply audio at once. No
audio is stored anywhere; the station keeps the secret only in process memory.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alice_mcp import io_channel_pilot as iop
from alice_mcp import native_transport, quasar_client, station_asr
from alice_mcp import wake_pilot as wp
from alice_mcp.core import Controller, SafetyError, SSHInput, ssh_argv
from alice_mcp.vqe_experiment import _ssh
from alice_mcp.wake_simulate import read_pinned

# ARM32 glibc 2.23 builds; host paths are private policy (`voice.live_player/live_client`).
PLAYER_SHA256 = "6caee5b7e86478024c8c0f5c9e1e35d6acec3fa77d00e891a64f26867810fefe"
CLIENT_SHA256 = "c05fa81b4f0cc6827288424db1cf803df6a31d7abad42560a55a159af0c6087d"
LIMIT = 64 * 1024
SCRIPT = r"""set -u
umask 077
exec 2>/dev/null
check=$1; firmware=$2; psize=$3; ppin=$4; csize=$5; cpin=$6; root=$7; capin=$8; model=$9
shift 9; seconds=$1; idle=$2
end() { printf '{"event":"end","reason":"%s"}\n' "$1"; exit 0; }
[ "$(id -u)" = 0 ] || end not_owner
case "$model" in gpt-realtime-*) ;; *) end invalid_request;; esac
case "$psize$csize$seconds$idle" in ''|*[!0-9]*) end invalid_request;; esac
case "$root" in -|/bkupgrade/data/opt/alice-local-asr-*) ;; *) end invalid_request;; esac
DIR=$(mktemp -d /tmp/alice-mcp-live.XXXXXX) || end tmp_unavailable
trap 'rm -rf "$DIR"' EXIT
trap 'exit 0' HUP INT TERM
# bs=1: busybox head over-reads a pipe, and the secret follows these bytes on stdin.
dd bs=1 count="$psize" of="$DIR/player" 2>/dev/null
dd bs=1 count="$csize" of="$DIR/client" 2>/dev/null
[ "$(sha256sum "$DIR/player" | cut -d ' ' -f 1)" = "$ppin" ] || end pin_mismatch
[ "$(sha256sum "$DIR/client" | cut -d ' ' -f 1)" = "$cpin" ] || end pin_mismatch
[ "$root" = - ] || [ "$(sha256sum "$root/capture" | cut -d ' ' -f 1)" = "$capin" ] ||
 end pin_mismatch
# verified: the host matched the resident's firmware/boot/Quasar stamp to a full check.
[ "$check" = verified ] ||
 [ "$(sha256sum /system/vendor/quasar/maind | cut -d ' ' -f 1)" = "$firmware" ] ||
 end firmware_mismatch
pidof maind >/dev/null || end quasar_not_running
chmod 500 "$DIR/player" "$DIR/client"
set -- --player "$DIR/player" --model "$model" --max-seconds "$seconds" --idle-ms "$idle"
[ "$root" = - ] || set -- "$@" --capture "$root/capture"
LD_LIBRARY_PATH=/system/vendor/quasar/libs "$DIR/client" "$@"
"""
BLOCKED = frozenset({"not_owner", "invalid_request", "tmp_unavailable", "pin_mismatch",
                     "firmware_mismatch", "quasar_not_running", "player_unavailable"})
CLOUD = frozenset({"api_error", "response_failed", "connect_failed", "tls_failed",
                   "tls_unavailable", "ca_unavailable", "handshake_rejected"})


def _stamp(controller: Controller, station: Any) -> str:
    """The resident's firmware/boot/Quasar stamp, or "-" so the station hashes fully.

    The resident serves one call at a time and the ring's wake check may hold it. This
    read-only query is safely retried; the stamp only lets the station skip a hash.
    """
    for _ in range(10 if station.native_mcp else 0):
        try:
            return native_transport._state(controller, station, "led.show")
        except SafetyError as error:
            if "state unavailable" in str(error):
                break  # The resident is not running (e.g. after a reboot): no point waiting.
            time.sleep(0.15)
    return "-"


def mint(key: str, session: dict[str, Any], timeout: float, ttl: int = 60) -> str:
    import httpx

    response = httpx.post("https://api.openai.com/v1/realtime/client_secrets",
                          headers={"Authorization": f"Bearer {key}"}, trust_env=False,
                          json={"expires_after": {"anchor": "created_at", "seconds": ttl},
                                "session": session}, timeout=timeout)
    response.raise_for_status()
    secret = response.json()["value"]
    if not (isinstance(secret, str) and secret.startswith("ek_") and len(secret) <= 4096
            and all("!" <= c <= "~" for c in secret)):
        raise ValueError("invalid client secret")
    return secret


def run(controller: Controller, station_id: str, key: str, session: dict[str, Any],
        text: str, *, listen: bool, deadline: float, cancel: threading.Event,
        on_event: Callable[[dict[str, Any]], None] = lambda _: None,
        minter: Callable[[str, dict[str, Any], float], str] = mint,
        popen: Callable[..., Any] = subprocess.Popen,
        tool: Callable[[str, dict[str, Any]], str] | None = None) -> dict[str, Any]:
    """One station-side session; returns its end event. `cancel` stops it at once.

    The model's function calls arrive as `tool` events; `tool(name, arguments)` runs them
    on this PC and its text goes back as the call's output. Every call gets an answer.
    """
    station = controller.station(station_id)
    iop._permitted(controller, station)
    voice = station.controls["voice"]
    player = read_pinned(Path(voice["live_player"]), PLAYER_SHA256, LIMIT)
    client = read_pinned(Path(voice["live_client"]), CLIENT_SHA256, LIMIT)
    stamp = _stamp(controller, station)
    verified = stamp != "-" and quasar_client._verified.get(station.id) == stamp
    seconds = min(900, int(deadline - time.monotonic()))
    if seconds < 1:
        raise SafetyError("deadline_exceeded")
    command, source = quasar_client._loader(SCRIPT, (
        "verified" if verified else "full", station.controls["firmware_sha256"],
        str(len(player)), PLAYER_SHA256, str(len(client)), CLIENT_SHA256,
        voice["asr_directory"] if listen else "-", station_asr.MANIFEST["capture"],
        session["model"], str(seconds), "7000" if listen else "200"))
    launch = SSHInput(command, source + player + client)
    end: dict[str, Any] = {"event": "end", "reason": "unknown"}
    connected = False
    with popen(ssh_argv(_ssh(station), launch), stdin=subprocess.PIPE,
               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            # The station checks pins and firmware while this PC mints the secret.
            process.stdin.write(launch.data)
            process.stdin.flush()
            try:
                secret = minter(key, session, max(1.0, min(15.0, deadline - time.monotonic())))
            except Exception:
                raise SafetyError("api_error") from None
            process.stdin.write(secret.encode() + b"\n" + " ".join(text.split()).encode()
                                + b"\n")
            process.stdin.flush()
            del secret
            lock = threading.Lock()

            def send(line: bytes) -> None:
                with lock, contextlib.suppress(OSError, ValueError):
                    process.stdin.write(line)
                    process.stdin.flush()

            def stopper() -> None:
                while process.poll() is None:
                    if cancel.wait(0.05):
                        send(b"stop\n")
                        return

            threading.Thread(target=stopper, name="alice-live-stop", daemon=True).start()
            for raw in iter(lambda: process.stdout.readline(8192), b""):
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if type(event) is not dict or not isinstance(event.get("event"), str):
                    continue
                if event["event"] == "end":
                    end = event
                    break
                connected = connected or event["event"] == "connected"
                if event["event"] == "tool":
                    threading.Thread(target=_answer, args=(send, event.get("message"), tool,
                                                           b"stop\n"),
                                     name="alice-live-tool", daemon=True).start()
                on_event(event)
        finally:
            with contextlib.suppress(OSError, ValueError):
                process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    if connected and stamp != "-":
        quasar_client._verified[station.id] = stamp  # The station's full check passed.
    return _outcome(end, cancel)


def _answer(send: Callable[[bytes], None], message: Any,
            tool: Callable[[str, dict[str, Any]], str] | None, abort: bytes) -> None:
    """Every function call gets an output line; an unanswerable one ends the session."""
    call = message if type(message) is dict else {}
    call_id, name, raw = call.get("call_id"), call.get("name"), call.get("arguments")
    if not (isinstance(call_id, str) and 0 < len(call_id) <= 128):
        send(abort)
        return
    try:
        arguments = json.loads(raw) if isinstance(raw, str) and len(raw) <= 4096 else 0
        if tool is None or not isinstance(name, str) or type(arguments) is not dict:
            raise ValueError
        output = str(tool(name, arguments))[:4000]
    except Exception:
        output = "Инструмент сейчас недоступен."
    send(b"tool " + json.dumps({"type": "conversation.item.create", "item": {
        "type": "function_call_output", "call_id": call_id, "output": output}},
        ensure_ascii=False, separators=(",", ":")).encode() + b"\n")


def _outcome(end: dict[str, Any], cancel: threading.Event) -> dict[str, Any]:
    reason = end.get("reason")
    reason = reason if isinstance(reason, str) and len(reason) <= 64 else "unknown"
    report: dict[str, Any] = end["player"] if type(end.get("player")) is dict else {}
    finished = reason in {"idle", "deadline"} or reason == "stopped" and cancel.is_set()
    outcome = ("blocked" if reason in BLOCKED else "cloud_failed" if reason in CLOUD
               else "complete" if finished and report.get("outcome") == "complete"
               else "unknown")  # Playback may have happened: never retried.
    mic: dict[str, Any] = end["mic"] if type(end.get("mic")) is dict else {}
    return {"outcome": outcome, "reason": reason, "barge_ins": end.get("barge_ins", 0),
            "mic": {k: mic[k] for k in ("buffered_ms", "sent_ms", "peak", "sent_peak",
                                        "mean_square")
                    if type(mic.get(k)) is int},
            "player": {k: report[k] for k in ("outcome", "frames_written", "flushes",
                                              "underruns_recovered", "cleanup_verified")
                       if k in report}}


STANDBY_SCRIPT = r"""set -u
umask 077
exec 2>/dev/null
check=$1; firmware=$2; psize=$3; ppin=$4; csize=$5; cpin=$6; root=$7; capin=$8; detpin=$9
shift 9; seconds=$1
end() { printf '{"event":"end","reason":"%s"}\n' "$1"; exit 0; }
[ "$(id -u)" = 0 ] || end not_owner
case "$psize$csize$seconds" in ''|*[!0-9]*) end invalid_request;; esac
case "$root" in /bkupgrade/data/opt/alice-local-asr-*) ;; *) end invalid_request;; esac
DIR=$(mktemp -d /tmp/alice-mcp-live.XXXXXX) || end tmp_unavailable
trap 'rm -rf "$DIR"' EXIT
trap 'exit 0' HUP INT TERM
# bs=1: busybox head over-reads a pipe, and the secret follows these bytes on stdin.
dd bs=1 count="$psize" of="$DIR/player" 2>/dev/null
dd bs=1 count="$csize" of="$DIR/client" 2>/dev/null
[ "$(sha256sum "$DIR/player" | cut -d ' ' -f 1)" = "$ppin" ] || end pin_mismatch
[ "$(sha256sum "$DIR/client" | cut -d ' ' -f 1)" = "$cpin" ] || end pin_mismatch
[ "$(sha256sum "$root/capture" | cut -d ' ' -f 1)" = "$capin" ] || end pin_mismatch
[ "$(sha256sum "$root/wake-detector" | cut -d ' ' -f 1)" = "$detpin" ] || end pin_mismatch
# verified: the host matched the resident's firmware/boot/Quasar stamp to a full check.
[ "$check" = verified ] ||
 [ "$(sha256sum /system/vendor/quasar/maind | cut -d ' ' -f 1)" = "$firmware" ] ||
 end firmware_mismatch
pidof maind >/dev/null || end quasar_not_running
chmod 500 "$DIR/player" "$DIR/client"
set -- --player "$DIR/player" --max-seconds "$seconds" --idle-ms 7000 \
 --capture "$root/capture" --wake-detector "$root/wake-detector"
LD_LIBRARY_PATH=/system/vendor/quasar/libs "$DIR/client" "$@"
"""


# The running standby per station, so speech.openai can reuse its warm process.
ACTIVE: dict[str, Standby] = {}


class Standby:
    """The station listener: microWakeWord there, then one direct session per wake.

    One station process captures `vqe_0` once, feeds the pinned wake detector and keeps
    eight seconds in RAM, so the command said right after «компьютер» reaches the model
    from the wake word on while the session connects. Nothing leaves the station before
    this PC answers a wake with a fresh one-session secret. Detector lines stay untrusted.
    """

    def __init__(self, controller: Controller, station_id: str, key: str,
                 sink: Callable[[dict[str, Any]], None], stop: threading.Event, *,
                 popen: Callable[..., Any] = subprocess.Popen,
                 minter: Callable[..., str] = mint) -> None:
        self.controller, self.station_id, self.key = controller, station_id, key
        self.sink, self.stop, self.popen, self.minter = sink, stop, popen, minter
        self.process: Any = None
        self.events: queue.Queue[dict[str, Any]] | None = None
        self.lock = threading.Lock()
        self.busy = threading.Lock()  # Held for the one session the standby runs at a time.
        # One spare speech secret kept on this PC (10 min), so a say needs no mint first.
        self.spare: tuple[str, float] | None = None
        self.spare_used = threading.Event()
        self.connected = False

    def send(self, line: bytes) -> None:
        with self.lock, contextlib.suppress(OSError, ValueError, AttributeError):
            self.process.stdin.write(line)
            self.process.stdin.flush()

    def run(self) -> dict[str, Any]:
        """One bounded (< 1 h) station listener; returns how it ended."""
        station = self.controller.station(self.station_id)
        iop._permitted(self.controller, station)
        voice = station.controls["voice"]
        player = read_pinned(Path(voice["live_player"]), PLAYER_SHA256, LIMIT)
        client = read_pinned(Path(voice["live_client"]), CLIENT_SHA256, LIMIT)
        stamp = _stamp(self.controller, station)
        verified = stamp != "-" and quasar_client._verified.get(station.id) == stamp
        command, source = quasar_client._loader(STANDBY_SCRIPT, (
            "verified" if verified else "full", station.controls["firmware_sha256"],
            str(len(player)), PLAYER_SHA256, str(len(client)), CLIENT_SHA256,
            voice["asr_directory"], station_asr.MANIFEST["capture"], wp.FT_DETECTOR_SHA256,
            "3540"))  # The detector's one-hour audio bound includes capture restarts.
        launch = SSHInput(command, source + player + client)
        wakes = station_asr.WakeEvents()
        end: dict[str, Any] = {"reason": "unknown"}
        with self.popen(ssh_argv(_ssh(station), launch), stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            try:
                process.stdin.write(launch.data)
                process.stdin.flush()
                self.process = process
                ACTIVE[station.id] = self

                def stopper() -> None:
                    while process.poll() is None and not self.stop.wait(0.2):
                        pass
                    self.send(b"stop\n")

                threading.Thread(target=stopper, name="alice-live-standby", daemon=True).start()
                threading.Thread(target=self._keep_spare, args=(process,),
                                 name="alice-live-spare", daemon=True).start()
                for raw in iter(lambda: process.stdout.readline(16384), b""):
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if type(event) is not dict or not isinstance(event.get("event"), str):
                        continue
                    if event["event"] == "end":
                        end = event
                        break
                    if event["event"] == "detector":
                        try:
                            wake = wakes.accept(json.dumps(event.get("line")).encode())
                        except SafetyError:
                            end = {"reason": "wake_unverified"}
                            self.send(b"stop\n")
                            continue
                        if wakes.events[-1]["event"] == "ready" and len(wakes.events) == 1:
                            self.sink({"event": "ready", "wake_source": "station_mww"})
                        if wake is not None:
                            self.sink(wake)
                        continue
                    events = self.events
                    if events is not None:
                        events.put(event)
            finally:
                self.process = None
                if ACTIVE.get(station.id) is self:
                    del ACTIVE[station.id]
                with contextlib.suppress(OSError, ValueError):
                    process.stdin.close()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
        if self.connected and stamp != "-":
            quasar_client._verified[station.id] = stamp
        reason = end.get("reason")
        done = reason == "deadline" or reason == "stopped" and self.stop.is_set()
        return {"outcome": "complete" if done else "unknown",
                "reason": reason if isinstance(reason, str) and len(reason) <= 64 else "unknown"}

    def session(self, session: dict[str, Any], text: str, *, listen: bool, deadline: float,
                cancel: threading.Event,
                on_event: Callable[[dict[str, Any]], None] = lambda _: None,
                tool: Callable[[str, dict[str, Any]], str] | None = None) -> dict[str, Any]:
        """RealtimeTurn's `direct`: the standby connects and hears the owner from the wake."""
        if not listen:
            raise SafetyError("live_unavailable")
        return self._run_session("session", session, "", deadline, cancel, on_event, tool)

    def _keep_spare(self, process: Any) -> None:
        """Keep one fresh speech secret while this standby runs; it stays on this PC until used."""
        from alice_mcp import realtime_voice as rv

        while self.process is process and not self.stop.is_set():
            spare = self.spare
            if spare is None or time.monotonic() - spare[1] > 480:
                try:
                    self.spare = (self.minter(self.key, rv.speak_session(), 15, 600),
                                  time.monotonic())
                except Exception:
                    self.spare = None  # A say mints on demand instead.
                    self.stop.wait(30)
                    continue
            self.spare_used.wait(5)
            self.spare_used.clear()

    def say(self, text: str, *, deadline: float, cancel: threading.Event) -> dict[str, Any]:
        """speech.openai through the running standby: no SSH launch, firmware hash or mic."""
        from alice_mcp import realtime_voice as rv

        return self._run_session("say", rv.speak_session(), " " + " ".join(text.split()),
                                 deadline, cancel, lambda _: None, None)

    def _run_session(self, verb: str, session: dict[str, Any], text: str, deadline: float,
                     cancel: threading.Event, on_event: Callable[[dict[str, Any]], None],
                     tool: Callable[[str, dict[str, Any]], str] | None) -> dict[str, Any]:
        process = self.process
        seconds = min(900, int(deadline - time.monotonic()))
        if process is None or process.poll() is not None:
            raise SafetyError("live_unavailable")
        if seconds < 1:
            raise SafetyError("deadline_exceeded")
        if not self.busy.acquire(blocking=False):
            raise SafetyError("live_busy")  # One station session at a time: never overlap.
        events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.events = events
        end: dict[str, Any] = {"reason": "unknown"}
        try:
            try:
                spare, self.spare = (self.spare, None) if verb == "say" else (None, self.spare)
                if spare is not None and time.monotonic() - spare[1] < 540:
                    secret = spare[0]
                    self.spare_used.set()  # Mint the next one in the background.
                else:
                    secret = self.minter(self.key, session,
                                         max(1.0, min(15.0, deadline - time.monotonic())))
            except Exception:
                raise SafetyError("api_error") from None
            self.send(f"{verb} {session['model']} {seconds} {secret}{text}\n".encode())
            del secret
            cancelled = False
            while True:
                if cancel.is_set() and not cancelled:
                    self.send(b"cancel\n")
                    cancelled = True
                try:
                    event = events.get(timeout=0.05)
                except queue.Empty:
                    if self.process is not process:
                        break  # The standby ended mid-session.
                    continue
                if event["event"] == "session_end":
                    end = event
                    break
                self.connected = self.connected or event["event"] == "connected"
                if event["event"] == "tool":
                    threading.Thread(target=_answer, args=(self.send, event.get("message"), tool,
                                                           b"cancel\n"),
                                     name="alice-live-tool", daemon=True).start()
                on_event(event)
        finally:
            self.events = None
            self.busy.release()
        return _outcome(end, cancel)


def invoke(controller: Controller, station: Any, request: dict[str, Any]) -> dict[str, Any]:
    """controls.invoke for speech.openai: preview binds the pinned builds, apply speaks once."""
    from alice_mcp import realtime_voice as rv

    voice = station.controls["voice"]
    key = rv.api_key()
    try:
        if not key:
            raise ValueError
        read_pinned(Path(voice["live_player"]), PLAYER_SHA256, LIMIT)
        read_pinned(Path(voice["live_client"]), CLIENT_SHA256, LIMIT)
    except (ValueError, OSError, KeyError):
        raise SafetyError("Live voice key or pinned builds unavailable") from None
    state = hashlib.sha256(repr((station.controls["firmware_sha256"], voice["live_player"],
                                 voice["live_client"], PLAYER_SHA256, CLIENT_SHA256)).encode())
    if request["mode"] == "inspect":
        return {"outcome": "ready", "state_sha256": state.hexdigest()}
    if request.get("expected_sha256") != state.hexdigest():
        return {"outcome": "blocked", "error_code": "preview_state_changed"}
    standby, deadline = ACTIVE.get(station.id), time.monotonic() + 180
    try:
        result = (standby.say(request["params"]["text"], deadline=deadline,
                              cancel=threading.Event()) if standby is not None else
                  run(controller, station.id, key, rv.speak_session(),
                      request["params"]["text"], listen=False, deadline=deadline,
                      cancel=threading.Event()))
    except SafetyError as error:  # Busy or gone before anything was sent: nothing spoken.
        return {"outcome": "blocked", "error_code": str(error)}
    # A cloud failure may follow partial speech, so only a clean end counts as applied.
    return {"outcome": {"complete": "applied", "blocked": "blocked"}.get(result["outcome"],
                                                                         "unknown"),
            "reason": result["reason"], "playback": result["player"]}
