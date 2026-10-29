---
name: station-standalone
description: Assess standalone reuse as an unproven model-specific experiment, not a subscription unlock; triggers include колонка без Яндекса, заблокированная Станция, офлайн Алиса.
---

# Standalone is a requirements decision, not a proven unlock

Require owner authorization and clarity about ownership/account rights.
Keep Quasar enabled by default. Root, OTA, flash, manual installation and Quasar shutdown are
guidance-only; a separate explicit shutdown request is mandatory before planning
any whole-platform outage. Never bypass that boundary through another recipe.

## Prerequisites

Ask what “standalone” means: Bluetooth playback, local files, AirPlay, another voice
backend or verified vendor-network isolation. Identify retail model, firmware,
normal function and existing secure access. Subscription-blocked devices have
**no verified reuse procedure** in the cited page; do not promise access or unlock.

## Diagnosis

1. Distinguish account/subscription restriction from boot, power, Wi-Fi and audio
   failure. Do not factory-reset merely to determine which one applies.
2. Existing Bluetooth daemons may be separate from Quasar on sampled Buildroot,
   but this does not prove independent Bluetooth playback on the owner's model.
   Check the requested function with the normal stack left running first.
3. AirPlay can depend on service-stopping hooks; an installation request does not
   authorize them. Local file playback may be a smaller experiment than replacing
   the whole voice backend. Saturn is a hub, not an audio-reuse candidate.
4. A third-party Spotify Connect report for Mini 2 is not evidence for all stations.
   Full ASR is normally cloud-based; offline speech requires a replacement pipeline,
   not simply blocking vendor DNS or killing a daemon.

## Preview and confirmation

Describe the smallest owner-run test, retained/lost functions, privacy goal and
recovery access. Leave all unproven functions marked unknown. Any later request to
disable Quasar must explicitly accept loss of voice, timers or hub functions as
applicable, identify the exact model's services and include a restoration plan.
Never recommend stopping all services, blocking a whole vendor domain indiscriminately
or rooting as an unannounced prerequisite.

## Backup and rollback

Preserve existing startup, audio and endpoint configuration before a separately
approved manual change. Keep independently secured access and a known-good supply.
Rollback returns the exact chosen function to its original route and reenables only
services intentionally changed. No broad data deletion, account reset or generic
firmware flash is a valid substitute for a missing rollback.

## Verification

Have the owner check one requested function and normal Quasar afterward. For an
explicitly approved standalone-mode experiment, also test the documented return to
normal operation. “It plays offline once” is not proof of account independence,
permanent offline operation or no cloud traffic. Disclose what was not measured.

## Limitations

The upstream page is a research request rather than a working subscription-block
guide. Hardware similarity and normal-device success cannot fill that evidence
gap. This skill supplies no bypass payloads or shutdown scripts and runs no station
operations. Prefer a supported existing function over speculative platform surgery.

Sources: [standalone research gaps](https://hackalice.me/recipes/standalone/),
[AirPlay service effects](https://hackalice.me/recipes/airplay/),
[local playback](https://hackalice.me/recipes/sounds/),
[replacement voice backends](https://hackalice.me/recipes/llm/).
