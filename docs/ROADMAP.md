# Roadmap: reference-controller parity and local voice

Status as of 2026-10-29. `REFERENCE_FEATURE_PARITY.md` maps the gaps; this file
assigns the remaining work. Nothing here is a claim that a feature is delivered.

## Scope

In scope: every row of the parity map except those listed below.

Out of scope by owner decision (2026-10-29): host camera (all rows), persistent
dialogue history and notifications, acoustic security guard. Already out of scope
by design: Telegram UI/forwarding, terminals and raw shell, script/program upload,
installer/self-repair/process cleanup, host autostart.

## Workstreams

| ID | Owner | Scope | Needs the owner at the station |
|---|---|---|---|
| W0 | OpenCode session | Initial local voice DONE: guarded resident installed, Piper bound, native `speech.say` NORMAL (73,472 frames, 3.350 s vs 3.332 s WAV; SSH disabled); [PR #2](https://github.com/fireostendere/Alice_MCP/pull/2) `c246e71`, CI green, open. Combined-build upgrade (W0 resident → new binary → TLS check → config migration with `"all"`; Python `preview()`/`apply()` and CLI): [PR #4](https://github.com/fireostendere/Alice_MCP/pull/4) `984c107` → `feat/reference-parity`, 1181 tests, CI green, reproducible ARM32 builds; nothing installed yet | Upgrade run: no (Claude runs it) |
| W1 | Claude Code agent | Native typed adapters: `audio.volume`, registered-media playback, typed health probes (memory, storage, uptime, services). Source-tested, not merged: 768 tests pass, Go ARM32 build OK. Acceptance after merge and resident deploy: `.venv/bin/python -m alice_mcp.native_acceptance {health,volume,media} --station <private alias>`. Deploy the new binary before adding the new device config fields | No; live checks are batched |
| W2 | Claude Code agent | LED parity effects (temporal rainbow, breathe, spin, custom-colour spin, static, off, notify, police) for Mini 1 and Lite, native LED adapter, channel-calibration runner; display/clock feasibility only. Source-tested, not merged: 841 tests pass; new `led.show` action, design in `docs/LED_PARITY.md`. Covers Mini 1, Lite and Mini 3 (6×1 BGR framebuffer); only Mini 3 has a clock, so display is read-only probe only. Mini 1 needs `calibrate` before `show` | Yes: calibration and effects check |
| W3 | Claude Code agent | Owned jobs: timers/countdown/alarm, owned cancellation ("stop"), bounded siren, reboot under a disruptive-action policy, declarative presets. Source-tested, not merged: 790 tests pass; tools `start_job`/`list_jobs`/`cancel_job`, design in `docs/JOBS.md`. Jobs live in host memory and are lost when the MCP process exits; reboot is SSH-only; native steps limited to `speech.say` until W1/W2 adapters are wired in at merge | No; live checks are batched |
| W4 | Claude Code agent | Voice capture: design for capture alongside Quasar with a read-only probe, physical-mute test runner. Source-tested, not merged: design in `docs/VOICE_CAPTURE.md`, candidates were a Quasar audio-channel IPC subscription, ALSA dsnoop and a second substream. Mini 1 read-only probe (2026-10-29, after a BusyBox 1.26 awk fix): second substream and dsnoop excluded, so the IO-channel subscription is the only path. Mute runner needs the owner | Probe: done; mute test: yes |
| W5 | Claude Code agent | On-station wake word (owner decision): microWakeWord models trained on the host for «компьютер», then «окей дом» and «маруся»; static armhf TFLite Micro helper (target < 8 MiB RSS; Mini 1 has ~133 MiB free and no Python); evaluation against W4b's Vosk baseline; integration plan in `docs/MICRO_WAKE_WORD.md`. Source-tested, not merged: «компьютер» usable (false rejects 1–2/16, unseen voice 7/60, 0.35 false accepts/h); «окей дом» borderline; «маруся» not usable. Static armhf helper with three models: 604,488 B, reproducible; x86 peak 780 KiB, about 0.2% CPU; ARM not yet run. Models are personal-use only (Piper/MMS licences) | No; live test is W6 |
| W6 | Claude Code agent | Live wake pilot: one guarded, bounded on-station run that subscribes to `vqe_0`, pipes it into W5's helper and returns detection events plus ARM RSS/CPU; no audio stored or exported | Owner says the wake word during the run |
| W7 | Claude Code agent | Voice → agent: PC wake listener (station `vqe_0` stream over chunked SSH with overlap; Vosk + fine-tuned mww). Live 2026-10-29: 14/14 «компьютер», 0 false accepts on «компот»/«компания», 0 gaps over 235 s, Quasar unchanged. In progress: command extraction and shared dedup, a voice service inside the alice MCP (`voice_listen_*`, `get_voice_events`), an opencode plugin that hands commands to the `alice` agent plus a panel, and spoken replies | Live test after build |
| W4c | Claude Code agent | Quasar IO-channel subscription: protocol research (`docs/IO_CHANNEL.md`), read-only IPC probe, bounded level-only subscriber pilot (no audio stored or exported). Source-tested, not merged; agent's feasibility estimate about 40%. Mini 1 read-only probe (2026-10-29): `platformd` loopback services include `yio_audio` (9850, no clients), `iohub_services` (9903), `iohub_clients` (9910), `aliced` (9875); `audiod` is absent. Pilot order: 9850, then 9903, then 9875, one run each. Live round 1 (2026-10-29, `vqe`, 5 s): no audio frames on 9850 (silent), 9903 (6 frames), 9910 (3) or 9875 (3); Quasar unchanged and cleanup verified on every run. The services answer, so a handshake or another request shape is needed. Live round 2: **`yio_audio` (9850) streams audio when sent the full `QuasarMessage{159: IOChannelRequest}` envelope**: 312 frames of `vqe_0` in 5 s, 16 kHz PCM, ~31.9 kB/s (S16 mono), quiet room about −79 dBFS, Quasar unchanged. The packed request, the `main` channel and the iohub ports gave no audio. Round 3: `raw_mic_0` also streams (16 kHz PCM), but its level stayed at about −58 dBFS RMS both in silence and while the station played 7–15 s of local TTS. Owner voice check (17:25): **live room audio confirmed**. `vqe_0` went from −78.9 to −45.0 dBFS RMS (peak −27.0) and `raw_mic_0` from −57.7 to −38.8 (peak −22.7) while the owner spoke; Quasar was unchanged. The station's own playback does not appear in either channel. Next: a resident streaming source feeding W5's detector | Pilot: owner-authorized, run by Claude |
| W4b | Claude Code agent | Host streaming wake/ASR on a synthetic corpus with false-accept/false-reject metrics, destination adapters (MCP / stock Alice); files named `wake_*`. Source-tested, not merged: results in `docs/WAKE_PIPELINE.md` (140 synthetic cases: false rejects 25–27%, 12.5% without «Сол»; false accepts 5–8% on near-miss negatives; endpoint delay median 0.72 s; about 367 MiB). MCP route implemented, not yet exposed as a tool; stock Alice text submission has no documented guarded path. Live use needs W4's capture source | No |

## Rules for parallel work

- W1–W4 run in separate Git worktrees based on `5c897ea`. They do not access
  devices and do not commit. Their changes are merged after W0 lands.
- W0 owns `native_transport.py`, `pcm_pilot.py`, `agent_upgrade*.py`,
  `local_voice_binding.py`, `native/pcm_player.c`, `native/agent/pcm.go`,
  `README.md` and `docs/NATIVE_AGENT.md`. Other workstreams keep their edits to
  these files small and additive.
- Each workstream ships: a failing test before the fix, synthetic tests only, docs
  marked "source-tested, not live-accepted", and one owner-run acceptance command.
  That command previews by default, needs an explicit typed confirmation for the
  live part, prints compact JSON and never retries an uncertain write.

## Batch live acceptance

After the merge, W0's guarded upgrade deploys one resident build that contains
the native changes from W1–W3. The owner then runs the acceptance commands in
this order: health → volume → media → LED calibration → LED effects →
timers/cancel → siren (low volume) → presets → W4 probes → reboot (last,
optional). The results are recorded in the docs as evidence. A failure stops
only that feature, and nothing is retried automatically.

The W0–W4b integration tree passes every check (1088 tests, ARM32 build,
`check_public`, gitleaks) and carries the ordered checklist in
`docs/LIVE_ACCEPTANCE.md`. Blockers before running it on Mini 1:

1. **Upgrade base (W0 owner).** `agent_upgrade.py` only upgrades the original
   install. It must accept the installed W0 resident as the verified base, pin the
   combined build's `AGENT_SHA256`, and add the new device config fields (`volume`,
   `media`, `led_map`, new actions) only after the new binary runs.
2. **Mini 1 volume.** Read-only checks (2026-10-29): no `amixer`/`tinymix` on the
   device; `pcm.!default` is `hw`; the `softvol` "Softmaster" PCM points at
   `hw:0,2`, which has no playback stream; only Quasar `platformd` holds
   `controlC0`, and Quasar keeps its own `last_user_volume`. W1's amixer-based
   mapping therefore cannot work on Mini 1. Options: ALSA control ioctls in the
   resident (risks fighting Quasar's state) or a volume request over Quasar IPC
   (shares W4c's protocol research). Volume is skipped on Mini 1 in this batch.
3. **Mini 1 LED evidence.** `led.capture` has no native adapter; take the fresh
   capture over the pinned SSH path before `led.show`.

## Estimate (2026-10-29)

| Work | Hours | Note |
|---|---|---|
| W0 | 1–3 | Read-only upgrade preflight passes; the upgrade itself is one operator step |
| W1 | 8–12 | Parallel with W2–W4 |
| W2 | 8–14 | Mini 1 channel mapping needs calibration |
| W3 | 8–14 | Builds on the W1/W2 primitives |
| W4 | 25–60 | Research; may conclude that capture is infeasible without stopping Quasar |
| Batch acceptance | 3–6 | With the owner present |
