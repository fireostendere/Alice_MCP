---
name: station-led
description: Inspect model-specific LED evidence and preview/apply a policy-bound reversible low-brightness runtime effect; triggers include подсветка Алисы, LED кольцо, анимация Станции.
---

# Match the driver before choosing an effect

Require owner authorization for visible effects. Keep Quasar enabled. The experimental
`led.effect` runtime implements bounded effects on known interfaces; unsupported LED
writes, animation replacement and installation remain guidance-only. Do not stop the
firmware renderer just because it overwrites a test frame. Avoid intense patterns.

## Prerequisites

Identify exact retail model, firmware and desired region: ring, logo, clock or panel.
Read [model evidence](../station-devices/references/models.md). Existing authorized
access must be secured; do not enable root/ADB as an implicit LED prerequisite.
Use pinned-host-key, key-only SSH, preinstalled Python 3.8+, the global write opt-in,
a private `led.effect` allowlist entry and the locally reviewed `maind` firmware
SHA-256. No automatic installation or native Windows device writes are available.

## Diagnosis

1. Call `get_model_interfaces(model)` for source evidence and
   `get_station_controls(station_id)` for declared policy aliases. Neither verifies
   hardware availability. Lite uses `micro:{red,green,blue}:ring1` through `ring3`
   brightness nodes; Mini 2 uses `mini2:{red,green,blue}:ring1` through `ring6`.
   This effect does not control Mini 2 clock segments. Do not infer Lite 2 support.
2. Mini 3/Bergamot uses a six-by-one, 24-bpp **BGR**, 18-byte framebuffer. Its related sysfs
   nodes can reflect framebuffer state rather than independently drive it.
3. Station 2 uses an **RGBW**, 32-bpp, 356-byte frame with 89 logical positions and
   88 visible LEDs: index 88 is not visible. Do not reuse Bergamot frame length or channel order.
4. Midi has ring/logo framebuffers and separate clock segments, but their byte
   ordering is not established by its page. Pickle, Mango and Saturn have different
   zone layouts and have no inferred adapter here. Mini 1's I2C ring protocol is
   unknown, not the Lite interface. Mini 1 and Midi LED writes are unsupported.
5. An effect disappearing when Alice speaks can be normal ownership contention,
   not a reason for raw I2C, direct register writes or Quasar shutdown.

## Preview and confirmation

Call `preview_station_action(station_id, "led.effect", parameters)` with exactly
`red`, `green`, `blue` (integers 0–32), `count` (integer 1–3) and `period_ms`
(integer 500–1000). This is read-only runtime inspection, not a write. It verifies
the exact firmware and applicable nodes, geometry/stride and triggers; unknown
formats fail closed. No shell, path, GPIO or I2C payload argument is accepted.

Describe the preview's region, low brightness, bounded timing and captured-state
restoration. Confirm the owner accepts temporary interference with status indication.
Only then use the returned `plan_id` and `expected_sha256` with
`apply_station_action`. Native human elicitation is required; a model cannot
approve. The preview expires after 300 seconds and is single-use even if cancelled.
Changed trust, private configuration or target state requires a fresh preview.

## Backup and rollback

The runtime captures and restores relevant LED state for the bounded effect;
“all off” is not restoration. Do not overwrite factory colour calibration or
manually fight another renderer. An interrupted action or cleanup can be unknown:
inspect locally, never auto-retry. Persistent animation replacement is not this
action and requires separate owner-managed backup and confirmation.
The helper lock does not lock the firmware renderer. Captured-state restoration
can race its updates; do not fight it or stop Quasar to obtain exclusivity.

## Verification

The owner checks region, colour order, brightness and duration, then normal listening,
mute and timer indications. If the firmware reclaims the display, report that
limitation instead of repeatedly fighting it. Keep the user's other display regions
unchanged and verify Quasar remains operational. File/control post-checks are not
proof of visible effects: `effect_observed` and `physical_effects_verified` remain
false. A state restoration check is not a human observation of the display.

## Limitations

Mini 2's six-zone .led interpretation remains explicitly unverified. Lite's textual
format is not a universal animation schema. Public evidence is pinned to revision
`1ab048bf95c0e5e547a1ff6c801d2ed9deb193db`, not device qualification. No station
access or hardware test was performed; integration checks are offline/synthetic. No
arbitrary GPIO/I2C/memory/register controls, copied scripts or downloaded RGB
installer are provided or executed here.

Sources: [LED recipe](https://hackalice.me/recipes/led/),
[Lite format](https://hackalice.me/devices/station-lite/),
[Mini 2 caveat](https://hackalice.me/devices/station-mini2/),
[Bergamot framebuffer](https://hackalice.me/devices/station-mini3/),
[Station 2 panel](https://hackalice.me/devices/station-2/),
[Mini 1 unknown protocol](https://hackalice.me/devices/station-mini/),
[Midi format unknowns](https://hackalice.me/devices/station-midi/).
