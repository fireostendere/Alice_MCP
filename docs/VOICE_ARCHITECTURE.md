# Local wake and OpenAI voice routes

Mini 1's existing Quasar `vqe_0` stream reaches the host through pinned SSH.
The local mww detector or Vosk partial prefix detects **компьютер** before the
utterance endpoint. That detection starts the red ring immediately; the indicator
does not wait for a complete command or OpenCode to pick up an event.

With `REAL_TIME_API_KEY` configured, only audio received after detection is sent
to OpenAI. The current 100 ms audio block may contain the end of the wake word.
The key remains on the host. Audio stays in RAM, and command/conversation
transcripts are not saved. Without a key, the existing OpenCode event route
remains available.

| Task | Model and transport |
| --- | --- |
| Recognize a command | `gpt-transcribe`, committed Realtime transcription turn |
| Understand a command and call station tools | `gpt-5.4-mini`, Responses API, `store=false` |
| Speak the resulting text | `gpt-realtime-2.1-mini`, text input and streamed audio output |
| **компьютер, давай поговорим** | `gpt-realtime-2.1`, ongoing speech-to-speech session |
| **компьютер, начни диктовку** | `gpt-live-transcribe`, ongoing transcription session |

Say **закончи разговор** to leave conversation mode, or **закончи диктовку** to
leave dictation mode. Subsequent conversation turns need no wake word. Dictation
deltas and final transcripts, with `item_id` and `final`, are available through
`get_voice_events`; they stay in the bounded RAM event queue and are never sent
to the command agent. They remain untrusted data. Local Vosk endpoints commit
transcription turns; `gpt-live-transcribe` uses no server VAD.

Responses tool calls are bound to the listening station. They reuse the existing
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
configured Mini 1. `voice_status().realtime` reports the selected route, models,
stage and sanitized errors, never the key or transcript.

## Current limits and evidence

This implementation requires the host PC. It does not install an ARM cloud
client or compose the separate resident-wake PR into an on-station runtime.
Playback reuses guarded half-second WAV requests to the resident; it can have
gaps between chunks. Incoming audio is suppressed during playback because the
existing resident has no cancellable streaming playback action; barge-in is not
qualified. Ring ownership cannot yet distinguish concurrent stock Alice writes.
Quasar remains enabled.

Synthetic tests cover early wake, route selection, no pre-wake cloud connection,
PCM conversion, indicator lifetime, error/timeout cleanup and unverified playback.
Real API checks accepted both transcription session configurations and returned
audio from the mini TTS model; those checks did not play audio on a station.
Physical LED timing, gap-free audio, microphone accuracy and firmware compatibility
still require the owner/device acceptance run.

Protocol references: [Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription),
[Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations),
[WebSocket authentication](https://developers.openai.com/api/docs/guides/voice-websockets?api=realtime).
