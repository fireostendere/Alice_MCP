---
name: station-integrations
description: Assess third-party on-station integrations - Home Assistant voice satellite (Pulsar), bt-proxy MPRIS-to-Bluetooth bridge and the MidiPult web panel - for scope, exposure and rollback without installing them; triggers include Home Assistant на Алисе, колонка как спутник HA, bt-proxy, веб-панель Миди, MidiPult.
---

# Integrations: know what each one replaces or exposes

Require owner authorization and existing secure access. Keep Quasar enabled: one
integration below stops stock Alice, which needs its own explicit request and is
never an implied step. Installing daemons, autostart files, D-Bus changes and web
panels is guidance-only; nothing here downloads or runs upstream scripts or binaries.

## Prerequisites

Identify model and build, hold an off-station copy of the system files and a
recovery path, and list the stock functions that must survive. Get household
consent where microphones are handed to another system. Evidence is one unit each:

- Pulsar 0.5.0: Lite 2 (Plum), firmware 0.369.1.50, HA Core 2026.9.2, set up from Linux x86-64.
- bt-proxy: Lite 2 (plum), firmware 0.369.1.50, with shairport-sync 5.6.2 and a real phone at once.
- MidiPult panel 0.4.56 with Android shell 1.0.8: Midi (Cucumber), firmware 0.369.1.46, quasar 3.21.

## Diagnosis

1. **Home Assistant satellite (Pulsar).** Lite 2's microphones and speaker become
   an Assist satellite through HA's ESPHome integration (port 6053, encryption
   key). Recognition, agent and voice are chosen in HA; no model is installed on
   the station. Enabling it stops stock Alice: her answers, Yandex Music and cloud
   home control do not serve requests in this mode. Wake word and microphone
   processing still use firmware libraries, and the station keeps vendor DNS/NTP
   and its account binding; isolation from the vendor is not verified. Autostart
   adds an init file on the system partition. It does not block vendor updates.
   The station needs the 32-bit build although its kernel reports aarch64. The
   key grants microphone access and control of the station: treat it as a secret.
2. Pulsar conflicts: AirPlay on Lite 2 relies on bt-proxy and a running Quasar, and
   joint use is untested; the ChatGPT voice mode needs stock Quasar and the same
   microphones; Quasar Bridge LLM settings do not carry over into Assist.
3. **bt-proxy.** Any MPRIS player on the station is shown to stock Alice as a
   connected Bluetooth phone, so her own Bluetooth logic handles audio focus,
   next/previous/pause, ducking while listening and the ring animation; the last
   source to start pauses the others. The Bluetooth daemons and pairing agent move
   to a private root-only bus, and the proxy owns the BlueZ names on the system
   bus, relaying calls unchanged and adding virtual devices. Installation renames
   the BlueZ D-Bus activation file on the system partition and adds an init
   script. It depends on a running Quasar.
4. bt-proxy pitfalls: with activation left on, a proxy crash starts a second
   Bluetooth daemon on the same adapter; after a hard kill `maind` keeps stale
   devices until its platform daemon is restarted, which is a separate explicit
   service decision; a player must use the station's own ALSA library (32-bit,
   glibc no newer than 2.30 on Lite 2) to mix under Alice, which Entware builds
   do not. The Spotify sketch on the page is untested.
5. **MidiPult.** A lighttpd and Python CGI panel on port 8055 of the station, with
   a state-cache daemon and a voice daemon that use Luna on loopback port 8099 and
   IPC port 9875. It drives display and ring, player and radio, LLM profiles and
   chats, a VPN client, Wi-Fi/Bluetooth, scenarios, backup, reboot and shutdown.
   It is HTTP only with an optional 4-8 digit PIN (five failures lock for five
   minutes) and is meant for a trusted LAN: never expose the port. LLM and VPN
   keys and chats sit in its data directory with owner-only permissions. The
   Android shell finds panels by UDP port 8056 and only opens them in a WebView.
   Alice can answer at the same time as a panel voice command. It needs root, SSH,
   Entware and a running Luna, and its installer adds Entware packages and autostart.

## Preview and confirmation

Give the owner a table per integration: what is replaced, what becomes reachable
(port, key, panel without TLS), where speech and text go (the HA host and whichever
recognition, model and voice services its assistant uses; the providers set in the
panel), which persistent changes are made and whether a reboot follows. Stop when
the model or build differs, when an earlier install exists (the Pulsar recipe is
first-install only), when vendor updates are unaddressed, when no off-station
backup exists, when a transfer is incomplete or a checksum differs, when the panel
would be reachable beyond the LAN, or when someone has not agreed to microphone
forwarding. Ask explicitly before anything that stops Alice or reboots.

## Backup and rollback

- Pulsar: its disable command clears the enabled flag and starts Quasar, and the
  boot script then skips it. Full removal also deletes the init file, after
  comparing it with the saved copy, the program directory and the ESPHome entry
  in HA; upstream did not exercise full removal on the tested unit.
- bt-proxy: remove its init script, restore the BlueZ activation file name, reboot.
- MidiPult: its uninstall script has a dry run, backs up first and removes only
  its own web files, init scripts and web-server configuration; a purge option
  also deletes keys and scenarios. Luna, YAM and Entware are left alone.

## Verification

Owner observation only; nothing was tested here. Pulsar: its status shows the
satellite mode after a reboot, the device returns in HA and a spoken question is
answered; after disable, Alice answers. bt-proxy: the proxy owns the BlueZ name,
its log lists the player, the `maind` log reports a connected and playing
Bluetooth source, and a real phone still plays. MidiPult: the panel opens only
inside the LAN with the PIN set, and Luna is not reachable from other hosts.

## Limitations

Single-unit evidence; other models are untested. Pulsar's setup wizard, HA-driven
update and the macOS/WSL2 command variants were not run end to end upstream.
MidiPult shows Zigbee status only and relays commands through Alice. Evidence is
pinned to public revision `520fbccd68cb90a8c4f01d06bf38d9a7db915304`. No scripts,
binaries, addresses or keys are distributed.

Sources: [Home Assistant satellite](https://hackalice.me/recipes/home-assistant/),
[bt-proxy](https://hackalice.me/recipes/bt-proxy/),
[MidiPult panel](https://hackalice.me/recipes/midipult/).
