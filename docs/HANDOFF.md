# Voice pilot and development handoff

Updated 2026-10-31. Start with [AGENTS.md](../AGENTS.md) and
[CONTRIBUTING.md](../CONTRIBUTING.md). This is a tested Mini 1 pilot, not general
Station/firmware qualification or a fully offline conversational assistant.

## Implemented

- Russian wake/command recognition runs on the Mini 1: `station_asr` uses the
  pinned ARM Vosk client and an independent personalized microWakeWord detector.
  Station microphone PCM stays on the device; only text and bounded activity
  metadata reach the host. MCP, OpenCode/Alice and response synthesis still need
  the host and their configured services.
- The wake word is `компьютер`. A separate keyword stream requests red feedback
  before a complete Vosk transcript. The stock player and its off-frame player
  are prepared on private FIFOs to avoid cold `maind` startup on that path.
- Empty command collection ends silently. The logical timer is three seconds
  after LED launch acknowledgement; visible dark arrives slightly later.
  Energy activity and recognized partials are tracked separately, so a noise
  burst cannot permanently latch the command window.
- Conversation aliases and semantic routing use the existing answer request;
  there is no additional Decision/classifier API request. Conversation uses
  Realtime and closes after seven seconds without input while waiting for the
  next turn. Response generation/playback do not count as waiting silence.
- Bare stop phrases can interrupt the assistant response. While busy, the same
  local model switches to a small stop grammar; native PCM cancellation drops
  ALSA output and verifies cleanup. Completed response audio is drained rather
  than discarded on a server disconnect.
- Prepared LED resources renew their idle watchdog after fresh checks, without
  recreating unchanged station/trust/animation assets. Preparation starts only
  after exclusive ASR readiness, so a failed second MCP listener cannot touch
  the active owner's LEDs. Unknown writes latch off instead of being retried.
- Native firmware hashing is cached with file identity/metadata checks; volume
  changes verify exact representable ALSA raw readback. Guarded upgrades and
  public/private artifact separation remain in place.
- The optional [agent session bridge](AGENT_SESSIONS.md) and its OpenCode plugin
  support project-local Codex/Claude lifecycle metadata and queued operator
  messages. They do not discover arbitrary client histories, wake idle agents,
  answer approvals or grant permissions. Their setup is independent of voice.

## Live evidence

These are generated Realtek phrases on one Mini 1, observed with a C922 camera
and microphone. They are not household false-positive rates or latency percentiles.

| Check | Observed result |
| --- | --- |
| Word-end to visible red, three trials | 856 / 783 / 591 ms |
| Visible pulse followed by sustained dark | 3.27 / 3.33 / 3.47 s; no spoken error |
| Same prepared process after 245 s idle | 538 ms to visible red; 3.40 s visible |
| Camera time resolution | 66.7 ms between frames |
| Realtime conversation | Native playback completed; normal `silence_timeout` |
| Stop during audible response | Verified native cancellation 113 ms after recognition |

The stop result is recognition-to-cancellation, not word-end-to-audible-silence.
Final observed state was listening with Realtime inactive, indicator prepared,
one recognizer and one waiting player, no error, and Quasar still enabled.
Realtek was restored to 4%. Room audio was not persisted.

Validation of the final implementation: 1598 Python tests passed, 13 skipped;
ruff and mypy passed. Publication checks additionally cover the MCP contract,
catalog, OpenCode integrations, native tests/build, source/distribution privacy
and gitleaks. Consult the publication PR/CI for their final results.

## Remaining work, in priority order

1. **Content-answer latency:** the two-second audible-answer target is unmet.
   Separate command-end, local final transcript, OpenCode answer, Realtime audio
   and acoustic output timings before optimizing. Do not substitute PC/cloud
   recognition or add a separate mode-classifier call to improve the numbers.
2. **Long conversations:** the current 90-second session cap can interrupt a
   long reply. Design safe renewal while preserving bounded device holds,
   fresh authorization and the seven-second waiting-silence behavior.
3. **Cold boot:** 160 MiB zram and resident-agent preparation are manual for the
   tested boot. Reboot persistence and the one-hour recognition rollover are
   unqualified. Keep Quasar, SSH trust and private station settings intact when
   preparing a reviewed startup change; do not rerun uncertain initialization.
4. **Human testing:** measure household voices/background noise, stop-word
   acoustic latency, varied long replies and repeated wake/command turns. The
   visible quiet timeout is about 3.3–3.5 s, not an exact three-second guarantee.
5. **Other hardware:** Mini 3 and other firmware are not qualified by this pilot.

## Code and supporting notes

| Area | Entry points |
| --- | --- |
| Local recognition, manifests, owned session and event validation | `src/alice_mcp/station_asr.py`, `src/alice_mcp/native/station_asr.c` |
| Wake/text/activity routing and source ownership | `src/alice_mcp/voice_service.py` |
| LED preparation, renewal, activation and cleanup | `src/alice_mcp/voice_indicator.py` |
| Conversation, quiet expiry, draining and cancellation | `src/alice_mcp/realtime_voice.py` |
| Existing OpenCode Alice answer route | `src/alice_mcp/opencode_voice.py` |
| Native transport and PCM cancellation | `src/alice_mcp/native_transport.py`, `src/alice_mcp/native/agent/pcm.go` |
| Resource probe | `src/alice_mcp/asr_resources.py` |
| Session bridge | `scripts/agent_sessions.py`, `integrations/opencode/agent-sessions/` |

See [on-station ASR](ON_STATION_ASR.md) for input/build pins, compatibility work
and the dated experiments; [voice architecture](VOICE_ARCHITECTURE.md) for
routing/setup; [native agent](NATIVE_AGENT.md) for deployment boundaries.

The original checkout also retains ignored operator material:

- `local/station-wake-fast/STATE.md` and `JOURNAL-2026-10-31.md`: latest device
  handoff, installed recognizer pin, rollback information and test evidence.
- `local/station-wake-fast/idle-v8-receipt.json` and the corresponding visual
  receipts/cropped video: idle-renewal and physical feedback measurements.
- `local/station-asr/build.py`, `manual_resume.py`, `reload_mcp.py`: reviewed
  operator build/recovery/reload workflow. Recovery is not an idempotent retry
  command; inspect the current boot/state first.

These local files, model/library downloads, generated executables, private
configuration and camera artifacts are intentionally absent from Git and release
archives. A fresh clone contains source and manifests, not the deployed assets.
Do not copy them into a public commit to make a new clone appear configured.

Use the existing project `alice` connection. Owner authorization for the live
project check included speaker/camera audio and pinned, key-only SSH; it does not
waive current policy/trust/state checks or authorize unrelated operations. Keep
inventories and credentials private, preserve standing station preauthorization,
keep Quasar enabled, and inspect any `unknown`/`do_not_retry` outcome before acting.
