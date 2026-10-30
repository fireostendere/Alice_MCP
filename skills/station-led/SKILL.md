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
Use pinned-host-key, key-only SSH, the global write opt-in,
a private `led.effect` allowlist entry and the locally reviewed `maind` firmware
SHA-256. Mini 3 uses stock shell utilities (`dd`, `base64`, `flock`, fractional
`sleep`); other effect adapters require preinstalled Python 3.8+. No automatic
installation or native Windows device writes are available.

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
   not the Lite interface. Mini 1 general RGB control and Midi LED writes are unsupported;
   Mini 1 has only the separately gated fixed pulse/animation profiles described below.
5. An effect disappearing when Alice speaks can be normal ownership contention,
   not a reason for raw I2C, direct register writes or Quasar shutdown.

## Preview and confirmation

For an owner-requested action, call `run_station_action(station_id, action, parameters)`: it previews and applies in one call
(no form when preauthorized). Use the separate preview/apply tools only to show a plan before acting.

Call `preview_station_action(station_id, "led.effect", parameters)` with exactly
`red`, `green`, `blue` (integers 0–32), `count` (integer 1–3) and `period_ms`
(integer 500–1000). This is read-only runtime inspection, not a write. It verifies
the exact firmware and applicable nodes, geometry/stride and triggers; unknown
formats fail closed. No shell, path, GPIO or I2C payload argument is accepted.

Run read-only diagnostics and previews within an authorized task without asking
again. Describe the preview's region, low brightness, bounded timing, captured-state
restoration and interference with status indication, then call `apply_station_action`
with the returned `plan_id` and `expected_sha256`. Apply asks for native confirmation;
do not add a separate chat approval question. When the preview reports
`approval_required=false`, the owner has explicitly preauthorized this `led.effect`
in private policy and no native confirmation is needed. A model cannot grant this
permission. The preview expires after 300 seconds and is single-use even if cancelled.
Changed trust, private configuration or target state requires a fresh preview.

## Backup and rollback

### Mini 1 protocol research: bounded capture

Discovery (`station_probe(..., "i2c_trace")`) only inspects metadata. It never
means capture is running. Mini 1 supports the separate experimental
`led.capture` action, not `led.effect`: preview with `{"duration_seconds": 10}`
(allowed 5–60), then apply through native approval unless the preview reports
`approval_required=false` under explicit station-level `preauthorize_all_actions=true`.
This same standing-authorization exception applies to replay, pulse and animation below.
Global write
opt-in, private `led.capture` permission and reviewed firmware are required.
Do not enable these gates merely because discovery succeeded.

The fixed shell helper traces only existing SN3236 LED writes at bus 0/address
0x3c in a new isolated instance, after checking the fixed I2C client name.
Unverified 1-0x1f traffic is excluded. It needs no Python, `stat` or `timeout`,
issues no I2C writes, changes no global tracing and leaves Quasar running. Its
separate watchdog auto-stops within the approved window and removes its own
instance even after worker death. Read a fresh
`get_capture_status(station_id, capture_id)`: **only** `outcome="capturing"`,
`capture_ready=true` and positive `remaining_seconds` permit a speech cue such as
«можно говорить». Preview `ready`, saved state or successful fork is not enough.
Transport delay, expired windows, changed filters and dead workers must not be
presented as ready. Never ask the owner to speak before the actual status check.

After completion, use only bounded structured LED records, not raw traces or
audio. Keep device-side private results out of Git and reports. A killed worker
and watchdog together, or failed cleanup, can be unknown; inspect before another
start, never auto-retry.
An empty trace is not evidence of absent traffic, and captured bytes are not
permission to replay them. A separately authorized `led.replay` action accepts
only `{"capture_id": "<opaque id>"}` and repeats the recorded `25 aa` PWM-update
packet once. It needs its own preview and native approval unless preauthorized, sends no new color or
current values, never forces/unbinds a driver and promises no visible effect.
No other packet replay is implemented. The full color protocol remains unverified. See the
capture contract in the source repository's `docs/CONTROLS.md` for limits and
the distinction between runtime readiness and verified hardware behavior.

### Mini 1: one observed-channel pulse

`led.pulse` accepts only `capture_id` and a triplet `channel` 0–2; channel names
are not mapped to RGB. It needs separate action permission and approval unless preauthorized, exact
firmware/current boot, a cleaned capture no older than 30 minutes, validated
18-byte six-triple PWM/update pairs and an observed final zero frame/update.
Approve only if the owner sees the ring currently idle/dark. The helper sends
one fixed PWM-16/255 pulse for one second, then the captured idle frame; it never
changes calibration/current/shutdown or stops Quasar. A capture is not hardware
readback: restoration and visible color remain unverified even if utilities
succeed. Failed/interrupted writes are unknown, never automatically retried.

### Mini 1: operator-requested animation presets

Route operator chat requests to `led.animate`, not to a shell or a native Alice
voice-command claim:

- «помигай разными цветами» → `mode="blink"`: six logical colors, 0.5 s on / off; 6 s.
- «покажи радугу» → `mode="rainbow"`: six-color spatial frame held for 3 s.
- «покажи вихрь радуги» → `mode="rainbow_swirl"`: two logical rotations, 0.5 s steps; 6 s.

Parameters are only `capture_id` and `mode`. All PWM values are zero or 16/255;
there is no arbitrary frame, speed, brightness, duration or endless-mode argument.
Require separate `led.animate` permission, same-boot reviewed firmware and recent
cleaned capture evidence (within 30 minutes) with an observed final zero frame/update.
If that evidence is missing/stale, obtain a fresh separately approved LED capture;
never weaken the gate just to honor a preset name. Each show needs approval unless preauthorized,
with the owner observing the ring currently idle/dark. Return to the observed idle
frame and inspect unknown/interrupted outcomes before any new action.

Color order, physical zone adjacency and rotation direction are not calibrated.
These presets are synthetically tested, not visually verified. Keep Quasar running;
it can overwrite a show, and the show can temporarily disrupt status indication.
Do not automatically replay it to fight the firmware renderer. No daemon or
persistent pattern installation is part of this action.

The runtime captures and restores relevant LED state for the bounded effect;
“all off” is not restoration. Do not overwrite factory colour calibration or
manually fight another renderer. An interrupted action or cleanup can be unknown:
inspect locally, never auto-retry. Persistent animation replacement is not this
action and requires separate owner-managed backup and confirmation.
The helper lock does not lock the firmware renderer. Captured-state restoration
can race its updates; do not fight it or stop Quasar to obtain exclusivity.
Mini 3's shell adapter restores on normal exit and handled HUP/INT/TERM, then
checks the exact 18-byte readback. Its in-memory snapshot cannot survive power
loss, SIGKILL or a stuck kernel operation; there is no detached recovery watchdog.

### Reference-style shows (`led.show`, Mini 1, Lite, Mini 3)

Route «радуга по кругу/переливы» → `rainbow`, «дыхание» → `breathe`, «крути/вращение»
→ `spin` or `spin_color`, «включи цвет» → `static`, «выключи подсветку» → `off`,
«мигни» → `notify`, «полиция» → `police`. Parameters: `effect`, `duration_seconds`
1–15, optional `speed` 1–5, colour 0–16 only for colour effects, `capture_id` on
Mini 1. Every show ends and restores; there is no "until stopped" mode or strobe.
`yielded=true` means the firmware took the ring back (e.g. mute): do not repeat.
Channel/zone mapping comes from the private `led_map` written by the owner-run
calibration runner. Details: `docs/LED_PARITY.md` in the source repository.

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
`1ab048bf95c0e5e547a1ff6c801d2ed9deb193db`, not device qualification. Bundled
integration checks are offline/synthetic, not proof of physical playback. No
arbitrary GPIO/I2C/memory/register controls, copied scripts or downloaded RGB
installer are provided or executed here.

Sources: [LED recipe](https://hackalice.me/recipes/led/),
[Lite format](https://hackalice.me/devices/station-lite/),
[Mini 2 caveat](https://hackalice.me/devices/station-mini2/),
[Bergamot framebuffer](https://hackalice.me/devices/station-mini3/),
[Station 2 panel](https://hackalice.me/devices/station-2/),
[Mini 1 unknown protocol](https://hackalice.me/devices/station-mini/),
[Midi format unknowns](https://hackalice.me/devices/station-midi/).
