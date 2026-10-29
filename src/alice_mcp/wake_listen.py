"""PC wake listener: Mini 1 vqe_0 (wake_stream) into W4b's Vosk pipeline and W5's mww.

    python -m alice_mcp.wake_listen --station ALIAS --client IO_CLIENT --mww MWW \\
        --libvosk LIBVOSK --vosk-zip MODEL_ZIP
    python -m alice_mcp.wake_listen --pcm-dir DIR ...   # offline, over voice_record clips

Unattended (no prompts). One JSON line per wake on stdout, {event:"wake", word, sources,
command, mww_max_q, t, trusted:false}, also delivered to an in-process W4b McpInbox;
per-utterance scores (never text) on stderr. `command` is only the words after the name, up
to the first pause or the next name; a name said alone waits up to 5 s for the next
utterance. `t` is seconds of audio since the listener started. Audio stays in RAM. Wake
events are untrusted data, never action authority.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import queue
import resource
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alice_mcp import io_channel_pilot as iop
from alice_mcp import wake_pilot as wp
from alice_mcp import wake_stream as ws
from alice_mcp.core import Controller, SafetyError
from alice_mcp.wake_pipeline import McpInbox, Recognizer, WakePipeline
from alice_mcp.wake_simulate import VOSK_ZIP_SHA256, VoskRecognizer, _vosk, read_pinned

RATE = 16000
WORDS = {"компьютер": "komp", "окей дом": "okey_dom", "маруся": "marusya"}
MODES = frozenset({"vosk", "mww", "either", "both"})
# W5's x86 builds of mww_detect.cc: "ft" = komp fine-tuned on the owner's recordings
# (model f5ea6953…1bcf), "old" = the round-3 three-model build (docs/MICRO_WAKE_WORD.md).
MWW_PINS = {"ft": "f8648a5c71c4382407f43a8a09b2edab0014d401ac02b2a01d9f0fe6205d059f",
            "old": "1978896141e666ee53368d590e9daa31bfb69c020bee61904ed5b2a46a5c0a23"}
MAX_UTTERANCE_S = 8.0  # Vosk transcript kept until its endpoint, at most this long.
ORPHAN_MS = 9000  # An mww hit no utterance claimed by then fires without a transcript.
ROTATE_MS = 600_000  # A fresh mww process every 10 min of audio (its own bound is 700 s).
# Command extraction and dedup, in stream seconds; calibration knobs, not protocol.
PAUSE_S = 0.6  # A gap this long between words ends the command.
FOLLOW_S = 5.0  # A name said alone takes speech starting this soon after it as its command.
COOLDOWN_S = 2.5  # One event per wake across Vosk and mww, unless a command lies between.
MATCH_S = 1.0  # An mww hit is the name Vosk heard ending this close to it.
LAG_S = 0.25  # mww fires after the name has begun at least this long ago.
Word = tuple[str, float, float]


class Mww:
    """The pinned host mww-detect with --trace: PCM in, strictly parsed events out."""

    def __init__(self, path: Path, model: str = "ft", *, max_seconds: int = 700) -> None:
        if model not in MWW_PINS:
            raise SafetyError("Unknown mww build")
        fd = os.open(path, os.O_RDONLY)
        try:
            with os.fdopen(os.dup(fd), "rb") as source:
                data = source.read(4 * 1024 * 1024 + 1)
            if hashlib.sha256(data).hexdigest() != MWW_PINS[model]:
                raise SafetyError("mww helper is not the pinned build")
            # The verified descriptor, not a replaceable path name, is what runs.
            self.process = subprocess.Popen(
                [f"/proc/self/fd/{fd}", "--trace", "--max-seconds", str(max_seconds),
                 "--gain-db", "0"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, pass_fds=(fd,), start_new_session=True)
        finally:
            os.close(fd)
        self.limit_ms = max_seconds * 1000
        self.vmhwm_kib = 0  # The detector's own peak RSS, read from /proc before it exits.
        self.audio_ms = 0
        self.models: dict[str, int] | None = None
        self.failed = False
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _event(self, value: Any) -> tuple[Any, ...]:
        if type(value) is not dict or "event" not in value:
            raise ValueError
        kind, models = value["event"], self.models or {}
        if kind == "ready" and self.models is None:
            self.models = wp._ready(value, 0)
            return ("ready",)
        if (kind == "p" and set(value) == {"event", "model", "t_ms", "q"}
            and value["model"] in models and iop._integer(value["t_ms"], 0, self.limit_ms)
            and iop._integer(value["q"], 0, 255)):
            return ("p", value["model"], value["t_ms"], value["q"])
        if (kind == "wake" and set(value) == {"event", "model", "t_ms", "probability", "peak",
                                              "last", "trusted"}
            and value["model"] in models and iop._integer(value["t_ms"], 0, self.limit_ms)
            and all(wp._unit(value[k]) for k in ("probability", "peak", "last"))
            and value["trusted"] is False):
            return ("wake", value["model"], value["t_ms"])
        if (kind == "end" and set(value) == {"event", "reason", "audio_ms", "detections"}
            and value["reason"] in wp.END_REASONS
            and iop._integer(value["audio_ms"], 0, self.limit_ms)):
            return ("end", value["reason"], value["audio_ms"])
        if (kind == "error" and set(value) == {"event", "error"}
            and value["error"] in wp.DETECTOR_ERRORS):
            return ("error", value["error"])
        raise ValueError

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                if len(line) > 4096:
                    raise ValueError
                event = self._event(wp._json(line.decode("utf-8", "strict")))
            except (ValueError, TypeError, KeyError):
                event = ("invalid",)
            self.events.put(event)
            if event[0] == "invalid":
                self.failed = True
                self.process.kill()
                return

    def feed(self, pcm: bytes) -> None:
        if self.failed or self.process.poll() is not None:
            return
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(pcm)
            self.process.stdin.flush()
            self.audio_ms += len(pcm) // 32
        except OSError:
            self.failed = True

    def poll(self) -> list[tuple[Any, ...]]:
        out = []
        while not self.events.empty():
            out.append(self.events.get())
        return out

    def alive(self) -> bool:
        return not self.failed and self.process.poll() is None

    def close(self) -> list[tuple[Any, ...]]:
        try:
            with open(f"/proc/{self.process.pid}/status", encoding="ascii") as status:
                for line in status:
                    if line.startswith("VmHWM:"):
                        self.vmhwm_kib = int(line.split()[1])
        except (OSError, ValueError, IndexError):
            pass
        try:
            assert self.process.stdin is not None
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        return self.poll()


def _words(result: dict[str, Any], words: Any) -> list[Word]:
    """Vosk's word times, or the text spread evenly when a recognizer gives none."""
    if (type(words) is list and words and all(
            type(w) is tuple and len(w) == 3 and type(w[0]) is str
            and all(type(x) in (int, float) for x in w[1:]) for w in words)):
        return [(w.casefold(), float(a), float(b)) for w, a, b in words]
    tokens = str(result["text"]).casefold().split()
    step = (result["end"] - result["start"]) / max(len(tokens), 1)
    return [(w, result["start"] + i * step, result["start"] + (i + 1) * step)
            for i, w in enumerate(tokens)]


class _Heard:
    """Recognizer wrapper keeping recent word times, so a wake can find its command."""

    def __init__(self, recognizer: Recognizer) -> None:
        self.recognizer = recognizer
        self.results: deque[tuple[float, float, list[Word], int]] = deque(maxlen=64)
        self.samples = 0
        self.endpoint = 0  # Stream sample of the latest result, empty (silence) or not.

    def _keep(self, result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is not None:
            self.endpoint = self.samples
            if result.get("text"):
                self.results.append((result["start"], result["end"], _words(
                    result, getattr(self.recognizer, "words", None)), self.samples))
        return result

    def accept(self, pcm: bytes) -> dict[str, Any] | None:
        self.samples += len(pcm) // 2
        return self._keep(self.recognizer.accept(pcm))

    def flush(self) -> dict[str, Any] | None:
        return self._keep(self.recognizer.flush())

    def words(self, start: float, end: float) -> tuple[list[Word], int]:
        for a, b, words, at in reversed(self.results):
            if abs(a - start) < 0.002 and abs(b - end) < 0.002:
                return words, at
        return [], self.endpoint


def _profile(word: str) -> dict[str, Any]:
    # Routing only; the schema's TTS block is required but never used here.
    return {"version": 1, "activation_color": "#000000",
            "wake_phrases": [{"phrase": word, "route": "mcp"}],
            "tts": {"backend": "piper", "model": "/nonexistent/unused.onnx",
                    "model_sha256": "0" * 64, "config_sha256": "0" * 64, "volume": 0.25}}


class Listener:
    """Vosk (W4b's WakePipeline) and mww in parallel; one event per spoken command."""

    def __init__(self, word: str, mode: str, recognizer: Recognizer,
                 mww: Callable[[], Any] | None, *, emit: Callable[[dict[str, Any]], Any],
                 log: Callable[[dict[str, Any]], Any] = lambda _: None,
                 inbox: McpInbox | None = None) -> None:
        if word not in WORDS or mode not in MODES:
            raise SafetyError("Unknown wake word or mode")
        self.word, self.mode, self.model = word, mode, WORDS[word]
        self.tokens = word.split()
        self.heard = _Heard(recognizer)
        self.pipeline = WakePipeline(_profile(word), self.heard,
                                     max_utterance_s=MAX_UTTERANCE_S)
        self.factory, self.mww, self.offset_ms, self.deaths = mww, None, 0, 0
        self.mww_vmhwm_kib = 0
        self.emit, self.log, self.inbox = emit, log, inbox
        self.samples, self.count, self.progress_ms = 0, 0, -1
        self.pending: list[dict[str, Any]] = []
        self.qs: deque[tuple[int, int]] = deque(maxlen=4000)
        self.hits: list[int] = []
        self.decisions: deque[dict[str, Any]] = deque(maxlen=1024)
        # Shared across sources: recent fires (wake end, consumed end, had a command), the end
        # of the last event's words, and a name said alone that waits for its command.
        self.fired: deque[tuple[float, float, bool]] = deque(maxlen=16)
        self.consumed = -1.0
        self.held: dict[str, Any] | None = None

    def _mww_events(self, events: list[tuple[Any, ...]]) -> None:
        for event in events:
            if event[0] == "p" and event[1] == self.model:
                t = self.offset_ms + event[2]
                self.qs.append((t, event[3]))
                self.progress_ms = max(self.progress_ms, t)
            elif event[0] == "wake" and event[1] == self.model:
                self.hits.append(self.offset_ms + event[2])

    def _mww_feed(self, pcm: bytes) -> None:
        if self.mww is not None and (not self.mww.alive() or self.mww.audio_ms >= ROTATE_MS):
            healthy, heard_ms = self.mww.alive(), self.mww.audio_ms
            self._close_mww()
            self.mww = None
            if not healthy and heard_ms < 60_000:
                self.deaths += 1
                if self.deaths >= 3:
                    self.factory = None
                    self.log({"mww": "disabled_after_repeated_exits"})
        if self.mww is None and self.factory is not None:
            self.mww, self.offset_ms = self.factory(), self.samples * 1000 // RATE
        if self.mww is not None:
            self.mww.feed(pcm)

    def _close_mww(self) -> None:
        assert self.mww is not None
        self._mww_events(self.mww.close())
        self.mww_vmhwm_kib = max(self.mww_vmhwm_kib, getattr(self.mww, "vmhwm_kib", 0))

    def feed(self, pcm: bytes) -> None:
        if type(pcm) is not bytes or len(pcm) % 2:
            raise SafetyError("Invalid PCM chunk")
        self._mww_feed(pcm)
        for i in range(0, len(pcm), 3200):
            self.pending += self.pipeline.feed(pcm[i:i + 3200])
        self.samples += len(pcm) // 2
        if self.mww is not None:
            self._mww_events(self.mww.poll())
        self._decide(final=False)

    def finish(self) -> None:
        self.pending += self.pipeline.finish()
        if self.mww is not None:
            self._close_mww()
            self.mww = None
        self._decide(final=True)

    def max_q(self) -> int | None:
        return max((q for _, q in self.qs), default=None)

    def _decide(self, final: bool) -> None:
        while self.pending:
            event = self.pending[0]
            if (not final and self.factory is not None and len(self.pending) < 16
                    and self.progress_ms < event["end_s"] * 1000 + 300):
                break  # Let mww catch up with the end of this utterance first.
            self.pending.pop(0)
            lo, hi = event["start_s"] * 1000 - 1000, event["end_s"] * 1000 + 1000
            hits = [t for t in self.hits if lo <= t <= hi]
            self.hits = [t for t in self.hits if t not in hits]
            words, at = self.heard.words(event["start_s"], event["end_s"])
            self._utterance(event["start_s"], event["end_s"], event["decision"], words, at,
                            hits, (lo, hi),
                            event["decision"] == "routed" and event.get("phrase") == self.word)
        now = self.samples / RATE
        if self.held is not None and not self.pending and (
                final or self.heard.endpoint > self.held["at"]
                or now > self.held["t"] + FOLLOW_S + MAX_UTTERANCE_S + 1):
            self._release([])  # Silence (Vosk's empty endpoint) or no utterance in time.
        for t in list(self.hits):
            if not self.pending and (final or now * 1000 >= t + ORPHAN_MS):
                self.hits.remove(t)
                self._utterance(t / 1000, t / 1000, "none", [], self.heard.endpoint, [t],
                                (t - 1000, t + 500), False)

    def _at(self, words: list[Word], i: int) -> bool:
        return [w for w, _, _ in words[i:i + len(self.tokens)]] == self.tokens

    def _take(self, words: list[Word], i: int, t: float) -> tuple[list[Word], int, float]:
        """Words after a name ending at t: repeats skipped, up to a pause or the next name."""
        n, out = len(self.tokens), list[Word]()
        while self._at(words, i) and words[i][1] - t <= FOLLOW_S:
            t, i = words[i + n - 1][2], i + n
        while i < len(words) and not self._at(words, i):
            if words[i][1] - (out[-1][2] if out else t) > (PAUSE_S if out else FOLLOW_S):
                break
            out.append(words[i])
            i += 1
        return out, i, t

    def _candidates(self, words: list[Word], hits: list[int],
                    vosk: bool) -> list[dict[str, Any]]:
        """Wakes in one utterance: Vosk's prefix name and mww hits, merged per spoken name."""
        n = len(self.tokens)
        found: dict[int, dict[str, Any]] = {
            i: {"start": words[i][1], "t": words[i + n - 1][2], "next": i + n,
                "sources": set(), "hit": None, "heard": True}
            for i in range(len(words)) if self._at(words, i)}
        if vosk and 0 in found:
            found[0]["sources"].add("vosk")
        out: list[dict[str, Any]] = []
        for hit in sorted(hits):
            near = [i for i in found if abs(found[i]["t"] - hit / 1000) <= MATCH_S]
            if near:
                cand = found[min(near, key=lambda i: abs(found[i]["t"] - hit / 1000))]
                cand["sources"].add("mww")
                cand["hit"] = cand["hit"] if cand["hit"] is not None else hit
                continue
            # Vosk heard another word here: the one under way at the hit was the name.
            # ponytail: LAG_S guess at mww's delay; a late hit can take the first command word.
            k = sum(1 for _, a, _ in words if a <= hit / 1000 - LAG_S)
            t = max(words[k - 1][2], hit / 1000) if k else hit / 1000
            out.append({"start": t, "t": t, "next": k, "sources": {"mww"}, "hit": hit,
                        "heard": False})
        return sorted(out + [c for c in found.values() if c["sources"]], key=lambda c: c["t"])

    def _duplicate(self, cand: dict[str, Any]) -> bool:
        if cand["t"] <= self.consumed:
            return True  # A repeat already skipped as part of the last event.
        # A name Vosk heard after the last command's words starts a new command.
        return any(abs(cand["t"] - t) < COOLDOWN_S
                   and not (command and cand["heard"] and cand["start"] >= end)
                   for t, end, command in self.fired)

    def _utterance(self, start: float, end: float, decision: str, words: list[Word], at: int,
                   hits: list[int], window: tuple[float, float], vosk: bool) -> None:
        if self.held is not None and words:  # Not an orphan mww hit.
            command, i, t = self._take(words, 0, self.held["t"])
            if command or (i and i == len(words)):
                self.consumed = max(self.consumed, command[-1][2] if command else t)
                if command:
                    self._release(command)
                else:
                    self.held.update(t=t, at=at)  # Only the name again: keep waiting.
            else:
                self._release([])  # The next speech began too late to be the command.
        scores = [q for t, q in self.qs if window[0] <= t <= window[1]]
        best = max(scores) if scores else None
        fired = False
        for cand in self._candidates(words, hits, vosk):
            if not {"vosk": "vosk" in cand["sources"], "mww": "mww" in cand["sources"],
                    "either": True, "both": cand["sources"] == {"vosk", "mww"}}[self.mode]:
                continue
            if self._duplicate(cand):
                continue
            fired = True
            command, i, t = self._take(words, cand["next"], cand["t"])
            self.consumed = max(self.consumed, command[-1][2] if command else t)
            self.fired.append((cand["t"], self.consumed, bool(command)))
            event = {"event": "wake", "word": self.word, "sources": sorted(cand["sources"]),
                     "mww_max_q": best,
                     "t": round(cand["hit"] / 1000 if cand["hit"] is not None else cand["t"],
                                2), "trusted": False}
            if not command and i == len(words) and cand["heard"]:
                self.held = {"event": event, "t": t, "at": at}  # Wait for the command.
            else:
                self._send(event, command)
        self.count += 1
        entry = {"utterance": self.count, "start": round(start, 2), "end": round(end, 2),
                 "vosk": decision, "vosk_hit": vosk, "mww_hit": bool(hits), "mww_max_q": best,
                 "fired": fired}
        self.decisions.append(entry)
        self.log(entry)

    def _release(self, command: list[Word]) -> None:
        assert self.held is not None
        held, self.held = self.held, None
        if command:
            self.fired.append((held["t"], self.consumed, True))
        self._send(held["event"], command)

    def _send(self, event: dict[str, Any], command: list[Word]) -> None:
        text = " ".join(w for w, _, _ in command)
        self.emit({**event, "command": text})
        if self.inbox is not None:
            self.inbox.deliver({"phrase": self.word, "text": text})


def _tally(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"files": len(rows), "vosk": sum(r["vosk"] for r in rows),
            "mww": sum(r["mww"] for r in rows),
            "either": sum(r["vosk"] or r["mww"] for r in rows),
            "both": sum(r["vosk"] and r["mww"] for r in rows)}


def evaluate(directory: Path, word: str, recognizer: Callable[[], Recognizer],
             mww: Callable[[], Any] | None, *, heldout: tuple[int, int] = (0, 0),
             log: Callable[[dict[str, Any]], Any] = lambda _: None) -> dict[str, Any]:
    """Each voice_record clip as its own stream: hits per engine, never the transcript."""
    if word not in WORDS:
        raise SafetyError("Unknown wake word")
    manifest: dict[str, dict[str, Any]] = {}
    if (directory / "manifest.jsonl").exists():
        for line in (directory / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if type(entry) is dict and type(entry.get("file")) is str:
                manifest[entry["file"]] = entry
    before = [resource.getrusage(who) for who in (resource.RUSAGE_SELF,
                                                  resource.RUSAGE_CHILDREN)]
    wall, samples, rows, mww_kib = time.monotonic(), 0, [], 0
    lead, tail = b"\0" * 9600, b"\0" * 32000  # 0.3 s and 1 s: room for Vosk endpoints.
    for clip in sorted(directory.glob("*.wav")):
        entry = manifest.get(clip.name, {})
        label = entry.get("label") or next(
            (k for k in ("positive", "negative") if f"-{k}-" in clip.name), "")
        target = entry.get("word") or clip.stem.rsplit("-", 1)[-1]
        if label not in {"positive", "negative"} or label == "positive" and (
                target != WORDS[word]):
            continue
        pcm = lead + wp.pcm_from_wav(clip) + tail
        listener = Listener(word, "either", recognizer(), mww, emit=lambda _: None, log=log)
        listener.feed(pcm)
        listener.finish()
        samples += len(pcm) // 2
        mww_kib = max(mww_kib, listener.mww_vmhwm_kib)
        rows.append({"file": clip.name, "label": label,
                     "vosk": any(d["vosk_hit"] for d in listener.decisions),
                     "mww": any(d["mww_hit"] for d in listener.decisions),
                     "mww_max_q": listener.max_q()})
    after = [resource.getrusage(who) for who in (resource.RUSAGE_SELF,
                                                 resource.RUSAGE_CHILDREN)]
    cpu = sum(a.ru_utime + a.ru_stime - b.ru_utime - b.ru_stime for a, b in zip(after, before,
                                                                                strict=True))
    positives = [r for r in rows if r["label"] == "positive"]
    negatives = [r for r in rows if r["label"] == "negative"]
    result: dict[str, Any] = {
        "word": word, "positives": _tally(positives), "negatives": _tally(negatives),
        "files": rows, "audio_s": round(samples / RATE, 1),
        "resources": {"cpu_s": round(cpu, 2), "wall_s": round(time.monotonic() - wall, 2),
                      "real_time_factor": round(cpu / max(samples / RATE, 1e-9), 4),
                      "peak_rss_mib": round(after[0].ru_maxrss / 1024),
                      "mww_vmhwm_kib": mww_kib},
        "evidence_class": "analytical"}
    if heldout != (0, 0):
        held = [positives[len(positives) - heldout[0]:] if heldout[0] else [],
                negatives[len(negatives) - heldout[1]:] if heldout[1] else []]
        result["heldout"] = {"positives": _tally(held[0]), "negatives": _tally(held[1])}
        result["trained"] = {"positives": _tally([r for r in positives if r not in held[0]]),
                             "negatives": _tally([r for r in negatives if r not in held[1]])}
    return result


@contextmanager
def engines(libvosk: Path, vosk_zip: Path, mww: Path,
            mww_model: str = "ft") -> Iterator[tuple[Callable[[], Recognizer],
                                                     Callable[[], Mww]]]:
    """Pinned Vosk (W4b) and mww (W5) factories; the model ZIP is unpacked to a temp dir."""
    archive = read_pinned(vosk_zip, VOSK_ZIP_SHA256)
    lib, fd = _vosk(libvosk)
    made: list[VoskRecognizer] = []
    try:
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(archive)) as z:
            z.extractall(tmp)  # Content fixed by the archive pin.
            model = lib.vosk_model_new(str(Path(tmp) / z.namelist()[0].split("/")[0]).encode())
            if not model:
                raise SafetyError("Vosk model unavailable")
            Mww(mww, mww_model).close()  # Fail early on a wrong build.

            def recognizer() -> Recognizer:
                if made:  # One recognizer at a time: the previous stream has finished.
                    lib.vosk_recognizer_free(made.pop().handle)
                made.append(VoskRecognizer(lib, model, RATE))
                return made[-1]

            try:
                yield recognizer, lambda: Mww(mww, mww_model)
            finally:
                for item in made:
                    lib.vosk_recognizer_free(item.handle)
                lib.vosk_model_free(model)
    finally:
        os.close(fd)


def _line(stream: Any) -> Callable[[dict[str, Any]], None]:
    def write(value: dict[str, Any]) -> None:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), file=stream,
              flush=True)
    return write


def main(argv: list[str] | None = None) -> int:
    env = os.environ.get
    parser = argparse.ArgumentParser(description="Mini 1 wake listener on this PC")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--station", help="Private station alias: live vqe_0 stream")
    source.add_argument("--pcm-dir", type=Path, help="voice_record clips: offline scores")
    parser.add_argument("--word", choices=sorted(WORDS), default="компьютер")
    parser.add_argument("--mode", choices=sorted(MODES), default="either")
    parser.add_argument("--client", type=Path, default=env("ALICE_MCP_IO_CLIENT"),
                        help="Pinned ARM IO-channel client (live only)")
    parser.add_argument("--mww", type=Path, default=env("ALICE_MCP_MWW_HELPER"),
                        help="Pinned x86 mww-detect build")
    parser.add_argument("--mww-model", choices=sorted(MWW_PINS), default="ft")
    parser.add_argument("--libvosk", type=Path, default=env("ALICE_MCP_WAKE_LIBVOSK"))
    parser.add_argument("--vosk-zip", type=Path, default=env("ALICE_MCP_WAKE_VOSK_ZIP"))
    parser.add_argument("--lead-s", type=int, choices=range(0, 30), default=5,
                        help="Next chunk starts this early; 0 = sequential chunks")
    parser.add_argument("--heldout-positives", type=int, default=0)
    parser.add_argument("--heldout-negatives", type=int, default=0)
    args = parser.parse_args(argv)
    missing = [n for n in ("mww", "libvosk", "vosk_zip") if getattr(args, n) is None]
    if args.station and args.client is None:
        missing.append("client")
    if missing:
        parser.error("missing: " + ", ".join(missing))
    log = _line(sys.stderr)
    try:
        with engines(args.libvosk, args.vosk_zip, args.mww, args.mww_model) as (vosk, mww):
            if args.pcm_dir:
                result = evaluate(args.pcm_dir, args.word, vosk, mww, log=log,
                                  heldout=(args.heldout_positives, args.heldout_negatives))
                _line(sys.stdout)(result)
                return 0
            inventory = env("ALICE_MCP_INVENTORY")
            if not inventory:
                parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
            with args.client.open("rb") as handle:
                client = handle.read(64 * 1024 + 1)
            controller = Controller(Path(inventory),
                                    writes=env("ALICE_MCP_ALLOW_WRITES") == "1")
            listener = Listener(args.word, args.mode, vosk(), mww, emit=_line(sys.stdout),
                                log=log, inbox=McpInbox("analytical"))
            item = ws.Stream(client, lead_s=args.lead_s)
            plan = ws.preview(controller, args.station, item)
            print("Mini 1 room audio streams to this PC for wake detection; RAM only, "
                  "nothing saved. Ctrl+C stops.", file=sys.stderr, flush=True)
            summary = ws.apply(controller, plan["plan_id"], plan["expected_sha256"], item,
                               listener.feed)
            listener.finish()
    except SafetyError as error:
        log({"outcome": "blocked", "error": str(error)})
        return 1
    log({"session": summary})
    return 0 if summary["outcome"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
