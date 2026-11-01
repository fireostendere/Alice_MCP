# Local wake and OpenAI voice routes

## Current smart-speaker pilot (2026-11-01)

### One question, one generated answer

The active Mini 1 route is now `station_oneshot`, not automatic free conversation.
The station still uses a direct audio connection, but the reviewed client stops
microphone transmission at the first question endpoint, drains exactly one answer
and closes that session. Later room speech is not sent until another wake word.
Only an explicit request to talk may call `start_conversation`; that one-use host
transition re-enables the microphone and installs conversation instructions/tools.
Repeated transitions are harmless. Tool work and outputs are bound to their own
session/cancel token, so a late search cannot affect a later turn. Silent wake-only
responses preserve a question spoken while that response was pending.

The short-answer model is `gpt-realtime-2.1-mini`, now with voice **coral** for
wake-triggered one-shot sessions and their explicitly requested continuation.
Chat-triggered literal speech retains its existing **marin** voice. It generates the answer afresh;
there is no table of clock/weather/translation responses. Initial time questions
use a fresh host-clock snapshot supplied when the question session is configured,
not a network lookup. That snapshot can precede the question endpoint by the
question's duration; it is not a continuously updated clock inside a long session.
Free conversation has `get_time` for new clock readings.

The short-question endpointer now defaults to **200 ms**, independently of the
**350 ms** conversation default. Optional `voice.command_vad_silence_ms` and
`voice.vad_silence_ms` accept integers from 200 to 1000 (not booleans). The explicit
conversation transition restores its own configured timing in the live session;
it does not inherit the shorter command pause. Long pauses within a question can
still end it early: these defaults are tunable, not universal human-speech VAD.

Default weather is **Belgrade, Serbia**. The idle worker prefetches validated
[Open-Meteo](https://open-meteo.com/en/docs) model weather and
[BBC Russian RSS](https://feeds.bbci.co.uk/russian/rss.xml) headlines. Only data are
cached in RAM, for at most five minutes; spoken answers are never cached. Weather
units, finite ranges, WMO codes and observation/date freshness are checked. News
requires a publisher URL and a publication timestamp within the preceding day;
undated/old items and XML DTDs are rejected. Expired data are not served on failure.
Other cities, periods and topical news use actual web search and can take longer.
The status exposes cache-ready booleans, never the private connection settings.
Only compact city/current/forecast and publisher/headline data enter the initial
voice prompt. Source URLs, duplicate source arrays and news publication/retrieval
metadata remain in the validated caches, not the voice context. When a fresh
snapshot is present, the corresponding fetch tool is omitted from that one-shot
request to avoid an unnecessary second lookup. Missing snapshots retain those
tools; explicit conversation restores the full tool list. No cached evidence is
mutated or discarded by this optimization.

One-shot input now disables the additional cloud noise-reduction filter because
this route already receives Quasar's processed `vqe_0` PCM. Explicit conversation
restores `far_field` along with its own VAD setting. Other voice routes retain
their existing filter. This is not a bypass of the station echo cancellation.
The weather observation's time-of-day is also omitted from the voice projection:
one intermediate clock answer matched that observation time rather than the host
clock. Source caches retain the timestamp and its freshness checks; the host-clock
snapshot is now the only time-of-day in the short-answer prompt.

The explicit preconnected text-only socket is reused for a question via `askwarm`;
no microphone is sent during idle preparation. Older reviewed clients cannot
silently fall back to an unbounded conversation for a one-shot request.
Build: Zig 0.15.1, `arm-linux-gnueabihf.2.23`, `-O2 -Wall -Wextra -Werror -s`,
`native/live_client.c -ldl`; two byte-identical 33,536-byte artifacts, SHA-256
`c64164b24e1cff594426c81fc091cae68f6268e577bfc2893ff19871c75856d7`.

#### Acoustic acceptance, not a universal speed guarantee

Generated SAPI/Realtek phrases, one Mini 1 and the C922 microphone; no room
recordings retained. A 10-ms reference-envelope alignment located the generated
question's end; answer onset used a sustained RMS threshold. These are individual
trials, not human-voice percentiles; sensor latency is uncalibrated and the
separate video stream is not audio-synchronized.

**Latest processed-input follow-up:** three alternating weather pairs measured
1.56/1.41/1.54 s versus stock 1.62/1.64/1.54 s (medians **1.54/1.62 s**). After the
weather timestamp projection fix, another weather trial measured 1.54 s. All
recovered the default Belgrade current temperature/conditions. Three later clock
pairs measured 1.64/1.38/1.59 s versus stock 1.37/1.63/1.22 s (medians
**1.59/1.37 s**); clock values matched the host snapshot, including one equivalent
12-hour rendering. Thus the latest clock median is about **220 ms slower** than
stock, and the strict speed criterion remains **unmet**. A prior six-minute clock
error is not counted as a successful time answer.

The same final source produced “Goodnight” at about 1.39 s and no response to the
later unwoken arithmetic question. Explicit conversation and recognized «хватит»
both worked with verified native cleanup. One news trial was stopped before a
measurable acoustic onset and is excluded from latency acceptance; a later one
started at about 1.56 s and was interrupted. Its recovered Poland headline fragment
was not a fully reliable verbatim transcription, so do not claim exact-news-wording
qualification or a substantive-news speed comparison from it.

The paired provider-only noise-filter trial measured first audio at
1.200/1.083 s with the extra filter and 0.990/1.033 s without it (about 130 ms
median difference). A roughly halved prompt byte count gave essentially identical
medians (~1.084 s), so that prompt rewrite was not deployed. A 100-ms command VAD
was also not deployed: it ended a synthetic request containing a 150-ms internal
pause about 1.38 s before the command ended, producing an initial silent response;
200 ms kept that request together. The native silent-response path can preserve
later speech, but that experiment does not qualify the earlier endpoint as a
complete-question endpoint or demonstrate an end-to-end gain. No native buffer
reduction was made: review found no intentional first-audio accumulation delay in
the queue/poll path, and the requested ALSA latency is not a proven audible bound.

The following table is the **earlier** coral/far-field baseline, not the latest
processed-input result above.

These earlier trials used the 200-ms/compact-context/**coral** configuration; the
previous 350-ms trials had clock/weather/news gaps of about 2.31/1.79/2.81 s.
Several settings changed together, so the measured difference is not attributable
to VAD alone. Some quality trials ran under host CPU contention; this is not a
controlled load benchmark. A short-noise false onset was excluded: the final
diagnostic requires at least 150 ms of above-threshold RMS within 300 ms and
bounds answer detection before any subsequent generated question. This remains
an onset heuristic, not speaker identification. Room PCM/video remain RAM-only.

| Request | Question-end to audible onset | Result |
| --- | --- | --- |
| Translation of «спокойной ночи» | about 1.42 s | “Good night”; later arithmetic question without wake produced no answer through the capture end |
| Belgrade weather, default city | about 1.78 s | Correct Russian temperature/conditions; fresh-source answer, no preamble |
| Clock | about 1.40 s | Correct current time in Russian, no lookup/preamble |
| Stock Alice clock, comparable phrase | about 1.45 s | Stock time answer |
| Stock Alice weather | about 1.60 s | Stock temperature/conditions; faster than this coral trial |
| General news | about 1.97 s | Independent ASR recovered the supplied Poland headline; recognized «хватит» and native cleanup; the rest was intentionally not auditioned |
| Stock Alice news | about 1.83 s | This is its introductory promise, NOT a substantive headline; interrupted with «хватит» |
| Fact: capital of Portugal | about 1.49 s | Lisbon recovered by independent ASR |
| Explicit «давай поговорим» | mode transition observed | `start_conversation`, spoken greeting/question, recognized stop and verified cleanup |

Intermediate marin trials reached clock 1.34/1.58 s, weather 1.53 s and news
1.50–1.72 s, but news/fact transcripts were unreliable. A provider-only synthetic
comparison then found correct Russian response text with poorly recovered audio
for marin/cedar; coral's generated PCM was correctly transcribed for the same
fact. This bypassed the station transport entirely. It supports a voice/rendering
or ASR compatibility issue, not proof of a native transport defect. The coral
device trials above were subsequently checked with independent `gpt-4o-transcribe`.
These samples do not establish correctness for every headline or language. An
earlier intermediate build also produced an English clock answer and a news
preamble; instruction-following is not a hard guarantee.

A separate RAM-only provider comparison of the same synthetic clock audio measured
roughly 1.19 s to first audio for mini versus 1.24 s for full Realtime after commit;
the full model did not improve that isolated trial. This is not a device benchmark.
That earlier warm clock trial was comparable to stock; its 50-ms difference is below
what these few uncalibrated trials can establish. Weather was about 180 ms slower.
Strict stock-or-better speed for every simple question is **not demonstrated**.
The earlier blanket claim that a faster backend was the only possible next step
was premature: local context/tool/endpointer changes measurably improved the pilot.
The stop-colour issue below also remains unresolved; a red wake indicator is not
proof of a red native stop animation.

Separate synthetic component measurements did not justify a new serial pipeline:
streaming ASR final text took about 0.81–0.98 s after speech, stateless Luna JSON
about 1.02–1.67 s, and cloud TTS first PCM about 0.82–0.95 s. Benchmark-only warm
Piper synthesis took about 0.13–0.18 s (cold about 2.66 s); it is not implemented as
a runtime cache, and the explicit OpenAI output binding was not bypassed. A
provider-only VAD trial measured 350/200-ms server VAD first audio at about
1.123/0.994 s after speech; semantic-high measured 1.360 s. These isolated samples
are not end-to-end station acceptance or a universal lower bound.

Earlier one-shot checks: **1,820 Python tests passed, 13 skipped**, one existing fork
deprecation warning; 15 Node integration tests, ruff, mypy and MCP contract passed.
These results include local fake-TLS native checks, not only Python mocks.

Full publication checks repeated after the processed-input/clock-projection and
prepared-hold verification-mode changes: **1,831 passed, 13 skipped**, with the
existing fork deprecation warning; ruff, mypy, 15 Node tests and MCP contract
passed. An intervening run under heavy host load showed failures and timed out;
it is not counted as a pass. The completed rerun passed without relaxing tests.
The active native artifact is unchanged by the Python-only optimization.

Prepared LED holds are also bound to their verification mode: switching between
resident-verified and SSH-only preparation recreates the helper. Fresh state stamps
within the same mode can still renew it. A synthetic native→SSH→firmware-change
regression verifies blocking before any first coloured frame, without station access.

### Fast red wake feedback

The owner requires **red**; changing to stock purple is not an acceptable fix.
The earlier 1.2–2.3-second indicator startup was a cold path: the configured
resident channel was unavailable, so idle preparation was skipped. The explicit
SSH indicator can now prepare its FIFO-blocked stock player without that channel.
Preparation never sends a coloured frame. Each activation still consumes a fresh
preview, rechecks local policy/configuration and pinned SSH trust, and stays bound
to the existing boot-scoped SSH process. This is not a general SSH fallback for
native tools or a bypass of a resident policy rejection.

SSH-only preparation first verifies the full firmware hash. The new reviewed
client's fixed read-only `--firmware-state` command returns file identity including
nanosecond mtime/ctime, excluding access time. The helper accepts no caller path,
cannot write, and is reused only from a private listener directory after its exact
binary hash is checked. The verified executable descriptor stays open to prevent
pathname substitution. Metadata is checked before and after the initial full
hash; unchanged identity permits a cheap activation check. Changed or unavailable
identity requires the full firmware hash again. An absent/mismatched client pin
retains the full-check path. Firmware mismatch blocks before the first frame;
uncertain writes still latch off instead of retrying.

Fast-client build: Zig 0.15.1, the ARM target/flags below, two byte-identical
32,272-byte artifacts, SHA-256
`5d84e74ba1c2703022a18fb38f66f8c96d9d8c931a6476b78fb65c404d7022b4`
(`live_voice.FAST_LED_CLIENT_SHA256`). Older pinned clients remain accepted.

Live wake-only tests used a single DirectShow audio/video input, in-memory NUT
packets, 20-fps red-mask metadata and 10-ms RMS-envelope matching against the
generated SAPI word. No recordings were retained. Two repeated trials measured
approximately **350 / 360 ms from acoustic word end to the red optical rise**;
reference-envelope correlations were 0.935 / 0.946. Player launch acknowledgement
was 49 / 46 ms after recognition in inspected turns. These boundaries differ:
recognition precedes the player acknowledgement, while the camera includes the
recognizer and physical display delay. Video resolution is 50 ms; sensor latency
is not separately calibrated. This is a few generated-voice trials, not a human
latency percentile or a measured comparison against stock Alice.

Final fast-feedback regression: 1,799 Python tests passed, 13 skipped; one existing
fork-from-multithreaded-process deprecation warning. The 15 OpenCode integration
tests, ruff, mypy, contract/catalog, package and publication/privacy checks passed.

Stop acceptance remains separate: in a later far-field story test and a chat-speech
test, the synthesized stop did not end playback. An independent bounded read-only
stock spotter subscription returned `complete` with zero recognized phrases.
The completed speech had verified native cleanup, but this is not successful
interruption evidence. No large blue-colour rise was seen in the story capture;
without a recognized stop it does not qualify the stock stop animation either.
Do not infer stop reliability or colour-consistency acceptance from fast red wake
feedback or from the earlier successful cancellation trials.

#### Recognized stop-colour failure

Subsequent trials did recognize «хватит» and showed the native purple/blue pulse
for roughly 0.6 s. A bounded red acknowledgement using the existing player did
not suppress it: whole-frame blue rose from about 550 to 2,186 pixels while red
remained near 1,436. The reported 54-ms player acknowledgement was not red optical
acceptance. That ineffective acknowledgement was removed and the running project
refreshed with verified listener cleanup. Cancellation remains shared across the
stock spotter and local stop routes, covering both standby/chat speech and Realtime.

A fixed read-only `LedCapability` AddListener experiment on the known local IPC
received three frames but no matching capability snapshot within five seconds.
It exported only sanitized counts/flags and left Quasar running. This does not
prove capability absence or handler access. Compiled `DrawAnimation` schemas and
`Foreground`/`Substitution` enum names do not establish supported Mini 1 parameters,
animation encoding or an authorized external route. No native animation directive
was sent. Stop-colour acceptance is still blocked; do not fight the stock renderer,
replace firmware assets or disable recognition as an implicit workaround.

Post-rollback regression: **1,800 Python tests passed, 13 skipped**, one existing
fork deprecation warning; 15 Node tests, lint/types, MCP contract and scoped source
privacy checks passed. These are regression results, not a stop-colour PASS.

### Answer routes and earlier measurements

The selected Mini 1 route is `station_live`: the station's local wake detector opens
direct Realtime speech-to-speech, not the older transcript/OpenCode command path
described below. This does not change the default route on other installations.
The project still uses one `alice` MCP connection and leaves Quasar enabled.

`ask_station(station_id, question)` answers ordinary chat questions and invokes the
existing guarded `speech.say` exactly once. Time uses the host clock and includes
its UTC offset; greetings and missing-city weather clarification are local. Other
facts use one Responses request with a required completed web search, `store=false`,
no station-action tools, bounded spoken text and at most six separate source URLs.
Search failure yields an explicit unavailable answer, not unsourced model facts.
The speech permission is checked before lookup and dispatch gets a fresh one-use
preview plus current policy/state/trust checks. An uncertain or cancelled utterance
is never automatically replayed. Source links show the lookup's evidence, not an
independent guarantee of each generated claim.

The OpenCode plugin's optional `station` alias binds ordinary Alice questions to
that speaker. Its final session-context hook preserves project/security sources
and adds the speaker instructions even when a later config transform replaces the
agent's base prompt. The two direct tool executors reject another station for
Alice, without changing other agents. The pilot's ordinary chat also has durable
station-dialogue instructions. A successful answer is already spoken: no second
`speech.say` call. No permission is derived from spoken text or search results.

Realtime has a host-clock `get_time` tool and shares the same verified-search
implementation. `voice.vad_silence_ms` defaults to 350 ms (integer, 200–1000), rather
than 500 ms. The reviewed native client enables TCP_NODELAY and reports
`speech_stopped`; `command_endpoint` is that provider event, not the acoustic end
of the owner's question. New client build: same Zig/target/flags as the warm build
below, two byte-identical 31,220-byte artifacts, SHA-256
`8c58160a7bb0b537d70d97071a59c1afa2d5d668e43c20fac894f1e080713364`
(`live_voice.SMART_CLIENT_SHA256`). Older pinned clients remain supported.

Stock «стоп»/«хватит» now cancels the standby's current speech as well as the voice
turn. A verified interrupted playback returns `cancelled`; an unverified end stays
`unknown`. The stock spotter and its volume handling are not disabled. The optional
process binding `ALICE_MCP_INDICATOR_COLORS` maps an alias to a validated indicator
colour; it cannot enable an off indicator, changes preview identity, and does not
rewrite inventory/SSH permissions. The owner requires `red`; the pilot now
recolours the stock listening pattern red. The earlier purple-palette experiment
was rejected and is not a workaround for stock stop animations. The indicator
does not suppress firmware animations or claim exclusive control of the ring.

### Live evidence and limits

One Mini 1, generated Russian SAPI phrases through Realtek, C922 microphone/camera.
`scripts/voice/room_check.py` keeps bounded PCM/RGB only in RAM and returns 50-ms
RMS intervals plus whole-frame colour counts. No room recordings were retained;
audio/video synchronization and a ring-only colour region were not verified.

| Test | Observed result |
| --- | --- |
| Baseline clock voice question, 500-ms endpoint | About 2.50 s acoustic question-end to response; 4,074 ms wake-to-first-audio data |
| Arithmetic, 350-ms endpoint/new client | About 1.90 s acoustic gap; connected 1,519, endpoint 2,707, first audio 3,783 ms after wake |
| Separate clock voice question after activation | About 1.65 s acoustic gap; playback continued beyond the 16-s capture |
| Science-news voice question | About 1.45 s to the initial audible reaction; later speech after the search is not a two-second substantive-answer claim |
| Natural weather question in bound chat | One `ask_station`, `applied`; approximately 24.1 s through the agent and complete speech |
| Time question in a new ordinary Alice chat, without attached session instructions | One `ask_station`, local `host_clock`, `applied`; approximately 12.65 s through the agent and complete speech |
| Technology news in bound chat, latest sources enabled | One `ask_station`, `applied`, six source URLs; first TTS data 897 ms after dispatch, full playback about 75.9 s, chat about 90.9 s |
| Stock stop during long chat-triggered speech | `cancelled/stopped`, 139,184 frames, one flush, no underruns, verified cleanup; room activity ended at 3.75 s and stayed below threshold through the 12-s capture |

The arithmetic player completed 192,000 frames with one flush, zero underruns and
verified cleanup. Its provider-endpoint-to-first-audio-data interval was 1,076 ms;
that is not the acoustic gap. Indicator launch acknowledgement in that earlier
trial took 2,318 ms after wake; the fast red-feedback measurements above supersede
that cold-start result.
The whole-frame camera counts changed around stop but cannot establish an exact
ring colour or prove absence of a brief firmware colour transition.

The science-news turn reported a tool call and completed 1,656,000 frames with
verified cleanup, one flush and one recovered underrun. Endpoint/first-audio/tool
events were 2,615 / 3,149 / 3,951 ms after wake. The bounded room capture does not
identify precisely which later syllable starts the substantive searched answer.

During activation, saved agent/plugin changes were not reflected in the cached
location. The operator used `DELETE /api/debug/location` scoped explicitly by
`location[directory]` to this project, then verified the rebuilt Alice role and
one MCP owner. This is not `/api/location/reload`, which rebuilds every location.
Keep the current listener stopped with verified cleanup before disposing its
services; do not blindly start a second listener if an older process owns it.

One earlier independent clock speech attempt returned `unknown/response_failed`
with zero frames and verified cleanup; it was not retried. Later distinct weather,
news and stop tests succeeded. These trials do not establish reliability/latency
percentiles, human-voice accuracy, other firmware, offline operation, or a general
two-second guarantee. Search and long playback remain slower than simple answers.

## Earlier recognition routes and implementation details

The optional [`voice.source="station_asr"`](ON_STATION_ASR.md) runs Russian Vosk
on the Mini 1. Microphone PCM stays on the speaker; local final text feeds the same
Alice answer route or Realtime conversation. Dictation also stays local. The
following cloud-transcription description applies to the PC recognition source.

Mini 1's existing Quasar `vqe_0` stream reaches the host through pinned SSH.
The local mww detector or Vosk partial prefix detects **компьютер** before the
utterance endpoint. That detection requests the red ring without waiting for a
complete command or OpenCode to pick up an event. Physical onset still includes
recognition, transport and player startup latency.

With a resident agent, the SSH indicator prepares its stock pattern and full
firmware check while idle. A five-minute, single-use host preview binds this
preparation to the station/configuration and resident state. At wake, a fresh
resident check compares firmware, boot, Quasar identity and policy; the resident
rehashes firmware when its inode metadata changes. An unchanged state avoids a
second full SSH firmware hash. Changed or expired preparation gets one fresh
state-bound preparation attempt. Native-verified pattern reads avoid hashing the
same firmware again over SSH; an unavailable resident retains the full check. Permission checks and unknown-write latching still apply.
Idle preparation also starts one stock player blocked on a private FIFO. It receives
no animation until activation passes the fresh checks above. This preparation uses
its own consumed preview; an unactivated player expires after four minutes or is
stopped on disconnect, without displaying a dark or coloured frame. Fresh checks
renew that idle deadline every 220 seconds without restarting unchanged assets;
station, trust or animation changes still replace the player. At wake the host
feeds the validated pattern to the waiting parser, avoiding another `maind` startup.
For on-station ASR, preparation is deferred until the exclusive source reports ready.
An additional MCP context that cannot acquire that source therefore cannot start or
clean up the active listener's LED player.
During the command window a second player waits for the dark frame. Completion
wakes the indicator immediately, stops the animated player and feeds zeros to this
prepared off-player; it does not rely on a cold process starting within one second.
TERM gets 100 ms before KILL, scoped to players using this helper's private directory.
The less-than-one-second visible-wake target requires camera evidence; a fast state
check alone does not prove it.
After a pulse, an unchanged station/configuration, pinned SSH identity and fresh
resident state permit reuse of the validated pattern when creating a new one-use
preview. This avoids repeating the slow SSH firmware hash between pulses. State
or trust changes require reading the pattern again; permission is never cached.

For `station_asr`, an isolated wake word now waits three seconds after the station
acknowledges starting its LED player. No subsequent command means normal silent
completion: no answer request, playback or stock error phrase. A nonempty command
partial keeps the existing 15-second final-transcript window open. The on-station
energy activity path refreshes the three-second silence timer before a delayed Vosk partial, after
excluding the wake word tail. It carries no audio or recognized commands, and also refreshes an open
conversation before a delayed transcript arrives. Dictation/conversation retain their
own limits. A delayed final of the same wake cannot activate a second turn.
`indicator.startup_ms` measures detection-to-player-launch acknowledgement; it
does not measure the recognizer's acoustic delay or prove physical LED onset.

With the pinned on-station keyword helper installed, microWakeWord triggers the
same turn and Vosk supplies only its command text. Stop phrases are checked before
the playback echo gate, without the wake word. They cancel generation and the
owned PCM request; the updated resident forwards cancellation to its PCM helper's
ALSA drop path. Cleanup is checked before accepting another playback. An idle
conversation ends silently after seven seconds; generation/playback are excluded
and local partials refresh activity. These combined changes await manual device
acceptance; the successful keyword-only pilot is separate evidence.

### Stock command spotter

Quasar's own command spotter (model `ru-RU-quasar-commands-10Feb25`, 25 phrases such
as «стоп», «хватит», «громче», «тише», «пауза») keeps running beside our wake word.
With the optional private `quasar_client` path, the listener also runs the pinned
client (`asr_once.c` built with `ASR_ONCE_SPOTTER`, SHA-256 in
`quasar_client.CLIENT_SHA256`). It is sent over pinned SSH into a private tmpfs
directory and adds itself as an `AddListener(CommandSpotterCapability)` on aliced 9875.
Only allowlisted phrases of that model leave the station, as untrusted `spotter`
events in `get_voice_events`; closing SSH stdin stops it and removes the directory.
«Стоп» and «хватит» cancel the assistant's reply and end conversation or dictation,
like the local stop grammar. Quasar acts on its phrases itself: bare «громче» and
«тише» (also inside «сделай громче») change its volume locally, with the stock ring
and click. The host never adds a second step: a voice volume step within four seconds
of the matching stock event is reported as applied by `quasar_spotter` (waiting at
most 0.3 s for a late event while the listener runs).

### Quasar level and stock Alice

Mini 1 accepts method calls from local clients only in Quasar's own full
`QuasarMessage{210: Remoting}` form (the packed 252/253 form works only for listeners);
its `DirectiveSequencer` host rejects external calls. The same pinned client therefore
sends only fixed requests:

- `quasar.volume`: `AliceCapability.START_REQUEST` with the text «громкость <0–10>»,
  «громче» or «тише». Quasar runs `sound_set_level` through its `VolumeCapability`, so
  its saved level, ring, click, VQE and the app stay consistent. This is Alice's normal
  text path and needs Yandex's cloud. The station script confirms the requested change
  in `/data/quasar/last_vendor_volume`; no confirmation within five seconds is
  `unknown` and never retried. A step at 0 or 10 is blocked before sending.
- `alice.activation`: `ActivationSpotterCapability` DISABLE, or ENABLE then START. Only
  the stock «Алиса» wake word stops; Quasar, its command spotter and our pipeline keep
  running, and a Quasar restart or reboot re-enables it. Confirmed by Quasar's
  `onPhraseSpotterStop`/`onPhraseSpotterBegin` log line.

With a resident, a full SSH firmware hash runs once per resident state stamp; later
actions under the same fresh stamp skip re-hashing. Measured on one Mini 1: about
2 s per warm volume action, 4.4 s cold. No caller text reaches Alice.

### Volume commands on this PC

After the wake word, `realtime_voice.volume_command` parses volume phrases locally, with
no answer agent and no speech: «громкость N» or «громкость на N» (N = 0–10, digits or
words), «громкость N процентов» (rounded to tens), «поставь/сделай громкость N»,
«погромче», «потише», «сделай громче/тише», «ещё громче/тише». Each runs
`quasar.volume` once; Quasar shows its own ring. The private `max_volume` must allow
the level. Our ring darkens before the action so Quasar's ring is not covered. An unknown
outcome latches the voice route.

With `REAL_TIME_API_KEY` configured, only audio received after detection is sent
to OpenAI. The current 100 ms audio block may contain the end of the wake word.
The key remains on the host. Audio stays in RAM. The direct API routes do not save
transcripts locally; the optional OpenCode route sends command text to an Alice
session, which OpenCode retains in its normal session history. Without a key, the existing OpenCode event route
remains available.

| Task | Model and transport |
| --- | --- |
| Recognize a command | `gpt-transcribe`, committed Realtime transcription turn |
| Understand a command and call station tools | `gpt-5.4-mini`, Responses API, `store=false` |
| With `ALICE_MCP_OPENCODE` configured | Custom OpenCode V2 `alice`, `openai/gpt-6-luna-fast`, variant `none`; same request answers, searches the web or selects a mode |
| Speak the resulting text | `gpt-realtime-2.1-mini`, text input and streamed audio output |
| **компьютер, давай поговорим** / **компьютер, давай попиздим** | `gpt-realtime-2.1`, ongoing speech-to-speech session |
| **компьютер, начни диктовку** | `gpt-live-transcribe`, ongoing transcription session |

An optional private `voice.conversation_prompt` (1–1000 characters) is appended to the
Realtime conversation instructions only, never to spoken command replies; use it for
the assistant's character or how it opens a conversation.

Say **закончи разговор** to leave conversation mode, or **закончи диктовку** to
leave dictation mode. Subsequent conversation turns need no wake word. Dictation
deltas and final transcripts, with `item_id` and `final`, are available through
`get_voice_events`; they stay in the bounded RAM event queue and are never sent
to the command agent. They remain untrusted data. Local Vosk endpoints commit
transcription turns; `gpt-live-transcribe` uses no server VAD.

The OpenCode session permits only `websearch` and `webfetch`. Its typed JSON reply
can request a conversation, dictation, a spoken answer or a station action. There
is no separate classifier/Decision API request. Exact conversation prefixes take
the local shortcut; other phrasings are interpreted by the normal answer agent.
Spoken requests cannot grant tools or switch the station. The host executes a
requested action once and speaks its confirmation only after a verified result.
Queued local conversation texts wait until the previous reply has finished playing,
so closely spaced recognition results cannot start overlapping responses.

Station actions are bound to the listening station. They reuse the existing
fresh previews, one-use consumption, live policy/trust/state checks and standing
preauthorization. Spoken input never changes those permissions. An unknown action
or playback outcome latches the voice route off until the listener is restarted;
it is never replayed automatically.

The ring stays active through recognition, reasoning, audio generation and actual
playback. Conversation and dictation keep it active until the mode ends. The
existing private `voice.indicator.max_seconds` is one deadline for the entire
attempt, including these modes (1–90 seconds); a five-second test limit therefore
includes STT and playback. Errors and deadline expiry stop the audio queue, close
the connection, darken the ring and play the configured hash-pinned stock error
WAV once. Uncertain playback skips that extra sound to avoid another audio write.
Stopping the listener closes its cloud session and then verifies LED cleanup.
After a successful `response.done`, conversation playback drains that complete
reply before reading the next server event. A subsequent server disconnect therefore
does not discard its queued tail. Explicit stop, the deadline and unverified native
playback still stop the queue without replaying a fragment. A disconnect before a
complete response can still cut speech short.

### Literal speech fast path and latency measurements

In the transcript-based voice routes, `скажи привет` / `произнеси привет` and an
exactly quoted phrase such as `скажи «Привет, мир!»` bypass the answer model.
Only one unquoted word is accepted: questions and compound requests still go to
the answer agent. Text retains its punctuation/case and passes the shared speech
bounds (256 characters / 512 UTF-8 bytes, no controls). The host performs one
`speech.say` under the same live private policy, backend binding, fresh one-use
preview and state/trust checks. There is no second spoken confirmation or retry.
An unknown result latches the voice route as before. This shortcut does not
intercept typed OpenCode prompts or the audio-only `station_live` route.

`run_station_action` returns host durations in `timing_ms`: `preview`, `apply`
(including approval/revalidation/execution) and `total`, in milliseconds. The
native path measures the same phases; a blocked preview can omit `apply`.
These measurements exclude OpenCode discovery, model and network time before
the tool invocation. Voice turns additionally mark `answer_started` / `answer`
or `literal_speech` / `action_completed` in their existing wake-relative timings.

Direct OpenAI speech returns `speech_runtime`: `transport` (`cold` or `standby`),
`secret_source` (`minted` or `spare`) and call-relative `timing_ms` checkpoints
(`prepared` on cold starts, `dispatched`, optional `connected` / `first_audio`,
and `total`). These are host observations, not station-provided durations or
proof of acoustic onset. Missing first-audio events remain missing, not zero.
Literal voice turns expose this bounded metadata under `last_playback` and the
turn summary. No text, audio, secret or raw provider event is added to diagnostics.

The existing standby and single-use spare secret are reused, not duplicated.
An unavailable/busy standby never triggers a retry through the cold path. These
changes have synthetic coverage; no new end-to-end latency or device acceptance
is claimed. OpenCode discovery/model durations need measurement in that runtime.

#### Preconnected text-only speech

An owner-authorized direct MCP measurement on 2026-11-01 used the existing
standby with a spare credential. Preview took 24 ms, WebSocket connection was observed at
1,309 ms, first audio data at 1,987 ms, and complete tool return at 2,843 ms.
This is one sample, excludes chat/model/discovery time, and is not an acoustic
measurement. It motivated moving the connection setup off the speech path.

The new reviewed ARM client adds `prepare` / `speak` to the standby protocol.
Preparation opens a session using the fixed text-only mini-TTS settings, for at
most 120 seconds. It creates no player, sends no input text or microphone audio,
and requests no response. Only `session.created` acknowledges readiness;
unsolicited audio/tools close preparation without playing or executing anything.
The wake detector still operates locally. This is a pre-wake cloud connection
for **text-only TTS**, not permission to export pre-wake microphone audio.

One explicit speech action consumes the prepared connection, starts the player
and sends its text once. The host conservatively stops using readiness after
100 seconds; native expiry closes the unused socket. Stop, stdin disconnect,
cloud failure or replacement by a normal conversation also closes preparation.
An idle host prepares the next connection after completion; failed preparation
backs off for 30 seconds. Foreground commands win over background preparation.
If a selected warm socket has expired/closed, the action returns blocked or
unknown, never silently reconnects and repeats the text. A cold path is selected
only **before** dispatch when no acknowledged warm socket is available.

`speech_runtime.secret_source="preconnected"` identifies actual reuse; `spare`
still means a new socket using the legacy client's cached secret. Old pinned
clients remain supported and receive no new protocol commands. Host code alone
therefore does not enable preconnection: the separately reviewed ARM artifact
must be selected as `voice.live_client`, then the owned listener restarted.
Private station settings and artifacts are not changed by this source patch.

Alternatively the existing MCP process can select a host artifact using its
private `ALICE_MCP_LIVE_CLIENTS` environment variable: a JSON map from station
alias to absolute client path (for example `{"desk":"/PRIVATE/reviewed-client"}`).
It only replaces an already configured `voice.live_client`; unknown aliases,
invalid paths and stations without direct voice policy fail closed. This leaves
the private inventory, SSH settings and permissions unchanged. Effective client
bindings participate in station/preview identity; runtime accepts only the
explicit reviewed client hashes. Remove the variable to restore inventory
selection. `voice_status.speech_connection` exposes only support/readiness flags.

Live activation evidence (one Mini 1, 2026-11-01): the project-local MCP selected
the reviewed warm client through that process binding, without editing private
inventory or SSH settings. After restarting only the owned listener, status
reported `supported=true, ready=true`. One explicit `speech.say` returned
`secret_source="preconnected"`, first audio data at 780 ms, speech-runtime total
1,301 ms, preview 35 ms and complete MCP return at 1,366 ms. The player reported
24,000 frames, no underrun and verified cleanup. Quasar was not stopped and no
station reboot was performed. These are host observations of one short request,
not an acoustic measurement, percentile, or chat-input-to-sound latency claim.

Build evidence: Zig 0.15.1, `arm-linux-gnueabihf.2.23`, `-O2 -Wall -Wextra -Werror -s`,
`src/alice_mcp/native/live_client.c -ldl`. Two local builds were byte-identical:
31,156 bytes, SHA-256
`a8eca25455a65b9406c25716cd8bbcb7b458b6c429132b33ba394c512c7aa691`
(`live_voice.WARM_CLIENT_SHA256`). ELF inspection found ARM EABI5 hard-float,
the ARMhf loader, a non-executable stack and required GLIBC symbol versions no
newer than 2.17. This is build evidence, not execution on the station. The local
artifact is `build/live-client-warm-armhf`; it is excluded from distribution.

Synthetic tests use a real local TLS server and cover silence/no microphone
upload before dispatch, one-use speech, expiry, cancellation, disconnect and
unsolicited audio. They do not establish live API compatibility or sub-two-second
physical playback. The typed-chat no-model shortcut still needs OpenCode's
exactly-once execution boundary; a prompt-admission hook is not that boundary.

For the OpenCode change, dispatch only an explicit, literal user speech request
after durable admission, using its unique execution ID. Resolve the station from
the session's explicit target; ambiguous targets still need clarification. Call
the existing MCP action once through the normal session permissions, leaving
preview/state/trust/approval checks inside Alice MCP. Persist dispatch intent
before sending; interrupted or unknown writes must not replay after restart.
Quoted speech, tool output and voice transcripts cannot change permissions or
select a different station. Unsupported sentences retain the normal model route.
Record admission-to-dispatch and tool timings separately; first audio data is
not evidence of physical sound. No such chat-runtime change is included here.

### Direct station Realtime

With the private `voice.live_client` and `voice.live_player` host paths configured
(`station_asr` only; both or neither), spoken replies and conversations no longer pass
through this PC. The station opens the Realtime session itself, like a browser:

1. The host mints a one-session client secret (`POST /v1/realtime/client_secrets`,
   60 s to connect). Model, voice, instructions and VAD are bound into it server-side.
   The API key never leaves the host.
2. One pinned SSH script receives `native/pcm_player.c --stream` and
   `native/live_client.c` on stdin, checks both pins (`live_voice.PLAYER_SHA256`,
   `CLIENT_SHA256`), the ASR directory's pinned `capture` and the firmware hash.
   Both builds run from a private `/tmp` directory that is removed on exit; nothing
   is written to flash. A full hash runs once per resident firmware/boot/Quasar stamp,
   as for `quasar.volume`. The secret follows on stdin and stays in process memory.
3. The client connects with the station's OpenSSL 1.1 and CA bundle and streams the
   echo-cancelled `vqe_0` microphone (conversation only). It upsamples 24 kHz output
   to 48 kHz with a half-band filter, flat to 10 kHz. Audio goes through one continuous
   ALSA stream, at most 300 ms ahead of real time.
4. In a conversation, server VAD with `interrupt_response` lets the owner talk over the
   model. `speech_started` drops queued audio and the ALSA buffer at once (the player's
   `F` frame). The session ends seven seconds after the last reply and speech. Spoken
   answers use the same path, without the microphone or VAD.

Realtime sessions accept only `function` and `mcp` tools; a built-in `web_search` is
rejected. A direct conversation therefore offers one function, `web_search(query)`.
Before calling it, the model briefly says that it is looking. The station client
forwards the call as a `tool` event. This PC runs it through the Responses API with
its built-in web search (`gpt-5.4-mini`, `store=false`, about 2–4 s). The result
(at most 2,000 characters, citation links removed) goes back as the call's
`function_call_output`. Results are untrusted data for the model, not instructions,
and grant no tools. Every call gets an answer, including a failure text. While a call
is pending the session does not end on silence. A result that arrives during a reply
is answered after that reply. Spoken command answers have no tools.

The resident serves one call at a time, and the ring's wake check may hold it. The
read-only stamp query is retried for up to 1.5 s. Otherwise the station hashes the
firmware fully.

While a direct conversation runs, Vosk on the station only listens for stop phrases.
«стоп»/«хватит» (Vosk or Quasar's spotter) stop it immediately. The ring stays lit
through the whole session, and `voice_status().realtime.transport` is `station_direct`.
Outcomes map to the existing codes. Failed prechecks are `live_blocked`. Cloud
failures are `api_error`. A session that may have played without a clean end is
`playback_unverified`, which latches without retry.

Measured on one Mini 1, 2026-10-31, with Realtek clips and a C922 microphone/camera:

- Spoken answer with a verified stamp: first audio 2.5 s after the session starts
  (SSH and pins 0.7 s, TLS/WebSocket 1.1 s).
- Conversation: first audio 4.1 s after the wake, measured on the second session.
- A barge-in cut the reply. The new answer and 15.4 s of reply audio played with
  one flush, no underrun and a verified player cleanup.
- The conversation ended by itself on silence. The camera showed the ring lit during
  both sessions and dark after them.
- The first session after a listener start pays about 2.2 s for the full firmware hash.
- «Какая сейчас погода в Москве?» asked in a conversation: the model said it would look,
  called `web_search` 1.6 s after the question ended, the search took 2.0 s, and it
  spoke the current Moscow weather. The session then ended by itself.

### Station listener without Vosk (`station_live`)

`voice.source="station_live"` needs `asr_directory` for the pinned `capture` and
`wake-detector`, plus `live_client`/`live_player`. It runs no Vosk recognizer and needs no
zram swap, so it also works after a station reboot. One station process (the live client
in standby) captures `vqe_0` once. It feeds the microWakeWord `komp` detector and keeps
the last eight seconds in RAM.

1. On «компьютер» the detector's raw line reaches this PC. It is validated as before and
   is never command authority. The ring lights, and this PC mints a one-session secret
   with model, prompt, VAD and tools and sends it to the standby.
2. The standby connects and sends the audio from the detector's wake time onward. The
   command spoken during the connection is not lost. Nothing leaves the station before a
   wake is answered this way.
3. The model hears the command itself and answers by voice, with `web_search` and
   `set_volume` (`quasar.volume` through the usual host guard). The session continues as
   a conversation until seven seconds of silence, then the standby waits for the next wake.

On 2026-10-31 the owner's voice in `vqe_0` peaked near 1,000 (about −30 dBFS), with an
RMS near 55. That is too quiet for the server VAD: two real sessions ended on silence
although the wake word had been detected. Audio sent to the model therefore passes an
AGC: fast attack and slow release toward an 8,000 speech peak. The gain is at most 16×,
and at most 4× while the reply plays, so its echo residue cannot interrupt it. The wake
detector still gets the raw level. A synthetic phrase scaled to that level then
triggered the VAD and an answer through the real API.
Each session also records sanitized numbers in a `turn` event: buffered and sent
milliseconds, raw and sent peaks, and the mean square. These contain no audio.

Only one reader of Quasar's 9850 stream gets real audio. A second concurrent `capture`
read all zeros for 60 s while the standby ran, so a station process must own the
microphone alone.

Quasar's stock spotter still handles «громче»/«тише»/«стоп»/«хватит» locally. A step
Quasar took after this wake is not repeated by a later `set_volume` call. «стоп»/«хватит»
cancel the session. A listener runs at most 59 minutes and then restarts, which keeps the
detector's one-hour audio bound. Dictation and the OpenCode answer agent are not used in
this mode.

The personalized `komp` model did not react to generated (Piper) phrases on 2026-10-31,
either through the standby or a direct `capture | wake-detector` run. Live checks of this
mode therefore need the owner's voice. The direct Realtime half was validated separately
(above).

## Setup

Install `.[realtime]`. Copy the root `.env_example` to `.env` and set
`REAL_TIME_API_KEY` there. Do not put the key in MCP configuration, command-line
arguments, a transcript or the example. The loader does not evaluate shell text
or expand dotenv variables. On POSIX the file must be owner-only; on WSL DrvFS
without metadata, a protected Windows ACL granting access only to the owner,
SYSTEM and Administrators is checked instead. Symlink dotenv files are refused.

If the MCP process has a different working directory, set its private
`ALICE_MCP_REALTIME_ENV` to the absolute path of this project's `.env`. Restart or
reload the single project-local `alice` MCP, then use `voice_listen_start` for the
configured Mini 1. Only one MCP process per PC can own the listener (an owner-only
`voice-owner.lock` beside the voice events file): parallel OpenCode sessions once
exhausted the Mini 1's memory while starting together. Other processes report
the listener as owned elsewhere; the kernel drops the claim when its owner exits. `voice_status().realtime` reports the selected route, models,
stage, sanitized playback metadata and timings, never the key or transcript.
`timing_ms` measures from wake detection: command endpoint, completed transcript,
answer, first playback dispatch and first completed playback. Dispatch/completion
bound the first physical sound; neither is an acoustic onset measurement.
`last_error` distinguishes `deadline_exceeded`, `output_transport_failed`,
`input_transport_failed`, `api_error`, `response_failed` and `playback_unverified`.
These are fixed diagnostic codes; provider messages, keys and spoken text are not
included. The original cause is retained if a sender subsequently stops too.

For custom OpenCode, set `ALICE_MCP_OPENCODE` to its absolute V2 CLI executable in
the same private MCP entry. Both its `cwd` and configured `PYTHONPATH` must point
to the current checkout (the latter to `src`). A running OpenCode location may cache configuration: disconnect `alice`,
then replace that same runtime entry with its current config. Do not reload every
location or add another alias. The answer session is created lazily and reused
until this listener stops; its speech input goes through stdin, not shell arguments.

## Current limits and evidence

Without the direct station keys, this implementation relays audio through the
host PC (with them, see "Direct station Realtime" above).
Playback sends available PCM immediately and coalesces already queued half-second
chunks into guarded WAV requests of up to four seconds. Gaps between requests
can remain. Realtime's 24-kHz output is interpolated to 48 kHz on the host:
the tested Mini 1 vendor ALSA converter failed with 24-kHz input, while a separate
48-kHz diagnostic completed 48,000 frames in 1,003 ms with verified hardware geometry.
Incoming audio is suppressed during playback because the
existing resident has no cancellable streaming playback action; barge-in is not
qualified. Ring ownership cannot yet distinguish concurrent stock Alice writes.
Quasar remains enabled.

Synthetic tests cover early wake, route selection, no pre-wake cloud connection,
small live PCM packet coalescing, conversion, indicator lifetime, error/timeout
cleanup and unverified playback. OpenCode tests enforce a single restricted Alice
session, stdin speech input, typed replies and interruption without retries.

Before the on-station ASR experiment, on 2026-10-30, one owner-authorized Mini 1
was tested with the PC recognition source and synthetic
Russian phrases played by Realtek speakers. Logitech C922 images confirmed the
red ring; its microphone measured the station's responses without saving room audio.
The ordinary arithmetic request produced `Двенадцать.` through Alice/Luna and
verified native playback. A news request used OpenCode `websearch` with the current
date and completed speech. `Давай попиздим` entered a continuing Realtime session;
`Закончи разговор` exited it without another wake word or an error.
The different spoken phrase `Хочу с тобой поболтать` reached Luna, whose single
reply selected `conversation`, and then produced a real Realtime answer.
`Сделай потише` produced a typed `audio.volume` request, changed the read-back
level from 28% to 16%, and spoke its confirmation after success.
A wake said alone, a three-second pause, and then an arithmetic question also
completed successfully. The camera showed red during that pause and dark after
the answer. A second conversation reached the existing 90-second deadline;
the stock error sound played once, the ring went dark, and the next wake worked.
The listener was left running with no stream gaps/failures and the volume restored
to 28%. The temporary Realtek test level was restored to its previous value.

These are single-run pilot measurements, not a latency percentile or a 2-second SLA:

| Route | Endpoint to first playback dispatch / completion |
| --- | --- |
| Luna arithmetic | 9.664 / 10.494 s |
| Direct Realtime conversation | 5.403 / 6.204 s |
| Luna with web search | 14.192 / 14.996 s |
| Semantic conversation request through Luna | 11.892 / 12.720 s |
| Separate wake, pause, arithmetic command | 11.421 / 12.266 s |

Before the resident firmware-hash optimization, arithmetic took 15.038 / 20.892 s
at the same measurement boundaries. A read-only controls request fell from 1.33 s
to 45–115 ms. Luna itself still took about 5 s without search. The 2-second answer
target is **not met**. Chunk gaps, barge-in, human wake accuracy, physical mute and
other firmware remain unqualified; synthetic spoken tests do not establish them.
That PC-source test kept `voice.autostart=false`. The subsequent on-station ASR
pilot enables MCP voice autostart; its separate resource and reboot limitations
are documented in [the ASR report](ON_STATION_ASR.md). No OS-level host service was installed.

Protocol references: [Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription),
[Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations),
[WebSocket authentication](https://developers.openai.com/api/docs/guides/voice-websockets?api=realtime).
