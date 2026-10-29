---
name: station-airplay
description: Diagnose an existing Shairport Sync AirPlay receiver without implicit Quasar shutdown or blind binary patches; triggers include AirPlay на Станции, нет звука AirPlay.
---

# AirPlay: separate reception from audio ownership

Require owner authorization and existing secure access. Keep Quasar enabled by
default. Installation, patching, audio ownership and service shutdown are
guidance-only; stopping Quasar needs its own explicit request and confirmation.

## Prerequisites

Identify model/build, existing receiver origin and versions, configuration and
backup. The Mini 3 recipe assumes a pre-existing `/data/shairport` installation;
the wiki does **not** provide its base installation procedure. Do not invent one
or fetch a lookalike package. Station 2 techniques are separate versioned evidence.

## Diagnosis

1. Separate discovery failure, accepted session with silence, and duplicate button
   handling. Confirm the owner's sender and configured receiver are on the intended
   trusted network without scanning for unrelated devices.
2. Mini 3's reported Shairport Sync 4.3.7/nqptp 1.2.4 failure involves a stalled
   companion process and FIFO. Verify exact versions before suggesting its patch.
   The documented binary change can affect the displayed clock.
3. On Station 2, maind media ownership can yield ALSA busy even when music is idle.
   Device ownership is not a reliable playing-state detector; the source uses an
   established receiver session. Session ports change across receiver versions.
4. For remote buttons, distinguish the audio event channel from the remote-control
   channel and check receiver-version support. Older DACP assumptions do not
   establish AirPlay 2 command support. A successful D-Bus client exit can still
   mean no reply was awaited in the reported musl environment.
5. Discovery flapping can be competing mDNS address publication, not broken audio.
   Check existing maind/avahi ownership without launching another publisher.

## Preview and confirmation

Offer diagnosis first. The upstream Mini 3 hooks stop Quasar during playback;
Station 2 ownership scripts also interrupt services and fight the supervisor.
Neither is an acceptable implicit prerequisite for “make AirPlay work.” If the
owner has not separately requested that outage, stop at a written plan.

For a manual binary repair, require an exact artifact match, preimage hash and
byte checks, known rollback and owner consent. Never recommend force-patching a
mismatch. Preview each hook, startup change, expected outage and restoration path.

## Backup and rollback

Preserve the original executable, receiver configuration, hooks, permissions and
startup ownership. Restore only introduced hooks and the matching original binary.
Do not restart or stop all audio services as a cleanup shortcut. Keep an independent
secure access path; a reinstall may erase hooks and invalidate the old preview.

## Verification

After an owner-performed approved change, test discovery, one quiet playback,
pause, resumption and one button event. Confirm Quasar functionality returns and
the clock still meets the owner's requirements. Inspect readiness for data/audio
devices before considering autostart; init.d numbering alone is insufficient.

## Limitations

Mini 3 fixes and Station 2 development builds are not interchangeable. Custom
ALSA configuration may be necessary for musl binaries to avoid 32-bit plugins.
No supplied patch, ownership script or receiver is executed or bundled here.

Sources: [AirPlay recipe, missing base package and model-specific techniques](https://hackalice.me/recipes/airplay/),
[mDNS/ALSA pitfalls](https://hackalice.me/internals/gotchas/).
