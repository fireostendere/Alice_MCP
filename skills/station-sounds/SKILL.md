---
name: station-sounds
description: Plan safe one-shot sounds, streams or reversible earcon changes on native Quasar stations; triggers include свои звуки, звук включения, проиграть файл на Алисе.
---

# Quiet one-shot before persistence

Require owner authorization, including permission to make sound in the room.
Keep Quasar enabled. Playback changes, media transfers, system replacements and
startup installation are guidance-only; no downloaded scripts or media run automatically.

## Prerequisites

Confirm native Buildroot maind/GStreamer and the current audio route. Android
Max/gen1 need other procedures; Saturn has no speaker pipeline. Use an owner-held
or appropriately licensed small media file, low volume and available data space.
Do not redistribute the wiki's branded chime or assume its reuse license.

## Diagnosis

1. Distinguish file/codec failure from no output or wrong output. The documented
   native pipeline decodes, converts and resamples to ALSA `default`; that destination
   may already be USB headphones rather than the built-in speaker.
2. Check whether ordinary Quasar sound works before suggesting extra packages.
   Vendor-library path or working-directory requirements can explain maind's
   standalone invocation failure; do not replace system libraries.
3. For streams, verify the source is owner-approved and trusted. Do not convert
   an arbitrary supplied URL into shell syntax or bypass certificate verification.
4. Custom system sounds are Ogg Opus in the source. Perform any conversion on the
   host and inspect duration/level before considering a replacement.

## Preview and confirmation

Propose one short foreground playback first: exact local file, duration, output
route, starting volume and stop method. Ask separate confirmation for a boot sound
or system earcon replacement. Do not hide a background startup job inside a media
test, and do not mask microphone-mute or other important status indications.

## Backup and rollback

One-shot playback should not alter startup or vendor assets. Record the original
volume/route and restore them manually after the test. For a requested persistent
change, preserve the exact sound/startup file, permissions and hash; restore only
that file or remove only the newly introduced entry. Pickle rootfs startup edits
are not persistent, so do not promise a durable boot chime there.

## Verification

Have the owner confirm intelligible, unclipped output at low volume, correct
destination and working Quasar afterward. Treat system-sound hot reload as unknown:
do not restart Quasar merely to see whether a replacement appears. A scheduled
reboot needs separate owner approval and is not required for a one-shot test.

## Limitations

No live audio was played here. Source filenames and available GStreamer plugins
can vary by firmware; generic “all Amlogic” claims do not establish every model.
The wiki does not verify whether replacing system sounds takes effect without reboot.

Source: [custom sounds, streams and replacement caveats](https://hackalice.me/recipes/sounds/).
