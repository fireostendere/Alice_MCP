"""microWakeWord training recipe for Russian wake words (owner workstation only).

Runs only inside an isolated scratch venv holding the pinned training stack
(see docs/MICRO_WAKE_WORD.md). It never touches a station, microphone or the
MCP server. Datasets, generated audio, features and weights stay outside Git.

    python -m scripts.voice.mww_train prepare  --dl DL --data DATA
    python -m scripts.voice.mww_train subsample --data DATA
    python -m scripts.voice.mww_train generate --word komp --voices DIR --data DATA
    python -m scripts.voice.mww_train generate --word komp --voices DIR --data DATA \
        --multispeaker LIBRITTS_DIR --multispeaker-count 2500
      (then keep only clips Vosk transcribes as the wake phrase: wav/positive_lt)
    python -m scripts.voice.mww_train features --word komp --data DATA
    python -m scripts.voice.mww_train train    --word komp --data DATA --mww MWW_SRC

Follows kahrendt/microWakeWord's basic_training_notebook at MWW_COMMIT, with
Russian Piper positives, Russian near-miss negatives and FLEURS ru speech added.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import io
import json
import os
import random
import re
import subprocess
import sys
import tarfile
import wave
import zipfile
from pathlib import Path
from typing import Any

MWW_COMMIT = "4665173cd35f1cff9a61e06fc427f124766c488e"
RATE = 16000
# rhasspy/piper-voices (model = published LFS SHA-256, config = locally observed), as W4b.
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
# 904-speaker English Piper model (LibriTTS-R, CC-BY-4.0) driven with Russian espeak
# phonemes: accented, so its clips are kept only if Vosk hears the wake phrase.
MULTISPEAKER = {
    "en_US-libritts_r-medium": ("10bb85e071d616fcf4071f369f1799d0491492ab3c5d552ec19fb548fac13195",
                                "b471dc60d2d8335e819c393d196d6fbf792817f40051257b269878505bc9afb3"),
}
# HuggingFace LFS SHA-256 (published); fetched with exact size bounds.
DATASETS = {
    "kahrendt/microwakeword/speech.zip":
        "68b6b811b347b8321169f9e75ea2b9be73245943b4470af9532ca288d9f1b3cc",
    "kahrendt/microwakeword/dinner_party.zip":
        "18a0885d595ced7faa73736a8680206f5ba6e80113ca3e6ce43130e510aac18f",
    "kahrendt/microwakeword/no_speech.zip":
        "722dbcc967275c64c9581ecb85729549830a9094ce605ee22b2ddab9dbabf3c8",
    "kahrendt/microwakeword/dinner_party_eval.zip":
        "e08e23a5e654dd4415a1c41a4b6cd02d1f983c821512367ad937bac1aeed8299",
    "mchl914/fma_xsmall/fma_xs.zip":
        "e5876c13cfb0f7ef668327c75d1c40bc4a2ed9d5b8e62ce383d093319c9ff663",
    "agkphysics/AudioSet/data/bal_train/09.parquet":
        "1121a990b52fdc9805adf9e6d704cc053c7fed02b5ce6f5b83cf73d3e9eccad7",
    "google/fleurs/data/ru_ru/audio/dev.tar.gz":
        "4dc533cd0f312f749bd6d0fe968f3183288d938e07ed97dc141d9f10570457f9",
    "google/fleurs/data/ru_ru/audio/test.tar.gz":
        "8a0d6a0d23c3421f50c575bcf65d875cd19dadae2f7cabb415024f81b65178b1",
    "google/fleurs/data/ru_ru/audio/train.tar.gz":
        "1966e34c17d612317f03916fb45159c793947de77e1988fe76b3b53cf6c40b44",
}
# Any FLEURS utterance containing one of these stems is excluded from every negative set.
WAKE_STEMS = ("компьют", "марус", "окей", "о кей")

WORDS: dict[str, dict[str, list[str]]] = {
    "komp": {
        "positive": ["Компьютер.", "Компьютер!", "Компьютер?", "Компьютер,", "компьютер",
                     "Компьютер...", "Эй, компьютер!", "Компьютер!!"],
        # End-aligned near misses: the clip ends where a detection would fire.
        "near_miss": [
            "компьютера", "компьютеры", "компьютеру", "компьютером", "компьютерный",
            "компьютерщик", "компьютерная", "мой компьютер", "новый компьютер",
            "этот компьютер", "старый компьютер", "твой компьютер", "наш компьютер",
            "в компьютере", "о компьютере", "компот", "композитор", "компания", "кампания",
            "компостер", "компас", "компресс", "компрессор", "комплимент", "компромисс",
            "компонент", "компаньон", "компилятор", "кампус", "катер", "питер", "пюпитр",
            "калькулятор", "кондитер", "кондуктор", "команда", "принтер", "монитор",
            "компьют", "пьютер", "компактный", "конкурент", "комбинат", "капитан",
            "компьютерная игра", "компьютерный стол", "компьютерная мышь", "Юпитер",
            "Маруся", "Алиса", "Окей, дом", "Сол", "Яндекс", "Салют", "Окей, гугл"],
    },
    "okey_dom": {
        "positive": ["Окей, дом.", "Окей дом!", "Окей, дом?", "окей дом", "Окей, дом,",
                     "О кей, дом.", "Окей-дом!"],
        "near_miss": [
            "окей", "дом", "окей, дома", "окей, домой", "окей, домик", "окей, дому",
            "Окей, гугл", "окей, том", "окей, сом", "окей, ком", "о, кей", "хокей",
            "хоккей", "оке", "окей, давай", "окей, понял", "окей, домофон", "окей, дон",
            "мой дом", "наш дом", "новый дом", "большой дом", "окей, это дом",
            "Маруся", "Алиса", "Компьютер", "Сол", "Яндекс"],
    },
    "marusya": {
        "positive": ["Маруся.", "Маруся!", "Маруся?", "Маруся,", "маруся", "Маруся...",
                     "Эй, Маруся!"],
        "near_miss": [
            "Марусю", "Маруси", "Марусе", "Марусей", "Марусенька", "Марусин", "Марусина",
            "Муся", "Маша", "Мария", "Маруська", "Руся", "Ксюша", "Катюша", "Андрюша",
            "марля", "Маруха", "моя Маруся", "наша Маруся", "у Маруси", "русый",
            "Марат", "маршрут", "мадам", "Мару", "Алиса", "Компьютер", "Окей, дом",
            "Сол", "Яндекс", "Салют"],
    },
}
# General Russian sentences (context negatives, random truncation). None contains a wake word.
SENTENCES = [
    "Включи свет в коридоре.", "Какая завтра будет погода?", "Поставь чайник, пожалуйста.",
    "Я сегодня поздно вернусь домой.", "Где мои ключи от машины?", "Давай закажем пиццу.",
    "Сколько времени осталось до поезда?", "Купи хлеба и молока по дороге.",
    "Мы поедем на дачу в субботу.", "Ребёнок уже спит, говори тише.",
    "Позвони маме вечером.", "Эта книга оказалась очень интересной.",
    "Открой окно, здесь душно.", "Наш сосед купил новую машину.",
    "Фильм начинается в восемь часов.", "Собака просится на улицу.",
    "Не забудь выключить утюг.", "У меня завтра важная встреча.",
    "Сделай музыку погромче.", "Мне нужно починить кран на кухне.",
    "Котлеты уже готовы, садись ужинать.", "Программа зависла, перезагрузи её.",
    "Ноутбук разрядился, где зарядка?", "Принтер опять зажевал бумагу.",
    "Монитор мерцает уже второй день.", "Отправь мне файл по почте.",
    "В комнате слишком жарко.", "Пойдём гулять в парк.", "Сегодня по телевизору футбол.",
    "Мама приготовила борщ.", "Завтра обещают дождь со снегом.", "Ты видел мои очки?",
    "Мы опоздали на автобус.", "Купи билеты в театр.", "Компания объявила о слиянии.",
    "Композитор написал новую симфонию.", "Капитан команды забил гол.",
    "Хоккей начнётся через час.", "Окна выходят во двор.", "Дома никого нет.",
    "Маша и Миша пошли в кино.", "Мария Ивановна проверяет тетради.",
]


def _optional(name: str) -> Any:
    """Import an optional training/eval dependency present only in the scratch venv."""
    return importlib.import_module(name)


def _rng(*parts: object) -> random.Random:
    return random.Random(hashlib.sha256(repr(parts).encode()).digest())


def training_phrases(word: str) -> dict[str, list[str]]:
    """A detector hears the wake phrase inside a sentence too; keep its label positive."""
    phrase = {"komp": "компьютер", "okey_dom": "окей дом", "marusya": "маруся"}[word]
    spec = {key: list(values) for key, values in WORDS[word].items()}
    negatives = []
    for text in spec["near_miss"]:
        tokens = " " + " ".join(re.findall(r"\w+", text.casefold())) + " "
        if " " + phrase + " " in tokens:
            spec["positive"].append(text)
        else:
            negatives.append(text)
    spec["near_miss"] = negatives
    return spec


def _split(name: str) -> str:
    """Deterministic 90/10 split; the 10% part is held out for evaluation only."""
    return "eval" if hashlib.sha256(name.encode()).digest()[0] < 26 else "train"


def write_wav(path: Path, samples: Any, rate: int = RATE) -> None:
    np = _optional("numpy")

    data = np.clip(np.asarray(samples), -32768, 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data)


def to_16k(samples: Any, rate: int) -> Any:
    """Polyphase resample of mono audio to 16 kHz float32 (−1..1 if input is)."""
    from math import gcd

    np = _optional("numpy")
    resample_poly = _optional("scipy.signal").resample_poly

    samples = np.asarray(samples, dtype=np.float32)
    if rate == RATE:
        return samples
    g = gcd(RATE, rate)
    return resample_poly(samples, RATE // g, rate // g).astype(np.float32)


def _decode(data: bytes) -> tuple[Any, int]:
    np = _optional("numpy")
    soundfile = _optional("soundfile")

    audio, rate = soundfile.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return np.mean(audio, axis=1), int(rate)


def prepare(dl: Path, data: Path) -> None:
    """Background/noise WAVs (90/10 split) and FLEURS ru WAVs with wake-stem filtering.

    Each output directory that already has files is kept, so a rerun only adds splits.
    """
    for split in ("train", "eval"):
        (data / f"bg_{split}").mkdir(parents=True, exist_ok=True)
    if not any((data / "bg_train").iterdir()):
        _backgrounds(dl, data)
    for split in ("dev", "test", "train"):
        out = data / f"fleurs_{split}"
        if not out.exists() or not any(out.iterdir()):
            out.mkdir(parents=True, exist_ok=True)
            _fleurs(dl, split, out)


def _backgrounds(dl: Path, data: Path) -> None:
    pq = _optional("pyarrow.parquet")
    with zipfile.ZipFile(dl / "mchl914__fma_xsmall/fma_xs.zip") as fma:
        for name in sorted(n for n in fma.namelist() if n.endswith(".mp3")):
            audio, rate = _decode(fma.read(name))
            stem = "fma_" + Path(name).stem
            write_wav(data / f"bg_{_split(stem)}" / f"{stem}.wav", to_16k(audio, rate) * 32767)
    table = pq.read_table(dl / "agkphysics__AudioSet/data__bal_train__09.parquet",
                          columns=["video_id", "audio"])
    for video, audio_row in zip(table.column("video_id").to_pylist(),
                                table.column("audio").to_pylist(), strict=True):
        try:
            audio, rate = _decode(audio_row["bytes"])
        except Exception:  # noqa: BLE001 - skip undecodable clips, never guess content
            continue
        stem = "as_" + str(video)
        write_wav(data / f"bg_{_split(stem)}" / f"{stem}.wav", to_16k(audio, rate) * 32767)


def _fleurs(dl: Path, split: str, out: Path) -> None:
    tsv = dl / f"google__fleurs/data__ru_ru__{split}.tsv"
    with tsv.open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))
    keep = {r[1] for r in rows if not any(s in r[3] for s in WAKE_STEMS)}
    with tarfile.open(dl / f"google__fleurs/data__ru_ru__audio__{split}.tar.gz") as tar:
        for member in tar:
            name = Path(member.name).name
            if member.isfile() and name in keep:
                source = tar.extractfile(member)
                if source is None:
                    continue
                audio, rate = _decode(source.read())
                write_wav(out / name, to_16k(audio, rate) * 32767)
    print(json.dumps({"fleurs": split, "kept": len(keep), "rows": len(rows)}))


def _trim(audio: Any, floor: float = 0.05) -> Any:
    """Trim leading/trailing frames whose 10 ms RMS is below `floor` of the loudest frame."""
    np = _optional("numpy")

    frames = len(audio) // 160
    if not frames:
        return audio
    rms = np.sqrt(np.mean(audio[: frames * 160].reshape(frames, 160) ** 2, axis=1))
    loud = np.flatnonzero(rms >= floor * max(float(rms.max()), 1e-9))
    return audio[max(0, loud[0] - 2) * 160: min(frames, loud[-1] + 2) * 160]


def generate(word: str, voices: Path, data: Path, count: int, workers: int,
             multispeaker: Path | None = None, multispeaker_count: int = 0) -> None:
    """Piper positives and end-aligned near-miss / sentence negatives (16 kHz, trimmed).

    With `multispeaker`, also `multispeaker_count` raw LibriTTS-R clips in
    wav/positive_lt_raw; filter them (docs/MICRO_WAKE_WORD.md) into wav/positive_lt.
    """
    from concurrent.futures import ProcessPoolExecutor

    spec = training_phrases(word)
    jobs: list[tuple[str, str, str, int]] = []
    if multispeaker is None:
        for voice in VOICES:
            jobs += [("positive", voice, text, i) for i in range(count)
                     for text in [spec["positive"][i % len(spec["positive"])]]]
            jobs += [("near_miss", voice, text, i) for text in spec["near_miss"]
                     for i in range(4)]
            jobs += [("sentences", voice, text, i) for text in SENTENCES for i in range(2)]
    else:
        voices = multispeaker
        jobs += [("positive_lt_raw", name, spec["positive"][i % len(spec["positive"])], i)
                 for name in MULTISPEAKER for i in range(multispeaker_count)]
    for kind in {job[0] for job in jobs}:
        (data / word / "wav" / kind).mkdir(parents=True, exist_ok=True)
    chunks = [jobs[i::workers] for i in range(workers)]
    with ProcessPoolExecutor(workers) as pool:
        done = sum(pool.map(_synth_jobs, [str(voices)] * workers, [str(data / word)] * workers,
                            chunks))
    print(json.dumps({"word": word, "clips": done}))


def _synth_jobs(voices: str, out: str, jobs: list[tuple[str, str, str, int]]) -> int:
    import logging

    np = _optional("numpy")
    piper = _optional("piper")
    PiperVoice, SynthesisConfig = piper.PiperVoice, piper.SynthesisConfig

    logging.getLogger("piper.voice").disabled = True
    loaded: dict[str, Any] = {}
    for kind, voice, text, index in jobs:
        if voice not in loaded:
            model, config = Path(voices) / f"{voice}.onnx", Path(voices) / f"{voice}.onnx.json"
            pins = VOICES.get(voice) or MULTISPEAKER[voice]
            if (hashlib.sha256(model.read_bytes()).hexdigest() != pins[0]
                    or hashlib.sha256(config.read_bytes()).hexdigest() != pins[1]):
                raise SystemExit(f"voice not pinned: {voice}")
            loaded[voice] = PiperVoice.load(model, config_path=config)
            loaded[voice].config.espeak_voice = "ru"  # Russian phonemes for every model
        rng = _rng(kind, voice, text, index)
        speakers = loaded[voice].config.num_speakers
        options = SynthesisConfig(speaker_id=rng.randrange(speakers) if speakers > 1 else None,
                                  length_scale=rng.uniform(0.75, 1.3),
                                  noise_scale=rng.uniform(0.4, 1.0),
                                  noise_w_scale=rng.uniform(0.5, 1.2))
        pcm = b"".join(c.audio_int16_bytes for c in loaded[voice].synthesize(text, options))
        rate = loaded[voice].config.sample_rate
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        # Speed+pitch perturbation by relabelling the rate (vocal-tract-length-like).
        audio = _trim(to_16k(audio, int(rate * rng.uniform(0.9, 1.1))))
        digest = hashlib.sha256(f"{voice}|{text}|{index}".encode()).hexdigest()[:16]
        write_wav(Path(out) / "wav" / kind / f"{voice}_{digest}.wav", audio * 32767)
    return len(jobs)


def _augmenter(backgrounds: list[str], rirs: str) -> Any:
    Augmentation = _optional("microwakeword.audio.augmentation").Augmentation

    # Notebook settings; PitchShift raised 0.1 -> 0.25 because only four voices exist.
    return Augmentation(
        augmentation_duration_s=3.2,
        augmentation_probabilities={
            "SevenBandParametricEQ": 0.1, "TanhDistortion": 0.1, "PitchShift": 0.25,
            "BandStopFilter": 0.1, "AddColorNoise": 0.1, "AddBackgroundNoise": 0.75,
            "Gain": 1.0, "RIR": 0.5},
        impulse_paths=[rirs], background_paths=backgrounds,
        background_min_snr_db=-5, background_max_snr_db=10,
        min_jitter_s=0.195, max_jitter_s=0.205)


def features(word: str, data: Path) -> None:
    """Notebook feature generation (RaggedMmap) for positives and Russian negatives."""
    np = _optional("numpy")
    Clips = _optional("microwakeword.audio.clips").Clips
    SpectrogramGeneration = _optional(
        "microwakeword.audio.spectrograms").SpectrogramGeneration
    RaggedMmap = _optional("mmap_ninja.ragged").RaggedMmap

    np.random.seed(int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "little"))
    augmenter = _augmenter([str(data / "bg_train")], str(data / "mit_rirs"))
    root = data / word / "features"
    for kind in ("positive", "positive_lt", "near_miss"):
        if not (data / word / "wav" / kind).is_dir():
            continue
        clips = Clips(input_directory=str(data / word / "wav" / kind), file_pattern="*.wav",
                      max_clip_duration_s=None, remove_silence=False, random_split_seed=10,
                      split_count=0.1)
        for split, name, repeat, slide in (("training", "train", 2, 10),
                                           ("validation", "validation", 1, 10),
                                           ("testing", "test", 1, 1)):
            out = root / kind / split / "wakeword_mmap"
            if out.exists():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            spectrograms = SpectrogramGeneration(clips=clips, augmenter=augmenter,
                                                 slide_frames=slide, step_ms=10)
            RaggedMmap.from_generator(
                out_dir=str(out), batch_size=100, verbose=False,
                sample_generator=spectrograms.spectrogram_generator(split=name, repeat=repeat))
    # Long Russian negatives: per-word sentences (clean/augmented) and, shared by all
    # words, FLEURS ru train speech (augmented and clean). FLEURS ru dev, whole and
    # clean, is validation ambient: checkpoint selection then penalises Russian FAs.
    shared = data / "ru_features"
    for source, augmented, out in (
            ("sentences", False, root / "ru_sentences_0" / "training"),
            ("sentences", True, root / "ru_sentences_1" / "training"),
            ("fleurs_train", True, shared / "fleurs_train_1" / "training"),
            ("fleurs_train", False, shared / "fleurs_train_0" / "training"),
            ("fleurs_dev", False, shared / "fleurs_dev_ambient" / "validation_ambient")):
        directory = data / word / "wav" / source if source == "sentences" else data / source
        out = out / "ru_mmap"
        if out.exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        clips = Clips(input_directory=str(directory), file_pattern="*.wav",
                      max_clip_duration_s=None, remove_silence=False, random_split_seed=None)
        spectrograms = SpectrogramGeneration(
            clips=clips, augmenter=_long_augmenter(data) if augmented else None, step_ms=10,
            split_spectrogram_duration_s=None if source == "fleurs_dev" else 3.0)
        RaggedMmap.from_generator(out_dir=str(out), batch_size=100, verbose=False,
                                  sample_generator=spectrograms.spectrogram_generator())
    print(json.dumps({"word": word, "features": str(root)}))


def _long_augmenter(data: Path) -> Any:
    Augmentation = _optional("microwakeword.audio.augmentation").Augmentation

    return Augmentation(
        augmentation_duration_s=None,
        augmentation_probabilities={"AddBackgroundNoise": 0.5, "Gain": 1.0, "RIR": 0.5,
                                    "AddColorNoise": 0.1},
        impulse_paths=[str(data / "mit_rirs")], background_paths=[str(data / "bg_train")],
        background_min_snr_db=0, background_max_snr_db=15, min_gain_db=-25, max_gain_db=0)


NEGATIVE_KEEP = 0.2


def subsample(data: Path, keep: float = NEGATIVE_KEEP) -> None:
    """Deterministic subset of the recipe's training negatives (25 GB uint16 features).

    ponytail: host page cache (~14 GB) cannot hold the full set and random mmap reads
    made training disk-bound (~0.5 step/s); a 20% subset stays cached. Use the full
    set on a machine with more RAM.
    """
    RaggedMmap = _optional("mmap_ninja.ragged").RaggedMmap

    for name in ("speech", "dinner_party", "no_speech"):
        for source in sorted((data / "negative_datasets" / name / "training").glob("*_mmap")):
            out = data / "negative_subset" / name / "training" / source.name
            if out.exists():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            features = RaggedMmap(str(source))
            rng = _rng("subsample", name, source.name)
            chosen = [i for i in range(len(features)) if rng.random() < keep]
            RaggedMmap.from_generator(out_dir=str(out), batch_size=1000, verbose=False,
                                      sample_generator=(features[i] for i in chosen))
            print(json.dumps({"set": name, "mmap": source.name, "kept": len(chosen),
                              "of": len(features)}), flush=True)
    # ponytail: validation_ambient (9.7 h CHiME-6) expands to ~7 GB of float windows per
    # validation; keep the first third of each session (~3.2 h). testing_ambient is unchanged.
    evaluation = data / "negative_subset" / "dinner_party_eval"
    for split, fraction in (("validation_ambient", 3), ("testing_ambient", 1)):
        for source in sorted((data / "negative_datasets" / "dinner_party_eval" / split)
                             .glob("*_mmap")):
            out = evaluation / split / source.name
            if out.exists():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            features = RaggedMmap(str(source))
            RaggedMmap.from_generator(
                out_dir=str(out), batch_size=10, verbose=False,
                sample_generator=(f[: len(f) // fraction] for f in features))


def training_config(word: str, data: Path, steps: int) -> dict[str, Any]:
    root, negatives = data / word / "features", data / "negative_subset"

    def feature(path: Path, weight: float, truth: bool, strategy: str) -> dict[str, Any]:
        return {"features_dir": str(path), "sampling_weight": weight, "penalty_weight": 1.0,
                "truth": truth, "truncation_strategy": strategy, "type": "mmap"}

    return {
        "window_step_ms": 10, "train_dir": str(data / word / "trained"),
        "features": [
            feature(root / "positive", 1.5, True, "truncate_start"),
            *([feature(root / "positive_lt", 1.5, True, "truncate_start")]
              if (root / "positive_lt").is_dir() else []),
            feature(root / "near_miss", 3.0, False, "truncate_start"),
            feature(root / "ru_sentences_0", 2.0, False, "random"),
            feature(root / "ru_sentences_1", 2.0, False, "random"),
            feature(data / "ru_features" / "fleurs_dev_ambient", 0.0, False, "split"),
            feature(data / "ru_features" / "fleurs_train_1", 8.0, False, "random"),
            feature(data / "ru_features" / "fleurs_train_0", 4.0, False, "random"),
            feature(negatives / "speech", 10.0, False, "random"),
            feature(negatives / "dinner_party", 10.0, False, "random"),
            feature(negatives / "no_speech", 5.0, False, "random"),
            feature(negatives / "dinner_party_eval", 0.0, False, "split"),
        ],
        "training_steps": [steps], "positive_class_weight": [1],
        "negative_class_weight": [20], "learning_rates": [0.001], "batch_size": 128,
        "time_mask_max_size": [0], "time_mask_count": [0], "freq_mask_max_size": [0],
        "freq_mask_count": [0], "eval_step_interval": 500, "clip_duration_ms": 1500,
        "target_minimization": 0.9, "minimization_metric": None,
        "maximization_metric": "average_viable_recall",
    }


# The notebook's MixedNet arguments, unchanged.
MIXEDNET = ["mixednet", "--pointwise_filters", "64,64,64,64", "--repeat_in_block", "1,1,1,1",
            "--mixconv_kernel_sizes", "[5], [7,11], [9,15], [23]",
            "--residual_connection", "0,0,0,0", "--first_conv_filters", "32",
            "--first_conv_kernel_size", "5", "--stride", "3"]


def train(word: str, data: Path, mww: Path, steps: int) -> int:
    yaml = _optional("yaml")

    config = data / word / "training_parameters.yaml"
    config.write_text(yaml.safe_dump(training_config(word, data, steps)), encoding="utf-8")
    command = [sys.executable, "-m", "microwakeword.model_train_eval",
               f"--training_config={config}", "--train", "1", "--restore_checkpoint", "1",
               "--test_tf_nonstreaming", "0", "--test_tflite_nonstreaming", "0",
               "--test_tflite_nonstreaming_quantized", "0", "--test_tflite_streaming", "0",
               "--test_tflite_streaming_quantized", "1", "--use_weights", "best_weights",
               *MIXEDNET]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(mww), os.environ.get("PYTHONPATH", "")]))
    return subprocess.call(command, env=env, cwd=data / word)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=["prepare", "subsample", "generate", "features",
                                         "train"])
    parser.add_argument("--word", choices=sorted(WORDS), default="komp")
    parser.add_argument("--dl", type=Path)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--voices", type=Path)
    parser.add_argument("--multispeaker", type=Path, help="dir with the LibriTTS-R model")
    parser.add_argument("--multispeaker-count", type=int, default=0)
    parser.add_argument("--mww", type=Path)
    parser.add_argument("--count", type=int, default=600)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--steps", type=int, default=10000)
    args = parser.parse_args(argv)
    if args.step == "prepare" and args.dl:
        prepare(args.dl, args.data)
    elif args.step == "subsample":
        subsample(args.data)
    elif args.step == "generate" and args.voices:
        generate(args.word, args.voices, args.data, args.count, args.workers,
                 args.multispeaker, args.multispeaker_count)
    elif args.step == "features":
        features(args.word, args.data)
    elif args.step == "train" and args.mww:
        return train(args.word, args.data, args.mww, args.steps)
    else:
        parser.error("missing argument for this step")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
