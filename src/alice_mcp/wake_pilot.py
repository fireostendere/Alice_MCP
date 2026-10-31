"""Owner-run Mini 1 on-station wake-word pilot (docs/IO_CHANNEL.md); not an MCP tool.

Two temporary hash-pinned helpers arrive over bounded binary SSH stdin: the IO-channel
client in --stream mode subscribes to Quasar's vqe_0 for at most 60 s and pipes the PCM
straight into the microWakeWord detector (docs/MICRO_WAKE_WORD.md) on the station. Only
detection events, frame counts, input levels and the detector's peak RSS and CPU ticks
(sampled from /proc) return. Audio never leaves station RAM and nothing but the two
helpers is written; they are removed. Quasar is not stopped or reconfigured. Uncertain
outcomes are never retried. Requires a fresh state-bound preview.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import sys
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alice_mcp import io_channel_pilot as iop
from alice_mcp.core import Controller, SafetyError, Station, _unique, confirm
from alice_mcp.pcm_pilot import _Transfer, _transfer
from alice_mcp.vqe_experiment import _ssh

# W5's three-model ARM32 build (komp, okey_dom, marusya); docs/MICRO_WAKE_WORD.md.
DETECTOR_SHA256 = "d2b73953eeaa144ff4b8baa08f06a660bbbc8be698852a3f4881f39c25be45c4"
# Personalized komp model; ARM build inspected locally, room latency still unqualified.
FT_DETECTOR_SHA256 = "bbafcf720f097f08a5effb1e162c535bed1e9fc4c23f5e82f2c1e8267421631f"
ACTION = "wake.pilot"
OUTPUT_LIMIT = 32768
REPLAY_LIMIT = 131072  # 10 s traced by three models is about 1,000 lines, 55 KB.
CLIP_MAX = 320000  # 10 s of 16 kHz S16 mono.
FLAGS = ("cleanup_verified", "quasar_running", "quasar_unchanged")
METRICS = ("detector_exit", "detector_vmhwm_kib", "detector_cpu_ticks")
END_REASONS = frozenset({"eof", "limit", "signal", "read_error", "frontend"})
DETECTOR_ERRORS = frozenset({"usage", "max_seconds_required", "frontend", "resolver", "schema",
                             "arena", "allocate", "model_shape", "invoke"})
NAME = re.compile("[a-z][a-z0-9_]{0,31}")

# Fixed wrapper; only validated numbers, pins and a fixed purpose are passed as arguments.
# purpose=wake keeps audio in station RAM; purpose=record (voice_record.py, owner-confirmed)
# is the one export: the client's vqe_0 PCM goes to the host for wake-word training;
# purpose=replay sends one host clip (the first stdin segment, not the client) into the
# detector with --trace and never reads room audio.
SCRIPT = r'''
OWNER_UID=0
LC_ALL=C; export LC_ALL
umask 077
exec 2>/dev/null
mode=$1; nonce=$2; port=$3; seconds=$4; gain=$5; firmware=$6; first_sha=$7; detector_sha=$8
first_size=$9; expected=${10}; purpose=${11}
blocked() {
    # Drain the helpers so the host's bounded send completes and the code arrives.
    [ "$mode" != apply ] || cat >/dev/null
    printf '{"outcome":"blocked","error_code":"%s"}\n' "$1"
    exit 0
}
[ "$(id -u)" = "$OWNER_UID" ] || blocked not_owner
case "$nonce" in ''|*[!0-9a-f]*) blocked invalid_request;; esac
[ "${#nonce}" = 32 ] || blocked invalid_request
case "$port" in ''|0*|*[!0-9]*) blocked invalid_request;; esac
[ "${#port}" -le 5 ] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || blocked invalid_request
case "$purpose:$seconds" in wake:[1-9]|wake:[1-5][0-9]|wake:60|record:[1-9]|record:10) ;;
    replay:[1-9]|replay:10) ;; *) blocked invalid_request;; esac
case "$gain" in [0-9]|[12][0-9]|30) ;; *) blocked invalid_request;; esac
case "$first_size" in ''|0*|*[!0-9]*) blocked invalid_request;; esac
[ "${#first_size}" -le 6 ] || blocked invalid_request
[ "$purpose" != replay ] || [ "$first_size" -le $((seconds * 32000)) ] || blocked invalid_request
for pin in "$firmware" "$first_sha" "$detector_sha"; do
    [ "${#pin}" = 64 ] || blocked invalid_request
    case "$pin" in *[!0-9a-f]*) blocked invalid_request;; esac
done
for c in awk cat chmod cut dd grep id kill ls mkdir od pidof readlink rm rmdir sha256sum \
    sleep sort; do
    command -v "$c" >/dev/null 2>&1 || blocked runtime_tools_missing
done
''' + iop.GUARDS + r'''OUT=$DATA/opt/alice-mcp-wake-$nonce
CLAIM=$DATA/opt/alice-mcp-wake-active
[ ! -e "$OUT" ] && [ ! -L "$OUT" ] || blocked temporary_path_busy
boot=$(sha256sum /proc/sys/kernel/random/boot_id | cut -d ' ' -f 1)
before=$(identity)
STATE=$(printf '%s\n' "$actual" "$boot" "$DATA" "$nonce" "$port" "$seconds" "$gain" \
    "$first_sha" "$detector_sha" "$first_size" "$before" "$inode" "$purpose" |
    sha256sum | cut -d ' ' -f 1)
if [ "$mode" = inspect ]; then
    printf '{"outcome":"ready","state_sha256":"%s"}\n' "$STATE"
    exit 0
fi
[ "$mode" = apply ] && [ "$expected" = "$STATE" ] || blocked preview_state_changed
own_out=0; own_claim=0; det=; det_exit=-1; hwm=-1; ticks=-1
cleanup() {
    trap - 0 HUP INT TERM
    if [ -n "$det" ] && [ "$(readlink /proc/$det/exe)" = "$OUT/detector" ]; then
        kill -TERM "$det"
    fi
    wait  # The client then stops on the closed pipe, at the latest at its own window end.
    clean=true
    if [ "$own_out" = 1 ]; then
        rm -f "$OUT/client" "$OUT/detector" "$OUT/clip" && rmdir "$OUT" || clean=false
        [ ! -e "$OUT" ] && [ ! -L "$OUT" ] || clean=false
    fi
    if [ "$own_claim" = 1 ]; then rmdir "$CLAIM" || clean=false; fi
    running=false; unchanged=false
    if pidof maind >/dev/null; then
        running=true
        [ "$(identity)" != "$before" ] || unchanged=true
    fi
    printf '{"outcome":"cleanup","cleanup_verified":%s,"quasar_running":%s,' "$clean" "$running"
    printf '"quasar_unchanged":%s,"detector_exit":%s,"detector_vmhwm_kib":%s,' \
        "$unchanged" "$det_exit" "$hwm"
    printf '"detector_cpu_ticks":%s}\n' "$ticks"
}
mkdir "$CLAIM" || blocked temporary_path_busy
own_claim=1
trap cleanup 0
trap 'exit 0' HUP INT TERM
mkdir "$OUT" || exit 0
own_out=1
# bs=1 stops exactly at the first segment's end (client, or the replay clip); the detector
# is the rest of stdin.
if [ "$purpose" = replay ]; then
    (set -C; dd bs=1 count="$first_size" > "$OUT/clip") || exit 0
    [ "$(sha256sum "$OUT/clip" | cut -d ' ' -f 1)" = "$first_sha" ] || exit 0
else
    (set -C; dd bs=1 count="$first_size" > "$OUT/client") || exit 0
    [ "$(sha256sum "$OUT/client" | cut -d ' ' -f 1)" = "$first_sha" ] || exit 0
    chmod 500 "$OUT/client" || exit 0
fi
exec 3>&1
if [ "$purpose" = record ]; then
    # The one export: PCM through cat to the host, then a newline and the client's report.
    report=$( { "$OUT/client" --stream "$port" "$seconds" </dev/null 2>&4 3>&- 4>&- |
        cat >&3 3>&- 4>&-; } 4>&1 )
    printf '\n%s\n' "$report"
    exit 0
fi
(set -C; cat > "$OUT/detector") || exit 0
[ "$(sha256sum "$OUT/detector" | cut -d ' ' -f 1)" = "$detector_sha" ] || exit 0
chmod 500 "$OUT/detector" || exit 0
if [ "$purpose" = replay ]; then
    # The pipe stays open 2 s after the clip, so /proc samples see the finished detector.
    { cat "$OUT/clip"; sleep 2; } </dev/null 3>&- |
        "$OUT/detector" --trace --max-seconds 90 --gain-db "$gain" 3>&- &
else
    # PCM goes only through this pipe; the client's numbers-only report goes to stdout via 3.
    "$OUT/client" --stream "$port" "$seconds" </dev/null 2>&3 3>&- |
        "$OUT/detector" --max-seconds 90 --gain-db "$gain" 3>&- &
fi
det=$!
# ponytail: 1 s /proc sampling; CPU after the last sample (<= 1 s of audio) is not counted.
while kill -0 "$det"; do
    set -- $(awk '$1 == "Name:" { n = $2 } $1 == "VmHWM:" { h = $2 }
        FILENAME ~ /stat$/ { t = $14 + $15 }
        END { if (n == "detector") print h + 0, t + 0 }' "/proc/$det/status" "/proc/$det/stat")
    if [ "$#" = 2 ]; then
        [ "$1" -le "$hwm" ] || hwm=$1
        [ "$2" -le "$ticks" ] || ticks=$2
    fi
    sleep 1
done
wait "$det"
det_exit=$?
det=
exit 0
'''


@dataclass(frozen=True)
class Pilot:
    seconds: int
    client: bytes = field(repr=False)
    detector: bytes = field(repr=False)
    gain_db: int = 0
    port: int = 9850  # yio_audio on Mini 1 (docs/IO_CHANNEL.md, round 2).
    nonce: str = field(default_factory=lambda: secrets.token_hex(16))

    def validate(self) -> None:
        if (type(self.seconds) is not int or not 1 <= self.seconds <= 60
            or type(self.gain_db) is not int or not 0 <= self.gain_db <= 30
            or type(self.port) is not int or not 1024 <= self.port <= 65535
            or not re.fullmatch("[a-f0-9]{32}", self.nonce)
            or type(self.client) is not bytes or type(self.detector) is not bytes
            or hashlib.sha256(self.client).hexdigest() != iop.HELPER_SHA256
            or hashlib.sha256(self.detector).hexdigest() not in {
                DETECTOR_SHA256, FT_DETECTOR_SHA256}):
            raise SafetyError("Wake pilot input or reviewed helpers invalid; no transfer")

    @property
    def digest(self) -> str:
        return hashlib.sha256(repr((
            SCRIPT, self.seconds, self.gain_db, self.port, self.nonce,
            hashlib.sha256(self.client).hexdigest(), hashlib.sha256(self.detector).hexdigest(),
        )).encode()).hexdigest()

    @property
    def impact(self) -> str:
        return (f"Two temporary pinned helpers: Quasar's vqe_0 room audio from loopback port "
                f"{self.port} is piped for {self.seconds} s into the on-station wake detector; "
                "only detection events, frame counts, levels and detector RSS/CPU return. No "
                "audio stored or exported, no Quasar stop or configuration change")


@dataclass(frozen=True)
class Replay:
    """One host clip (raw S16LE 16 kHz mono, at most 10 s) into the pinned detector."""

    pcm: bytes = field(repr=False)
    detector: bytes = field(repr=False)
    gain_db: int = 0
    port: int = 9850  # Only the read-only listener guard; replay never connects to Quasar.
    nonce: str = field(default_factory=lambda: secrets.token_hex(16))

    @property
    def seconds(self) -> int:
        return max(1, -(-len(self.pcm) // 32000))

    def validate(self) -> None:
        if (type(self.pcm) is not bytes or not 0 < len(self.pcm) <= CLIP_MAX or len(self.pcm) % 2
            or type(self.gain_db) is not int or not 0 <= self.gain_db <= 30
            or type(self.port) is not int or not 1024 <= self.port <= 65535
            or not re.fullmatch("[a-f0-9]{32}", self.nonce) or type(self.detector) is not bytes
            or hashlib.sha256(self.detector).hexdigest() not in {
                DETECTOR_SHA256, FT_DETECTOR_SHA256}):
            raise SafetyError("Replay clip or reviewed detector invalid; no transfer")

    @property
    def digest(self) -> str:
        return hashlib.sha256(repr((
            SCRIPT, "replay", self.gain_db, self.port, self.nonce,
            hashlib.sha256(self.pcm).hexdigest(), hashlib.sha256(self.detector).hexdigest(),
        )).encode()).hexdigest()

    @property
    def impact(self) -> str:
        return (f"One host clip ({len(self.pcm) / 32000:.2f} s, sha256 "
                f"{hashlib.sha256(self.pcm).hexdigest()[:12]}) is replayed into the temporary "
                "pinned on-station detector with --trace; only per-model maxima and detector "
                "RSS/CPU return. No room audio is read; clip and detector are removed; no "
                "Quasar change")


def pcm_from_wav(path: Path) -> bytes:
    """The PCM of a voice_record clip: 16 kHz mono S16 WAV, at most 10 s."""
    try:
        with wave.open(str(path), "rb") as clip:
            frames = clip.getnframes()
            if ((clip.getnchannels(), clip.getsampwidth(), clip.getframerate(),
                 clip.getcomptype()) != (1, 2, 16000, "NONE")
                or not 0 < frames <= CLIP_MAX // 2):
                raise ValueError
            data = clip.readframes(frames)
        if len(data) != 2 * frames:
            raise ValueError
        return data
    except (OSError, EOFError, wave.Error, ValueError):
        raise SafetyError("Replay clip must be a 16 kHz mono S16 WAV of at most 10 s") from None


def shell(station: Station, mode: str, expected: str, purpose: str, *, nonce: str, port: int,
          seconds: int, first: bytes, gain_db: int = 0, detector: bytes = b"") -> _Transfer:
    """The fixed wrapper's call; inspect carries no transfer stanza and no helper bytes."""
    source = SCRIPT if mode == "apply" else SCRIPT[:SCRIPT.index('[ "$mode" = apply ]')]
    args = [mode, nonce, str(port), str(seconds), str(gain_db),
            station.controls["firmware_sha256"], hashlib.sha256(first).hexdigest(),
            hashlib.sha256(detector).hexdigest() if detector else DETECTOR_SHA256,
            str(len(first)), expected, purpose]
    return _Transfer("sh -c " + shlex.quote(source) + " sh "
                     + " ".join(shlex.quote(a) for a in args),
                     first + detector if mode == "apply" else b"")


def _command(station: Station, item: Pilot | Replay, mode: str,
             expected: str = "") -> _Transfer:
    item.validate()
    if isinstance(item, Replay):
        return shell(station, mode, expected, "replay", nonce=item.nonce, port=item.port,
                     seconds=item.seconds, first=item.pcm, gain_db=item.gain_db,
                     detector=item.detector)
    return shell(station, mode, expected, "wake", nonce=item.nonce, port=item.port,
                 seconds=item.seconds, first=item.client, gain_db=item.gain_db,
                 detector=item.detector)


def preview(controller: Controller, station_id: str, item: Pilot | Replay) -> dict[str, Any]:
    station = controller.station(station_id)
    iop._permitted(controller, station)
    item.validate()
    ssh = _ssh(station)
    state = iop.ready_state(controller.runner(ssh, _command(station, item, "inspect").command))
    request = {"pilot": item.digest, "ssh_trust": ssh.trust_sha256}
    digest = hashlib.sha256(repr((station, request, state)).encode()).hexdigest()
    plan_id = controller.store_plan(station, ACTION, digest, request=request,
                                    remote_sha256=state)
    return {"plan_id": plan_id, "expected_sha256": digest, "expires_in_seconds": 300,
            "audio_exported": False, "impact": item.impact}


def _unit(value: Any) -> bool:
    return type(value) in (int, float) and 0 <= value <= 1


def _ready(value: dict[str, Any], gain_db: int) -> dict[str, int]:
    if (set(value) != {"event", "rate", "threshold", "window", "cooldown_ms", "gain_db",
                       "models"}
        or not iop._integer(value["rate"], 16000, 16000) or not _unit(value["threshold"])
        or not iop._integer(value["window"], 1, 50)
        or not iop._integer(value["cooldown_ms"], 0, 60000)
        or not iop._integer(value["gain_db"], gain_db, gain_db)
        or type(value["models"]) is not list or not 1 <= len(value["models"]) <= 8):
        raise ValueError
    names: dict[str, int] = {}
    for model in value["models"]:
        if (type(model) is not dict or set(model) != {"name", "sha256", "stride", "arena_bytes"}
            or type(model["name"]) is not str or not NAME.fullmatch(model["name"])
            or model["name"] in names or type(model["sha256"]) is not str
            or not re.fullmatch("[a-f0-9]{64}", model["sha256"])
            or not iop._integer(model["stride"], 1, 10)
            or not iop._integer(model["arena_bytes"], 0, 160 * 1024)):
            raise ValueError
        names[model["name"]] = model["stride"]
    return names


def _detector(events: list[dict[str, Any]], gain_db: int, trace: bool = False, *,
              limit_ms: int = 90000) -> tuple[
        dict[str, int] | None, list[dict[str, Any]], dict[str, Any] | None, str | None,
        dict[str, list[int]]]:
    """Detector lines in their own order: ready, (p|wake)*, then end or error. Untrusted."""
    models: dict[str, int] | None = None
    detections: list[dict[str, Any]] = []
    end: dict[str, Any] | None = None
    error: str | None = None
    traces: dict[str, list[int]] = {}
    last: dict[str, int] = {}
    for value in events:
        kind = value["event"]
        if end is not None or error is not None:
            raise ValueError
        if kind == "error" and set(value) == {"event", "error"}:
            if value["error"] not in DETECTOR_ERRORS:
                raise ValueError
            error = value["error"]
        elif kind == "ready" and models is None:
            models = _ready(value, gain_db)
        elif (kind == "p" and trace and models is not None
              and set(value) == {"event", "model", "t_ms", "q"} and value["model"] in models
              and iop._integer(value["t_ms"], last.get(value["model"], -1) + 1, limit_ms)
              and iop._integer(value["q"], 0, 255)):
            expected_ms = (2 + (len(traces.get(value["model"], [])) + 1)
                           * models[value["model"]]) * 10
            if value["t_ms"] != expected_ms:
                raise ValueError
            traces.setdefault(value["model"], []).append(value["q"])
            last[value["model"]] = value["t_ms"]
        elif (kind == "wake" and models is not None
              and set(value) == {"event", "model", "t_ms", "probability", "peak", "last",
                                 "trusted"}
              and value["model"] in models and iop._integer(value["t_ms"], 0, limit_ms)
              and all(_unit(value[k]) for k in ("probability", "peak", "last"))
              and value["trusted"] is False
              and len(detections) < max(128, limit_ms // 2500 + 1)):
            detections.append({"word": value["model"], "probability": value["probability"],
                               "t_ms": value["t_ms"], "trusted": False})
        elif (kind == "end" and models is not None
              and set(value) == {"event", "reason", "audio_ms", "detections"}
              and value["reason"] in END_REASONS
              and iop._integer(value["audio_ms"], 0, limit_ms)
              and type(value["detections"]) is list
              and all(type(n) is int for n in value["detections"])
              and value["detections"] == [sum(d["word"] == name for d in detections)
                                          for name in models]):
            end = value
        else:
            raise ValueError
    return models, detections, end, error, traces


def trailer(value: Any) -> dict[str, Any]:
    if (type(value) is not dict or set(value) != {"outcome", *FLAGS, *METRICS}
        or value["outcome"] != "cleanup" or any(type(value[k]) is not bool for k in FLAGS)
        or not all(iop._integer(value[k], -1, 2**31 - 1) for k in METRICS)):
        raise ValueError
    return {k: value[k] for k in (*FLAGS, *METRICS)}


def stream_report(value: Any) -> dict[str, Any]:
    """The --stream client's report: W4c's strict shape, vqe_0 only, no frame metadata."""
    io = iop._helper(value, "full_envelope")
    if io["other"] or len(io["channels"]) > 1 or any(
        (c["name"], c["type"], c["sample_rate"], c["format"]) != ("vqe_0", 6, 16000, 1)
        for c in io["channels"]
    ):
        raise ValueError
    return io


def io_summary(io: dict[str, Any]) -> dict[str, Any]:
    return {**{k: io[k] for k in ("error_code", "connected", "subscribe_sent", "unsubscribe_sent",
                                  "frames", "audio_frames", "other_frames", "bytes",
                                  "first_audio_ms", "elapsed_ms")},
            "input": io["channels"][0] if io["channels"] else None}


def _split(raw: str, limit: int, most: int) -> tuple[
        dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    """Body JSON objects and the trailer, or an early result for every other shape."""
    base: dict[str, Any] = {"outcome": "unknown", "do_not_retry": True, "audio_exported": False}
    lines = raw.splitlines() if len(raw) <= limit else []
    try:
        if len(lines) == 1:
            value = _json(lines[0])
            if (type(value) is dict and set(value) == {"outcome", "error_code"}
                and value["outcome"] == "blocked"
                and str(value["error_code"]) in iop.PROFILE_ERRORS):
                return dict(base, outcome="blocked", error_code=value["error_code"]), [], {}
            final = trailer(value)
            return dict(base, error_code="helper_output_missing",
                        **{k: final[k] for k in FLAGS}), [], final
        if not 2 <= len(lines) <= most:
            raise ValueError
        final = trailer(_json(lines[-1]))
    except (ValueError, TypeError):
        return dict(base, error_code="helper_output_invalid"), [], {}
    try:
        values = [_json(line) for line in lines[:-1]]
        if any(type(v) is not dict for v in values):
            raise ValueError
    except (ValueError, TypeError):
        return dict(base, error_code="helper_output_invalid",
                    **{k: final[k] for k in FLAGS}), [], final
    return None, values, final


def _summary(end: dict[str, Any] | None, error: str | None,
             final: dict[str, Any]) -> dict[str, Any]:
    ticks = final["detector_cpu_ticks"]
    return {"end_reason": end["reason"] if end else None,
            "audio_ms": end["audio_ms"] if end else None, "error": error,
            "exit": final["detector_exit"], "vmhwm_kib": final["detector_vmhwm_kib"],
            # USER_HZ is 100 in the Linux ARM userspace ABI.
            "cpu_ticks": ticks, "cpu_ms": ticks * 10 if ticks >= 0 else -1}


def parse_result(raw: str, gain_db: int) -> dict[str, Any]:
    """Typed allowlisted numbers and events only; every uncertain shape is unknown."""
    early, values, final = _split(raw, OUTPUT_LIMIT, 256)
    if early is not None:
        return early
    base: dict[str, Any] = {"outcome": "unknown", "do_not_retry": True, "audio_exported": False}
    flags = {k: final[k] for k in FLAGS}
    try:
        reports = [v for v in values if "event" not in v]
        if len(reports) != 1:
            raise ValueError
        io = stream_report(reports[0])
        models, detections, end, error, _ = _detector([v for v in values if "event" in v],
                                                      gain_db)
    except (ValueError, TypeError, KeyError):
        return dict(base, error_code="helper_output_invalid", **flags)
    channel = io["channels"][0] if io["channels"] else None
    if not all(flags.values()):
        code = "quasar_changed_or_cleanup_unverified"
    elif io["outcome"] != "complete":
        code = io["error_code"]
    elif error:
        code = "detector_error"
    elif end is None or end["reason"] != "eof" or final["detector_exit"] != 0:
        code = "detector_incomplete"
    elif channel is None or end["audio_ms"] != channel["bytes"] // 320 * 10:
        code = "pcm_count_mismatch"  # Every whole 10 ms hop the client sent must arrive.
    else:
        code = "none"
    return dict(
        base, outcome="complete" if code == "none" else "unknown", error_code=code,
        evidence_class="analytical", detections=detections, models=list(models or []),
        io=io_summary(io), detector=_summary(end, error, final), **flags)


def _stats(q: list[int]) -> dict[str, Any]:
    sums = [sum(q[i:i + 5]) for i in range(len(q) - 4)]
    return {"max_q": max(q, default=None),
            "trace_sha256": hashlib.sha256(bytes(q)).hexdigest(),
            "max_mean5": round(max(sums) / 5 / 256, 4) if sums else None,
            "q_ge_250": sum(v >= 250 for v in q), "inferences": len(q)}


def parse_replay(raw: str, gain_db: int, clip_bytes: int) -> dict[str, Any]:
    """Per-model trace maxima only; the raw trace lines are parsed strictly, never returned."""
    early, values, final = _split(raw, REPLAY_LIMIT, 4096)
    if early is not None:
        return early
    base: dict[str, Any] = {"outcome": "unknown", "do_not_retry": True, "audio_exported": False}
    flags = {k: final[k] for k in FLAGS}
    try:
        if any("event" not in v for v in values):
            raise ValueError
        models, _, end, error, traces = _detector(values, gain_db, trace=True)
    except (ValueError, TypeError, KeyError):
        return dict(base, error_code="helper_output_invalid", **flags)
    hops = end["audio_ms"] // 10 if end else 0
    if not all(flags.values()):
        code = "quasar_changed_or_cleanup_unverified"
    elif error:
        code = "detector_error"
    elif end is None or end["reason"] != "eof" or final["detector_exit"] != 0:
        code = "detector_incomplete"
    elif end["audio_ms"] != clip_bytes // 320 * 10:
        code = "pcm_count_mismatch"
    elif any(len(traces.get(name, [])) != max(0, hops - 2) // stride
             for name, stride in (models or {}).items()):
        code = "trace_incomplete"  # The first two 10 ms hops only fill the 30 ms window.
    else:
        code = "none"
    return dict(
        base, outcome="complete" if code == "none" else "unknown", error_code=code,
        evidence_class="analytical",
        model_sha256={m["name"]: m["sha256"] for v in values if v.get("event") == "ready"
                      for m in v["models"]},
        models={name: _stats(traces.get(name, [])) for name in models or {}},
        detector=_summary(end, error, final), **flags)


def _json(line: str) -> Any:
    return json.loads(line, object_pairs_hook=_unique)


def apply(controller: Controller, plan_id: str, expected: str,
          item: Pilot | Replay, *,
          on_event: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    try:
        plan = controller.take_plan(plan_id, expected)
        with controller._execution_lock:
            station = controller.station(plan.station.id)
            iop._permitted(controller, station)
            ssh = _ssh(station)
            if (plan.action != ACTION or station != plan.station
                or controller.clock() >= plan.expires
                or plan.request != {"pilot": item.digest, "ssh_trust": ssh.trust_sha256}):
                raise SafetyError("Wake pilot preview changed or expired")
            request = _command(station, item, "apply", plan.remote_sha256)
            try:
                if isinstance(item, Replay):
                    return parse_replay(_transfer(ssh, request, limit=REPLAY_LIMIT),
                                        item.gain_db, len(item.pcm))
                if on_event is not None:
                    events: list[dict[str, Any]] = []

                    def line(raw: bytes) -> None:
                        value = _json(raw.decode("utf-8", "strict"))
                        if type(value) is not dict:
                            raise ValueError("Invalid detector event")
                        if "event" in value:
                            events.append(value)
                            _detector(events, item.gain_db)
                            # Provisional, untrusted observations; final cleanup still matters.
                            on_event(value)

                    return parse_result(_transfer(ssh, request, limit=OUTPUT_LIMIT,
                                                  on_line=line), item.gain_db)
                return parse_result(_transfer(ssh, request, limit=OUTPUT_LIMIT), item.gain_db)
            except Exception:
                return {"outcome": "unknown", "error_code": "transport_uncertain",
                        "do_not_retry": True, "audio_exported": False}
    except (SafetyError, ValueError):
        return {"outcome": "blocked", "error_code": "preview_changed_or_expired",
                "do_not_retry": True, "audio_exported": False}


def operator_run(controller: Controller, station_id: str, item: Pilot, *,
                 ask: Any = input) -> dict[str, Any]:
    """Human-run: state-bound preview first, then the exact typed word, then one apply."""
    plan = preview(controller, station_id, item)
    try:
        answer = ask(f"Mini 1 will pipe its vqe_0 room audio into the on-station wake detector "
                     f"for {item.seconds} s. Audio stays in station RAM; only detection events, "
                     "frame counts and levels return. No audio is stored or exported and Quasar "
                     "is not stopped. Make sure people nearby agree. Type WAKE to start: ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if miss := confirm(answer, "WAKE"):
        try:
            controller.take_plan(plan["plan_id"], plan["expected_sha256"])  # Consume it.
        except SafetyError:
            pass
        return {"outcome": "cancelled", "do_not_retry": True, "audio_exported": False, **miss}
    return apply(controller, plan["plan_id"], plan["expected_sha256"], item)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-run Mini 1 on-station wake pilot")
    parser.add_argument("--station", required=True, help="Private station alias, never an endpoint")
    parser.add_argument("--client", type=Path,
                        help="Already-built IO-channel client matching its pinned SHA-256")
    parser.add_argument("--detector", required=True, type=Path,
                        help="Already-built microWakeWord detector matching its pinned SHA-256")
    parser.add_argument("--replay", type=Path, help="Replay a host WAV without room capture")
    parser.add_argument("--live", action="store_true",
                        help="Apply one replay; without this flag replay only previews")
    parser.add_argument("--seconds", type=int, choices=range(1, 61), default=45)
    parser.add_argument("--gain-db", type=int, choices=range(0, 31), default=0,
                        help="Calibration knob only (docs/MICRO_WAKE_WORD.md); default 0")
    args = parser.parse_args(argv)
    if args.live and not args.replay:
        parser.error("--live requires --replay; room-audio pilots use the WAKE prompt")
    if not args.replay and not args.client:
        parser.error("--client is required for a room-audio pilot")
    if not args.replay and not sys.stdin.isatty():
        parser.error("Use a terminal beside Mini 1, or call preview()/apply() from Python")
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
    try:
        with args.detector.open("rb") as source:
            detector = source.read(2 * 1024 * 1024 + 1)
        controller = Controller(Path(inventory),
                                writes=os.environ.get("ALICE_MCP_ALLOW_WRITES") == "1")
        if args.replay:
            item = Replay(pcm_from_wav(args.replay), detector, args.gain_db)
            plan = preview(controller, args.station, item)
            result = (apply(controller, plan["plan_id"], plan["expected_sha256"], item)
                      if args.live else {"outcome": "preview", **plan})
        else:
            with args.client.open("rb") as source:
                client = source.read(64 * 1024 + 1)
            result = operator_run(controller, args.station,
                                  Pilot(args.seconds, client, detector, args.gain_db))
    except SafetyError as error:
        result = {"outcome": "blocked", "error": str(error), "audio_exported": False}
    except Exception:
        result = {"outcome": "blocked", "error_code": "operator_inputs_unavailable",
                  "audio_exported": False}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if result["outcome"] in ("complete", "preview") else 1


if __name__ == "__main__":
    raise SystemExit(main())
