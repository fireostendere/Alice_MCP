---
name: station-streaming
description: Assess model-bound Spotify, YouTube Music and SoundCloud add-ons and their voice bridges without touching subscription, DRM or account limits; triggers include Spotify на Станции, ютуб музыка на Алисе, SoundCloud голосом.
---

# Streaming add-ons: model-bound integrations, service limits unchanged

Require owner authorization, the owner's own service account and existing secure
access. Keep Quasar enabled: every recipe here reuses Alice's recognition. Installing
apps, receivers, voice bridges, VPN clients and autostart is guidance-only. Nothing
here works around subscriptions, DRM, regional rules or account restrictions.

## Prerequisites

Match the exact model first; no recipe below is verified on another one.

| Recipe | Verified scope | What it integrates | Needs |
|---|---|---|---|
| Spotify Connect | Station 2, firmware 0.369.1.50, librespot 0.8.0 | Connect receiver sharing Alice's volume, plus a voice bridge over the Web API | The Station 2 AirPlay bundle, key-only SSH, a Premium account (the receiver does not work with a free one), the owner's own developer application for voice |
| YouTube Music, ytm-alice | Mini 2 with Quasar 3.21 and YAM 0.2.5; Lite 1 by the project's author; no packages for other models | Voice phrases, LAN web panel, console client and a player on the firmware's GStreamer that Alice sees through bt-proxy | Root, SSH, YAM 0.2.x, about 20 MB on the data volume |
| Spotify by voice, Station 1 | Station 1 (tulip), Android 6, AliceBridge root, Spotify TV 1.134.2, Free account | The unmodified official Android TV app plus a voice bridge; pairing by code without HDMI | AliceBridge root; SSH only for the Windows installer |
| SoundCloud by voice, Station 1 | Same Station 1 baseline, HDMI detached | A patched NewPipe 0.29.1 build with a first-track adapter, a voice bridge and the v2rayNG client | The owner's own VPN profile; HDMI and a remote may be needed once for Android's VPN prompt |

Every recipe also needs the service to be reachable from the owner's network. When
it is not, that is a network matter outside the station and outside this skill.

## Diagnosis

1. Station 2 Spotify has three distinct failures. Phone discovery on the LAN is
   unreliable, so the recipe prefers a one-time browser login kept in the receiver's
   cache; an editor that auto-forwards the callback port can consume that login.
   A session that never initialises while the phone app still logs in indicates
   blocked HTTPS on the owner's network. Login followed by every track being skipped
   with audio-key errors means the service issues no track keys to this client for
   that account: not an install fault, and upstream had no fix in late October 2026.
2. Its volume uses Alice's `Master`. A mixer range below 25 silently becomes a linear
   scale, and the launcher passes the current level so a start does not move Alice's volume.
3. Its voice bridge reads recognised text from a local channel. Alice still answers
   and briefly starts her own playback; about seven seconds pass before sound. Only
   artist play, pause and quieter were tested by voice. While Alice listens a separate
   duck control drops; it returns after 15 seconds at the latest. Short commands
   without the wake word reach only Alice's own player.
4. ytm-alice reporting an unavailable track after several skips can be service rate
   limiting on a shared carrier address; the page offers waiting or a dedicated address. Release
   0.7.2-hotfix has no seek and no phone casting, which exists only on the main
   branch. Installation restarts Bluetooth and moves its services to a separate bus.
5. Station 1 Spotify: a status without a media session does not prove logout. First
   launch can take tens of seconds and control commands wait up to 20. The base APK
   alone does not start; the installer adds the ARMv7 library and resource splits.
   A location message is the service's own Free-tier rule about use away from the
   account country; the installer changes neither country nor network.
6. Station 1 SoundCloud: on network errors check the owner's VPN profile and a search
   inside the app. Old REALITY profiles may need re-import through the helper because
   of an Android 6 path-encoding quirk. Results are limited to tracks, subscription-only
   tracks stay unavailable, and the Alice app must keep running.

## Preview and confirmation

Before any owner-run install, list apps, files, autostart entries and what changes.
Station 2 Spotify inherits the AirPlay bundle's update blocking and media-daemon
handover, and upstream warns that the service may block accounts used with third-party
Connect daemons; the owner accepts that explicitly. The ytm-alice panel has no
access control: anyone on the LAN can drive it, so it is never port-forwarded.
Station 1 Spotify leaves Alice, volume and network alone and stops on an unknown
app version or signature. Station 1 SoundCloud replaces an official NewPipe with a
differently signed build after saving it, enables VPN autostart and adds a Wi-Fi
egress rule for the VPN client's UID only. Developer keys, pairing codes, cookies
and VPN links are entered by the owner on the device, never pasted into chat.
Upstream one-command installers are owner-reviewed manual steps, never run from here.

## Backup and rollback

- Station 2: remove both init entries and the bundle directory, restore stock updates
  through the update script's restore mode, reboot; this also removes AirPlay. Lighter:
  clearing the receiver's executable bit makes the launcher skip it.
- ytm-alice: its daemon uninstall removes both init scripts and returns stock
  Bluetooth; then remove the YAM package and reboot. Upgrades keep queue and login.
- Station 1 Spotify: disable and enable toggle the bridge; uninstall removes only its
  autostart line. App, login and backups stay; logout is separate and local only.
- Station 1 SoundCloud: uninstall removes the bridge, its autostart, the key helper
  and its routing rule; NewPipe, the VPN client, profiles and backups stay. A restore
  mode returns official NewPipe with data from the backup; VPN autostart has a toggle.

## Verification

Have the owner run one quiet test per function: appearance in the service's app,
one voice request, pause, resume and volume. Then confirm ordinary Alice requests,
timers, smart home and her own volume still work. Check autostart only at an
owner-scheduled reboot. Bridges log counters or music commands, not other speech.

## Limitations

Each recipe is one maintainer's snapshot from October–November 2026; third-party
clients break when a service changes. Free-tier advertising and limits remain. Voice
coverage is partial and usually plays the first search hit. For YouTube on HDMI with
Station 1, see the TV kit in [station-gen1](../station-gen1/SKILL.md); the Station 2
dependency is in [station-airplay](../station-airplay/SKILL.md). No script, APK,
key or account data is bundled or executed here.

Sources: [Spotify Connect on Station 2](https://hackalice.me/recipes/spotify/),
[YouTube Music with ytm-alice](https://hackalice.me/recipes/youtube-music/),
[Spotify by voice on Station 1](https://hackalice.me/recipes/station1-spotify/),
[SoundCloud by voice on Station 1](https://hackalice.me/recipes/station1-soundcloud/),
[Station 1 TV kit](https://hackalice.me/recipes/station1-tv/),
[Station 2 AirPlay bundle](https://hackalice.me/recipes/airplay/),
[recipe index and model coverage](https://hackalice.me/recipes/).
