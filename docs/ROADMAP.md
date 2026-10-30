# Alice MCP roadmap

Status: 2026-10-30. Existing PRs remain open; none was merged. The source review
and overnight changes use `night/review-integration`, based on PR #5. No work in
the active voice-indicator worktree or private station configuration was changed.

## Delivered evidence and remaining work

| Priority | Work | Actual status | Remaining gate |
|---|---|---|---|
| 1 | PR #2–#5 consistency | Reviewed PR metadata, diffs and shared runtime/upgrade contracts; regression fixes for BusyBox block writes, native default preauthorization, voice-start race, evaluation mixing models and accepting shortened comparisons. Existing PR branches preserved. | This is not a completed security audit of every line; final composed-tree validation remains required. |
| 2 | Personal «компьютер» ARM replay | Replay now validates inference timestamps and returns model/trace digests; preview-first CLI available. Host fine-tuned helper: 17/20 supplied positive clips, 556 KiB peak RSS, 0.29 s CPU / 110 s audio. | No station access: configured private-inventory validation was rejected by automatic approval review. No fine-tuned ARM helper was available in the supplied artifact directory. The manifest has no 5/15 holdout split; do not invent it. ARM probabilities/RSS/CPU are unmeasured. |
| 3 | Station wake source | Source/tests: pinned IO client → pinned detector children, bounded RAM PCM pipe, fixed vqe_0 subscription, authenticated TLS events with trusted:false; host source PC/station switch. No deployment. | Review matching ARM helper/pin and composed resident target; verify physical mute. Station mode currently emits wake-only events, empty command, in serialized 20 s windows with possible gaps. Command ASR stays on PC. |
| 4 | Mini 1 volume without amixer | Source/tests: typed ALSA control ioctl backend with exact reviewed metadata, range/ceiling, fresh read and one write/readback. Read-only audio probe supports it. | Actual control ID/name/range and platformd/last_user_volume synchronization are unknown. No remote inspection or ioctl was run. Do not configure a guessed mapping. |
| 5 | «окей дом» / «маруся» training | Training/build/evaluation moved to scripts/voice, excluded from the MCP wheel. Fixed contradictory contextual negative labels and multi-model evaluation. Existing model pins preserved. | No new training runs or weights. Supplied artifacts do not contain the full training stack/data/checkpoints. Need a fresh private dataset, independently held-out clips and honest FA/hour comparison before promoting another model. |
| 6 | Documentation / acceptance | README, native/wake notes and LIVE_ACCEPTANCE updated; one ordered morning checklist. | Owner-observed speech/LED/mute checks remain daytime work. |

Host evaluation used a fresh detector for each clip, 1 s lead/1.5 s tail silence,
threshold 0.98, five-frame window, zero gain. It includes training recordings and
must not be called held-out accuracy. Models, recordings, manifests, credentials,
endpoints and binaries were not copied into the source tree or publication package.

## Merge order and conflicts

1. **#2** `fix/mini1-pcm-hardware-format` → `main`: W0, owner-qualified local voice.
2. **#3** `feat/reference-parity` → updated `main`: host/native parity and owner defaults.
3. **#4** `feat/combined-upgrade` → updated `main`: W0 baseline, TLS barrier, then config migration.
4. **#5** `feat/wake-pilot` → updated `main`: PC wake and voice service.
5. **Overnight follow-up** `night/review-integration`: fixes, package boundary and experimental native adapters.

A read-only merge-tree calculation for the existing #4/#5 heads finds one conflict
in `docs/ROADMAP.md`: retain both the completed combined-upgrade source status and
PC wake evidence. Their Python/native runtime changes otherwise compose at those
heads. The overnight branch also changes `main.go` next to #4's `LEDMap` additions:
retain both optional config fields and both validations, plus the true default for
missing preauthorization. Reconcile this document and `NATIVE_AGENT.md` by evidence,
not by choosing one whole side. No merge or branch rebase was performed overnight.

The final composed Go source differs from #4's pinned executable. Rebuild it
reproducibly, then review/update the target pin and guarded config schema together.
A successful ARM build alone does not authorize installation. Keep the pinned W0
baseline, TLS barrier, one-use expiry, policy/trust/state checks and no uncertain
retry. #4's current migrator does not provision the new wake or ALSA mapping fields.

## Scope and safety

Still excluded: cameras, persistent dialogue history/notifications, acoustic
security guard, Telegram UI/forwarding, arbitrary shell/program upload, rooting,
factory reset, flashing and host autostart. One project-local alice MCP connection.
Keep Quasar enabled. New actions retain the owner's all/preauthorized defaults;
necessary hardware mappings and verified artifacts are prerequisites, not action
enabling forms. Do not narrow existing private policy as test cleanup.

Overnight station activity: **none**. No sound, LEDs, reboot, update, settings,
Quasar stop, microphone capture or replay occurred. Read-only station work and the
allowed quiet replay were blocked at access setup; the rejection was not bypassed.
