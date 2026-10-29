---
name: station-sounds
description: Preview/apply bounded volume, approved media upload/playback and reversible system-sound changes on native Quasar stations; triggers include свои звуки, звук включения, проиграть файл на Алисе.
---

# Quiet one-shot before persistence

Require owner authorization, including permission to make sound in the room.
Keep Quasar enabled. Experimental runtime actions cover approved clips, volume
and fixed sound slots. Streams, startup installation and unsupported/high-risk
changes remain guidance-only; no downloaded scripts or media run automatically.

## Prerequisites

Confirm native Buildroot maind/GStreamer and the current audio route. Android
Max/gen1 need other procedures; Saturn has no speaker pipeline. Use an owner-held
or appropriately licensed small media file, low volume and available data space.
Do not redistribute the wiki's branded chime or assume its reuse license.

Runtime actions require pinned-host-key, key-only SSH, preinstalled Python 3.8+,
global write opt-in, private per-action policy and a locally reviewed `maind`
firmware SHA-256. Native Windows device writes and automatic installation are unavailable.

## Diagnosis

1. Use `get_model_interfaces(model)` and `get_station_controls(station_id)` to
   distinguish source evidence from declared action/asset aliases. Neither verifies
   hardware availability. Distinguish file/codec failure from no output or wrong
   output. The documented native pipeline decodes, converts and resamples to ALSA
   `default`; that may already be USB headphones rather than the built-in speaker.
2. Check whether ordinary Quasar sound works before suggesting extra packages.
   Vendor-library path or working-directory requirements can explain maind's
   standalone invocation failure; do not replace system libraries.
3. For streams, verify the source is owner-approved and trusted. Do not convert
   an arbitrary supplied URL into shell syntax or bypass certificate verification.
4. Custom system sounds are Ogg Opus in the source. Perform any conversion on the
   host and inspect duration/level before considering a replacement.
5. `audio.volume` requires probing an available `Master` control on reviewed
   Buildroot. The public exact mixer command is documented only for Lite; do not
   infer that all models share it. The maind/GStreamer recipe is not universal
   proof of plugins, codecs or working speakers. Unknown support fails closed.

## Preview and confirmation

For an owner-requested action, call `run_station_action(station_id, action, parameters)`: it previews and applies in one call
(no form when preauthorized). Use the separate preview/apply tools only to show a plan before acting.

Use `preview_station_action(station_id, action, parameters)` for read-only runtime
inspection, selecting exactly one action:

- `audio.volume`: `percent`, an integer from 0 to the private `max_volume`
  ceiling (default 30%). This is a persistent control change with a restore record.
- `audio.play`: approved `asset` alias and integer `duration_seconds` from 1 to 10.
- `media.upload`: approved `asset` alias and `content_base64`; decoded size is at
  most 49,152 bytes and SHA-256 must match the preregistered private asset hash.
  This ceiling deliberately bounds the SSH argument, not just file storage.
- `sound.replace`: approved `asset` alias and `slot`, one of `spotter_start`,
  `spotter_cancel`, `volume_sound` or `notification_sound`.
- `speech.say`: literal `text`, at most 256 Unicode scalars / 512 UTF-8 bytes,
  no control/bidi-control characters. Mini 1 and Mini 3 with runtime-verified ARM32
  firmware only; requires a separate reviewed
  ARM TTS-only client and firmware/private opt-in. Native approval is the default;
  explicit station-level `preauthorize_all_actions=true` permits execution without forms.
  The ARM TTS-only client is built/pinned. One Mini 3 pilot has owner-confirmed
  audible output; native completion events and Mini 1 audible TTS remain unverified.
  It uses native Yandex cloud synthesis with Append requested, not a dialogue
  command. Preserve volume/route and Quasar; use an idle station and a short first
  phrase. A 20-second observation timeout does not stop or bound playback.
  Only request-correlated start/completion plus cleanup establish protocol success;
  have the owner confirm audible/intelligible sound. Never retry an uncertain send.

Assets refer only to privately registered `/data/sounds/<safe-name>.opus`, `.wav`
or `.mp3` files. Do not supply a path, URL, shell command or arbitrary slot.
Do not mask important status indications. Propose low volume and short playback
first, not a hidden startup job. Explain the chosen action's exact impact, then
call `apply_station_action(plan_id, expected_sha256)` only with the preview's
returned values. Apply requests the native confirmation; do not add a separate
chat approval question. If `approval_required=false`, private operator policy
preauthorizes that bounded `audio.volume` or `audio.play` action, including its
volume ceiling and registered assets. Station-level `preauthorize_all_actions=true`
instead preauthorizes all already-permitted actions, including speech and persistent edits.
Other actions require native approval. Neither mode grants new action/asset permissions.
Each action has a 300-second single-use preview and rechecks configuration/trust/state.
A model cannot grant preauthorization. Run read-only diagnostics and previews
within the owner's task without asking again.

Bounded playback may stop only the process group started by the helper, never
`killall maind` or Quasar. Upload, volume and replacement each require their own
preview/approval; consent to hearing a clip does not approve persistent changes.
Playback uses an unlinked snapshot of verified bytes through an inherited
`/proc/self/fd/<fd>` descriptor, not the mutable asset path. Procfs and native
descriptor preservation are required and unverified on real firmware; do not
fall back to unchecked playback if unavailable.

## Backup and rollback

One-shot playback does not install startup hooks or change vendor assets. Before
persistent volume/media/sound edits, the helper saves an owner-only record under
`/data/opt/alice-mcp/backups/<id>.json`. Keep original values private. For recovery,
preview `restore` with `backup_id` (32 lowercase hexadecimal characters) and get
native approval unless the preview reports `approval_required=false` under explicit
standing authorization. Optimistic state checks refuse changes they observe;
they are not atomic compare-and-swap. External file/mixer writers must be idle
throughout apply/restore: an update after the final check can be overwritten or
deleted, and the helper lock does not lock external writers. If exclusivity cannot
be ensured, do not apply; never stop Quasar to obtain it. Only a file's own record
may delete a file it created. No broad rollback/reset.

Read-only rootfs edits fail by default. Only explicit private per-station
`allow_rootfs_remount=true` permits guarded temporary read-write access followed
by the original read-only mount state. This does not guarantee reboot/OTA
persistence. Unknown outcomes or cleanup failures require local inspection, never
automatic retry. Pickle rootfs startup edits are not a durable boot-chime solution.

## Verification

Have the owner confirm intelligible, unclipped output at low volume, correct
destination and working Quasar afterward. Treat system-sound hot reload as unknown:
do not restart Quasar merely to see whether a replacement appears. A scheduled
reboot needs separate owner approval and is not required for a one-shot test.
File/control post-checks do not prove audible output; `effect_observed` and
`physical_effects_verified` remain false. Service restart is a separate explicit
service preview/approval, never an implied configuration reload.

## Limitations

No station access or hardware test was performed; integration checks are
offline/synthetic. Source filenames and available GStreamer plugins can vary by firmware;
generic “all Amlogic” claims do not establish every model.
The wiki does not verify whether replacing system sounds takes effect without reboot.
Evidence is pinned to public revision `1ab048bf95c0e5e547a1ff6c801d2ed9deb193db`;
synthetic checks and policy declarations are not hardware qualification.

Source: [custom sounds, streams and replacement caveats](https://hackalice.me/recipes/sounds/).
