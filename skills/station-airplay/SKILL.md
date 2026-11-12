---
name: station-airplay
description: Choose and diagnose a model-specific AirPlay 2 receiver path without implicit Quasar shutdown, update blocking or blind binary patches; triggers include AirPlay на Станции, нет звука AirPlay.
---

# AirPlay: pick the path, then separate reception from audio ownership

Require owner authorization and existing secure access. Keep Quasar enabled by
default. Installation, patching, audio ownership, update blocking and service
shutdown are guidance-only; stopping Quasar needs its own explicit request and
confirmation.

## Prerequisites

Identify model/build, any receiver already present with its origin and versions,
configuration and backup. Two receivers contend for the same ports, so an existing
one is disabled or removed first. The wiki documents six paths; none transfers to
another model without rechecking daemon paths, ports and startup.

| Path | Scope and evidence | Effect on Alice |
|---|---|---|
| Station AirPlay, an independent receiver (not Shairport) | Rooted Linux stations with YAM 0.2.x, or its standalone archive. iPhone playback reported on Midi and Station 2, grouped with HomePod mini from 0.2.2; on other models it installs but sound is not reported everywhere. Not for Android Max or Station 1 | Described as leaving firmware configuration, Alice's player, volume and updates untouched; voice pause and resume from 0.2.0, track and volume only from the phone |
| Station 1, Android 6 with AliceBridge root and Entware | Entware OpenSSL build 4.3.6 with nqptp 1.2.4; owner-reported iPhone playback and restart after reboot. Clean-Entware install untested | Alice is not stopped; direct ALSA output without mixing or voice control |
| Mini 3 repair | Only an already installed `/data/shairport` package, 4.3.7 with nqptp 1.2.4. The wiki still does **not** say where that package comes from | Its hooks stop Quasar while AirPlay plays |
| Lite 2 (Plum), firmware 0.369.1.50 | Custom 5.6.2 build, nqptp 1.2.9, Entware avahi and bt-proxy | Alice stays up and treats AirPlay as a Bluetooth phone on her own audio chain |
| Station 2 bundle, firmware 0.369.1.50 | Source-built 5.6.2 and nqptp 1.2.9 in one data directory | Alice's media daemon is stopped for each session; the installer also blocks OTA and adds an update watchdog |
| Lite 1 (Micro) | The Lite 2 armhf build runs unchanged with Entware avahi | Plays through the shared mixer; no bt-proxy, so Alice cannot pause it |

Do not invent a Mini 3 base package or fetch a lookalike. Quasar Bridge carries its
own receiver; use one or the other.

## Diagnosis

1. Separate discovery failure, accepted session with silence, and duplicate button
   handling. Confirm the owner's sender and receiver share one trusted Wi-Fi without
   client isolation; never scan for unrelated devices. A receiver that vanishes after
   a few minutes suggests router IGMP snooping without a querier, not broken audio.
2. Mini 3's reported failure is a stalled companion process and FIFO. Verify exact
   versions before suggesting its patch; the upstream script patches only matching
   bytes, and the change can freeze the displayed clock.
3. On Station 2, maind media ownership yields ALSA busy even when music is idle.
   Device ownership is not a playing-state detector; the bundle uses an established
   receiver session. Before 5.6 the AirPlay 2 port was fixed, later builds use the
   configured one, so a stale check loses the session after a receiver update.
4. For remote buttons, DACP is not offered by AirPlay 2 senders. Commands travel on
   the audio session's event channel, which must already be open; the remote-control
   channel swallows them. Upstream has this from 5.6 development builds; 4.3.7 would
   need a port. A D-Bus client that exits successfully without awaiting a reply
   cancelled the call in the reported musl environment.
5. Visible but not connectable: the extra avahi must reuse the host name maind
   publishes and must not publish addresses; a unique name ends in endless renames.
   An explicit device identifier is needed; the interface setting does not supply it.
   On Lite 1, autostart must test the D-Bus daemon process, not a leftover socket file.
6. Hiccups about twice a second on Lite or Lite 2: the card really plays only 48 kHz
   and silently accepts other rates. Set the receiver's output rate or route through
   the 48 kHz mixer; a latency offset does not cure drift. On Lite 2, MPRIS reports
   stopped unless receiver metadata is enabled, so Alice never learns AirPlay plays.
7. Station 1 silence with a live session can be Alice's player holding the direct
   output. That recipe resamples through a plug device with a half-second buffer.

## Preview and confirmation

Offer diagnosis first and state what each path costs. The Mini 3 hooks stop Quasar
during playback. The Station 2 bundle stops the media daemon, restarts the receiver
after every session and installs update blocking that a watchdog reapplies every two
minutes. The Lite 2 installer disables BlueZ D-Bus activation and adds bt-proxy.
None of these is an acceptable implicit prerequisite for “make AirPlay work.” If the
owner has not separately requested that outage or update block, stop at a written plan.

For a manual binary repair, require an exact artifact match, preimage hash and
byte checks, known rollback and owner consent. Never recommend force-patching a
mismatch. Preview each hook, startup change, expected outage and restoration path.
Remote one-command installers are owner-reviewed manual steps, never run from here.

## Backup and rollback

Preserve the original executable, receiver configuration, hooks, permissions and
startup ownership. Documented exits: Station AirPlay is disabled through its service
helper and removed as a YAM package. The Mini 3 fix script has an uninstall mode
that removes hooks and restores the original binary; reinstalling the package drops
the hooks. Lite 2 and Station 2 installers have an uninstall mode with reboot; on
Station 2 the update watchdog is removed separately and stock updates come back
through the update script's restore mode. Station 1 saves changed files to its own
backup directory; disabling stops the receiver and nqptp but leaves shared D-Bus,
Avahi, packages and backups. Do not restart or stop all audio services as a cleanup
shortcut, and keep an independent secure access path.

## Verification

After an owner-performed approved change, test discovery, one quiet playback with
the sender's volume lowered first, pause, resumption and one button event. Confirm
Quasar functionality returns and the clock still meets the owner's requirements.
Readiness of the data volume and sound devices matters for autostart; init.d
numbering alone is insufficient, and Lite 2 keeps its receiver in a restart loop.

## Limitations

Paths are model-bound and their version pairs must match. On Station 2 the Apple
Home tile shows no player or buttons, and musl binaries need their own ALSA
configuration to avoid 32-bit plugins. On Lite 2 the stock Entware receiver cannot
share the system mixer, and next/previous depend on the sender. A separate YAM
plugin with clock and lights claims shared audio but is unverified by the wiki and
needs the old YAM daemon. No patch, ownership script or receiver is executed or
bundled here.

Sources: [AirPlay recipe, six model paths and rollback table](https://hackalice.me/recipes/airplay/),
[mDNS, IGMP and ALSA pitfalls](https://hackalice.me/internals/gotchas/).
