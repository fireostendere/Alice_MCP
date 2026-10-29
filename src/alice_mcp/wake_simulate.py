"""Synthetic wake benchmark: Piper corpus -> pinned Vosk streaming -> routing -> adapters.

python -m alice_mcp.wake_simulate --simulate --libvosk LIB --vosk-zip ZIP --voice ONNX [...]

Generated audio only: no microphone, station, playback, network or waveform file.
Output always states synthetic=true and hardware_verified=false. The optional
library, model and voices are hash-pinned, never downloaded or bundled here.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import io
import json
import math
import os
import random
import statistics
import tempfile
import time
import wave
import zipfile
from array import array
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from typing import Any, NamedTuple

from alice_mcp.core import SafetyError
from alice_mcp.wake_pipeline import Dispatcher, McpInbox, WakePipeline

# libvosk.so inside the PyPI vosk 0.3.45 manylinux x86_64 wheel (wheel SHA-256 matched PyPI).
LIBVOSK_SHA256 = "85c4654de3acdeb99abab86eeb2a6e603927d37089597c0fcc33d8638dc2ccaf"
# Locally observed small Russian model 0.22 ZIP pin, as in NATIVE_AGENT.md.
VOSK_ZIP_SHA256 = "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4"
# rhasspy/piper-voices: model = published LFS SHA-256, config = locally observed.
VOICES = {
    "ru_RU-denis-medium": ("15fab56e11a097858ee115545d0f697fc2a316c41a291a5362349fb870411b0a",
                           "831c860dac0b5073eaa81610a0a638ec23d90a6cf8e5f871b4485c2cec3767c8"),
    "ru_RU-dmitri-medium": ("f073356ebc4bd0f80c5af58df2953a5988bd5bdab1eb38635ce960b071fbefcb",
                            "667ef3117bc642c2892dff7690d8bdc8ca4228aeaa783b2dc1416df632855e0d"),
    "ru_RU-irina-medium": ("8ff38212d23da300bbe3705c645e6e5b9475f0bfde01558eb17813e22acaaaaa",
                           "c2ec28bb38e2b59e93b959b3e40348c1afebbd272f30fed5d41205d08e98a9d7"),
    "ru_RU-ruslan-medium": ("72a5f88e0b20928064eb45d88e1daa21f8af62d18613580d32cbb4aed48dcf7f",
                            "706a4fb17bc304abd07809b552deae615e64dcbffbfbd09854ba37ca59e88117"),
}
PHRASES = {"компьютер": "mcp", "сол": "mcp", "окей дом": "mcp", "маруся": "mcp",
           "алиса": "alice"}
LEAD_S, TRAIL_S, CHUNK_S = 0.5, 2.0, 0.1


class Case(NamedTuple):
    """kind: positive | negative | echo | retrigger | noise | silence."""

    id: str
    kind: str
    text: str
    phrase: str = ""
    command: str = ""
    gain_db: float = 0.0  # applied to speech (clipped)
    snr_db: float | None = None  # white noise relative to this case's speech RMS
    noise_dbfs: float | None = None  # absolute white-noise RMS for noise-only cases


C = Case
CORPUS = [
    C("name_komp", "positive", "Компьютер.", "компьютер"),
    C("cmd_komp", "positive", "Компьютер, включи свет на кухне.", "компьютер",
      "включи свет на кухне"),
    C("name_sol", "positive", "Сол.", "сол"),
    C("cmd_sol", "positive", "Сол, какая сегодня погода?", "сол", "какая сегодня погода"),
    C("name_okei", "positive", "Окей дом.", "окей дом"),
    C("cmd_okei", "positive", "Окей дом, выключи музыку.", "окей дом", "выключи музыку"),
    C("name_marusya", "positive", "Маруся.", "маруся"),
    C("cmd_marusya", "positive", "Маруся, поставь таймер на пять минут.", "маруся",
      "поставь таймер на пять минут"),
    C("cmd_alice", "positive", "Алиса, какая погода?", "алиса", "какая погода"),
    C("quiet_komp", "positive", "Компьютер, включи свет.", "компьютер", "включи свет",
      gain_db=-20),
    C("clipped_marusya", "positive", "Маруся, стоп.", "маруся", "стоп", gain_db=12),
    C("noisy_sol", "positive", "Сол, включи музыку.", "сол", "включи музыку", snr_db=10),
    C("quiet_noisy_okei", "positive", "Окей дом, стоп.", "окей дом", "стоп", gain_db=-10,
      snr_db=10),
    C("noisy_komp", "positive", "Компьютер, который час?", "компьютер", "который час",
      snr_db=10),
    C("very_noisy_marusya", "positive", "Маруся, включи свет.", "маруся", "включи свет",
      snr_db=0),
    C("no_name", "negative", "Включи свет на кухне."),
    C("embedded_komp", "negative", "Мой компьютер снова завис."),
    C("embedded_marusya", "negative", "Вчера я видел Марусю в парке."),
    C("similar_sol", "negative", "Соль закончилась, купи соль."),
    C("similar_sok", "negative", "Сок стоит в холодильнике."),
    C("similar_komp", "negative", "Компьютерный стол стоит у окна."),
    C("similar_okei", "negative", "Окей, дома никого нет."),
    C("similar_solnce", "negative", "Солнце светит ярко."),
    C("similar_alice", "negative", "Алисин кот спит на диване."),
    C("trailing_komp", "negative", "Сколько стоит новый компьютер?"),
    C("noisy_embedded", "negative", "Мой компьютер снова завис.", snr_db=10),
    C("quiet_similar_sol", "negative", "Соль на столе.", gain_db=-20),
    C("noise", "noise", "", noise_dbfs=-30),
    C("loud_noise", "noise", "", noise_dbfs=-15),
    C("silence", "silence", ""),
    C("echo_komp", "echo", "Компьютер, я слушаю.", "компьютер"),
    C("echo_marusya", "echo", "Маруся здесь, чем помочь?", "маруся"),
    C("echo_okei", "echo", "Окей дом, команда принята.", "окей дом"),
    C("repeat_marusya", "retrigger", "Маруся, стоп.", "маруся", "стоп"),
    C("repeat_komp", "retrigger", "Компьютер.", "компьютер"),
]


def read_pinned(path: Path, sha256: str, limit: int = 128 * 1024 * 1024) -> bytes:
    try:
        with path.open("rb") as source:
            data = source.read(limit + 1)
        if len(data) > limit or hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError
        return data
    except (OSError, ValueError):
        raise SafetyError("Optional wake artifact missing or not the pinned build") from None


def voice_pins(path: Path) -> tuple[str, str]:
    if path.suffix != ".onnx" or path.stem not in VOICES:
        raise SafetyError("Voice is not a pinned synthetic-corpus voice")
    return VOICES[path.stem]


def _clip(value: float) -> int:
    return max(-32768, min(32767, round(value)))


def _speech(case: Case, voice: Path) -> tuple[array[int], int]:
    from alice_mcp.local_tts import synthesize

    model_sha, config_sha = voice_pins(voice)
    with wave.open(io.BytesIO(synthesize(case.text, voice, model_sha, config_sha,
                                         volume=0.25)), "rb") as wav:
        rate = wav.getframerate()
        samples = array("h", wav.readframes(wav.getnframes()))
    factor = 10 ** (case.gain_db / 20)
    return array("h", (_clip(s * factor) for s in samples)), rate


def build_corpus(voices: list[Path], corpus: list[Case]) -> dict[str, Any]:
    """Concatenate cases into one continuous stream with ground-truth regions."""
    stream, rate = array("h"), 0
    spans: list[tuple[int, int]] = []
    regions: list[dict[str, Any]] = []
    levels: list[float] = []
    for voice in voices:
        for case in corpus:
            rms = 0.0
            if case.text:
                speech, case_rate = _speech(case, voice)
                if rate and case_rate != rate:
                    raise SafetyError("Corpus voices must share one sample rate")
                rate = case_rate
                rms = math.sqrt(sum(s * s for s in speech) / len(speech))
                levels.append(round(20 * math.log10(rms / 32767), 1))
                if case.kind == "retrigger":  # the same utterance twice, 1 s apart
                    speech = speech + array("h", bytes(2 * rate)) + speech
            else:
                rate = rate or 22050
                speech = array("h", bytes(int(2.5 * rate) * 2))
            body = array("h", bytes(int(LEAD_S * rate) * 2)) + speech + array(
                "h", bytes(int(TRAIL_S * rate) * 2))
            if case.snr_db is not None or case.noise_dbfs is not None:
                rng = random.Random(f"{voice.stem}:{case.id}")
                sigma = (rms / 10 ** (case.snr_db / 20) if case.snr_db is not None
                         else 32767 * 10 ** ((case.noise_dbfs or 0.0) / 20))
                body = array("h", (_clip(s + rng.gauss(0, sigma)) for s in body))
            start = len(stream)
            speech_end = start + int(LEAD_S * rate) + len(speech)
            if case.kind == "echo":
                spans.append((start + int(LEAD_S * rate), speech_end))
            stream += body
            regions.append({"case": case.id, "voice": voice.stem, "kind": case.kind,
                            "start_s": start / rate, "speech_end_s": speech_end / rate,
                            "end_s": len(stream) / rate, "route": PHRASES.get(case.phrase, ""),
                            "phrase": case.phrase, "command": case.command})
    return {"pcm": stream.tobytes(), "rate": rate, "speaking": spans, "regions": regions,
            "speech_rms_dbfs": levels}


def _vosk(path: Path) -> tuple[ctypes.CDLL, int]:
    fd = os.open(path, os.O_RDONLY)
    with os.fdopen(os.dup(fd), "rb") as source:
        if hashlib.sha256(source.read(64 * 1024 * 1024)).hexdigest() != LIBVOSK_SHA256:
            os.close(fd)
            raise SafetyError("Vosk library is not the pinned build")
    # The verified descriptor, not a replaceable path name, selects the library.
    lib = ctypes.CDLL(f"/proc/self/fd/{fd}")
    p, i = ctypes.c_void_p, ctypes.c_int
    lib.vosk_set_log_level.argtypes = [i]
    lib.vosk_model_new.argtypes, lib.vosk_model_new.restype = [ctypes.c_char_p], p
    lib.vosk_model_free.argtypes = [p]
    lib.vosk_recognizer_new.argtypes = [p, ctypes.c_float]
    lib.vosk_recognizer_new.restype = p
    lib.vosk_recognizer_set_words.argtypes = [p, i]
    lib.vosk_recognizer_accept_waveform.argtypes = [p, ctypes.c_char_p, i]
    lib.vosk_recognizer_accept_waveform.restype = i
    for name in ("vosk_recognizer_result", "vosk_recognizer_final_result"):
        getattr(lib, name).argtypes, getattr(lib, name).restype = [p], ctypes.c_char_p
    lib.vosk_recognizer_free.argtypes = [p]
    lib.vosk_set_log_level(-1)
    return lib, fd


class VoskRecognizer:
    """Unrestricted (open-vocabulary) Vosk; its own endpointer, absolute word times."""

    def __init__(self, lib: ctypes.CDLL, model: int, rate: int,
                 log: list[dict[str, Any]] | None = None) -> None:
        self.lib, self.log = lib, log
        self.handle = lib.vosk_recognizer_new(model, float(rate))
        if not self.handle:
            raise SafetyError("Vosk recognizer unavailable")
        lib.vosk_recognizer_set_words(self.handle, 1)

    def _parse(self, raw: bytes) -> dict[str, Any]:
        data = json.loads(raw)
        words = data.get("result") or [{"start": 0.0, "end": 0.0}]
        result = {"text": data.get("text", ""), "start": words[0]["start"],
                  "end": words[-1]["end"]}
        if self.log is not None and result["text"]:
            self.log.append(result)  # synthetic benchmark diagnostics only
        return result

    def accept(self, pcm: bytes) -> dict[str, Any] | None:
        code = self.lib.vosk_recognizer_accept_waveform(self.handle, pcm, len(pcm))
        if code < 0:
            raise SafetyError("Vosk rejected audio")
        return self._parse(self.lib.vosk_recognizer_result(self.handle)) if code else None

    def flush(self) -> dict[str, Any] | None:
        return self._parse(self.lib.vosk_recognizer_final_result(self.handle))


def _recognize(libvosk: str, model_dir: str, corpus: dict[str, Any],
               profile: dict[str, Any]) -> dict[str, Any]:
    """Runs in a fresh spawned process so peak RSS excludes Piper."""
    import resource  # POSIX-only; the module stays importable elsewhere

    lib, fd = _vosk(Path(libvosk))
    started = time.perf_counter()
    model = lib.vosk_model_new(model_dir.encode())
    if not model:
        raise SafetyError("Vosk model unavailable")
    load_s = time.perf_counter() - started
    heard: list[dict[str, Any]] = []
    rate, pcm = corpus["rate"], corpus["pcm"]
    recognizer = VoskRecognizer(lib, model, rate, heard)
    pipeline = WakePipeline(profile, recognizer, rate=rate)
    events, compute, step = [], 0.0, int(rate * CHUNK_S) * 2
    for offset in range(0, len(pcm), step):
        first, last = offset // 2, (offset + step) // 2
        speaking = any(a < last and b > first for a, b in corpus["speaking"])
        started = time.perf_counter()
        decided = pipeline.feed(pcm[offset:offset + step], speaking)
        elapsed = time.perf_counter() - started
        compute += elapsed
        events += [dict(e, compute_ms=round(elapsed * 1000, 1)) for e in decided]
    events += pipeline.finish()
    lib.vosk_recognizer_free(recognizer.handle)
    lib.vosk_model_free(model)
    os.close(fd)
    return {"events": events, "heard": heard, "model_load_s": round(load_s, 2),
            "compute_s": compute,
            "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}


def score(regions: list[dict[str, Any]], events: list[dict[str, Any]],
          heard: list[dict[str, Any]]) -> dict[str, Any]:
    """Exact route/phrase credit only; no near-miss or fuzzy matching."""
    counts = dict.fromkeys((
        "positives", "true_accepts", "false_rejects", "misroutes", "commands_expected",
        "commands_exact", "negatives", "false_accepts", "echo_cases", "echo_leaks",
        "retrigger_cases", "retrigger_duplicates", "unattributed_routed"), 0)
    delays: list[float] = []
    failures: list[dict[str, Any]] = []
    per_phrase: dict[str, dict[str, int]] = {}

    def inside(region: dict[str, Any], second: float) -> bool:
        return bool(region["start_s"] <= second < region["end_s"])

    for region in regions:
        routed = [e for e in events
                  if e["decision"] == "routed" and inside(region, e["start_s"])]
        kind, error = region["kind"], ""
        if kind == "positive":
            counts["positives"] += 1
            stats = per_phrase.setdefault(region["phrase"], {"positives": 0, "accepted": 0})
            stats["positives"] += 1
            counts["commands_expected"] += bool(region["command"])
            hits = [e for e in routed
                    if (e["route"], e["phrase"]) == (region["route"], region["phrase"])]
            if hits:
                counts["true_accepts"] += 1
                stats["accepted"] += 1
                delays.append(round(hits[0]["decided_s"] - region["speech_end_s"], 3))
                counts["commands_exact"] += bool(region["command"]
                                                 and hits[0]["text"] == region["command"])
            elif routed:
                counts["misroutes"] += 1
                error = "misroute"
            else:
                counts["false_rejects"] += 1
                error = "false_reject"
        elif kind == "echo":
            counts["echo_cases"] += 1
            if routed:
                counts["echo_leaks"] += 1
                error = "echo_leak"
        elif kind == "retrigger":
            counts["retrigger_cases"] += 1
            if len(routed) > 1:
                counts["retrigger_duplicates"] += 1
                error = "retrigger_duplicate"
        else:
            counts["negatives"] += 1
            if routed:
                counts["false_accepts"] += 1
                error = "false_accept"
        if error:
            failures.append({"case": region["case"], "voice": region["voice"], "error": error,
                             "heard": [h["text"] for h in heard if inside(region, h["start"])]})
    counts["unattributed_routed"] = sum(
        e["decision"] == "routed" and not any(inside(r, e["start_s"]) for r in regions)
        for e in events)
    return {"counts": counts, "endpoint_delay_s": delays, "per_phrase": per_phrase,
            "failures": failures}


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    p95 = statistics.quantiles(values, n=20)[-1] if len(values) > 1 else values[0]
    return {"median": round(statistics.median(values), 3), "p95": round(p95, 3),
            "max": round(max(values), 3)}


def simulate(libvosk: Path, vosk_zip: Path, voices: list[Path],
             corpus: list[Case] = CORPUS) -> dict[str, Any]:
    archive = read_pinned(vosk_zip, VOSK_ZIP_SHA256)
    first_sha, first_config = voice_pins(voices[0])
    profile = {"version": 1, "activation_color": "#000000",
               "wake_phrases": [{"phrase": p, "route": r} for p, r in PHRASES.items()],
               "tts": {"backend": "piper", "model": str(voices[0]), "model_sha256": first_sha,
                       "config_sha256": first_config, "volume": 0.25}}
    data = build_corpus(voices, corpus)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(io.BytesIO(archive)) as model:
        model.extractall(tmp)  # content fixed by the archive pin
        model_dir = Path(tmp) / model.namelist()[0].split("/")[0]
        with ProcessPoolExecutor(1, mp_context=get_context("spawn")) as pool:
            run = pool.submit(_recognize, str(libvosk), str(model_dir), data,
                              profile).result()
    inbox = McpInbox("synthetic")
    dispatcher = Dispatcher(inbox, {"mcp", "alice"})  # explicit synthetic opt-in
    receipts: dict[str, int] = {}
    released = True
    for item in run["events"]:
        if item["decision"] == "routed":
            receipt = dispatcher.dispatch(item)
            receipts[receipt["reason"]] = receipts.get(receipt["reason"], 0) + 1
            if receipt["delivered"]:
                released &= inbox.status(receipt["capture_id"])["untrusted_text"] is True
                try:
                    inbox.status(receipt["capture_id"])
                    released = False
                except SafetyError:
                    pass
    metrics = score(data["regions"], run["events"], run["heard"])
    counts = metrics["counts"]
    audio_s = len(data["pcm"]) / 2 / data["rate"]
    finalize = [e["compute_ms"] for e in run["events"]
                if e["decision"] == "routed" and "compute_ms" in e]
    return {
        "status": "completed", "synthetic": True, "hardware_verified": False,
        "recognizer": "vosk 0.3.45 unrestricted, vosk-model-small-ru-0.22",
        "voices": [v.stem for v in voices], "audio_seconds": round(audio_s, 1),
        "speech_rms_dbfs": _summary(data["speech_rms_dbfs"]),
        "metrics": {
            "counts": counts,
            "decisions": dict(Counter(e["decision"] for e in run["events"])),
            "false_reject_rate": round((counts["false_rejects"] + counts["misroutes"])
                                       / max(1, counts["positives"]), 3),
            "false_accept_rate": round(counts["false_accepts"]
                                       / max(1, counts["negatives"]), 3),
            "per_phrase": metrics["per_phrase"],
            "endpoint_delay_s": _summary(metrics["endpoint_delay_s"]),
            "decision_compute_ms": _summary(finalize),
            "real_time_factor": round(run["compute_s"] / audio_s, 3),
            "model_load_s": run["model_load_s"],
            "recognizer_peak_rss_mib": run["peak_rss_mib"]},
        "adapters": {"receipts": receipts, "one_shot_release_verified": released},
        "failures": metrics["failures"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true", required=True)
    parser.add_argument("--libvosk", type=Path)
    parser.add_argument("--vosk-zip", type=Path)
    parser.add_argument("--voice", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    missing = [name for name, value in (("libvosk", args.libvosk),
                                        ("vosk_zip", args.vosk_zip), ("voice", args.voice))
               if not value]
    if importlib.util.find_spec("piper") is None:
        missing.append("piper")
    base = {"synthetic": True, "hardware_verified": False}
    if missing:
        print(json.dumps(base | {"status": "unavailable", "missing": missing}))
        return 2
    try:
        result = simulate(args.libvosk, args.vosk_zip, args.voice)
    except SafetyError as error:
        print(json.dumps(base | {"status": "blocked", "error": str(error)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
