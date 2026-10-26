---
name: station-led
description: Select model-specific LED interfaces and plan a bounded reversible low-brightness effect; triggers include подсветка Алисы, LED кольцо, анимация Станции.
---

# Match the driver before choosing an effect

Require owner authorization for visible effects. Keep Quasar enabled. LED writes,
animation replacement and installation are guidance-only; do not stop the firmware
renderer just because it overwrites a test frame. Avoid flashing or intense patterns.

## Prerequisites

Identify exact retail model, firmware and desired region: ring, logo, clock or panel.
Read [model evidence](../station-devices/references/models.md). Existing authorized
access must be secured; do not enable root/ADB as an implicit LED prerequisite.

## Diagnosis

1. Lite uses three sysfs RGB zones; Mini 2 uses six plus clock segments. Enumerate
   existing documented nodes rather than guessing another model's sysfs names.
2. Bergamot uses a six-by-one, 24-bpp **BGR**, 18-byte framebuffer. Its related sysfs
   nodes can reflect framebuffer state rather than independently drive it.
3. Station 2 uses an **RGBW**, 32-bpp, 356-byte frame with 89 logical positions and
   88 visible LEDs. Do not reuse Bergamot frame length or channel order.
4. Midi has ring/logo framebuffers and separate clock segments, but their byte
   ordering is not established by its page. Pickle, Mango and Saturn have different
   zone layouts. Mini 1's I2C ring is not the Lite interface.
5. An effect disappearing when Alice speaks can be normal ownership contention,
   not a reason for raw I2C, direct register writes or Quasar shutdown.

## Preview and confirmation

Describe one low-brightness steady effect, exact region/interface, frame format,
duration and restoration method. Confirm the user accepts temporary interference
with status indication. Check framebuffer geometry before any owner-run write;
unknown format or node identity blocks the experiment.

## Backup and rollback

Capture only relevant current brightness/trigger settings or the known compatible
original animation file and its permissions/hash. Do not overwrite factory colour
calibration. Restore the previous state or release control to the normal renderer
after the bounded effect; “all off” is not always restoration. Persistent animation
replacement requires a separate backup and confirmation, not an effect test.

## Verification

The owner checks region, colour order, brightness and duration, then normal listening,
mute and timer indications. If the firmware reclaims the display, report that
limitation instead of repeatedly fighting it. Keep the user's other display regions
unchanged and verify Quasar remains operational.

## Limitations

Mini 2's six-zone .led interpretation remains explicitly unverified. Lite's textual
format is not a universal animation schema. No model-specific I2C payloads, framebuffer
write snippets or downloaded RGB installer are provided or executed here.

Sources: [LED recipe](https://hackalice.me/recipes/led/),
[Lite format](https://hackalice.me/devices/station-lite/),
[Mini 2 caveat](https://hackalice.me/devices/station-mini2/),
[Bergamot framebuffer](https://hackalice.me/devices/station-mini3/),
[Station 2 panel](https://hackalice.me/devices/station-2/).
