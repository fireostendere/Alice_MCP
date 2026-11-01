# Local wake and OpenAI voice routes

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
