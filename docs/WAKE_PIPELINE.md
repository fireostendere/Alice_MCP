# Host streaming wake pipeline: source-tested, not live-accepted

Workstream W4b. Everything here was measured on **synthetic Piper speech on the
host**. No microphone, station, room, playback or network was used. Results are
`synthetic=true`, `hardware_verified=false`. Live room acceptance depends on a
reviewed device-to-host PCM source (W4), which does not exist yet.

## Pieces

| File | Role | Dependencies |
|---|---|---|
| `src/alice_mcp/wake_pipeline.py` | Streaming segmentation, echo and retrigger suppression, routing, MCP inbox, dispatcher | stdlib only |
| `src/alice_mcp/wake_simulate.py` | Pinned Vosk via `ctypes`, Piper corpus, scoring, `--simulate` CLI | optional, never installed by default |
| `tests/test_wake_pipeline.py`, `tests/test_wake_simulate.py` | Scripted recognizer and scoring tests; one real Vosk/Piper test that skips without artifacts | none / optional |

## PCM source interface (for W4's capture candidates)

`WakePipeline(profile, recognizer, rate=...)` takes one call per chunk:
`feed(pcm, station_speaking)`. `pcm` is mono signed 16-bit little-endian
`bytes`, between one sample and one second long, at one fixed rate of
8–48 kHz. `station_speaking` is a `bool` that must come from the station's own
playback state, for example Alice's speaking/TTS state or an owned PCM-player
session. Do not infer it from the captured audio. `finish()` flushes the last
utterance.

Any W4 candidate (a Quasar audio-channel subscription over local IPC, ALSA
`dsnoop`, or a second hardware substream) plugs in by producing these chunks. The
pipeline never opens audio itself, never changes gain or mixer state, and has no
mute override: if the physical mute cuts the microphones, the source delivers
silence and nothing activates.

The recognizer is a two-method protocol. `accept(pcm)` returns
`{"text", "start", "end"}` (absolute stream seconds) at an endpoint, or `None`.
`flush()` forces a final result. `VoskRecognizer` implements it against the
Vosk 0.3.45 C API. Vosk's word times were checked to be absolute stream seconds
across endpoints.

## Behavior

- **Endpointing.** Vosk's built-in endpointer applies the model's rules: 0.5 s,
  1.0 s or 2.0 s of trailing silence (by decoder state), or 5 s with no speech.
  The pipeline also forces a flush 10 s after the last endpoint. That bounds
  utterance length even in continuous noise. Vosk 0.3.45 exports no endpointer
  tuning call.
- **Routing.** Reuses `assistant_profile.route_transcript`: profile phrases must
  be a transcript prefix ending on a word boundary. There is no fuzzy, alias or
  near-miss matching. Routed events carry `trusted: false`.
- **Privacy.** An utterance without an activation phrase yields only timing and
  `decision: "no_activation"`. Its transcript is discarded and never leaves the
  pipeline.
- **Echo suppression.** A wake utterance whose word span overlaps any
  station-speaking run, widened by 0.5 s on each side, becomes
  `suppressed_echo`. There is deliberately no barge-in while the station speaks.
- **Retrigger suppression.** A wake utterance starting less than 1.5 s after
  the previous wake utterance ended becomes `suppressed_retrigger`, and the
  window restarts from it. This also drops a quick, legitimate second command,
  which is the safer failure.
- **Bounds.** Invalid chunks, rates or recognizer results raise `SafetyError`.
  Station-speaking runs older than the longest possible utterance are pruned.

## Destinations

`Dispatcher(inbox, enabled_routes=())` makes **at most one destination decision
per utterance**. It refuses repeated or out-of-order utterance ids. With no
enabled routes, which is the default, every routed event returns
`route_disabled`. A later live integration must take its enabled routes from
private station policy. Any text the pipeline routes is never a permission and
never selects a tool or action.

| Receipt reason | Meaning |
|---|---|
| `mcp_inbox` | Delivered to the MCP inbox as untrusted data |
| `stock_handles_own_activation` | Route `alice` with a stock wake word: the station's own detector hears the same room, so the host submits nothing |
| `no_documented_text_submission` | Route `alice` with a custom name: no guarded submission path exists (see below) |
| `stock_may_also_respond` | An MCP utterance contains a word starting with `алис` or `яндекс`; stock Alice might actuate too, so MCP delivery is held |
| `route_disabled`, `not_routed`, `duplicate_utterance`, `inbox_full` | Fail-closed outcomes; nothing is delivered |

### MCP: untrusted data through the one-shot lifecycle

`McpInbox` stores a routed utterance in RAM and returns an opaque 32-hex
`capture_id`. `status(capture_id)` returns the same shape that
`get_capture_status` already uses for a speech transcript:
`outcome: "complete"`, `capture_ready: false`, `untrusted_text: true`,
`do_not_retry: true` and `evidence_class`, plus `route`, `phrase` and `text`.
The text is **released once**. Later reads, unknown ids and entries older than
120 s raise an error. The inbox holds at most 16 unread events and never evicts
one to make room. The caller must name the evidence class, so a live source
cannot inherit the `synthetic` label by default.

The inbox is **not yet wired into an MCP tool**. No live source runs inside the
server process, and changing the tool contract is outside W4b. The intended
wiring is a fallback lookup in `get_capture_status` for an unknown `capture_id`.
It adds no new action permission. A client agent that reads the text must still
go through the existing private policy, preview and approval for any action.

### Stock Alice: no documented, guarded text submission (gap)

Sources were reviewed for a non-destructive way to put text into stock Alice's
dialogue on Mini 1 / Mini 3. None meets the guard model, so none is
implemented.

1. **Repository knowledge.** `station-internals` notes that text input skips
   ASR. The pinned catalog records that [glagol protocol details are deferred
   upstream](https://hackalice.me/internals/network/) ("протокол в этой вики
   пока не описан"). For Station 3 it records that glagol TLS acceptance did not
   yield responses to older commands.
2. **Glagol `sendText` (community).** [AlexxIT/YandexStation at
   `4e8567b8`](https://github.com/AlexxIT/YandexStation/tree/4e8567b8dd06679d233233ebb17f9d56a9b2003a)
   (MIT) sends `{"command": "sendText", "text": ...}` over the local port 1961
   websocket (`conversation.py:62`, `core/yandex_station.py:964,992`). Each
   message carries a `conversationToken` obtained from
   `https://quasar.yandex.net/glagol/token` with the owner's authenticated Yandex
   account session (`core/yandex_glagol.py:57`). This is a reverse-engineered
   protocol, not a Yandex-documented API. It needs a **new account-bound
   credential class**, which AGENTS.md excludes. The text is processed as a
   full Alice request, so it can actuate music or the smart home.
3. **Native IPC schema.** At [quasar-protos
   `1044fbdf`](https://github.com/v3rt3p/quasar-protos/tree/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6),
   `remoting.proto` has `AliceCapabilityMethod.START_REQUEST = 0` with
   `vins_request = 2` / `alice_request = 20`. `model_objects.proto:1531` defines
   `VinsRequest.RequestType.TEXT_INPUT = 1`, but its payload is a free-form
   `event_json` string with no specified format. `capability.proto` has a
   `TStartTextInputDirective`. These schemas were extracted from a **Station 2**
   `maind` (license not asserted). They define field numbers, not semantics, and
   nothing was observed on Mini 1 / Mini 3. Guessing the payload of an actuating
   dialogue request is not non-destructive.
4. **Yandex documentation.** No Yandex-published API for submitting text to a
   Station's dialogue was found in the reviewed sources.

W4 independently reached the same three findings. W4b checked items 2 and 3
against the pinned source revisions above.

**Verdict.** Stock routing is supported only when the activation phrase is a
stock wake word. In that case nothing is submitted (`stock_handles_own_activation`)
and MCP never also receives the utterance. A custom name routed to `alice`
returns `no_documented_text_submission`. Submitting an untrusted transcript
into an actuating dialogue would also bypass per-action preview and approval.
Even with a documented protocol, it would need its own guarded design.

The host cannot stop stock Alice from mis-waking on its own, for example on an
MCP name that sounds like "Алиса". Profiles should keep MCP names acoustically
distant from stock wake words.

## Synthetic corpus

The profile has five phrases: `компьютер`, `сол`, `окей дом` and `маруся` route
to `mcp`, and `алиса` routes to `alice`. Each voice speaks 35 cases. The four
pinned Russian Piper voices (denis, dmitri, irina, ruslan) give **140 cases**
and about 600 s of audio. Cases are concatenated into **one continuous 22.05 kHz
stream**, fed in 100 ms chunks, so Vosk's endpointer has to segment them. Each
case has 0.5 s of lead silence and 2 s of trailing silence.

| Class | Cases per voice | Content |
|---|---|---|
| Positive | 15 | Name only and name + command for every phrase, including "Сол" and stock "Алиса"; −20 dB quiet speech; +12 dB clipped speech; white noise at 10 dB SNR (also combined with −10 dB speech); one case at 0 dB SNR |
| Negative | 15 | No name; embedded or trailing names ("Мой компьютер…", "…Марусю…", "…новый компьютер?"); similar words ("Соль", "Сок", "Солнце", "Компьютерный", "Окей, дома…", "Алисин"); a noisy embedded name; quiet "Соль"; digital noise at −30 and −15 dBFS; 2.5 s of digital silence |
| Echo | 3 | Wake phrase spoken while `station_speaking=true` |
| Retrigger | 2 | The same wake utterance twice, 1 s apart |

Speech uses `local_tts.synthesize` at its maximum bounded volume of 0.25. The
measured speech RMS is low, a median of about −36 dBFS. Noise for speech cases is
therefore set **relative to each case's measured speech RMS** (SNR). An earlier
run with fixed-dBFS noise put the noise *above* the speech; it was discarded,
and a regression test now covers the calibration. Piper samples random noise
during synthesis, so the audio, and the results, vary between runs.

Scoring gives credit only for an exact `(route, phrase)` match within the case's
region. A wrong phrase is a misroute. Any routed event in a negative case is a
false accept. Rates below are per case, not per hour.

## Measured results (two identical-code runs, 2026-10-29)

Host: AMD Ryzen 5 9600X under WSL2; unrestricted Vosk 0.3.45 with
`vosk-model-small-ru-0.22`.

| Metric | Run A | Run B |
|---|---|---|
| False rejects (all positives) | 16/60 (26.7%) | 15/60 (25.0%) |
| False rejects excluding "Сол" | 6/48 (12.5%) | 6/48 (12.5%) |
| Misroutes (wrong phrase or route) | 0/60 | 0/60 |
| False accepts | 5/60 (8.3%) | 3/60 (5.0%) |
| Of which went to MCP / to stock no-op | 3 / 2 | 2 / 1 |
| Commands recognized exactly after the name | 26/44 | 27/44 |
| Echo leaks (routed while station speaking) | 0/12 (9 suppressed, 3 not heard as a wake phrase) | 0/12 (11 suppressed) |
| Retrigger duplicates | 0/8 (7 suppressed) | 0/8 (8 suppressed) |
| Endpoint delay after speech end (median / p95 / max) | 0.72 / 1.14 / 1.22 s | 0.73 / 1.18 / 1.26 s |
| Compute for the deciding 100 ms chunk (median / p95 / max) | 4.7 / 9.7 / 24.5 ms | 4.7 / 12.3 / 16.7 ms |
| Real-time factor, whole stream | 0.024 | 0.026 |
| Model load | 0.34 s | 0.41 s |
| Peak RSS of the recognizer process | 367 MiB | 366 MiB |
| MCP inbox deliveries / stock no-ops; one-shot release | 50 / 6; verified | 51 / 5; verified |

Accepted positives by phrase (A / B): `компьютер` 16/16, 14/16; `окей дом`
11/12, 11/12; `маруся` 11/16, 13/16; `алиса` 4/4, 4/4; **`сол` 2/12, 3/12**.

Latency here is the endpoint delay: decision position minus the end of the
synthesized audio, in stream time. It is dominated by Vosk's trailing-silence
rules. Compute is wall time for the chunk that produced the decision. A live
source would add capture and transport delay, which was not measured. Peak RSS
covers Python, `libvosk`, the model, the pipeline and the in-memory corpus
stream (about 26 MB). Piper ran in the parent process and is excluded.

Observed failure modes, from the recognizer's own transcripts on synthetic audio:

- **"Сол" stays the known hard case.** It was decoded as "со", "соль",
  "сорок", "сок" or nothing, and in reverse "Соль…" became "сол…" (false
  accepts). Short single-syllable names are not supported reliably. No alias or
  fuzzy rule was added to hide this.
- **Inflection is a strict reject.** "компьютер" was decoded as "компьютера"
  (denis voice), and the profile's word boundary correctly rejects it.
- **Voice dependence.** The denis voice caused most `маруся` rejects ("нравился",
  "на люси", "молюсь"). Synthetic voices are not a population of speakers.
- **Noise turns embedded names into prefixes.** At 10 dB SNR, "Мой компьютер
  снова завис" lost "Мой" and routed. Prefix routing is only as good as the
  recognizer's first word.
- **"Алисин кот"** was heard as "алиса …". That routes to the stock no-op, so
  the host did nothing, but the station's own detector could still respond.

## Pinned optional artifacts (not bundled, not installed by default)

| Artifact | SHA-256 | Provenance |
|---|---|---|
| Vosk 0.3.45 wheel, tag `py3-none-manylinux_2_12_x86_64.manylinux2010_x86_64` | `25e025093c4399d7278f543568ed8cc5460ac3a4bf48c23673ace1e25d26619f` | Matches PyPI's published digest. Used only as a container; the Python package was **not** used because its `srt` dependency is source-only, and building it would run a setup script |
| `libvosk.so` from that wheel | `85c4654de3acdeb99abab86eeb2a6e603927d37089597c0fcc33d8638dc2ccaf` | Hashed through an open descriptor, then loaded via `/proc/self/fd` |
| Small Russian model 0.22 ZIP | `961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4` | The same locally observed pin as NATIVE_AGENT.md; Apache-2.0 per the model catalog |
| Piper voices `ru_RU-{denis,dmitri,irina,ruslan}-medium.onnx` | in `VOICES` | ONNX digests match HuggingFace `rhasspy/piper-voices` LFS SHA-256; `.onnx.json` digests are locally observed |

Voice dataset licenses per their model cards: denis and dmitri are CC0; irina
is "Unknown" (RHVoice); ruslan is CC-BY-NC-SA-4.0. Piper itself is
GPL-3.0-or-later. This is a personal, noncommercial synthetic test; no weights
or generated audio are distributed. `libvosk.so` is x86-64 host-only and
targets no station.

## Running it

No station or network is involved. The artifacts must already be on the owner's
machine:

```sh
PYTHONPATH=src python -m alice_mcp.wake_simulate --simulate \
  --libvosk /PRIVATE/libvosk.so --vosk-zip /PRIVATE/model-small-ru-0.22.zip \
  --voice /PRIVATE/ru_RU-denis-medium.onnx --voice /PRIVATE/ru_RU-irina-medium.onnx
```

The interpreter needs `piper-tts` (the `tts` extra). It prints compact JSON with
`synthetic: true` and `hardware_verified: false`, and exits 2 with
`status: "unavailable"` when artifacts are missing. The one real-Vosk test runs
only when `ALICE_MCP_WAKE_LIBVOSK`, `ALICE_MCP_WAKE_VOSK_ZIP` and
`ALICE_MCP_WAKE_VOICE` are set; otherwise it skips.

## Feasibility and gaps

- **Host streaming wake/ASR: partially feasible on synthetic audio.** Distinct
  multi-syllable names (`компьютер`, `окей дом`) and the stock name work well
  at moderate noise. `маруся` is voice-dependent. `сол` is not viable with this
  recognizer. False accepts of 5–8% on adversarial near-miss negatives rule out
  unattended actuation. Routed text must stay untrusted input to the existing
  preview and approval flow.
- **Not measured:** room acoustics, far-field microphones, station VQE/AEC
  output, real speakers or children, music or TV, continuous false wakes per
  hour, real capture latency, or ARM on-station execution. 140 cases per run are
  not a false-wake-rate study.
- **Echo suppression is only as good as `station_speaking`.** There is no
  acoustic echo cancellation here and no barge-in.
- **The retrigger debounce** drops a legitimate second command within 1.5 s.
  There is no follow-up window: a name spoken alone, followed by a pause, routes
  with empty text, and the next utterance is not attached to it.
- **The stock-word stem check** (`алис`, `яндекс`) is a conservative heuristic.
  It assumes the station's stock wake word is one of these.
- **Not wired yet:** the MCP inbox is not exposed through `get_capture_status`.
  Live use needs W4's capture source, a private policy opt-in for enabled
  routes, and separate owner acceptance.
