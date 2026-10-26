# Experimental runtime controls

This is the bounded runtime API, not just a set of manual recipes. It is an
experimental development preview: no station access or hardware test was performed.
Offline/synthetic checks do not establish
model, firmware, electrical or acoustic compatibility. Require owner authorization
and keep Quasar enabled. Unsupported/high-risk operations remain guidance-only.

## API and approval lifecycle

The server exposes 13 tools in total; these four add runtime control to the
existing knowledge, probe and service tools:

| Tool | Boundary |
|---|---|
| `get_model_interfaces(model)` | Local source-linked LED/audio/GPIO facts and explicit unknowns; no station access. |
| `get_station_controls(station_id)` | Declared action, asset, binding and option aliases from private policy; no device probe, private paths or option values. |
| `preview_station_action(station_id, action, parameters)` | Read-only inspection over secure SSH; returns a state-bound `plan_id`, `expected_sha256` and 300-second TTL, without editing device files. |
| `apply_station_action(plan_id, expected_sha256)` | Consumes the preview, requests native human form elicitation, revalidates configuration/trust/state, executes once and post-checks. |

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

Plans expire 300 seconds after preview creation, not after the human responds.
Use the exact returned digest; it is not a firmware hash to type manually.
The plan is consumed before elicitation, including on cancellation or unavailable
elicitation. A model-generated `approve=true` is not approval. Changed private
inventory, SSH trust metadata, firmware or relevant target state blocks the old
plan. A fresh preview is required after a known conflict or expiry. An uncertain
write is different: inspect locally first, never retry it automatically.

## Exact actions

All integer limits below are inclusive. Boolean substitutes, additional fields
and unapproved aliases are not valid parameters. Aliases match
`[a-z][a-z0-9-]{0,31}` and should contain no personal information.

| `action` | Exact `parameters` | Effect and boundary |
|---|---|---|
| `led.effect` | `red`, `green`, `blue`: integers 0–32; `count`: integer 1–3; `period_ms`: integer 500–1000 | Bounded low-brightness RGB effect using the exact model adapter; restores captured LED state, not simply “all off.” |
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
The complete SSH command (helper plus encoded policy/media) is also bounded to
120 KiB; requests near the media ceiling, especially with larger policies, may
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
| `allowed_actions` | Explicit subset of the seven action names above; possession of an alias never grants action permission. Include `restore` if runtime recovery is to be available. |
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

The fixed bundled helper executes over pinned-host-key, key-only SSH with
preinstalled **Python 3.8+**, trying only these executable locations:

- `/data/opt/bin/python3`
- `/opt/bin/python3`
- `/data/opt/entware/bin/python3`
- `/usr/bin/python3`

It never installs an interpreter, packages or drivers, roots a station, enables
ADB, runs a downloaded script or falls back to unrestricted SSH. Python version,
exact firmware hash, known paths, driver format, geometry/stride and applicable
LED triggers must pass runtime checks; missing or unknown interfaces fail closed.

The following are public community reports at revision
`1ab048bf95c0e5e547a1ff6c801d2ed9deb193db`, not tested hardware availability:

| Retail model | Source-backed LED interface | Runtime qualification limit |
|---|---|---|
| [Lite](https://hackalice.me/devices/station-lite/) | Sysfs `micro:{red,green,blue}:ring1` through `ring3` brightness nodes. | Exact nodes/triggers and reviewed firmware required; do not infer Lite 2 support. |
| [Mini 2](https://hackalice.me/devices/station-mini2/) | Sysfs `mini2:{red,green,blue}:ring1` through `ring6`. | Ring only; clock and the six-zone animation-file interpretation are not established by this adapter. |
| [Mini 3 / Bergamot](https://hackalice.me/devices/station-mini3/) | Framebuffer 6×1, 24-bpp BGR, 18-byte frame. | Verify geometry and stride; do not substitute Mini 3 Pro/Pickle. |
| [Station 2](https://hackalice.me/devices/station-2/) | Framebuffer 89×1, 32-bpp RGBW, 356-byte frame. | Index 88 is not visible; do not substitute retail Midi. |
| [Mini 1](https://hackalice.me/devices/station-mini/) | I2C ring; command protocol unknown. | Unsupported LED writes; never invent I2C payloads. |
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
