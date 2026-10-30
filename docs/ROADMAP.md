# Alice MCP integration status

Status: 2026-10-30. This integration combines PRs #2–#6, the local voice-indicator
and OpenAI voice work, and the remaining unpublished owner-training helpers.
The older W1–W6 worktrees were compared with their already integrated successors;
newer runtime fixes are retained rather than replaced by old draft copies.

## Implemented source and evidence

| Area | Included behavior | Evidence and remaining limits |
| --- | --- | --- |
| W0 native voice | Reviewed PCM geometry/timing, Piper binding, guarded resident upgrade | The historical W0 build has owner-confirmed normal speech. The newly composed resident has not been installed. |
| Guarded combined upgrade | Exact W0 baseline, new binary with legacy config, production TLS barrier, then policy/config migration | Both led_map and wake schemas and validations survive. The composed ARM32 target is reproduced and pinned; merging does not install it. |
| W1 native adapters | Typed health/media/volume, source-only ALSA ioctl support | Mini 1 control metadata and Quasar volume synchronization still need qualification. No guessed mapping is installed. |
| W2 LEDs | Bounded effects, calibration and byte-by-byte Mini 1 PWM writes | Frame length, partial failures, no uncertain retry and idle restoration stay checked. Physical mapping and stock Alice ring ownership remain separate acceptance work. |
| W3 jobs | Owned timers/presets/siren and cancellation, guarded optional reboot | Jobs live in host RAM and do not survive restart. Volume-dependent auditions need a qualified mapping. |
| W4 room audio | Pinned Quasar IO-channel source, probes and mute runner | The full vqe_0 envelope is the known source; Quasar stays enabled. Physical mute requires owner observation. |
| W5 training | Source-only preparation/build/evaluation, owner segmentation, fine-tuning and holdout evaluation | Tools remain under scripts/voice, outside the wheel. Label/model-selection fixes survive. No private audio, weights or new training result is imported. |
| PC voice and indicator | Early local keyword, managed indicator through playback/error, fresh attempt timeout | Live stream startup/stop reported listening and verified cleanup with unchanged Quasar. Physical LED timing and audible Realtime response remain unconfirmed. |
| OpenAI voice | Command STT → Responses → mini TTS; conversation → Realtime; dictation → live transcription | Real API and synthetic-audio probes passed. The PC remains required; half-second playback chunks may have gaps. |
| Station wake source | Pinned children and untrusted native TLS events, preserving PC settings | Source-only bounded windows, empty commands and possible gaps. This is not an on-station cloud voice client. |

The native recipe and target pin are in
[NATIVE_AGENT.md](NATIVE_AGENT.md#reproducible-combined-binary). The guarded
migrator does not provision wake artifacts or invent ALSA metadata. Baseline pins,
TLS verification, one-use expiry, live trust/state checks and no uncertain retry
remain in force.

## Validation and acceptance

The composed tree is checked with pytest, ruff, strict mypy, MCP snapshot/catalog,
wheel/sdist builds, publication/privacy checks and redacted gitleaks on staged
content, history and distributions. Native checks include Go tests, race/vet,
format and independent ARM32 builds. These are not device acceptance.

Use [LIVE_ACCEPTANCE.md](LIVE_ACCEPTANCE.md) for the owner/device checklist and
[VOICE_ARCHITECTURE.md](VOICE_ARCHITECTURE.md) for voice and timeout limits.
The overnight helper accepted 17/20 supplied positive clips, including training
examples; this is not held-out accuracy. A matching personal ARM detector and an
identified historical 5/15 holdout split remain unavailable.

## Preserved scope and private material

Use one project-local alice connection. Keep Quasar enabled and preserve all
preauthorized defaults and private station/SSH settings. Cameras, persistent
dialogue history/notifications, acoustic guard, Telegram UI, arbitrary shell,
program upload, rooting, factory reset, flashing and host autostart stay outside
this implementation.

The supplied Telegram tree/ZIP, private settings/history/assets and root
rgb_effects.sh remain local references, as documented in REFERENCE_FEATURE_PARITY.md.
The actual .env, credentials, inventories, weights, captures, binaries and local
client settings stay outside Git; only the empty root .env_example is published.
Original draft worktrees are preserved for their owners.
