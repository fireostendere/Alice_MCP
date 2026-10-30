"""Evaluate the microWakeWord helper: parity, W4b corpus, false accepts per hour.

    python -m scripts.voice.mww_eval parity --helper BIN --model M.tflite --pcm AUDIO
    python -m scripts.voice.mww_eval corpus --helper BIN --corpus PREFIX [--vosk PREFIX.vosk.json]
    python -m scripts.voice.mww_eval faph   --helper BIN --data DATA
    python -m scripts.voice.mww_eval sweep  --corpus-trace C.json --faph-trace F.json
    python -m scripts.voice.mww_eval clips  --helper BIN --positive DIR --negative DIR [--snr DB]

Host-only and synthetic/offline: the helper runs on this machine reading stdin.
`parity` needs the isolated training venv (pymicro-features + ai-edge-litert);
the other steps need numpy/scipy only. Results say synthetic=true and
hardware_verified=false; no station, microphone or network is involved.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import random
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

RATE, STEP, FEATURES = 16000, 160, 40
TARGET_PHRASE = {"komp": "компьютер", "okey_dom": "окей дом", "marusya": "маруся"}
ECHO_MARGIN_S = 0.5  # same widening as W4b's pipeline


def _optional(name: str) -> Any:
    """Import an optional training/eval dependency present only in the scratch venv."""
    return importlib.import_module(name)


def detect(trace: list[tuple[int, int]], threshold: float, window: int, cooldown_ms: int
           ) -> list[dict[str, Any]]:
    """Exact mirror of mww_detect.cc's rule on (t_ms, q); q/256 is the uint8 probability."""
    events: list[dict[str, Any]] = []
    recent: list[int] = []
    cooldown_until = -1
    for t_ms, q in trace:
        recent = (recent + [q])[-window:]
        if len(recent) < window or t_ms < cooldown_until:
            continue
        mean = sum(recent) / window / 256
        if mean < threshold:
            continue
        events.append({"t_ms": t_ms, "probability": round(mean, 4)})
        cooldown_until = t_ms + cooldown_ms
        recent = []
    return events


def _vm_hwm_kib(pid: int) -> int:
    """Peak RSS of the exec'd helper itself (wait4's ru_maxrss also counts the forked
    Python parent's pages before exec, so it is not used)."""
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return -1


def run_helper(helper: Path, pcm: bytes, args: list[str]) -> dict[str, Any]:
    """Run the helper on S16LE 16 kHz PCM; collect JSON lines, peak RSS and CPU time."""
    started = time.perf_counter()
    process = subprocess.Popen([str(helper), *args], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdin and process.stdout
    peak = [-1]

    def feed() -> None:
        assert process.stdin
        try:
            process.stdin.write(pcm)
            process.stdin.flush()
        except BrokenPipeError:
            pass
        peak[0] = _vm_hwm_kib(process.pid)  # still alive: it waits for EOF
        process.stdin.close()

    writer = threading.Thread(target=feed)
    writer.start()
    lines = [json.loads(line) for line in process.stdout]
    writer.join()
    _, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    wall = time.perf_counter() - started
    audio_s = len(pcm) / 2 / RATE
    cpu = usage.ru_utime + usage.ru_stime
    return {"lines": lines, "exit": process.returncode, "wall_s": round(wall, 2),
            "cpu_s": round(cpu, 2), "cpu_per_audio_s": round(cpu / max(audio_s, 1e-9), 4),
            "vm_hwm_kib": peak[0]}


def trace_of(lines: list[dict[str, Any]], model: str | None = None) -> list[tuple[int, int]]:
    return [(int(e["t_ms"]), int(e["q"])) for e in lines
            if e.get("event") == "p" and (model is None or e["model"] == model)]


def events_of(lines: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    return [e for e in lines if e.get("event") == "wake" and e.get("model") == model]


def benchmark_run(helper: Path, pcm: bytes, args: list[str], model: str) -> dict[str, Any]:
    run = run_helper(helper, pcm, args)
    lines = run["lines"]
    if (run["exit"] != 0 or len(lines) < 2 or lines[0].get("event") != "ready"
        or sum(m.get("name") == model for m in lines[0].get("models", [])) != 1
        or lines[-1].get("event") != "end" or lines[-1].get("reason") != "eof"
        or lines[-1].get("audio_ms") != len(pcm) // (2 * STEP) * 10):
        raise ValueError("benchmark needs a complete input and the requested embedded model")
    return run


def read_wav16k(path: Path) -> bytes:
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, RATE):
            raise ValueError("expected mono S16 16 kHz WAV")
        return audio.readframes(audio.getnframes())


def resample_to_16k(pcm: bytes, rate: int) -> bytes:
    """Same polyphase resampler as training (mww_train.to_16k), int16 out."""
    np = _optional("numpy")

    from scripts.voice.mww_train import to_16k

    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    return bytes(np.clip(np.rint(to_16k(audio, rate)), -32768, 32767).astype("<i2").tobytes())


def reference_trace(model: Path, pcm: bytes, optimized: bool = False
                    ) -> tuple[list[tuple[int, int]], Any]:
    """Python/TFLite reference: pymicro-features + LiteRT on identical int8 inputs.

    Default: TFLite BUILTIN_REF kernels (what TFLM's reference kernels implement).
    optimized=True: LiteRT's default optimized/XNNPACK kernels, as microWakeWord's
    own inference.py and recipe ROC use; their rounding differs on a few frames.
    """
    np = _optional("numpy")
    litert = _optional("ai_edge_litert.interpreter")
    MicroFrontend = _optional("pymicro_features").MicroFrontend

    frontend = MicroFrontend()
    features, times = [], []
    for offset in range(0, len(pcm) - len(pcm) % (2 * STEP), 2 * STEP):
        result = frontend.process_samples(pcm[offset:offset + 2 * STEP])
        if result.samples_read != STEP:
            raise ValueError("frontend consumed an unexpected sample count")
        if result.features:
            features.append(result.features)
            times.append((offset + 2 * STEP) // 2 * 1000 // RATE)
    kernels = {} if optimized else {
        "experimental_op_resolver_type": litert.OpResolverType.BUILTIN_REF}
    interpreter = litert.Interpreter(model_path=str(model), **kernels)
    interpreter.allocate_tensors()
    detail = interpreter.get_input_details()[0]
    output = interpreter.get_output_details()[0]
    stride = int(detail["shape"][1])
    scale = float(detail["quantization_parameters"]["scales"][0])
    zero = int(detail["quantization_parameters"]["zero_points"][0])
    raw = np.asarray(features, dtype=np.float32)  # already u16 * 0.0390625 (exact)
    quantized = np.clip(np.rint(raw.astype(np.float64) / np.float64(np.float32(scale))) + zero,
                        -128, 127).astype(np.int8)
    trace = []
    for end in range(stride, len(quantized) + 1, stride):
        window = quantized[end - stride:end].reshape(detail["shape"])
        interpreter.set_tensor(detail["index"], window)
        interpreter.invoke()
        trace.append((times[end - 1], int(interpreter.get_tensor(output["index"]).ravel()[0])))
    return trace, quantized


def parity(helper: Path, model: Path, pcm: bytes) -> dict[str, Any]:
    """Native q trace vs. TFLite reference kernels (and, for information, optimized)."""
    run = run_helper(helper, pcm, ["--trace", "--max-seconds", "86400"])
    lines = run["lines"]
    if (run["exit"] != 0 or not lines or lines[0].get("event") != "ready"
        or lines[-1].get("event") != "end" or lines[-1].get("reason") != "eof"
        or lines[-1].get("audio_ms") != len(pcm) // (2 * STEP) * 10):
        raise ValueError("helper did not finish the complete input")
    pin = hashlib.sha256(model.read_bytes()).hexdigest()
    names = [m["name"] for m in lines[0]["models"] if m["sha256"] == pin]
    if len(names) != 1:
        raise ValueError("reference model must match exactly one embedded model")
    native = trace_of(lines, names[0])
    result: dict[str, Any] = {"inferences": len(native)}
    for name, optimized in (("reference_kernels", False), ("optimized_kernels", True)):
        reference, _ = reference_trace(model, pcm, optimized)
        paired = list(zip(reference, native, strict=False))
        diffs = [abs(a[1] - b[1]) for a, b in paired]
        result[name] = {"inferences": len(reference),
                        "same_length": len(reference) == len(native),
                        "time_aligned": len(reference) == len(native) and all(
                            a[0] == b[0] for a, b in paired),
                        "exact": sum(d == 0 for d in diffs), "over_1": sum(d > 1 for d in diffs),
                        "max_abs_q": max(diffs, default=0)}
    result["helper"] = {k: run[k] for k in ("exit", "wall_s", "cpu_s", "cpu_per_audio_s",
                                            "vm_hwm_kib")}
    return result


def score_corpus(meta: dict[str, Any], events: list[dict[str, Any]], word: str
                 ) -> dict[str, Any]:
    """W4b-style per-case scoring for one wake word; echo gated by station_speaking."""
    target = TARGET_PHRASE[word]
    rate = meta["rate"]
    spans = [(a / rate - ECHO_MARGIN_S, b / rate + ECHO_MARGIN_S) for a, b in meta["speaking"]]
    kept = [e for e in events if not any(a <= e["t_ms"] / 1000 <= b for a, b in spans)]
    counts = dict.fromkeys(("positives", "true_accepts", "negatives", "false_accepts",
                            "other_wake_cases", "other_wake_accepts", "echo_cases",
                            "echo_leaks", "echo_suppressed", "retrigger_cases",
                            "retrigger_duplicates", "unattributed"), 0)
    failures = []
    for region in meta["regions"]:
        hits = [e for e in kept if region["start_s"] <= e["t_ms"] / 1000 < region["end_s"]]
        raw = [e for e in events if region["start_s"] <= e["t_ms"] / 1000 < region["end_s"]]
        kind, error = region["kind"], ""
        if kind == "positive" and region["phrase"] == target:
            counts["positives"] += 1
            counts["true_accepts"] += bool(hits)
            error = "" if hits else "false_reject"
        elif kind == "positive":
            counts["other_wake_cases"] += 1
            counts["other_wake_accepts"] += bool(hits)
            error = "other_wake_accept" if hits else ""
        elif kind == "echo":
            counts["echo_cases"] += 1
            counts["echo_leaks"] += bool(hits)
            counts["echo_suppressed"] += bool(raw and not hits)
            error = "echo_leak" if hits else ""
        elif kind == "retrigger":
            if region["phrase"] == target:
                counts["retrigger_cases"] += 1
                counts["retrigger_duplicates"] += len(hits) > 1
                error = "retrigger_duplicate" if len(hits) > 1 else ""
            else:
                counts["other_wake_cases"] += 1
                counts["other_wake_accepts"] += bool(hits)
                error = "other_wake_accept" if hits else ""
        else:
            counts["negatives"] += 1
            counts["false_accepts"] += bool(hits)
            error = "false_accept" if hits else ""
        if error:
            failures.append({"case": region["case"], "voice": region["voice"], "error": error})
    counts["unattributed"] = sum(
        not any(r["start_s"] <= e["t_ms"] / 1000 < r["end_s"] for r in meta["regions"])
        for e in kept)
    return {"counts": counts, "false_reject_rate": round(
        1 - counts["true_accepts"] / max(1, counts["positives"]), 3),
        "false_accept_rate": round(counts["false_accepts"] / max(1, counts["negatives"]), 3),
        "failures": failures}


def long_negative_stream(data: Path, seed: int = 7) -> tuple[bytes, dict[str, Any]]:
    """FLEURS ru test speech (wake stems removed) + held-out noise/music, never trained on."""
    np = _optional("numpy")

    rng = random.Random(seed)
    noise = [np.frombuffer(read_wav16k(p), dtype="<i2").astype(np.float32)
             for p in sorted((data / "bg_eval").glob("*.wav"))]
    parts, mixed = [], 0
    gap = np.zeros(RATE // 4, dtype=np.float32)
    for path in sorted((data / "fleurs_test").glob("*.wav")):
        speech = np.frombuffer(read_wav16k(path), dtype="<i2").astype(np.float32)
        if noise and rng.random() < 0.5:
            bed = noise[rng.randrange(len(noise))]
            bed = np.resize(bed, len(speech))
            snr = rng.uniform(5, 20)
            power = np.mean(speech ** 2) / max(np.mean(bed ** 2), 1e-9)
            speech = speech + bed * np.sqrt(power / 10 ** (snr / 10))
            mixed += 1
        parts += [speech, gap]
    speech_s = sum(len(p) for p in parts) / RATE
    parts += noise  # held-out noise/music alone
    stream = np.clip(np.rint(np.concatenate(parts)), -32768, 32767).astype("<i2")
    return stream.tobytes(), {"speech_s": round(speech_s), "noise_s": round(
        sum(len(n) for n in noise) / RATE), "mixed_utterances": mixed}


def clip_stream(positive: Path, negative: Path, word: str, noise_dir: Path | None = None,
                snr_db: float | None = None, seed: int = 11) -> tuple[bytes, dict[str, Any]]:
    """Held-out clips as one stream (1 s lead, 1.5 s tail), W4b-format regions."""
    np = _optional("numpy")

    rng = random.Random(seed)
    noise = [np.frombuffer(read_wav16k(p), dtype="<i2").astype(np.float32)
             for p in sorted(noise_dir.glob("*.wav"))] if noise_dir and snr_db is not None else []
    parts, regions, position = [], [], 0
    for kind, folder in (("positive", positive), ("negative", negative)):
        for path in sorted(folder.glob("*.wav")):
            clip = np.frombuffer(read_wav16k(path), dtype="<i2").astype(np.float32)
            body = np.concatenate([np.zeros(RATE), clip, np.zeros(RATE * 3 // 2)])
            if noise and snr_db is not None:
                bed = np.resize(noise[rng.randrange(len(noise))], len(body))
                power = np.mean(clip ** 2) / max(float(np.mean(bed ** 2)), 1e-9)
                body = body + bed * np.sqrt(power / 10 ** (snr_db / 10))
            regions.append({"case": path.stem, "voice": "heldout", "kind": kind,
                             "phrase": TARGET_PHRASE[word] if kind == "positive" else "",
                             "start_s": position / RATE,
                             "end_s": (position + len(body)) / RATE})
            parts.append(body)
            position += len(body)
    stream = np.clip(np.rint(np.concatenate(parts)), -32768, 32767).astype("<i2")
    return stream.tobytes(), {"rate": RATE, "speaking": [], "regions": regions}


def sweep(trace: list[tuple[int, int]], hours: float, positives: list[list[tuple[int, int]]],
          negatives: list[list[tuple[int, int]]], thresholds: list[float], windows: list[int],
          cooldown_ms: int) -> list[dict[str, Any]]:
    """FA/hour on the long stream, plus per-case rejects/accepts on corpus traces."""
    rows = []
    for window in windows:
        for threshold in thresholds:
            fa = len(detect(trace, threshold, window, cooldown_ms))
            missed = sum(not detect(p, threshold, window, cooldown_ms) for p in positives)
            near = sum(bool(detect(n, threshold, window, cooldown_ms)) for n in negatives)
            rows.append({"threshold": threshold, "window": window,
                         "fa_per_hour": round(fa / hours, 3), "false_accepts": fa,
                         "false_rejects": f"{missed}/{len(positives)}",
                         "corpus_false_accepts": f"{near}/{len(negatives)}"})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["parity", "corpus", "faph", "sweep", "clips"])
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--corpus-trace", type=Path)
    parser.add_argument("--faph-trace", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--pcm", type=Path, help="mono S16 16 kHz WAV")
    parser.add_argument("--corpus", type=Path, help="W4b corpus prefix (.pcm/.json)")
    parser.add_argument("--data", type=Path)
    parser.add_argument("--positive", type=Path)
    parser.add_argument("--negative", type=Path)
    parser.add_argument("--snr", type=float)
    parser.add_argument("--word", choices=sorted(TARGET_PHRASE), default="komp")
    parser.add_argument("--threshold", type=float, default=0.98)
    parser.add_argument("--window", type=int, default=5)
    parser.add_argument("--cooldown-ms", type=int, default=2500)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    base: dict[str, Any] = {"synthetic": True, "hardware_verified": False, "step": args.step}
    decision = ["--threshold", str(args.threshold), "--window", str(args.window),
                "--cooldown-ms", str(args.cooldown_ms)]
    if args.step == "sweep" and args.corpus_trace and args.faph_trace:
        corpus = json.loads(args.corpus_trace.read_text(encoding="utf-8"))
        long = json.loads(args.faph_trace.read_text(encoding="utf-8"))
        target = TARGET_PHRASE[args.word]
        regions = corpus["meta"]["regions"]

        def cases(keep: Any) -> list[list[tuple[int, int]]]:
            return [[(t, q) for t, q in corpus["trace"] if r["start_s"] <= t / 1000 < r["end_s"]]
                    for r in regions if keep(r)]

        positives = cases(lambda r: r["kind"] == "positive" and r["phrase"] == target)
        negatives = cases(lambda r: r["kind"] in {"negative", "noise", "silence"})
        grid = [round(0.5 + 0.05 * i, 2) for i in range(10)] + [0.97, 0.98, 0.99]
        result = base | {"rows": sweep([(int(t), int(q)) for t, q in long["trace"]],
                                       long["hours"], positives, negatives, grid,
                                       [1, 3, 5, 8], args.cooldown_ms)}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.helper is None:
        parser.error("--helper is required for this step")
    if args.step == "parity" and args.model and args.pcm:
        result = base | parity(args.helper, args.model, read_wav16k(args.pcm))
    elif args.step == "corpus" and args.corpus:
        meta = json.loads(args.corpus.with_suffix(".json").read_text(encoding="utf-8"))
        pcm = resample_to_16k(args.corpus.with_suffix(".pcm").read_bytes(), meta["rate"])
        run = benchmark_run(args.helper, pcm, ["--trace", "--max-seconds", "86400", *decision],
                            args.word)
        events = events_of(run["lines"], args.word)
        result = base | score_corpus(meta, events, args.word) | {
            "decision": decision, "helper": {k: run[k] for k in (
                "exit", "cpu_per_audio_s", "vm_hwm_kib")}}
        if args.out:
            args.out.write_text(json.dumps({"trace": trace_of(run["lines"], args.word),
                                            "meta": meta}))
    elif args.step == "clips" and args.positive and args.negative:
        pcm, meta = clip_stream(args.positive, args.negative, args.word,
                                args.data / "bg_eval" if args.data else None, args.snr)
        run = benchmark_run(args.helper, pcm, ["--trace", "--max-seconds", "86400", *decision],
                            args.word)
        events = events_of(run["lines"], args.word)
        scored = score_corpus(meta, events, args.word)
        result = base | {"snr_db": args.snr, "decision": decision} | scored
        if args.out:
            args.out.write_text(json.dumps({"trace": trace_of(run["lines"], args.word),
                                            "meta": meta}))
    elif args.step == "faph" and args.data:
        pcm, info = long_negative_stream(args.data)
        run = benchmark_run(args.helper, pcm, ["--trace", "--max-seconds", "86400", *decision],
                            args.word)
        events = events_of(run["lines"], args.word)
        hours = len(pcm) / 2 / RATE / 3600
        result = base | info | {"hours": round(hours, 3), "false_accepts": len(events),
                                "fa_per_hour": round(len(events) / hours, 3),
                                "decision": decision, "helper": {k: run[k] for k in (
                                    "exit", "wall_s", "cpu_s", "cpu_per_audio_s",
                                    "vm_hwm_kib")}}
        if args.out:
            args.out.write_text(json.dumps({"trace": trace_of(run["lines"], args.word),
                                            "hours": hours}))
    else:
        parser.error("missing argument for this step")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
