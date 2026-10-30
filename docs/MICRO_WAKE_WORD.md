# On-station wake word with microWakeWord (W5)

Status 2026-10-30: model training and evaluation are offline developer tools in
`scripts/voice/`, excluded from the runtime wheel. Historical synthetic results
below do not qualify room or ARM behavior. No new detector was deployed overnight;
ARM RSS/CPU and exact ARM/x86 comparison are still pending.

The available fine-tuned x86 helper (binary SHA-256
`f8648a5c71c4382407f43a8a09b2edab0014d401ac02b2a01d9f0fe6205d059f`, embedded
model `f5ea69530098109b717dc57d75808681da002d6cab95e5b7ee1d2f2e8f171bcf`)
was evaluated offline on all 20 supplied positive owner clips, with a fresh process
and 1 s lead/1.5 s tail silence per clip: **17/20 accepted**, threshold 0.98,
window 5, gain 0. Peak helper RSS: **556 KiB**. A separate 110 s concatenated
batch consumed 0.29 s CPU (0.26% of audio time). These are real recorded inputs,
not synthetic speech, and include training examples. The manifest does not identify
the five held-out positives, so this is **not held-out accuracy** or an ARM result.

Review corrections: evaluation now filters every event/trace by model, checks the
embedded model digest, and marks unequal trace lengths as unaligned. Replay verifies
the full inference clock and returns per-model probability trace digests and model
digests; equal maxima alone are insufficient. The replay pin still identifies the
old three-model ARM build, whereas PC default `ft` is the personal fine-tune. Do not
claim parity or substitute a new pin until the matching ARM build is reviewed.
Historical multi-model evaluation totals must be recomputed with model selection
before using them to choose or promote a model.

The next training recipe promotes exact wake phrases found in contextual negatives
(for example “моя Маруся”) to positives. Inflections and genuine near misses remain
negative. Generate into a **new private data directory**; existing WAV/features
are deliberately not deleted or reused as a corrected dataset. No new weights,
augmentation performance gain or improved «окей дом»/«маруся» accuracy is claimed.

## Pieces

| File | Role |
|---|---|
| `src/alice_mcp/native/mww/mww_detect.cc` | Device helper: stdin PCM → microfrontend → TFLite Micro → JSON lines |
| `scripts/voice/mww_build.py` | Stdlib build script (zig cross build or host `c++`); embeds the model(s) |
| `scripts/voice/mww_train.py` | microWakeWord recipe for Russian words; isolated scratch venv only |
| `scripts/voice/mww_eval.py` | Native/reference parity, W4b corpus scoring, FA/hour, threshold sweep |
| `tests/test_mww_build.py`, `tests/test_mww_eval.py` | Unit tests; native/parity tests skip without owner-built artifacts |

Model weights, datasets, generated audio, features and binaries are never in Git.
`scripts/check_public.py` allowlists only the reviewed `.cc` source.

## Training recipe

[kahrendt/microWakeWord @ `4665173c`](https://github.com/kahrendt/microWakeWord/tree/4665173cd35f1cff9a61e06fc427f124766c488e)
`notebooks/basic_training_notebook.ipynb`, followed step by step in
`mww_train.py` (no notebook was executed): Piper positives → recipe augmentation
(MIT RIRs p=0.5, background noise/music p=0.75 at −5…10 dB SNR, gain, EQ,
distortion, band-stop, colour noise, pitch shift) into 3.2 s clips ending
0.195–0.205 s after the word → 40-channel microfrontend spectrograms
(`RaggedMmap`) → the notebook's MixedNet (`64,64,64,64`, kernels
`[5],[7,11],[9,15],[23]`, first conv 32×5, stride 3), 10 000 steps, batch 128,
Adam 0.001, negative class weight 20, best weights by `average_viable_recall`,
then the recipe's int8 streaming conversion with internal state.

Deliberate deviations, each a known ceiling:

- **Positives, Russian voices:** only four Russian Piper voices exist (denis,
  dmitri, irina, ruslan). Each produced 600 clips from 6–8 spellings and
  punctuations with random `length_scale` 0.75–1.3, `noise_scale` 0.4–1.0,
  `noise_w` 0.5–1.2 and a ±10 % resample (speed+pitch, vocal-tract-like);
  PitchShift probability was raised from 0.1 to 0.25.
- **Positives, many speakers (round 3):** round 1 with those four voices alone
  rejected almost every «компьютер» from an unseen TTS voice (below). The
  recipe's own diversity source is a 904-speaker English model, so Piper's
  `en_US-libritts_r-medium` was driven with **Russian espeak phonemes**
  (`kʌmʲpjˈjutɛr`) and random speakers, 2 500 clips per word. The result is
  accented, so a clip was kept only if W4b's pinned Vosk (unrestricted
  `vosk-model-small-ru-0.22`) transcribed it with the wake phrase as a whole-word
  prefix (W4b's routing rule). Silero was not used: it needs `torch.package`
  (downloaded code execution).
- **Russian negatives added:** per word 28–55 end-aligned near misses
  (`компьютера`, `мой компьютер`, `компот`, other wake words, …; 4 voices × 4
  variants, same augmentation and splits as positives), 42 everyday sentences
  (×4 voices ×2, clean and augmented), FLEURS ru **train** (8.0 h, clean and
  augmented, round 3; round 1 used only FLEURS dev). FLEURS ru **dev** (1.08 h,
  clean) is a validation-ambient set in round 3, so checkpoint selection also
  penalises Russian false accepts, not only English CHiME-6.
- **Recipe negatives subsampled:** the 25 GB of recipe features (speech,
  dinner_party, no_speech) did not fit the host page cache and made training
  disk-bound, so a deterministic 20 % subset (5.0 GB) was used, and the CHiME-6
  validation-ambient set was cut to its first third (3.2 h) to keep validation
  under ~3 GB RAM. `dinner_party_eval` testing (DiPCo, 5.3 h) is unchanged.
- **Held-out audio:** a deterministic 10 % of FMA/AudioSet files (`bg_eval`,
  0.35 h) and FLEURS ru **test** were never used for training or augmentation.

### What was trained

| Per word | `komp` «компьютер» | `okey_dom` «окей дом» | `marusya` «маруся» |
|---|---|---|---|
| Piper positives (4 voices) | 2 400 | 2 400 | 2 400 |
| LibriTTS-R positives kept / generated | 669 / 2 500 | 205 / 2 500 | 66 / 2 500 |
| End-aligned near-miss clips | 880 | 448 | 496 |
| Sentence clips (clean + augmented) | 336 | 336 | 336 |
| Positive feature sets (train/val/test, slide 10) | 2.7 GB | 2.4 GB | 2.4 GB |

Shared negatives: FLEURS ru train 8.0 h (2 547 utterances, 15 with wake stems
removed), 5.0 GB of recipe features (20 % subset), backgrounds 2.77 h
(FMA + AudioSet), 270 RIRs. Datasets and TTS models downloaded: about 8.9 GB
(sizes in the pin table), plus the pinned wheels. Training: 10 000 steps per
model; round 1 took ~25 min per model (two in parallel), round 3 ~44 min for komp/okey_dom (three, then two in parallel) and
~16 min for marusya alone (its first parallel run was killed by memory pressure)
on the 8-core CPU-only WSL2 host (Ryzen 5 9600X).
Each model is a 26 049-parameter MixedNet; the int8 streaming `.tflite` is ~62 KB.

## Evaluation method (host, synthetic)

All numbers below come from the **x86-64 musl build of the same C++ helper**, fed
PCM on stdin; decisions were swept offline with `mww_eval.detect`, which mirrors
the helper's rule exactly (a test checks this against the helper's own `wake`
events). One global operating point was then chosen on W4b run A, the long
stream and the held-out voice, and is reported on run B as well.

- **W4b corpus:** regenerated twice (A, B) with W4b's own `build_corpus` (read-only
  import of its worktree; Piper noise makes runs differ), resampled 22.05 → 16 kHz
  with the training resampler. W4b's Vosk pipeline was scored on **the same
  regenerated streams**. Per word, positives are that word's positive cases; the
  60 W4b negatives are near misses, noise and silence; the other words' positive
  cases are counted separately ("other wake"). Echo cases are gated by W4b's
  `station_speaking` spans ±0.5 s; retrigger cases need ≤ 1 detection.
- **Long negatives (≥ 1 h, never trained on):** FLEURS ru **test** (767
  utterances, 2.47 h of read speech by real speakers, 8 wake-stem utterances
  removed; half mixed with held-out noise/music at 5–20 dB SNR), 0.25 s gaps,
  plus 0.35 h of held-out
  noise/music alone: **2.87 h**. W4b's Vosk pipeline was run on the identical
  stream for comparison.
- **Unseen TTS engine/voice:** `facebook/mms-tts-rus` (VITS, CC-BY-NC-4.0, never
  used in training): 40–60 positives and 24–42 near-miss negatives per word,
  clean and with held-out noise at 10 dB SNR. MMS mispronounces «компьютер»
  often, so the subset Vosk transcribes as the wake phrase is reported too.

## Results (host, synthetic, round 3 models)

### Parity: C helper vs. Python/TFLite reference

The helper's per-inference uint8 output was compared with pymicro-features +
LiteRT 2.3.0 on identical int8 inputs (`mww_eval parity`) over three inputs per
model: W4b corpus A (596 s), 20 padded Piper positives and 40 FLEURS test
utterances (528 s): 37 508–37 550 inferences per model.

| Reference kernels | Exact | Max abs. difference |
|---|---|---|
| TFLite `BUILTIN_REF` (what TFLM's reference kernels implement) | **100 %** for every model and input | **0** |
| LiteRT default optimized/XNNPACK (used by microWakeWord's `inference.py`) | 91.5–100 % per input | 15–31 q (0.06–0.12 probability), on frames near the decision slope |

Stated tolerance: ≤ 1 uint8 step (1/256) against the reference kernels; observed
0, for both the musl (zig) and glibc (host `c++`) builds. The optimized-kernel
differences come from LiteRT's own rounding, not from the helper; thresholds
were therefore chosen on the helper's own outputs.

### Operating point

**threshold 0.98, window 5 (mean of the last five 30 ms inferences), cooldown
2.5 s, gain 0 dB**, one setting for all models (the helper has one global
threshold). Chosen on W4b run A, the long stream and the MMS set, then reported
on run B too. It is the lowest threshold at which komp and okey_dom each made at
most one false accept in the 2.87 h long stream with ≤ 2/60 W4b near-miss false
accepts. At 0.97 komp makes 3 long-stream FAs instead of 1 for two fewer MMS
rejects; at 0.99 komp loses more corpus positives (4/16 on run B). One event in
2.87 h is a weak estimate (95 % upper bound about 1.9/h per model), and the FAs
of several loaded models add up. Window 3 and lower thresholds were swept
(`mww_eval sweep`) and traded more FAs for little recall.

### Comparison with W4b's Vosk pipeline (same regenerated corpora, same streams)

FR = false rejects of that word's positive cases; FA = routed/detected events in
the 60 W4b negatives (Vosk counts all five phrases; MWW only the given word).

| | Vosk A | Vosk B | MWW A | MWW B |
|---|---|---|---|---|
| «компьютер» FR | 2/16 | 3/16 | **1/16** | **2/16** |
| «окей дом» FR | 2/12 | 0/12 | 4/12 | 3/12 |
| «маруся» FR | 5/16 | 4/16 | 7/16 | 8/16 |
| FA in 60 negatives | 3/60 (сол, алиса ×2) | 2/60 | komp 1, okey_dom 1, marusya 0 | komp 2, okey_dom 0, marusya 0 |
| Other wake words accepted | 0 | 0 | 0 | okey_dom 1 («Компьютер, который час?» at 10 dB) |
| Echo leaks (12 cases) | 0 | 0 | 0 (3–4 suppressed by the gate) | 0 |
| Retrigger duplicates | 0/8 | 0/8 | 0/8 | 1/8 (marusya) |

| Held-out | Vosk (all phrases) | MWW komp | MWW okey_dom | MWW marusya |
|---|---|---|---|---|
| FA/h, 2.87 h FLEURS test + noise | **0** (0 events) | 0.35 (1) | 0.35 (1) | 0.70 (2) |
| MMS unseen voice, clean: FR | 42/60, 15/40, 17/40 | 7/60 | 7/40 | 31/40 |
| — of clips Vosk itself hears: FR | 0 by definition | 1/18 | 3/25 | 16/23 |
| MMS + 10 dB noise: FR | not run | 15/60 | 10/40 | 38/40 |
| MMS near misses: FA | 0/42, 0/24, 0/24 | 2/42 | 1/24 | 0/24 |
| Recipe test, DiPCo English 5.3 h (FA/h, FRR on augmented test split) | — | 0.56, 0.33 | 0.0, 0.42 | 0.0 @0.93, 0.40 |
| Peak RSS / CPU (x86) | ~366 MiB process; RTF 0.025 | 556 KiB; ~0.1 % of a core per model (3 models: 780 KiB, 0.22 %) | | |

**Verdict per word:** «компьютер» is the usable one on this synthetic evidence
(fewer corpus rejects than Vosk, 7/60 held-out voice rejects versus Vosk's
42/60, one FA in 2.87 h). «окей дом» is borderline (FA rate fine, recall lower
than Vosk, misses an unpaused command). **«маруся» is not usable**: only 66 of
2 500 multi-speaker clips survived the Vosk filter, and it misses most
held-out positives; it needs real recordings or another voice source.

Notes on the table:

- **W4b corpus voices were also training voices** (all four Piper voices), and
  W4b-like near-miss patterns («мой компьютер», «новый компьютер», «компьютерный»)
  were in the training negatives. Corpus numbers are therefore optimistic; the
  MMS and FLEURS rows are the held-out ones.
- MWW errors on the corpus: «Компьютерный стол…» fires (the streaming detector
  hears «компьютер» before «-ный»; Vosk's whole-word rule rejects it), noisy
  10 dB cases miss, and «Окей дом, выключи музыку» (no pause before the command)
  misses: positives were trained word-alone with 0.2 s of trailing silence.
- Vosk's prefix rule makes it nearly immune to FAs in running speech (0 in
  2.87 h) but it rejects most held-out MMS positives; MWW is the reverse trade
  at a fraction of the cost (below). Neither is evidence about real speakers.

### Round 1 → round 3

| | komp r1 | komp r3 | okey_dom r1 | okey_dom r3 |
|---|---|---|---|---|
| MMS clean FR @0.98 | 59/60 | 7/60 | 0/40 | 7/40 |
| Long-stream FA/h @0.98 | 0.35 | 0.35 | 5.92 | 0.35 |
| W4b A / B FR @0.98 | 1/16, 1/16 | 1/16, 2/16 | 0/12, 0/12 | 4/12, 3/12 |

Round 1 (four Piper voices, FLEURS dev only) did not generalise to an unseen
voice for «компьютер» (median peak probability 0.06 on MMS clips, even on those
Vosk transcribed correctly) and fired on ordinary Russian speech for «окей дом».
Many-speaker positives fixed the first; 8 h of Russian negatives and Russian
validation fixed the second at the cost of some okey_dom recall. (Round 2 was
superseded before finishing and is not reported.)

### Helper footprint (x86-64 build; ARM32 pending a device run)

| Build | Bytes | SHA-256 | Notes |
|---|---|---|---|
| x86-64 musl, 3 models | 675,864 | `1978896141e666ee53368d590e9daa31bfb69c020bee61904ed5b2a46a5c0a23` | measurement build |
| **ARM32 `arm-linux-musleabihf`, 3 models** | 604,488 | `d2b73953eeaa144ff4b8baa08f06a660bbbc8be698852a3f4881f39c25be45c4` | ELF32 ARM EABI5 hard-float, static, no `INTERP`, `GNU_STACK` RW; two clean builds byte-identical; **not run on ARM** |

Embedded models (int8 streaming, 62 304 bytes each, tensor arena 24 624 bytes):
komp `91cea83be8b6641fe5b0c80d0f9dcbe94d325d125abeac826ccc9b971780dff6`,
okey_dom `5ed99b99c91f46ae32e347c4379393d6b8f254e3084c14c3733ecf313b2f8836`,
marusya `2df2df4ac53c23f7849d306cc332986a375b315be05e2f71ec1d04d742af102f`.
Rebuild with `--model komp=…` alone (or komp + okey_dom) for deployment; the
digest changes with the model set.

On the 2.87 h stream (no trace, final builds): peak RSS (`VmHWM` of the exec'd
helper) 556 KiB with one model and 780 KiB with three; CPU 12.7 s and 22.7 s for
10 336 s of audio, i.e. 0.12 % and 0.22 % of one Ryzen 5 9600X core (repeat runs
ranged 0.10–0.22 %). These are x86 numbers. **ARM32 RSS/CPU on Mini 1
are pending a device run**; a Cortex-A-class core several times slower would
still be far inside the < 8 MiB / ≪ 1 core budget, but that is an estimate,
not a measurement.

### Live input level (owner's Mini 1 measurement, reported by the coordinator)

The owner's 5 s windows of `vqe_0` (16 kHz PCM on `yio_audio` 9850): silence
−78.9 dBFS RMS (peak −64.3), the owner speaking at normal distance −45.0 dBFS
RMS (peak −27.0). `raw_mic_0`: silence −57.7, speech −38.8 RMS (peak −22.7). The
station's own local-TTS playback raised neither channel. Speech therefore
arrives about 9 dB below the W4b corpus (median −36.4 dBFS) and 30 dB below full
scale.

Re-evaluation at that level (`level_eval`, scratch): W4b corpus A scaled so its
median speech is −45 dBFS, every MMS clip normalised to −45 dBFS, the long
stream scaled so active speech is −45 dBFS, all over a −79 dBFS held-out noise
floor; threshold 0.98, window 5; fixed gain before the helper:

| Gain | komp W4b FR / MMS FR / FA/h | okey_dom | marusya |
|---|---|---|---|
| 0 dB | 1/16 / 9/60 / 0.00 | 4/12 / 7/40 / 0.35 | 7/16 / 35/40 / 0.35 |
| +12 dB | 2/16 / 8/60 / 0.70 | 2/12 / 5/40 / 0.35 | 7/16 / 35/40 / 0.70 |
| +18 dB | 1/16 / 9/60 / 0.00 | 2/12 / 5/40 / 0.35 | 6/16 / 35/40 / 0.35 |
| +24 dB | 1/16 / 7/60 / 0.70 | 2/12 / 4/40 / 0.35 | 5/16 / 34/40 / 0.70 |
| +30 dB | 1/16 / 7/60 / 0.70 | 2/12 / 4/40 / 1.04 | 5/16 / 35/40 / 0.00 |

Training already covered −45 dB gain augmentation, and the frontend's noise
reduction + PCAN normalise level, so results are flat within ±2 cases.
**Chosen gain: 0 dB** (no fixed gain): extra gain did not help komp, added FA
events, and +24 dB would put the owner's −27 dBFS speech peaks at −3 dBFS, so
close or loud speech would clip. `--gain-db` (0–30, saturating, default 0)
exists only as a calibration knob for the live test, e.g. if speech from across
the room is far below −45 dBFS. Silence at −79 dBFS produced no detections.

## Pinned inputs (all outside Git)

| Artifact | Bytes | SHA-256 | Licence |
|---|---|---|---|
| microWakeWord @ `4665173c` (GitHub tarball) | 149,468 | `8df7950e68b5bcb2579331f0b55a14f36c1bba48d32303eeecdd871ba9617eb6` | Apache-2.0 |
| tflite-micro @ `e66bd969` (GitHub tarball) | 11,303,073 | `614d6394a2b8a29db25fd94807da5b4db25d60cdbf899aea1f4a83706f947baf` | Apache-2.0 |
| flatbuffers v25.9.23 zip (TFLM's MD5 `023eca1e…` matched) | 3,363,717 | `083a424984986ba7fcd1635d54417d4523a3c729e137575db2421e996a1fb816` | Apache-2.0 |
| pymicro-features 2.0.2 sdist (PyPI digest) | 54,713 | `0d0bed7843ec78b6ced82d1a2dcddeb4fe5df61b3af80a281d0868c8e279c727` | Apache-2.0; bundled kissfft BSD-3-Clause |
| `kahrendt/microwakeword` speech / no_speech / dinner_party / dinner_party_eval zips | 3,183,001,091 / 2,000,317,854 / 444,310,142 / 82,329,019 | HF LFS digests in `mww_train.DATASETS` | mixed (Common Voice, VOiCES, FSD50K, FMA, WHAM! CC-BY-NC, CHiME-6, DiPCo); the notebook says **non-commercial personal use only** |
| `mchl914/fma_xsmall` `fma_xs.zip` | 182,339,695 | `e5876c13…c9ff663` | card: MIT; tracks carry their artists' CC licences |
| `agkphysics/AudioSet` `bal_train/09.parquet` | 692,591,080 | `1121a990…dfa4e40` | card: CC-BY-4.0 labels; audio from YouTube uploaders |
| MIT environmental RIRs, 270 × 16 kHz WAV | 8,374,902 | manifest `207ab463…f47ecc` (each file matched its LFS digest) | card: unknown |
| FLEURS ru_ru `train` / `dev` / `test` `.tar.gz` | 1,427,743,798 / 187,377,526 / 433,142,634 | `1966e34c…40b44` / `4dc533cd…0457f9` / `8a0d6a0d…1b65178b1` | CC-BY-4.0 |
| Piper `ru_RU-{denis,dmitri,irina,ruslan}-medium` | — | W4b's `VOICES` pins | CC0, CC0, unknown (RHVoice), CC-BY-NC-SA-4.0 |
| Piper `en_US-libritts_r-medium.onnx` (904 speakers) | 78,580,914 | `10bb85e071d616fcf4071f369f1799d0491492ab3c5d552ec19fb548fac13195` | LibriTTS-R CC-BY-4.0 (model card) |
| W4b's Vosk 0.3.45 `libvosk.so` / `vosk-model-small-ru-0.22.zip` (filter + baseline) | — | W4b's pins (`85c4654d…`, `961d5ff9…`) | Apache-2.0 |
| `facebook/mms-tts-rus` `model.safetensors` (evaluation only) | 145,232,120 | `4ed1b47c11a9d3242ec1a094847f8252f7860c96aeb5ce316047985e58fe8759` | CC-BY-NC-4.0 |

Training venv (Python 3.12, `uv pip install --only-binary :all:`, PyPI digests
verified by the installer): tensorflow-cpu 2.21.0, keras 3.15.1, ai-edge-litert
2.3.0, pymicro-features 2.0.2, audiomentations 0.43.1, mmap-ninja 0.9.0,
datasets 3.6.0 (5.x needs torchcodec for audio), numpy 2.5.4, scipy 1.18.1,
soundfile 0.14.0, webrtcvad-wheels 2.0.14.post1, tensorboard 2.21.0,
piper-tts 1.8.0. `audio_metadata` (imported by the recipe, used only for
non-WAV clips) is replaced by a local stub that raises if called. MMS-TTS ran in
a separate venv (torch 2.9.1+cpu, transformers 4.57.1, safetensors only).

**Licence consequence:** the trained models inherit non-commercial terms
(recipe negatives, ruslan voice, WHAM!); they are for the owner's personal,
noncommercial use and are not distributed.

## Helper contract (`mww_detect.cc`)

- **Input:** raw mono S16LE PCM at 16 kHz on stdin, nothing else. It is consumed
  in 10 ms hops (160 samples); a trailing partial hop is ignored.
- **Features:** the TFLM microfrontend C sources from the pinned pymicro-features
  2.0.2 sdist, with the same configuration microWakeWord trains with: 30 ms
  window, 10 ms step, 40 channels, 125–7500 Hz, noise reduction, PCAN, log scale.
  Each `uint16` feature is scaled by 1/25.6 and quantized with the model's own
  input scale/zero point (round half to even, saturating to int8).
- **Model:** int8 streaming MixedNet with internal resource-variable state,
  invoked every `stride` (3) feature rows, i.e. every 30 ms. One or more models
  are compiled in by `mww_build.py`, so **one SHA-256 pins code and weights**.
  It opens no files, sockets or devices.
- **Decision:** mean of the last `--window` uint8 probabilities ≥ `--threshold`,
  then `--cooldown-ms` without detections and a fresh full window (defaults:
  the chosen 0.98, 5, 2500 ms).
  `mww_eval.detect` mirrors this rule exactly and is cross-checked by a test.
- **Output:** one JSON object per line: `ready` (models, digests, arena bytes),
  optional `p` trace lines (`--trace`), `wake` events and `end` (reason
  `eof|limit|signal|read_error|frontend`). Every `wake` carries
  `"trusted":false`. Diagnostics from TFLM go to stderr only.
- **Gain:** `--gain-db` 0–30 (default 0) multiplies the input with saturation
  before the frontend; it is a calibration knob, not needed at the measured level.
- **Bounds:** `--max-seconds` (1–86400 s of audio) is required; a wall-clock
  alarm at that value plus 60 s ends a stalled reader with exit 124.
  Invalid arguments exit 2 with `{"event":"error"}` before reading audio.

### Input source: Quasar's `vqe_0` (W4c live observation) and the 48 kHz fallback

The helper deliberately accepts one narrow format, and on Mini 1 the intended
source already delivers it:

- **`vqe_0` over the IO-channel subscription.** W4c's owner-authorized
  levels-only pilot on Mini 1 (2026-10-29, reported by the coordinator; W5 did
  not access the device): a loopback client of `yio_audio` on port 9850 (owner
  `platformd`) sent the full `QuasarMessage{159: IOChannelRequest{request_vqe}}`
  envelope (`full_envelope` in W4c's `IO_CHANNEL.md`) and received, in 5 s, 312
  `QuasarMessage{158: IOAudioChannelsData}` frames: channel `vqe_0`, type 6,
  `sample_rate` 16000, `format` 1 (PCM), 159 744 bytes ≈ 31.9 kB/s, i.e. **S16LE
  mono 16 kHz in ~512-byte (16 ms) frames**, about −79 dBFS RMS in a quiet room,
  after Quasar's echo cancellation. Quasar kept running. So the `data` bytes of
  each `vqe_0` entry go to the helper's stdin **unchanged: no resampling,
  downmix or format conversion.** The adapter must still check `name`, `type`,
  `sample_rate == 16000`, `format == 1` and an even byte count on every entry
  and stop on any change; it never guesses a format from content.
- **Raw PDM capture (fallback only):** 8 × S32_LE at 48 kHz, held by Quasar and
  not used. If it were ever the source: select one microphone channel (or
  average the microphone channels, never reference/loopback channels), take the
  top 16 bits with rounding and saturation (`(x + 0x8000) >> 16`), low-pass
  below 7.5 kHz (for example a 48-tap windowed sinc) and keep every third
  sample. That bypasses Quasar's AEC, so echo suppression would rest on the
  station-speaking gate alone.

## Integration plan (not implemented, nothing deployed)

1. **Build and pin.** Build `mww-detect-armhf` with `mww_build.py` (zig,
   `arm-linux-musleabihf`, static), inspect it with `readelf -h -l` (ELF32 ARM
   EABI5 hard-float, no `INTERP`, non-executable stack) and record the SHA-256
   printed by the build together with the embedded model digest(s). The helper
   is too large for W4c's 120 KiB octal transfer; use `pcm_pilot`'s bounded
   binary-stdin transfer or the existing operator-only `agent_provision.py`
   with a new pinned artifact entry. Never transfer an unpinned model.
2. **Start.** The Go resident verifies the installed helper's digest through an
   open descriptor and executes it via `/proc/self/fd`, exactly like the
   existing ASR/TTS clients: fixed arguments (`--threshold`, `--window`,
   `--cooldown-ms`, `--max-seconds 3600`), no shell, stdin a pipe, stdout parsed
   with a strict schema (unknown fields or events → stop), stderr bounded and
   discarded. It restarts the helper at most once per hour of audio and never
   after an unexplained exit without a fresh state check. Quasar stays running;
   nothing is stopped or reconfigured for detection.
3. **Feed.** One reader goroutine in the resident is a subscriber on
   loopback port 9850 (`yio_audio`, ownership checked against `platformd` as W4c's
   probe does, bound to the resident's boot/firmware/Quasar-identity stamp). It
   sends W4c's `full_envelope` `request_vqe` once, parses length-prefixed
   `QuasarMessage{158}` frames with W4c's bounds, validates each `vqe_0` entry
   (above) and writes its PCM bytes into a ~2 s ring that drops the oldest audio
   instead of blocking Quasar's socket. A second goroutine copies the ring into
   the helper's stdin; stdin is the narrow PCM interface, so the helper never
   talks to Quasar. On exit it sends `unsubscribe`; after a disconnect it
   re-subscribes only after a fresh Quasar-identity check, without a reconnect
   loop. Audio never leaves the device or the process pair and is not logged or
   stored. A standing subscription is a household-audio stream: it needs its own
   private policy action and owner approval (W4c's note), not just this plan.
4. **Gate (echo suppression).** `vqe_0` is already after Quasar's echo
   cancellation, but residual echo of the station's own voice can still match.
   The resident keeps "station speaking" spans from its own playback sessions
   (local PCM player active) and from AliceState (kind 3, SPEAKING) on the
   existing IPC, maps the helper's audio-time `t_ms` to the source's
   `record_start_timepoint_ms`, and drops any detection inside a span widened by
   0.5 s (W4b's rule). There is no barge-in. Retriggers are suppressed by the
   helper's 2.5 s cooldown (≈ W4b's "next utterance starts < 1.5 s after the
   previous one ended", measured from the detection at the word's end).
5. **Report.** A surviving detection becomes an **untrusted data event**
   `{model, t_ms, probability, trusted:false, evidence_class}` in a bounded RAM
   queue (16 entries, 120 s expiry, released once), readable through the
   existing read-only status path. It is never a permission: it neither runs an
   action nor bypasses the host's private policy, fresh one-use preview,
   approval and no-uncertain-retry rules. At most it lets the host start the
   already guarded `speech.listen` flow if policy allows it.
6. **Mute and failure.** The physical mute cuts the microphones; if the source
   delivers silence or nothing, there are no detections and nothing overrides
   it. Source loss, a format change or a helper exit makes the detector idle and
   reports `unknown`; nothing is retried blindly.

### Owner's later live test (not run)

1. W4c's level pilot has shown `vqe_0` frames (S16LE, 16 kHz) on 9850; repeat
   its read-only identity checks on the firmware being tested.
2. Host build and parity: `python -m pytest -q tests/test_mww_*.py` with
   `ALICE_MCP_MWW_HELPER` / `ALICE_MCP_MWW_MODEL` set to the host build.
3. A separately approved, bounded (≤ 120 s) operator pilot — same pattern as
   `pcm_pilot.py`: pinned key-only SSH, state-bound one-use preview, unchanged
   Quasar PIDs, cleanup, no retry — runs source + helper and returns only the
   JSON events, `VmHWM` and CPU ticks from `/proc`. No audio returns.
4. The owner says the wake word 20 times at 1 m and 3 m, plus the near-miss
   phrases and 10 minutes of normal TV/speech, with the station also speaking
   a few replies; detections are compared with the owner's notes. Repeat with
   the physical mute on (expect zero detections).
5. Only after that, and a separate policy opt-in for a standing audio stream,
   would a resident integration be proposed.

## Known limits

- Every number is synthetic: TTS voices, generated noise mixes and read speech.
  No real speaker of the household, far-field room, TV/music at home or real
  `vqe_0` stream was evaluated. Owner recordings of the wake word (tens of real
  utterances, 1–3 m, via the later pilot) are the most valuable next training data.
- The four Piper voices are both training positives and the W4b corpus voices.
- A streaming detector fires on inflected forms that start with the wake word
  («компьютерный») and may miss a wake word followed by a command without pause.
- FA/hour rests on 1–2 events per model in 2.87 h; real household FA/hour can be
  much higher. Several loaded models add their FAs.
- Recipe negatives were a 20 % subset and the CHiME-6 validation set a third,
  for host RAM; the full set might do better on a larger machine.
- «маруся» is not usable as trained; «окей дом» is borderline.
- ARM32 execution, RSS and CPU are unmeasured; nothing was deployed.


## Preserved workstation owner-recording workflow

The integrated source retains the unpublished owner preparation, segmentation,
hard-negative mining, fine-tuning and held-out evaluation in `scripts/voice`,
without putting these tools back into the MCP wheel:

```sh
python -m scripts.voice.mww_train owner --word komp --data DATA --owner PRIVATE_DIR --helper HOST_HELPER
python -m scripts.voice.mww_train features --word komp --data DATA
python -m scripts.voice.mww_train train --word komp --data DATA --mww MWW_SRC \
  --finetune-from DATA/komp/trained --steps 3000 --learning-rate 0.0002
python -m scripts.voice.mww_eval owner --helper HOST_HELPER --owner PRIVATE_DIR \
  --holdout DATA/komp/owner/holdout.json --model-name komp
```

Only explicitly selected private inputs are read when the operator runs these
commands. Preparation verifies each recording against its manifest hash and
selects the latest manifest-timestamped clips for a new held-out split. That rule
does not identify a historical 5/15 split or turn earlier training examples into
held-out evidence. Existing contextual-label and model-selection fixes remain.
No recording, model, dataset or training result was imported or produced during
the merge; synthetic segmentation tests are separate from acoustic acceptance.
