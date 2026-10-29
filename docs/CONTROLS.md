# Experimental runtime controls

This is the bounded runtime API, not just a set of manual recipes. It is an
experimental development preview. Automated coverage is offline/synthetic and does not establish
model, firmware, electrical or acoustic compatibility. Require owner authorization
and keep Quasar enabled. Unsupported/high-risk operations remain guidance-only.

## API and approval lifecycle

The server exposes 19 tools in total; these six add runtime control to the
existing knowledge, probe and service tools. Owned jobs (`start_job`, `list_jobs`,
`cancel_job`), the generated-tone siren, presets and the guarded reboot are in
[JOBS.md](JOBS.md) (source-tested, not live-accepted):

| Tool | Boundary |
|---|---|
| `get_model_interfaces(model)` | Local source-linked LED/audio/GPIO facts and explicit unknowns; no station access. |
| `get_station_controls(station_id)` | Declared action, asset, binding and option aliases from private policy; no device probe, private paths or option values. |
| `preview_station_action(station_id, action, parameters)` | Read-only inspection over secure SSH; returns a state-bound `plan_id`, `expected_sha256` and 300-second TTL, without editing device files. |
| `apply_station_action(plan_id, expected_sha256)` | Consumes the preview, uses private preauthorization or native human form elicitation, revalidates configuration/trust/state, executes once and post-checks. |
| `run_station_action(station_id, action, parameters)` | Preview and apply in one call: fresh one-use preview, then private preauthorization or one native approval form |
| `get_capture_status(station_id, capture_id)` | Read-only live readiness/result check. Never starts, renews or stops a capture. |

`get_station_controls` is a declaration, **not verified hardware availability**.
Even a successful preview is not proof of a physical effect. The preview requires
the action's local policy gates despite being read-only. No command, path, URL,
GPIO number or register address can be supplied as an action argument.

Device actions reuse the existing service controller's preview registry, pinned
key-only SSH, trust/configuration binding, expiry checks and serialized execution.
`ALICE_MCP_ALLOW_WRITES=1` and the station's `controls.allowed_actions` must permit
the action. Native Windows device writes are unavailable; WSL uses POSIX trust
checks with suitably protected files. Service start/restart separately requires
`allow_service_actions` and a locally reviewed `S99quasar` digest.

For a station explicitly configured with `native_mcp`, supported speech actions
instead run on the resident agent over TLS, with no SSH fallback or per-call
upload. Use `run_station_action`; legacy separate preview/apply and service actions
are not implemented on that transport. Both host and device policies must allow
the action and standing preauthorization is required. See [native agent](NATIVE_AGENT.md)
for its narrower tool/action set and verification limits, and
[native typed audio and probes](#native-typed-audio-and-probes-source-tested-not-live-accepted)
for `audio.volume`, `audio.play` and the `storage`/`services`/`audio` probes.

Plans expire 300 seconds after preview creation, not after the human responds.
Use the exact returned digest; it is not a firmware hash to type manually.
The plan is consumed before elicitation, including on cancellation or unavailable
elicitation. A model-generated `approve=true` is not approval. Changed private
inventory, SSH trust metadata, firmware or relevant target state blocks the old
plan. A fresh preview is required after a known conflict or expiry. An uncertain
write is different: inspect locally first, never retry it automatically.

For an already authorized task, run diagnostics and previews without additional
chat questions. Apply itself requests the one required native confirmation.
An operator may instead delegate only `led.effect`, `audio.volume` and `audio.play`
using private `controls.preauthorized_actions` (default empty). It must be a unique
list and a subset of `allowed_actions`; this selective list excludes other actions.
The delegation lasts until removed from the private inventory. It covers the
existing bounded parameters, registered assets and private volume ceiling, not
arbitrary light, audio or file operations. Choose a ceiling and assets suitable
for unattended playback before opting in.

`get_station_controls` exposes `preauthorized_actions`; a device preview includes
`approval_required`. If false, apply can execute without native elicitation even
on a client without form support. It still consumes the exact preview and rechecks
live configuration/trust/state and expiry. Revocation or any policy change blocks
an outstanding plan. No approval token is reused.

For standing authorization of all already-permitted actions, set the boolean
`preauthorize_all_actions` on the **station entry**, alongside `controls`. Default
is `true` (owner decision, 2026-10-29), as for `allow_service_actions` and
`allow_reboot`; an explicit `false` stays false. Strings and numbers are rejected.
When true, service start/restart and all
device actions, including capture, TTS, replay and persistent edits, skip native
elicitation. `get_station_controls` reports the flag and the effective device
`preauthorized_actions`; both service and device previews report `approval_required`.
This does not enable any action, widen parameters/assets/options, waive firmware/SSH
trust checks, extend TTL, allow retries, or authorize tasks outside the owner's request.
Use only with trusted local agents. Setting it to `false` revokes standing
authorization and invalidates outstanding previews; then other actions require native
approval.
The MCP client can independently ask for permission to call a tool.

## Exact actions

All integer limits below are inclusive. Boolean substitutes, additional fields
and unapproved aliases are not valid parameters. Aliases match
`[a-z][a-z0-9-]{0,31}` and should contain no personal information.

| `action` | Exact `parameters` | Effect and boundary |
|---|---|---|
| `led.effect` | `red`, `green`, `blue`: integers 0–32; `count`: integer 1–3; `period_ms`: integer 500–1000 | Bounded low-brightness RGB effect using the exact model adapter; restores captured LED state, not simply “all off.” |
| `led.capture` | `duration_seconds`: integer 5–60 | Mini 1 only: isolated `i2c_write` trace instance, fixed LED address filter, bounded buffer/results and on-device deadline. No I2C transactions are issued. |
| `led.replay` | `capture_id`: 32 lowercase hexadecimal characters | Mini 1 only: repeat the exact recorded bytes `25 aa` once, not an arbitrary packet. Requires a completed, cleaned capture on the same firmware containing that packet. No new PWM/color/current values. |
| `led.pulse` | `capture_id`: 32 lowercase hexadecimal characters; `channel`: integer 0–2 | Mini 1 only: one second at fixed PWM 16/255 on one observed triplet position, then return to the captured idle zero frame. Requires same-boot recent PWM/update evidence; no arbitrary level, duration, register or RGB mapping. |
| `led.animate` | `capture_id`: 32 lowercase hexadecimal characters; `mode`: `blink`, `rainbow`, `rainbow_swirl` | Mini 1 only: fixed low-brightness show for 3–6 s nominal, then the captured idle zero frame. No raw frame input, arbitrary level, duration, loops or daemon. |
| `led.show` | `effect`: `rainbow`, `breathe`, `spin`, `spin_color`, `static`, `off`, `notify`, `police`, `calibrate`; `duration_seconds` 1–15; optional `speed` 1–5; `red`/`green`/`blue` 0–16 for colour effects only; `channel` for `calibrate` only; `capture_id` on Mini 1 only | Mini 1, Lite, Mini 3: bounded reference-effect equivalents at ≤16/255, ≤2.5 flashes/s, hard deadline, then restore (Lite/Mini 3 yield to a firmware write instead). Private `led_map` calibrates channels. **Source-tested, not live-accepted**; see [LED parity](LED_PARITY.md). |
| `audio.volume` | `percent`: integer 0–private `max_volume` (default 30) | Sets a probed `Master` control and preserves a restore record; not proof of room loudness or every model's mixer layout. |
| `audio.play` | `asset`: approved media alias; `duration_seconds`: integer 1–10 | Bounded playback of the approved asset; no supplied path/stream URL, microphone recording or Quasar stop. |
| `media.upload` | `asset`: approved media alias; `content_base64`: encoded content, decoded size at most 49,152 bytes | Decoded SHA-256 must match the asset's preregistered policy hash. This limit deliberately bounds SSH arguments; it is not a streaming upload API. |
| `sound.replace` | `asset`: approved media alias; `slot`: `spotter_start`, `spotter_cancel`, `volume_sound` or `notification_sound` | Backs up and replaces only that fixed system-sound slot. Reload and audible effect remain unverified. |
| `config.select` | `binding`: approved config alias; `option`: one of that binding's approved option aliases | Changes an existing known JSON leaf to its private operator-approved value. No supplied URL/value, new field, backend installation or automatic reload. |
| `restore` | `backup_id`: 32 lowercase hexadecimal characters | Rechecks the recorded post-edit state before restoring one exact runtime backup. Optimistic check, not atomic CAS. Not factory recovery or a broad reset. |

For example, after local opt-in, a read-only preview request may be:

```json
{
  "station_id": "desk",
  "action": "led.effect",
  "parameters": {"red": 0, "green": 8, "blue": 0, "count": 1, "period_ms": 500}
}
```

Do not apply this merely because an example exists. Confirm the model, reviewed
firmware, preview impact and owner consent, then pass only the returned plan and
digest to `apply_station_action`. Media must be owner-held or appropriately licensed;
do not upload private recordings, copied branded chimes or unreviewed loud clips.
The complete bundled helper input (source plus encoded policy/media) is bounded to
120 KiB and sent through SSH stdin, not a long SSH exec argument. Requests near the media ceiling, especially with larger policies, may
require a smaller approved clip. Oversized requests fail before SSH, not by
bypassing the bound or splitting an unapproved write into retries.

## Private JSON policy

Extend a station's existing private inventory entry with a `controls` object.
Keep that inventory outside the checkout, with owner-only permissions. This is
**JSON only**, not TOML, commented JSON or an arbitrary executable configuration.
Do not paste the resulting inventory, private URLs or keys into chat or issues.

| Field | Meaning |
|---|---|
| `firmware_sha256` | Locally reviewed SHA-256 of `/system/vendor/quasar/maind`, as 64 lowercase hexadecimal characters; binds actions to that exact firmware binary. Not a hash copied blindly from a public example. |
| `allowed_actions` | `"all"` (the default when absent, per the owner decision of 2026-10-29) allows every implemented action, including ones added later. An explicit list allows exactly those names. Unknown names are rejected; an alias never grants a permission. The resident agent expands `"all"` to the implemented actions whose local prerequisites are configured. |
| `preauthorized_actions` | Optional unique subset of `allowed_actions`, or `"all"`, limited to `led.effect`, `audio.volume` and `audio.play`; permits apply without native elicitation. Default empty. Does not enable writes or bypass any preview, parameter, firmware, trust or state check. |
| `led_map` | Optional Mini 1/Lite/Mini 3 calibration for `led.show`: `channels` (per physical channel `[zone, colour]` or `null`) and `gain_percent` (0–100 per colour). Produced by the owner-run calibration runner; see [LED parity](LED_PARITY.md). |
| `max_volume` | Private volume ceiling; default 30 percent. Use a conservative ceiling suitable for the owner and room. |
| `allow_rootfs_remount` | Boolean, default `false`; explicit per-station permission for guarded temporary rootfs read-write access. |
| `media` | Object mapping approved asset aliases to `{"path": ..., "sha256": ...}`. Paths must be `/data/sounds/<safe-name>.opus`, `.wav` or `.mp3`; SHA-256 pins the exact approved content. |
| `configs` | Object mapping config binding aliases to the fields below. Only aliases and kinds/options, not paths or private values, are exposed by MCP. |

Each `configs` binding declares:

- `path`: `/system/vendor/quasar/quasar.cfg` **or** an explicitly reviewed,
  nonsensitive `.json`/`.cfg` file under `/data/opt/`.
- `pointer`: an array of existing JSON leaf keys, not a shell expression or
  permission to invent a schema. Files must contain JSON, including `.cfg` files.
- `kind`: `endpoint`, `voice` or `model`.
- `choices`: public option aliases mapped to private string values.
- `allow_insecure`: boolean, default `false`; applies to endpoint choices.

Native `quasar.cfg` supports only the `uniProxyUrl` and `uniProxyQuicUrl` endpoint
bindings. There is no arbitrary stock Alice voice replacement. `voice`/`model`
selection applies only to explicit operator bindings in existing, reviewed
adapter JSON; these are not manufacturer-certified schemas or an installed backend.
Endpoint choices use HTTPS/WSS by default. HTTP/WS requires explicit
`allow_insecure=true`; credentials, query strings and fragments are forbidden
regardless. Keep option values, private endpoints and API keys outside MCP output.

## Runtime prerequisites and model evidence

Except for Mini 3 `led.effect` and Mini 1 capture/replay/pulse/animate, the fixed bundled helper executes over pinned-host-key,
key-only SSH with preinstalled **Python 3.8+**, trying only these executable locations:

- `/data/opt/bin/python3`
- `/opt/bin/python3`
- `/data/opt/entware/bin/python3`
- `/usr/bin/python3`

It never installs an interpreter, packages or drivers, roots a station, enables
ADB, runs a downloaded script or falls back to unrestricted SSH. Python version,
exact firmware hash, known paths, driver format, geometry/stride and applicable
LED triggers must pass runtime checks; missing or unknown interfaces fail closed.

Mini 3 `led.effect` instead uses fixed bundled shell code through the same bounded
SSH stdin transport. It requires stock `dd` (including `ibs`, `obs`, `conv=notrunc`),
`base64 -d`, fractional `sleep`, `flock` and core utilities. Read-only preview checks
`yandex,bergamot`, the firmware digest, 6×1/24-bpp/18-byte geometry and an exact
18-byte readable snapshot. State binds the normalized request, helper source,
firmware, boot and snapshot; apply rechecks under the shared control lock.

Only six repeated BGR triples are written, within the existing RGB/timing limits.
Each cycle returns to the snapshot, and an exit/signal trap attempts a final restore
and bounded readback. A failed write or restore is `unknown`, never automatically
retried. The snapshot is in memory: power loss, SIGKILL or a stuck kernel operation
can prevent cleanup. This is not a detached watchdog or a durable LED backup.
Quasar remains running; it can overwrite the effect or race restoration. Successful
readback is not proof that any color was visible. This backend has synthetic tests,
not physical-device qualification, and is not used for Mini 3 Pro or Station 2.

The following are public community reports at revision
`1ab048bf95c0e5e547a1ff6c801d2ed9deb193db`, not tested hardware availability:

| Retail model | Source-backed LED interface | Runtime qualification limit |
|---|---|---|
| [Lite](https://hackalice.me/devices/station-lite/) | Sysfs `micro:{red,green,blue}:ring1` through `ring3` brightness nodes. | Exact nodes/triggers and reviewed firmware required; do not infer Lite 2 support. |
| [Mini 2](https://hackalice.me/devices/station-mini2/) | Sysfs `mini2:{red,green,blue}:ring1` through `ring6`. | Ring only; clock and the six-zone animation-file interpretation are not established by this adapter. |
| [Mini 3 / Bergamot](https://hackalice.me/devices/station-mini3/) | Framebuffer 6×1, 24-bpp BGR, 18-byte frame. | Verify geometry and stride; do not substitute Mini 3 Pro/Pickle. |
| [Station 2](https://hackalice.me/devices/station-2/) | Framebuffer 89×1, 32-bpp RGBW, 356-byte frame. | Index 88 is not visible; do not substitute retail Midi. |
| [Mini 1](https://hackalice.me/devices/station-mini/) | I2C ring; RGB/physical zone maps uncalibrated. | Arbitrary RGB effects unsupported; fixed replay/pulse/animate profiles require their own permissions and evidence. |
| [Midi / Cucumber](https://hackalice.me/devices/station-midi/) | Ring/logo framebuffers; channel order unknown. | Unsupported LED writes; do not guess frame order. |

Other model names or kernel interfaces do not establish a compatible write
adapter. A complete GPIO pinout is unavailable: Lite reports AO 497–511 and
EE 411–496 ranges plus named consumers, not verified safe output mappings.
Midi names `zb-reset-gpios`/`zb-boot-gpios` without line mappings. No arbitrary
GPIO, I2C, memory or register controls are exposed.

Volume requires runtime probing of `Master` on operator-reviewed Buildroot.
The public exact mixer command is documented only for Lite, not all stations.
Likewise the native maind/GStreamer playback recipe does not establish every
firmware's plugins, codec support, output route or speaker availability. Preserve
unknowns; never replace missing support with package installation or Quasar shutdown.

## Mini 1: isolated LED capture, not LED control

`led.capture` observes writes already issued by the firmware. It does not send
I2C requests, discover addresses, record audio, stop Quasar, load a module or
mount tracefs/debugfs. General Mini 1 RGB control remains unsupported: a capture is not
proof of a safe command protocol, channel order or brightness limit.
The fixed sysfs client name at `0-003c` must identify `sn3236`. A bound kernel
driver is not required for passive observation. Unconfirmed `1-001f` traffic is
excluded; declaring the model or finding an event does not establish LED scope.

Required: preinstalled shell/core utilities, `flock` and `setsid`, the exact reviewed firmware digest, global
write opt-in and per-station `controls.allowed_actions` containing `led.capture`.
Enabling trace events changes kernel state, so discovery permissions alone are
not enough. Preview checks metadata without creating files or an instance.
Apply uses the existing consumed-preview/native-approval/fresh-state lifecycle.
No private configuration or write gate is enabled merely by discovery. Local
opt-in must be explicit; actual trace writes still need the native approval.
The fixed shell helper needs neither Python nor `stat`/`timeout`; it is passed
through bounded SSH stdin, not installed as a script or downloaded from an external
project. The SSH exec command stays short for station SSH servers with small
command limits. A missing `/data/opt` is created owner-only only after approval;
preview merely verifies its existing parent and never creates it.

The fixed filter selects only `i2c:i2c_write` at `(adapter_nr=0, addr=0x3c)`,
with at most 256 payload bytes. Unknown event fields,
missing mounted tracing interfaces or unavailable independent instances fail
closed. No fallback to global tracing or arbitrary I2C/SMBus capture is provided.
The helper creates a fresh `alice-led-<opaque-id>` instance, uses the `nop`
tracer and a 16-KiB-per-CPU buffer (at most 16 CPUs). Only controls inside its
own instance are written; existing global/other-instance settings are untouched.

The detached on-device worker starts a separate watchdog before enabling events, so the
5–60-second limit does not depend on host polling or the SSH connection staying
open. Normal expiry, handled signals and initialization/read errors trigger
cleanup of only that instance. Both processes retain the existing control lock
to reject overlapping cooperating actions. The watchdog also collects and removes
the own instance after worker death. Losing both processes, kernel failure, lost
power or failed cleanup can still be unknown; inspect locally and never
automatically start another capture.

Apply returns an opaque `capture_id` when available. Query
`get_capture_status(station_id, capture_id)` before giving any speech cue.
Only a fresh `outcome="capturing"` and `capture_ready=true` confirms an active
worker and watchdog, unexpired window, matching instance inode, fixed filter, event enable
and tracing-on state. `remaining_seconds` is conservative and accounts for SSH
round-trip time. Discovery, preview `outcome="ready"`, an old status record or a
successful fork is **not** a running-capture confirmation. If startup/SSH is
uncertain, `capture_candidate_id` is only a possible result id to inspect, not a
claim of success. Status never renews the window or performs cleanup writes.

After stopping, at most 1 MiB plus a truncation-detection byte of the **own** trace is read on the station. A
second address/format check emits at most 64 structured `{bus, address, data_hex}`
records, each at most 256 bytes; process names, raw trace lines and non-LED data
never leave the helper. `truncated` reports explicit read/record limits and observed
ring overruns; empty results or a missing overrun flag do not prove lossless traffic.
Owner-only results remain under `/data/opt/alice-mcp/led-captures/`; at most 16
result directories are retained, then further starts fail until the operator archives or
removes old results. No captures belong in this Git repository or release pack.
Results remain readable with global writes off if the firmware and private
per-station policy still match. `cleanup_verified=true` verifies removal of the
own instance, not successful LED protocol identification or a visible effect.

Implementation evidence: [kernel trace instances](https://docs.kernel.org/trace/ftrace.html#instances),
[event filters](https://docs.kernel.org/trace/events.html#event-filtering), and
[Mini 1's documented peripherals](https://hackalice.me/devices/station-mini/).
Runtime field checks, not a kernel version guess, determine compatibility.
Tests use synthetic tracefs files and an offline detached worker; no station
capture, speech interaction or physical LED effect was tested.

## Mini 1: one recorded PWM update

The shell capture retains the latest 64 validated LED writes in order, not the
first 64; `truncated=true` still discloses discarded records or trace loss. The
ending sample is useful for investigating the firmware return-to-idle sequence,
not proof of a complete transaction history or a readable current PWM snapshot.
Private capture metadata also records the boot digest and completion uptime.

`led.replay` is not a general I2C replay interface. Its only possible write is
`i2cset -y 0 0x3c 0x25 0xaa b`, exactly once, without `-f`, mask, readback, a loop,
driver unbind, tracing changes or Quasar interruption. No bus, address, register,
payload, count or executable can be supplied. Other captured packets are not replayable.

The [Si-En SN3236 register map](https://uploadcdn.oneyac.com/attachments/files/brand_pdf/si-en/7A/B3/SI-EN-SN3236.pdf)
identifies `25h` as the PWM/LED-control update register. This profile only applies
already-buffered firmware values: it does not choose a new color or change current
limits, and no visible change is guaranteed. Register-map evidence does not certify
the full Mini 1 color protocol, another chip variant or another station model.

The firmware, client name, lack of a bound kernel driver, private capture directory,
completed/cleaned capture and exact `record=25aa` line must pass read-only preview.
The capture id and bounded records-file digest join the state hash. Apply reacquires
the shared control lock and rechecks state before the single write. Changed evidence
causes conflict, not replay of a new packet. The usual per-action opt-in, consumed
300-second preview and native human form are required; capturing alone does not
authorize replay. A nonzero exit or interrupted SSH is unknown and never retried.
There is no file rollback for this update; `command_succeeded` reports utility exit,
not physical I2C verification, RGB order or a visible effect.

## Mini 1: bounded observed-channel pulse

`led.pulse` requires its own private action permission and native approval.
The completed capture must match the reviewed firmware and current boot, have
verified cleanup and a completion uptime within 30 minutes. At most 64 retained
records must alternate exact `01` plus 18-byte frames and `25 aa` updates. Each
frame must contain six identical triples, with some nonzero traffic and a final
all-zero frame/update pair. Unknown formats, other registers, changed records,
active captures, a bound kernel driver or changed boot fail closed. The records,
channel, boot and completion metadata bind the preview; apply rechecks under the
shared lock. An older truncated sample alone is not a complete protocol description.

The helper requires stock `i2cset` advertising I2C block mode. It writes only
`0x01` plus 18 bytes on bus 0/address `0x3c`, then update `0x25=0xaa`. Exactly six
positions at the selected triplet offset get `0x10`; all other observed PWM
positions get zero. After one second it sends the captured all-zero frame/update.
It does not write initialization, current, calibration or shutdown registers,
force a driver, read I2C registers, stop Quasar or repeatedly fight its renderer.
Channel 0–2 is an observed byte offset, **not a verified red/green/blue name**.

Approve only while the ring is idle/dark and keep it idle during the test. This
is a last-observed idle frame, **not a readable current hardware snapshot**.
`restoration_command_succeeded` describes only utility exits;
`restoration_verified`, `effect_observed` and physical verification remain false.
Normal exit and handled signals attempt the idle frame once. A failed effect or
restore is unknown, not automatically retried. Power loss, SIGKILL or a stuck
kernel call can prevent cleanup; no detached recovery guarantee is made.
Owner observation is required to establish light/color and normal status behavior.

## Mini 1: fixed animation presets

`led.animate` reuses the pulse profile's firmware/boot/30-minute capture checks,
observed zero-frame requirement, shared lock, one-use preview, native approval
and final idle-frame attempt. Capture or pulse permission does not authorize
animation. `get_station_controls().led_modes` advertises names only, not availability.

All frames contain exactly 18 PWM bytes with values only zero or 16. Only the
same PWM bank and update register are written. The host builds fixed frames from
the selected alias; the tool cannot accept a frame array, executable, bus, address,
brightness, speed or indefinite loop. The helper checks frame count, length and
PWM bounds before writing, binds the complete frame sequence and mode into the
state digest, and stops on the first failed command without replaying the scene.

| Preset | Fixed sequence and nominal timing |
|---|---|
| `blink` | Six whole-ring logical colors alternating with idle, 0.5 s per frame; 12 frames / 6 s. |
| `rainbow` | One six-color spatial frame, held for 3 s. |
| `rainbow_swirl` | Six-color spatial palette shifted one logical zone per 0.5 s, twice around the logical map; 12 frames / 6 s. |

These are logical channel/zone patterns: RGB names, physical adjacency and swirl
direction are not verified. Synthetic frame/transport tests are not visual playback
evidence. Kernel/utility delays can lengthen nominal timing; SIGKILL, power loss
or blocked kernel I/O can prevent cleanup. Quasar is never stopped or forced out
of ownership; it can replace the display, and its status indication can be disrupted
by the bounded show. Keep the station idle and never automatically repeat a scene
to overpower the renderer. Only the owner can verify appearance and return to idle.
`restoration_verified` and `effect_observed` remain false even on successful utility
exits. No resident process, persistent animation, factory pattern replacement or
native firmware voice-command integration is installed.

## Backup, restoration and read-only rootfs

Before persistent edits, the helper creates owner-only backup records under
`/data/opt/alice-mcp/backups/<id>.json`. Records stay on the station and may contain
private original values; MCP returns only the opaque `backup_id`. Transient
effects restore their captured state. Persistent volume, media, sound and config
changes use the record-specific `restore` action with its own preview and approval.

Restore refuses target changes observed by its hash/state checks. A created file
can be deleted only by its own restore
record. Do not substitute broad deletion, full configuration reset or whole-device
rollback when a record conflicts. An owner-managed independent recovery path is
still required; these local records are not off-device backups.

These are **optimistic checks, not atomic compare-and-swap**. The helper's `flock`
serializes cooperating helpers, not firmware, editors or other mixer controls.
An external update after the final comparison can be overwritten or deleted by
replace, restore deletion or mixer mutation, even with a successful post-check.
Keep other target writers idle throughout apply/restore; if that cannot be
ensured, do not apply. Never stop Quasar to obtain exclusivity. Hard protection
needs a transactional interface or a lock honored by every writer; neither is
provided here. Synthetic race-characterization tests explicitly demonstrate this
limitation, not its resolution.

Read-only rootfs edits fail by default. Explicit per-station
`allow_rootfs_remount=true` allows guarded temporary read-write access followed
by restoration of the original read-only mount state. This does not make a rootfs
edit persistent across reboot or OTA. Failures can leave an unknown result;
inspect locally, including mount state, before any new action. Never auto-retry.

Quasar stays enabled throughout. Bounded playback may stop only the process group
started by the helper, never `killall maind` or the firmware renderer. Config and
sound edits do not automatically reload. If the owner explicitly requests service
restart, use the separate service preview/approval flow and disclose interruption;
a restart still does not prove that a particular config was loaded.

Playback passes an unlinked owner-only snapshot of the hash-verified bytes through
an inherited descriptor at `/proc/self/fd/<fd>`, not the mutable asset path. This
requires procfs and a native player that preserves the descriptor; compatibility
is unverified on real firmware. No fallback to an unchecked path is provided.

## Native typed audio and probes (source-tested, not live-accepted)

The resident agent adds typed equivalents of SSH behavior without new tools: the
same five native tools gain `audio.volume`/`audio.play` actions and `storage`,
`services` and `audio` probes. They use the existing native contract: host **and**
resident private policies must allow the action, standing preauthorization, a
fresh 300-second preview bound to station/trust/configuration and resident state,
`expected_state_sha256`, one consumption, serialized execution and no retry or
SSH fallback. Older residents reject the new policy fields and fail closed.

**Volume.** Parameters are `{"percent": N}` (0 to host `max_volume`) or
`{"step": "quieter" | "louder"}`. The resident needs a private, operator-reviewed
mapping; without it `audio.volume` cannot be allowed:

```json
"volume": {"amixer": "/PRIVATE/amixer", "amixer_sha256": "REVIEWED_SHA256",
           "card": 0, "control": "Master", "max_percent": 30}
```

The pinned `amixer` is executed from its verified descriptor with the SSH helper's
environment. Only one exact simple control whose capabilities are playback-only
(`pvolume`, optional `pvolume-joined`/`pswitch`) is accepted; any capture/joined
capability, capture line, unequal channels or ambiguous output fails closed, so
microphone gain is never touched. Steps are 10 points; `louder` never exceeds
`min(host max_volume, resident max_percent)`, `quieter` never raises the level and
an absolute set above the resident ceiling is refused. The prior and read-back
values are returned as `previous_percent`/`percent`; restore by setting
`previous_percent` with another bounded action. A failed or mismatched read-back
is `unknown`/`do_not_retry`. No resident restore record is written. The Mini 1
mixer mapping has **not** been reviewed: whether `Master` exists, whether
`amixer` and its ALSA configuration run with the vendor library, and whether
Quasar's output follows this mixer are all unknown.

**Registered media.** `{"asset": ALIAS, "duration_seconds": 1-10}` plays only a
host `controls.local_media` entry (native stations only):

```json
"local_media": {"chime": {"file": "/PRIVATE/chime.wav", "sha256": "REVIEWED_SHA256"}}
```

The host re-reads the file at preview and dispatch, requires the exact SHA-256,
canonical mono S16 16–48 kHz WAV with peak amplitude at most 8191, and a length
within `duration_seconds`. It sends that WAV over the existing local-PCM TLS path;
the resident additionally requires its own `"media": {"chime": "REVIEWED_SHA256"}`
registration, the `local_pcm` player configuration and Mini 1, then plays it once
through the pinned `pcm-player`. No station file, URL, executable upload, mixer
change or Quasar stop is involved. Results reuse the local-PCM correlation checks.

**Probes.** `storage` (statfs of `/data`: `total_kib`, `used_kib`, `free_kib`),
`services` (booleans for `maind`, `quasar_launcher2`, `dropbear`, `shairport-sync`,
`updatesd` and `agent`, from 15-byte kernel process names; presence, not health
or OTA state) and `audio` (`sound_devices`, `volume_control`
`ready|unconfigured|unavailable|busy`, `volume_percent` from the reviewed mapping
only). `health` keeps memory/uptime. Raw process, mixer and filesystem output never
leaves the resident.

Owner-run acceptance, read-only unless `--live` (a terminal and an exact word are
required; one write path each, no retry). Run in order with the owner beside the
Station after a resident containing this source is deployed:

```sh
python -m alice_mcp.native_acceptance health --station ALIAS
python -m alice_mcp.native_acceptance volume --station ALIAS --live
python -m alice_mcp.native_acceptance media --station ALIAS --asset ALIAS --seconds 3 --live
```

`volume` refuses unless the current level is readable and within the host ceiling,
asks for `VOLUME`, lowers one step, asks `QUIETER`/`SAME` about Alice's next reply,
then restores the prior value once (also after an interrupted answer, never after an
`unknown` step). `media` asks for `PLAY`, plays once and asks `HEARD`/`NOTHING`.
Tests use synthetic peers and a synthetic `amixer`; nothing here was run on a Station.

## Verification and limitations

Runtime responses are bounded structured status, not raw logs, file contents,
paths, private option values or key material. File/control post-checks are
analytical evidence, **not proof of visible/audible hardware effects**.
`effect_observed` and `physical_effects_verified` are false. Even
`restoration_verified=true` is a state check, not an observation of the room.
Have the owner separately confirm colour, low volume, correct route, normal
listening/mute/timer indications and required backend functions.

An SSH timeout or failed cleanup may occur after a write. `unknown` and
`do_not_retry` mean inspect locally before another action, not “try again.”
For uncertain persistent writes, `backup_candidate_id` identifies where a record
may exist; `backup_record_verified=false` means it was not verified. It is not a
confirmed backup or permission to restore without a new inspection and approval.
Keep policy declarations, public documentation, synthetic tests, runtime checks
and owner-observed effects distinct. Offline integration checks and code review do
not qualify live device compatibility, native Windows writes or universal
voice/backend support.

Sources: [LED recipe](https://hackalice.me/recipes/led/),
[sounds recipe](https://hackalice.me/recipes/sounds/),
[LLM alternatives](https://hackalice.me/recipes/llm/), and the model pages above.
See [source provenance](SOURCES.md) for the pinned evidence and its limits.
