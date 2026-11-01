# Voice pilot and development handoff

Updated 2026-11-01. Start with [AGENTS.md](../AGENTS.md) and
[CONTRIBUTING.md](../CONTRIBUTING.md). This is a tested Mini 1 pilot, not general
Station/firmware qualification or a fully offline conversational assistant.

## Final publication checkpoint (2026-11-01)

- The implementation is merged in [PR #12](https://github.com/fireostendere/Alice_MCP/pull/12),
  source commit `82e98a9758f5d51b5736ee20d141f88a06e92cb5`, merge
  `1588f30afef4f41f6241d5305c49e590aee7c6f1`.
- The test-only TLS follow-up is merged in
  [PR #13](https://github.com/fireostendere/Alice_MCP/pull/13), source commit
  `6c2f8dbcc888277bf57f2707a34083364c5aed99`, merge
  `dbdf3b595a98e7ea88955413dc1a4907945dc3c7`. The synthetic cancellation peer now
  accepts explicit EOF/reset disconnects. A deterministic EOF-to-BrokenPipe case
  reproduces the old fixture failure; cancellation deadlines and the exact
  single-request/no-resend assertion remain unchanged. No production change was
  needed for that CI failure.
- Final local source gate: **1,832 Python tests passed, 13 skipped**, 15 Node tests,
  Ruff, mypy (including the session bridge), MCP contract, wheel/sdist, source/index/
  archive privacy and redacted gitleaks on staged files, history and payloads passed.
  The full run reported the fork deprecation and an unrelated synthetic socket
  teardown thread warning; neither was suppressed. Both PRs passed GitHub Python
  3.11/3.12 and native checks before merge. CI run results remain authoritative.
  The post-merge [main CI](https://github.com/fireostendere/Alice_MCP/actions/runs/41349578534)
  at `dbdf3b595a98e7ea88955413dc1a4907945dc3c7` also passed all three jobs.
- The feature is usable, not fully accepted against every original target:
  clock median **1.59 s versus 1.37 s** stock is still slower; weather median
  **1.54 s versus 1.62 s** stock is from only three generated-voice pairs.
  Native stop-only red colour and reliable verbatim news audition remain open.
  Do not claim universal stock-speed parity or that red stop is implemented.
- The last clock-fields experiment was rejected (no latency gain); it was not
  deployed. Runtime remains one generated answer per wake, Belgrade default,
  `coral`, 200-ms command VAD, explicit conversation transition with 350-ms VAD.
  See [the architecture/evidence](VOICE_ARCHITECTURE.md#one-question-one-generated-answer)
  for the processed-input policy, cache validation, native build pin and caveats.
- Private inventories, SSH settings, `.env`, project-local OpenCode bindings,
  reviewed binaries, diagnostic experiments and local goal records were deliberately
  excluded from Git. A clone is not a configured station deployment. Preserve the
  existing private configuration; never copy these files into a publication.
- `goal_update` was unavailable in the offered tools. CLI/schema and current-session
  metadata checks did not expose an authoritative alternative. The local blocked
  record is **not** a goal-service update; no session metadata was fabricated.
- Owner's final instruction: leave the checkpoint/documentation, commit and merge,
  then **stop engineering work**. Leave the working voice setup and Quasar enabled;
  do not restart/reconfigure devices, run more room trials, or automatically resume
  the earlier speed goal without a new explicit owner request. Source publication
  does not prove a running MCP process reloaded every Python change; if deployment
  is requested later, use only the scoped project refresh and verify one owner.

## Current smart-speaker work

The active Mini 1 route is now `station_oneshot`: one generated answer per wake,
microphone cutoff at question endpoint, then session close. Explicit
`start_conversation` is the only transition to free conversation. Belgrade is the
weather default. Open-Meteo weather/BBC headlines are validated and cached in RAM
for at most five minutes, not as prepared spoken answers. Time comes from a fresh
host-clock snapshot. Late tool outputs cannot enter a new session; repeated
conversation transitions are harmless; silent-wake handling preserves queued speech.

Latest optimization is active: `command_vad_silence_ms=200`, independently of
`vad_silence_ms=350` for conversation; both accept integer 200–1000, excluding bool.
Explicit conversation restores its configured timing and full tools. Compact
weather/news voice context omits URLs and redundant metadata without mutating
the source caches. A fresh snapshot also omits its redundant fetch tool for that
one question; missing snapshots retain the fallback.

The final one-shot voice is **coral**; chat-triggered literal speech stays marin.
Provider-only checks found correct Russian text but unreliable ASR recovery of
marin/cedar audio; coral's synthetic PCM and subsequent device output were recovered
correctly. No native transport change was made to obtain that result.

Latest input optimization disables the extra cloud filter only for one-shot
processed `vqe_0`; explicit conversation restores `far_field` and its own VAD.
The weather observation time is no longer in the short-answer prompt, avoiding a
competing clock value. The validated cache retains it. An intermediate six-minute
clock error is explicitly excluded from accepted answers.

Latest three paired weather trials: ours 1.56/1.41/1.54 s, stock 1.62/1.64/1.54 s;
medians **1.54/1.62 s**. A further final-source weather trial also gave 1.54 s.
Latest paired clock trials: ours 1.64/1.38/1.59 s, stock 1.37/1.63/1.22 s; medians
**1.59/1.37 s**, still slower. Translation gave ~1.39 s, followed by no answer to
an unwoken follow-up. A news trial gave ~1.56 s before interruption; its transcript
was not reliably verbatim. A prior news attempt was stopped before measurable
audible onset and is not a latency acceptance sample. Stock news earlier began
with a preamble at ~1.83 s, not a substantive-news comparison. News and an
explicitly enabled conversation were stopped with recognized «хватит» and verified
native cleanup. These few generated-voice trials are not human latency percentiles
or a universal speed guarantee. Stop colour also remains unresolved. See the
[current acceptance evidence](VOICE_ARCHITECTURE.md#one-question-one-generated-answer).
The reviewed one-shot client pin is
`c64164b24e1cff594426c81fc091cae68f6268e577bfc2893ff19871c75856d7`;
the active project-only binding selects that build. No inventory or SSH setting
was replaced. The following fast-feedback and earlier chat evidence is historical.

Earlier one-shot regression: **1,820 Python tests passed, 13 skipped**, one existing
fork deprecation warning; 15 Node integration tests, ruff, mypy and MCP contract
checks passed. The package/privacy checks are recorded separately from the acoustic
acceptance: passing tests do not establish stock-speed parity or fix stop colour.

Latest full publication regression, including the verification-mode fix:
**1,831 passed, 13 skipped**,
with the existing
fork deprecation warning; 15 Node tests, ruff, mypy and MCP contract passed. A run
under heavy host load had failures and timed out; a later complete rerun passed
without loosening tests. Do not describe the interrupted run as passing.

Publication review also binds prepared indicator players to their verification
mode. A native/SSH mode transition recreates the helper instead of renewing a hold
prepared for the other mode. The synthetic regression switches native→SSH, changes
firmware during idle preparation, and requires activation to block before any
coloured frame; same-mode native state refresh still reuses the prepared player.

The strict speed criterion remains unmet: the latest paired clock median is about
220 ms slower than stock; there are no human-voice latency percentiles. This is a
concrete remaining acceptance
gap, not proof that no further local optimization can help. The current tool
profile does not expose `goal_update`; discovery returned no match, so no new
lifecycle status update was performed in this continuation.

Rejected follow-up candidates: halving prompt bytes showed no latency gain;
100-ms VAD split a command with a 150-ms internal pause before it finished. The
native queue/poll/ALSA review did not identify a confirmed removable first-audio
hold. Do not lower buffers or claim a backend-independent lower bound from that.

The pilot uses `station_live` on Mini 1, a pinned direct Realtime client,
command/conversation-specific endpointing and a red indicator (required by the owner).
Ordinary Alice chat questions use the guarded `ask_station` tool: host-clock time,
searched news/weather/facts, one spoken answer and separate source links. The
plugin's station binding and final context hook preserve the speaker role despite
later config-agent transforms; the bound chat also carries durable instructions.
There is still one project-local `alice` connection. Do not reload every OpenCode
location to refresh it or leave a second MCP process holding the voice-owner lock.

Fast red feedback is now active: the SSH indicator prepares its player even when
the resident channel is unavailable. A pinned read-only client identity check
avoids rehashing unchanged firmware at wake; the first full hash and rehash on
metadata changes remain mandatory. Two common-clock camera/microphone trials
measured about 350 / 360 ms from the generated wake word's end to the red optical
rise, with 50-ms video resolution and uncalibrated sensor latency. Inspected
recognition-to-player acknowledgements were 49 / 46 ms, not the acoustic boundary.
Earlier fast-feedback regression: 1,799 Python tests passed, 13 skipped; 15 Node tests, lint/types,
contract/catalog, package and privacy checks passed. The existing fork deprecation
warning remains.

The latest recognized «хватит» camera trials confirm a stock purple/blue pulse.
A bounded red acknowledgement through a second player failed visually and was
removed from source and the running MCP. The shared cancellation path remains;
synthetic regressions cover both «стоп» and «хватит». Fast red wake onset is unchanged.
Stop colour is **unresolved**, not an accepted red response. A fixed read-only
`LedCapability` subscription produced no matching snapshot within five seconds;
this neither establishes support nor proves absence. Generic animation schemas
are not Mini 1 capability/access evidence. No candidate native animation write
was attempted; Quasar and the stock spotter remain enabled.

Post-rollback validation: **1,800 Python tests passed, 13 skipped**, with the existing
fork deprecation warning; 15 Node tests, lint/types, MCP contract and explicit-source
privacy checks passed. The unrestricted checkout-tree scanner rejects private/local
paths as expected; those paths were not changed or included in the package.

Measured acoustic gaps on generated phrases: approximately 1.90 s for arithmetic
and 1.65 s for a separate time question, versus a 2.50-s baseline. News produced an
initial reaction in about 1.45 s, not a substantive searched answer in that time.
Stock stop now also cancels chat-triggered speech: verified native cleanup and
silence in the bounded microphone capture. The ring-only colour transition is
**not visually qualified**; changing to the stock palette is not an acceptable
workaround. Do not claim a universal two-second response or visual acceptance.

See [current architecture and evidence](VOICE_ARCHITECTURE.md#current-smart-speaker-pilot-2026-11-01)
for exact boundaries, build pin, successful weather/news tests and the one
unreplayed TTS failure. Remaining priorities: real-human/repeated-turn latency,
ring-only visual qualification, cloud failure rate, long searched responses and
boot persistence. Raw room recordings, private configuration and binaries remain
excluded from Git and distributions. The older on-station-ASR baseline follows.

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
