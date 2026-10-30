# Resident Mini 1 MCP agent

The optional Go agent runs on the column; the single host `alice` connection is
its client/router. Source: `src/alice_mcp/native/agent/`. No Go dependencies,
arbitrary shell, file browser, package manager or unauthenticated root API.

## Implemented boundary

- MCP Streamable HTTP, protocol `2025-11-25`, JSON responses over TLS 1.3 only.
  Private CA/server certificate and per-device bearer stay outside Git and chat.
- Five tools: `get_capabilities`, `get_station_controls`, `station_probe`,
  `run_station_action`, `get_capture_status`.
- Read-only probes: `health` (including agent RSS) and `microphone` metadata.
  Microphone metadata never captures audio or establishes working PCM access.
- Actions: literal `speech.say` and one-shot native Alice `speech.listen`.
  They reuse separately hash-pinned ARM32 clients through verified descriptors.
  The agent serializes its own actions; this does not lock Quasar or other writers.
- Both private host and device policies must allow the requested action; the host
  also requires global write opt-in and station-level standing preauthorization.
  The device has no interactive form transport: without its own standing
  preauthorization it blocks. Nothing silently falls back to SSH.
- Native writes now use the shared host plan registry: a fresh 300-second preview
  binds the exact request and host station/trust configuration, is consumed once,
  and is executed under the controller's serialization lock. Synthesis, policy,
  expiry and resident state are revalidated before dispatch. The five native tools
  are unchanged; controls add `state_contract=alice.native.state.v1` and
  `state_sha256`, and action requests require `expected_state_sha256`. The resident
  validates that digest under its action lock against policy/credential, firmware,
  boot, Quasar process identity, instance and generation; completion or refusal
  invalidates it. The resident independently expires snapshots after at most
  300 seconds; read-only revalidation never extends the old deadline. A timed-out
  caller must never reuse/retry the request.
- Older installed residents remain usable for read-only probes/controls but
  **writes fail closed** without the state-bound contract. One owner-qualified
  Mini 1 now runs the guarded upgrade below; other installations still require
  their own separately authorized upgrade, not an automatic reinstall.
- ASR observes stock Alice, not independent speech recognition. Preparation is
  bounded to 180 seconds, followed by a 5–60 second phrase window. One session is
  retained in RAM; text is untrusted data, released once after successful child
  exit/Quasar checks, and discarded after two minutes. Status never starts capture.
- The qualified Mini 1 now uses the explicitly selected local-PCM backend below.
  Other configurations may retain normal Yandex synthesis; local mode forbids
  cloud-text fallback and does not alter ordinary Quasar operation.
  Uncorrelated completion remains `unknown`/`do_not_retry`; a functioning TLS channel
  does not prove audible sound.
- Source (not installed) adds Mini 1 `led.show`: host-mapped, bounded dim PWM frames
  with the SSH helper's capture evidence and lock; see [LED parity](LED_PARITY.md).

## Operator provisioning and inventory

`agent_provision.py` is operator-only, not an MCP arbitrary-upload tool. Its
fresh, consumed-once 300-second preview binds station/trust/configuration,
firmware/boot and the exact canonical archive. It accepts only pinned agent,
ASR and TTS artifacts, private configuration/certificate/key/credential, and its
fixed startup script. Existing installation/startup destinations are refused.
The pinned combined binary is also used for new installs. Bootstrap canonicalizes
`allowed_actions` to `"all"` (including legacy input lists); standing preauthorization
remains `true`. The private input file is not edited.

Installation uses the verified persistent data volume and a dedicated
`/etc/init.d/S99zz-alice-mcp` hook. Vendor launch profiles, Quasar, SSH, mount flags
and SELinux are unchanged. Do not automatically reinstall after an uncertain
write or use reboot as an implicit acceptance test.

After verifying TLS, add only this field to the existing private station entry:

```json
{
  "native_mcp": {
    "url": "https://station.invalid:18443/mcp",
    "ca_file": "/PRIVATE/ca.pem",
    "token_file": "/PRIVATE/credential"
  }
}
```

Preserve other entries and SSH recovery settings. A native-only inventory may
omit SSH fields. All existing trust/permission checks still apply to the selected
transport. TLS errors, unsupported tools and invalid results fail closed.

### Guarded W0 → combined upgrade (source-tested, not live-accepted)

`agent_upgrade.py` is separate from bootstrap. The baseline is the **installed W0**
resident, SHA-256 `ec43eb7ada625616dc04c5c6f58ab619ac1ce758bcec5d5f7a92780c17303806`,
not the pre-W0 install. The W0 ASR/TTS and existing PCM player must match their
source pins. The canonical four-file package contains the pinned combined agent,
the **same** PCM player, migrated `config.json` and the reviewed startup template.
No arbitrary baseline or artifact hash is accepted. The old startup may predate
that template; its exact reviewed hash is bound and backed up, not executed by
the helper. Listener, TLS files, bearer, local-PCM pins, recovery SSH, host inventory
and the existing init hook are preserved.
W0's old config must still contain its explicit speech-action list, not `"all"`
or new-schema fields; the new policy is never used for the first startup.

The active config changes to `allowed_actions: "all"` and
`preauthorize_all_actions: true`. Optional `config_updates` contains only reviewed
`volume`, registered `media` aliases/digests, and `led_map`. No mixer, calibration
or media is invented: omit unavailable fields, especially Mini 1's unqualified
mixer mapping. Media digests must already match host `controls.local_media`.
Volume remains bounded by host `max_volume`. LED calibration uses the shared host
validator; the resident accepts it as state-bound metadata, not a second mapping
of already-mapped PWM frames. Host `controls.led_map` stays unchanged.

A consumed-once 300-second host preview binds the package, private configuration,
native and recovery-SSH trust, firmware, boot, Quasar process identities, existing
file hashes, current agent PID/start time and helper source. This is an explicit
operator maintenance transport, not a normal native-action SSH fallback. Existing
lifecycle/capture claims, a missing or changed W0 PCM player, wrong files, insufficient
space or active agent children block the transaction.

Read-only inspection sends bounded bundled source on stdin, not a long SSH exec
argument. Apply uses a short fixed request, reads exactly the source-prefix byte
count without consuming archive bytes, preserves trailing newlines with a sentinel,
and verifies source length **and SHA-256 before evaluating that reviewed source**.
Only then can the independently hashed canonical archive be consumed. Exact archive
byte reads leave the same SSH stdin open for a single TLS acknowledgement. No source
path/script text is accepted from an MCP action, no helper script is saved on the
Station, and the combined source+archive stays within the existing 20-MiB transfer
bound. Tests execute this actual wire path and reject truncated/shifted source
before staging. A former approximately 15-KiB exec request failed a real read-only
preflight; the short-request stdin path subsequently returned ready. The exact old
server-side rejection reason was not collected and is not guessed.

The fixed helper stages and verifies before stopping anything, creates a private
backup of the three replaced files, and freezes **only our identified resident**
before inspecting children. PID/start time/executable/artifact are rechecked after
STOP; a mismatched stopped identity is resumed, not killed. With no children, the
old resident is killed while stopped to avoid a resume/new-request race. Quasar is
never stopped. Replacement uses same-filesystem file renames and bounded startup
plus ownership of the listening socket. The strict install order is:

1. Replace/start the new binary with the **exact old W0 config still active**.
2. Pause the SSH transaction; the host checks the production authenticated TLS 1.3
   channel and state-bound `speech.say` controls through `native_transport._state`.
   No speech or device action is dispatched. Recheck policy/trust/expiry, then
   acknowledge this one transaction on its existing stdin.
3. Only after that acknowledgement rename the new config into place. The resident
   reloads policy per request; no additional process restart is needed. The host
   checks TLS controls once more before reporting verified installation.

Known startup, pre-migration TLS or config-rename failures attempt restoration
of matching backups and restart only the pinned W0 agent; the W0 PCM player remains
in place. A foreign/currently
changed file or lost lock ownership prevents blind rollback or cleanup. Backups
remain under the owned `.upgrade-<opaque-id>` directory; this is not full-device
backup or a user-supplied installer.

Recovery is armed before STOP/KILL, including handled-signal boundaries. Resume
cancels a pending STOP even before `/proc` reports state `T`; rollback waits for
the original identity to depart before reconciling its stale PID file. Tests model
both delayed stop visibility and delayed process disappearance. Preserved
credentials/helpers are rechecked before either new or rollback launch; a foreign
credential change is not overwritten or reported as a successful upgrade.

These are optimistic cooperating-writer checks, **not pidfd or kernel CAS**. An
uncoordinated root actor, hard power loss, SIGKILL of the helper or uncertain SSH
termination can leave a partial state. Initial numeric-PID STOP can race with exit;
post-STOP identity checking prevents killing a mismatched stopped process but does
not provide an atomic PID handle. Never rerun an uncertain update or remove its
claims automatically. Inspect the saved receipt and owned state first. Keep other
agent actions, startup operations and configuration editors idle during maintenance.

Missing/invalid TLS acknowledgements, transport uncertainty or a failed final TLS
check return `unknown`/`do_not_retry`; never rerun the write. A final TLS failure
after commit does not authorize another rollback/write from the host. Inspect the
owned state first. TLS controls success is not audio/LED or reboot acceptance.
After source updates, reconnect the existing single host `alice` process before
using it: a fresh operator Python client does not reload a previously running MCP
router. Do not create feature-specific connection aliases.

#### Operator API and CLI

`preview(controller, station_id, package)` and
`apply(controller, plan_id, expected_sha256, package)` are callable from Python
without a TTY or input prompt under the existing standing authorization. Keep the
same controller and bundle in one process: previews are in-memory, expire after
300 seconds, and are consumed once even on refusal. Build the bundle with:

```python
from alice_mcp import agent_upgrade

package = agent_upgrade.bundle(
    controller.station(station_id), agent_bytes, player_bytes,
    old_config_bytes, old_hashes, alsa_config_sha256, library_sha256,
    layout=layout, config_updates=updates,
)
plan = agent_upgrade.preview(controller, station_id, package)
result = agent_upgrade.apply(controller, plan["plan_id"], plan["expected_sha256"], package)
```

`old_config_bytes` must be the exact private W0 file, not reserialized JSON.
`old_hashes` follows `BASE_NAMES`: `agent`, `tts-once`, `asr-once`, `config.json`,
`certificate.pem`, `private-key.pem`, `credential`, `start`, `pcm-player`. These
digests are rechecked on the station; they do not replace the pinned binary hashes.
No credentials or private snapshots belong in Git or chat.

The CLI validates private file trust and size, previews by default and emits compact
JSON. `--old-hashes` is a private JSON object mapping those nine names to digests;
`--config-updates` is optional private JSON containing only the three supported
fields above. Run from this branch's worktree with `PYTHONPATH=src`, the owner's
Python executable and private inventory environment. For example, with those
environment variables and private absolute file variables already set:

```sh
"$PY" -m alice_mcp.agent_upgrade --station "$STATION" --layout "$LAYOUT" \
  --agent "$AGENT" --player "$PLAYER" --old-config "$W0_CONFIG" --old-hashes "$W0_HASHES"
```

Add `--live` only for the terminal CLI: it previews anew, requires the exact word
`UPGRADE` via `core.confirm`, then applies once. Cancellation consumes the preview.
Non-TTY operators must use the Python API, not pipe a confirmation word.

#### Reproducible combined binary

Go **1.27.3**, stdlib only, from `src/alice_mcp/native/agent/`:

```sh
CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 GOTOOLCHAIN=local \
  go build -trimpath -buildvcs=false -ldflags='-s -w -buildid=' -o "$AGENT" .
```

Two clean builds in different source directories with independent empty Go caches
produced identical 7,340,156-byte ARM32 ELF executables, SHA-256
`32f709fd80a9769f02bf44473719e403a8ce665234924be44270999e15f46f5e`.
`agent_provision.AGENT_SHA256` is the shared target pin; `agent_upgrade.BASE_AGENT_SHA256`
is the distinct W0 baseline. Binaries remain outside the source/distribution tree.

Host contract tests and disposable local-daemon shell tests cover guarded update,
rollback, changed files/claims, active children, failed new startup, PID-file-move
failure, post-STOP identity mismatch, Quasar metadata failure, TLS refusal and
config-rename failure. The actual duplex wire tests assert that the old config
is still active when the new executable reaches the host TLS barrier. These are
synthetic tests, not real firmware qualification. **No combined upgrade has been
installed or live-tested.**

Historical W0 evidence: a previous owner-operated update installed the W0 agent/player,
preserved the existing init hook, TLS credentials/helpers and Quasar identity, and bound only host
`controls.local_tts`. The owner separately approved one native audition and selected
`NORMAL`; the live result is recorded below. This bounded path accepts the reviewed
pre-W0 installation. It does not qualify this combined upgrade.

## Evidence and remaining blockers

One owner-authorized Mini 1 installation was checked with the standard Python
MCP SDK over actual TLS, then through the single reconnected host `alice`.
Health/microphone metadata and five tools responded; initial RSS was about 7 MiB.
An SSH-disabled host runner also verified native routing. Quasar process identity
was unchanged and SSH recovery remained available. Startup was registered, not
tested by reboot. Host tests and Go race/TLS/protocol tests use synthetic data.
No new household capture or TTS acceptance test was run during that initial installation;
the host action allowlist was preserved, not expanded to enable ASR.

The full independent assistant/wizard is **not live implemented or accepted**:

1. PCM capture is occupied by Quasar. After separate owner approval, a temporary
   Mini 1 VQE statistics observer was built/pinned and tested: channel type 6 was
   called with finite, plausible normalized float data. No PCM was exported.
   Its detached 60-second guard restored stock Quasar; the module and files were
   removed and normal capture/native MCP checked. This partially establishes the
   examined library ABI, not PCM/recognition acceptance or Plum compatibility.
   A production audio adapter and physical-mute signal test are still required.
2. No verified arbitrary-RGB adapter exists for Mini 1. Replaying a recorded LED
   update is not selecting an activation color; LED integration needs its own
   reviewed format/channel mapping and live acceptance.
3. The owner chose local TTS. Optional `local_tts.py` verifies an installed Russian
   Piper model/config by hash and returns at most 20 seconds of mono S16 WAV in
    memory. Synthetic bounds and the resident TLS/PCM path are tested; one Mini 1
    subsequently passed the owner-operated local-voice acceptance below. This is
    not independent microphone/wake or universal firmware qualification.
    Piper 1.8.0 is GPL-3.0-or-later;
   the tested Ruslan model card specifies CC-BY-NC-SA-4.0 for its dataset. Weights
   are not distributed here; this test is a personal noncommercial experiment.

## Local PCM backend (owner-qualified on one Mini 1)

Keep using the same host `speech.say` action and one `alice` connection. A private
host `controls.local_tts` object selects the hash-pinned installed Piper model:

```json
{
  "model_file": "/PRIVATE/voice.onnx",
  "model_sha256": "REPLACE_WITH_REVIEWED_MODEL_SHA256",
  "config_sha256": "REPLACE_WITH_REVIEWED_CONFIG_SHA256"
}
```

This selection requires native transport. It synthesizes in RAM, sends canonical
WAV plus its digest over authenticated TLS and **never falls back to cloud text
TTS or SSH**. Native private `local_pcm` contains `client` (the installed pinned
`pcm-player`), `alsa_config_sha256` (reviewed `/etc/asound.conf`) and
`library_sha256` (the resolved vendor `libs/libasound.so.2`). That device mode rejects
cloud text requests, even when an old host attempts one. Backend aliases, never
paths, appear in controls; host/device selections can be compared there.

`native/pcm_player.c` accepts only mono S16 canonical WAV: 16–48 kHz, 20 seconds
maximum and absolute sample amplitude at most 8191. It writes to an isolated ALSA
plug-to-hardware graph for the kernel-supported **candidate** playback port 0
(TDM-B), with an explicit 48-kHz/S32_LE/stereo slave and mono duplication.
The owner confirmed normal-tempo speech in both the bounded SSH pilot and the
subsequent resident/TLS audition below. The graph has no
user/global ALSA config hooks. No capture, mixer change, Quasar stop, waveform
file, external URL or playback retry is involved. The 25-second child alarm and
27-second resident deadline bound failures. Drain/exit is software evidence, not
proof of audible output or room volume. Unknown results must not be replayed.

Only typed PCM requests receive the larger 2,600,000-byte HTTP body allowance;
other requests retain 16 KiB. Authentication precedes body parsing. Native actions
remain serialized and recheck local policy, firmware and artifact trust. Tests
use a synthetic ALSA library and hardware-free file/null conversion;
cross-build and protocol success alone do not qualify the real audio path. Do not
use the bootstrap installer to overwrite an existing agent: other devices/versions
still require a separately guarded upgrade and their own live qualification.

A subsequent read-only check found that this Mini 1 has **no**
`/usr/lib/libasound.so.2`: the library is at
`/system/vendor/quasar/libs/libasound.so.2`. Both player and resident source now
use that reviewed vendor path. The required public ALSA entry names and built-in
hw/plug/dmix names were observed as strings; neither external plugin directory
exists. That observation alone was presence evidence, not a successful `dlsym` or
audible test; the later owner-qualified playback provides separate live evidence.
External ALSA plugins are disabled for this isolated graph.

`pcm_pilot.py` provides a separately owner-run, one-shot development qualification
before upgrading the installed agent. It reuses the bounded speech helper's
temporary file/claim cleanup, pinned key-only SSH, firmware/ALSA/WAV/source-bound
one-use preview and no-uncertain-retry behavior. It verifies unchanged Quasar PIDs
and requires the owner to approve sound in the room. It does not install startup
entries or change volume/Quasar. This explicit pilot is **not a normal speech
route or native-MCP SSH fallback**, and cannot by itself qualify resident playback.
Mini 1 has no `base64` utility or BusyBox decoder. The pilot now sends exact player
bytes followed by WAV on bounded pinned SSH stdin, with a fixed `sh -c` helper.
`dd bs=1 count=...` saves only the pinned executable and cannot read ahead into the
waveform; the player reads remaining audio in RAM. No WAV file or decoder utility
is needed. Transfer is bounded to 20 MiB, output to 4096 bytes and wall time to
120 seconds, with no retry. The unchanged canonical-WAV limits remain tighter
(at most 20 seconds). Actual generated-shell tests include the maximum-size WAV,
truncated executable/audio, trailing bytes, changed state, cleanup and silent
diagnostics. Bootstrap transfer/result contracts are not weakened. Normal resident
PCM retains its independent TLS size/duration bounds and never uses this pilot.

The source provisioning startup template also serializes start/stop/status with
an owner-local claim with an exclusive, no-clobber owner record; only its recorded
owner may clean up the claim. Directory creation alone is not sufficient authority
to remove a lock. A regression reproduces the observed double-directory-acquisition
race without weakening PID/lifecycle checks. Busy or stale claims require inspection, not blind
removal. Concurrent-start tests use a synthetic daemon and verify that the PID
file tracks the single live process; symlink PID targets and unrelated processes
are protected. Both bootstrap and later startup wait up to three seconds for the
same child PID to identify as the reviewed agent, rather than assuming one fixed
second is enough; the wait never launches a replacement process. A delayed-exec
regression exercises this bound. The installed startup script is unchanged. This is not permission
to overwrite the existing installation with the bootstrap procedure.

The generic `/etc/asound.conf` is not proof of the live Mini 1 hardware route.
Its `dmixer` names device 2 and `default` names device 3, but a subsequent kernel
capability read found playback only on devices 0/1 (TDM-B/TDM-C); device 2 is PDM
capture-only and there is no playback node 3. The old device-2 graph cannot play
on this observed layout and was removed, together with its unqualified IPC mixer.
The replacement initially left slave geometry unconstrained. Following the
fast-speech report below, the candidate now explicitly converts bounded input WAV
to 48-kHz/S32_LE/stereo; it still uses no separate IPC mixer or global/user
configuration hooks. Both operator
playback preview and resident execution require the playback-0 character node.
This is a bounded qualification candidate, not a claim that port 0 is already
audibly accepted. Port 1 is not tried automatically, and busy hardware must fail
closed without stopping Quasar or changing mixer/GPIO state.

The first owner-operated PCM pilot returned `unknown`; the owner reported no
audible sound. A subsequent read-only check found no owned temporary directory,
shared claim or player process, and confirmed stock Quasar plus the unchanged
installed agent. This establishes observed artifact/process cleanup, not playback
success or its cause. Fixed ELF dependency paths were present and a bounded kernel
marker sample showed no matching client fault; neither observation rules out a
loader/library failure. No playback was repeated.

The helper now retains its stage and child exit status, independently of player
stdout. Invalid/missing player output stays `unknown` with `do_not_retry=true`;
validated `helper_cleanup_verified` is separate from unverified player cleanup.
Tests execute the actual generated shell against a fake player, including absent,
malformed and extra/private stdout, not just a fabricated SSH result.

The pinned player also has an operator-only `--check` path. It loads the reviewed
ALSA library, resolves fixed functions and parses the in-memory graph, **without
audio input, `snd_pcm_open_lconf`, PCM setup, writes or capture**. `ready` here means
only library/config readiness, never audible playback or resident integration.
A hard five-second alarm ends even a stuck vendor call (the playback fallback
alarm remains 25 seconds); the helper reports exit 124 and cleans its owned files
if no player JSON survives. This silent diagnostic must not be replaced by an
automatic replay. Its synthetic tests and read-only station preview have passed;
actual station execution is still owner-operated and unverified.

An owner-run silent diagnostic subsequently exited **0** with helper cleanup and
unchanged Quasar, but the host rejected the player response as invalid. The exact
old stdout was not retained, so do not claim its particular rejection cause.
Two producer/consumer defects were reproduced independently: vendor-library stdout
could contaminate JSON in both check/play modes, and the host accepted only zero
although the C client treats every nonnegative configuration return as success.
The client now duplicates its typed-output descriptor with close-on-exec and
routes vendor stdout to discarded stderr before loading the library. The parser
accepts bounded nonnegative C success returns while still requiring ready stage,
parsed configuration, no PCM/audio flags, exit 0 and verified helper cleanup.
Malformed/noisy data is not salvaged or accepted as success. Unknown results now
also include a closed validation-reason code and bounded stdout-line count, not
raw text or unexpected fields. Synthetic noisy-library and positive-return tests
cover both fixes. They are not live audio acceptance, and no automatic check or
playback repeat was performed.

A later owner trial of playback **0** reported 6656 written frames at 22050 Hz,
`playback_started=true`, `playback_completed=false`, exit 1 and successful cleanup,
with unchanged Quasar. The owner heard a short “meow”, not intelligible speech.
The old result omitted input frame count; it cannot prove all input was queued.
No replay followed. A **synthetic** ALSA stub reproduced one possible failure:
nonblocking drain returned `EAGAIN`, then write-readiness wait failed during the
draining state. The player now switches to blocking mode after all writes and
calls native drain once under its existing hard alarm. Partial-write/EAGAIN offset,
mode-switch/drain/cleanup failures and an actually stuck 25-second drain are tested.
This does not prove that drain was the cause of the real meow or qualify output.

Playback responses now retain validated `input_frames`, closed `stage` and numeric
`alsa_return_code`, separately from helper exit/cleanup metadata. Consumers reject
uncorrelated counts, arbitrary stages and private/noisy fields. Hard termination
can still emit no player JSON; it remains unknown/no-retry, not a fabricated ALSA
completion. No sound or device deployment was performed during autonomous development.

A subsequent owner-operated longer-phrase trial wrote all **64,256 input frames**
at the declared WAV rate of **22,050 Hz** (2.914 seconds), exited 0 and reported
software completion, player/helper cleanup and unchanged Quasar. The owner heard
the phrase but clarified that it sounded approximately ten times too fast.
The original `HEARD` response establishes audibility, **not normal-tempo acceptance**.
Neither hardware geometry nor elapsed playback time was recorded in that trial;
the reported speed ratio is subjective, not measured.

Read-only metadata found a 48-kHz Quasar setting, S32_LE/stereo ALSA settings and
two-slot, 32-bit device-tree links. These observations are not a complete mapping
of every DAI and do not prove the earlier negotiated parameters or physical clock.
The 8.7075 ratio between candidate native and input byte rates is a hypothesis,
not a measured cause. The generic config's invalid device numbers remain excluded.

The revised isolated graph constrains the **same port 0** slave to 48 kHz,
S32_LE and two channels, with ALSA `route_policy duplicate`. This uses the
[documented plug slave conversion](https://www.alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html),
not an arbitrary TTS slowdown. After setup and **before any write**, the player
requires matching bounded kernel readback from
`/proc/asound/card0/pcm0p/sub0/hw_params`; missing, closed or mismatched geometry
blocks playback at stage `hardware`. `hardware_parameters_verified` means this
fixed geometry was read back, not that physical clocks or tempo were measured.
`sample_rate` remains the **input WAV rate**. `playback_elapsed_ms` uses the
monotonic interval from before the first write through drain, excluding transfer,
setup and cleanup; -1 means unavailable. Strict C/Go/Python contracts carry both
fields, and no longer accept old completion responses without this evidence.

A regression executes the production graph with **only the hardware leaf** replaced
by ALSA file/null, never a sound device. Generated one-second mono S16 inputs at
16, 22.05 and 48 kHz must yield one second of duplicated stereo S32_LE/48-kHz
output (at most 1 ms converter-tail rounding), with preserved sample amplitude.
The old unconstrained graph failed this test by retaining input byte geometry.
The file/null test's kernel-readback fixture is explicitly synthetic; it does not
qualify vendor conversion, a real clock, room sound or the installed resident.
There is no automatic replay, port-1 fallback, cloud use, mixer edit or Quasar stop.

A subsequent owner-authorized trial of the revised player completed all
**66,560 / 66,560 input frames** at 22,050 Hz, with kernel geometry verification,
exit 0, player/helper cleanup and unchanged Quasar. Measured write-through-drain
time was **3,031 ms**, versus **3,019 ms** nominal WAV duration. The owner explicitly
selected `NORMAL`, confirming the complete intelligible phrase at ordinary tempo.
This accepts the bounded **operator SSH pilot on that tested Mini 1**, not the
production resident/TLS route, reboot persistence, other firmware or microphone/
wake-word behavior. The machine result still correctly says
`physical_effects_verified=false`; owner confirmation is separate evidence.
The earlier fast trial remains recorded and is not retroactively accepted. No
automatic replay followed; the later resident change required a new explicit
operator session rather than reusing the SSH-pilot request.

### Native local-voice acceptance (2026-10-29, W0)

One explicit owner session completed guarded maintenance, native TLS/state-contract
verification and private Piper binding. A separate `PLAY NATIVE` confirmation then
authorized exactly one `speech.say` through the resident MCP. The fresh host router's
SSH runner was disabled for the audition: no executable transfer, cloud fallback,
microphone capture, volume change or Quasar restart was part of speech playback.

The resident wrote **73,472 / 73,472 input frames** at **22,050 Hz**, with hardware
geometry verified before writes, stage `complete`, ALSA code 0 and cleanup true.
Write-through-drain took **3,350 ms**, versus **3,332.063 ms** nominal WAV duration.
The owner selected `NORMAL`, confirming intelligible speech at ordinary tempo.
`physical_effects_verified=false` remains the machine result; owner confirmation
is separate evidence, not a fabricated device sensor or ALSA audibility claim.

A subsequent **read-only, no-audio** batch verified deployed agent/player/config/
startup hashes, preserved certificates/key/bearer and speech helpers, one matching
rollback backup, no lingering lifecycle claim, unchanged init hook/ALSA settings/
vendor library, and running Quasar. The private inventory changed only by adding
the selected station's `local_tts`; all three entries, recovery SSH fields and
standing permissions were preserved, with an owner-only original-inventory backup.
Fresh native-only controls and health also passed with SSH disabled. No sound was
replayed during these postchecks.

This accepts local voice on the tested Mini 1 and the reviewed builds only. It
does not accept reboot persistence, independent capture/wake/ASR, RGB, timers or
other workstreams; those require their own evidence. Reconnect the existing single
host `alice` process after host-code updates so an older loaded result parser is
not mistaken for the newly verified client. No additional connection alias is needed.

## Physical mute versus software status

[Yandex support](https://alice.yandex.ru/support/ru/station/settings/microphones)
explicitly includes the first Mini and describes microphone muting as hardware
disconnection. The [hardware team's circuit explanation](https://habr.com/ru/companies/yandex/articles/799737/)
states that the processor cannot switch microphone power back on; its detailed
examples cover Midi/Mini 2021, Station 2 and Max, not a Mini 2019 schematic.

An unreadable software mute flag is not evidence that hardware muting failed.
On the examined Mini 1, only speaker-mute GPIO labels were found; a bounded
DeviceState IPC watcher did not produce a recognized microphone state during an
owner-confirmed red-indicator on/off test. It reported unknown and cleaned up.
Do not mislabel this as unmuted, infer microphone state from speaker GPIOs, or
guess more RPC methods. Preserve the hardware button. Before enabling continuous
audio, verify VQE behavior across physical mute/unmute; software status may remain
unknown independently. No always-listening PCM loop is installed.

A first mute/unmute level-comparison attempt was rejected: owner-confirmed phases
occurred after the fixed 45-second collector window. It is not evidence about
mute effectiveness. Stock Quasar restoration and cleanup were verified. The
operator code now has a **synthetic-tested, not yet live-tested** deferred mode:
no collector runs while waiting for a person; each confirmed phase gets one
eight-second device-timed window. A detached guard requests stock restoration on
an explicit finish marker or at a boot-monotonic 900-second deadline; stock
startup then takes a few seconds. This timing is not an iteration-count timer.
Using this longer temporary profile requires disclosing the new timing before
another live test.
Level statistics remain quantized observations, not electrical proof of a power
cut or acceptance of wake-word recognition.

An operator can run `python -m alice_mcp.vqe_experiment --station ALIAS --audit
PINNED_AUDIT_PATH --collector PINNED_COLLECTOR_PATH`, with the inventory supplied
privately through `ALICE_MCP_INVENTORY`. It requires an interactive terminal,
explains the 900-second deadline and asks for explicit physical-state confirmations
before collecting either window. It requests finish in `finally`, including on
an interrupted prompt, then verifies stock restoration before cleanup/comparison.
No MCP tool or wizard silently calls this experimental module workflow. Do not
run it in the background or pipe confirmations; be beside the physical button.

The owner withdrew a first apparently covered comparison as incorrectly acquired.
A corrected operator run reported `phase_coverage_missing`, with restoration and
cleanup true. The old report omitted both phase counts and cannot establish which
window was empty or why. Neither result is accepted as a physical comparison.
The workflow now first asks for microphones ON/red OFF and a `BASELINE` confirmation:
one 20-second statistics-only window must contain at least three valid type-6
reports in its last eight seconds before the mute prompt is offered. Socket or
launcher readiness alone is not VQE data readiness. It retains per-window bounded
packet/type/sample counters even when comparison fails, including both counts.
Missing samples while muted may represent a paused output channel, not numerical
zero; do not label them a failed hardware mute or synthesize zero-level samples.
This corrected baseline gate has synthetic tests, not a new live acceptance run.

## Offline assistant profiles and simulation

`assistant_profile.py` validates versioned Unicode phrase/route/color settings and
an explicit hash-pinned local Piper selection. `route_transcript` returns an
**untrusted data event**, never an action or permission. It matches transcript
prefixes with word boundaries; it is not acoustic wake-word detection. Private
profile persistence is POSIX-only, owner-only and atomic create-only: existing
files, station inventories and symlinks are not replaced.

Run the effect-free installed-module smoke check with:

```sh
python -m alice_mcp.assistant_wizard --self-test
```

`--simulate` is an alias. It exercises Unicode normalization, both transcript
routes, a synthetic color sink and synthetic TTS contract. Output explicitly says
`synthetic=true` and `hardware_verified=false`. It neither loads a voice nor opens
audio/network/device interfaces. `activation_status` remains blocked regardless
of caller claims; capture, acoustic recognition, RGB and playback are unqualified.
The separate explicit `synthesize_reply` adapter returns RAM audio only, without
playback or cloud fallback. A profile saved successfully is **not** a working
hardware-connected setup wizard. No always-listening loop, LLM inference or
unrestricted smart-home actuation is installed on the column.

### Independent Russian recognition: host experiment, not live wake acceptance

An offline experiment fed Piper-generated Russian PCM directly from RAM to the
[Vosk 0.3.45 C API](https://github.com/alphacep/vosk-api/blob/v0.3.45/src/vosk_api.h),
using unrestricted recognition with `vosk-model-small-ru-0.22`. It recognized
synthetic “Маруся, включи свет” and “Алиса, какая погода”, which the separate
transcript router assigned to MCP/Alice respectively. A sentence without either
prefix and two seconds of digital silence produced no routed event. No microphone,
station connection, playback, cloud request or waveform file was used.

An expanded ten-case corpus added “Компьютер” and “Окей дом”, embedded-name and
similar-word negatives, plus deterministic digital noise. Nine expected routing
outcomes matched. “Сол” was decoded as “сок” (and “соул” in another run) and did **not** activate, despite “сол”
being present in the model vocabulary. This is a real synthetic false reject;
arbitrary names are not reliably supported merely because a profile accepts them.
No automatic alias or permissive fuzzy match was added to hide this failure.

A second recognizer used a finite phrase-only grammar plus `[unk]` on the **same
generated PCM**. It recovered “Сол”, but falsely routed the embedded-name and
similar-word negative examples. Its faster 0.02–0.06 s decoding does not justify
deployment: this test directly demonstrates the false-activation risk of treating
constrained ASR as wake detection. Neither recognizer is connected to real actions.

On this host, model load took about 0.33–0.37 s; unrestricted inference took about
0.24–0.55 s for 1.2–2.0 s synthesized utterances across the expanded runs. Peak RSS of the **combined Piper/Vosk process**
was about 880 MiB, not a measurement of Vosk alone. Ten synthetic cases establish
neither room accuracy, false-wake rate, echo resistance nor continuous streaming
latency. They are acoustic inference on generated audio, not merely supplied
transcript matching—but still **not live independent wake acceptance**.

The [official model catalog](https://alphacephei.com/vosk/models) lists the 45 MB
Russian small model under Apache-2.0 and notes roughly 300 MB runtime memory for
small models generally. The exact downloaded ZIP was 46,236,750 bytes, SHA-256
`961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4`.
This is a locally observed artifact pin, **not an independently published publisher
checksum**. The Linux x86-64 Vosk wheel was checked against PyPI's 0.3.45 SHA-256;
no Vosk package was installed and no downloaded Python script was executed.
Neither library nor model weights are bundled or deployed here.

For a first host baseline, Vosk supports mono S16 PCM with the actual sample rate;
its [grammar example](https://github.com/alphacep/vosk-api/blob/v0.3.45/python/example/test_words.py)
also shows finite phrase lists and an unknown alternative. A constrained decoder
can force plausible phrases, so it is not a low-false-alarm wake detector by
itself. [openWakeWord](https://github.com/dscripka/openWakeWord#language-support)
currently documents English-only pretrained support, not arbitrary Russian.
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) remains an unbenchmarked
open-vocabulary alternative; no comparative performance claim is made.

The next real prerequisite is still reviewed, bounded **device-to-host capture**
that coexists with Quasar and preserves physical mute. The existing TLS WAV route
goes in the opposite direction, for playback only. VQE statistics do not establish
PCM rate or sample semantics. Before continuous operation, endpointing, echo/retrigger
suppression and substantial negative-audio evaluation are also required. Returned
text and wake events must remain untrusted input to existing action policy.

## Overnight source additions (2026-10-30; not installed)

`voice.source="station"` on the host uses the same `alice` connection and native TLS
transport. The resident's optional private `wake` object contains `source_client`,
`detector`, `detector_sha256` and `model_sha256`. The IO client is independently
pinned to the reviewed `io_channel_levels.c` build. Both helpers are opened through
trusted descriptors, hashed before execution, and invoked with fixed arguments.
The resident checks ownership of the fixed yio_audio listener, firmware, policy,
Quasar identity and a one-use state preview. No arbitrary helper path, executable,
port, PCM or command text is accepted in the MCP request.

`wake.listen` runs one 5–20 s window. The IO child subscribes only to `vqe_0` on
9850 and feeds the detector through a bounded RAM pipe. All children are waited
for, stdout/stderr are bounded, and only complete, validated detections from the
pinned `komp` model leave the station. Events remain `trusted:false` over authenticated
TLS. Failed child, count/geometry mismatch, policy or Quasar change discards the
window and ends the host listener without retry. No helper is uploaded or installed
by this source. The host default remains `source="PC"`.

Limitations: station mode currently returns wake events with an empty command;
there is no on-station command recognizer. Each window holds the native action
lock, so other native actions can report busy for up to 20 s. Between completed
windows the host obtains a fresh state; gaps and model resets are possible. This
is bounded source integration, not seamless continuous capture or live acceptance.
The pinned personal ARM detector and physical-mute behavior remain unqualified.

The optional resident volume mapping now also accepts `backend="alsa_ctl"`,
`card`, `control`, `numid`, `raw_min`, `raw_max`, `channels` and `max_percent`.
It requires reviewed playback-only integer metadata, 1–2 matching channels, unit
step and the exact mapped name/ID/range. It opens only the corresponding ALSA
control character node and uses typed ELEM_INFO/READ/WRITE ioctls. A fresh read
precedes one write; failed write/readback is unknown and never retried. The existing
amixer backend remains available. The read-only audio probe understands both.

ABI source: Linux [sound/asound.h](https://github.com/torvalds/linux/blob/v6.12/include/uapi/sound/asound.h).
A local C fixture checks the host kernel UAPI layout; the resident cross-builds for
ARM EABI. Neither test identifies Mini 1's actual control or proves sound/Quasar
volume synchronization. `last_user_volume` is not edited. No platformd volume IPC
shape has been established, and no real ALSA ioctl was issued overnight. External
Quasar mixer activity is not serialized by our lock; skip live volume until its
mapping and state synchronization have been qualified through read-only research.

The resident defaults missing `preauthorize_all_actions` to true, matching the host
and owner decision; explicit false remains effective. No private policy was edited.

The integrated source composes PR #4's LED metadata/guarded migration with the
wake and ALSA adapters. Its reproduced target pin is the hash in the build section
above and `agent_provision.AGENT_SHA256`; the W0 baseline pin is unchanged.
No composed resident was installed. The migrator still accepts only its reviewed
optional fields; wake artifacts and the ioctl mapping need separate qualification.
Never reuse the target pin for a different binary or install an unpinned build.
