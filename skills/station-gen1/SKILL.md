---
name: station-gen1
description: Plan reversible first-generation Station 1 (Android, tulip) add-ons, namely analytics host blocking, the HDMI TV kit with web remote and Debian beside Alice; triggers include Станция 1, первая Станция, Debian на Станции, отключить метрики.
---

# Station 1: Android recipes, each with its own exit

Require owner authorization and existing AliceBridge root on the owner's own unit.
Keep Quasar enabled by default: booting into the Couchy launcher can silence Alice
and Debian runs without her, so each needs a separate explicit owner request. Scripts, apps,
host blocking and alternative OS images are guidance-only; nothing is run from here.

## Prerequisites

Scope is Station 1 only (YNDX-0001, board tulip, Android 6.0.1). Station Max is
also Android, but nothing below is verified there. Baselines differ per recipe.

- **Analytics blocking**: build MOB30J.20261013. Needs AliceBridge SSH
  enabled, because its start script carries the block across power cycles.
- **TV kit**: same build with HDMI. Needs a display, a phone on the same Wi-Fi and
  internet for missing apps. Installs a web remote, Couchy Launcher 1.0.6, a
  Station 1 build of SmartTube stable 32.68 and v2rayNG 1.9.45.
- **Debian 13 with a board-specific Linux 7.3 kernel**: firmware 2.369.5.46,
  installer release debian-v8. Needs a full root shell, 400 MB in the cache
  partition and 150 MB on data. It will not start on any other board.

Related Station 1 paths live in [station-airplay](../station-airplay/SKILL.md) and
[station-streaming](../station-streaming/SKILL.md).

## Diagnosis

1. Analytics blocking adds exact analytics host names to a saved copy of the hosts
   file and bind-mounts it. The system partition is not remounted; Quasar's
   configuration, DNS servers and Alice's processes are untouched. A check mode
   reports state. Tested: apply, loopback resolution, an ordinary voice command and
   restore with a matching original hash. Untested: persistence after reboot and
   long-term use.
2. It is a narrow measure. Identifiers, local records and queued reports remain,
   in-app collection may continue, open connections and cached DNS last until
   reconnect, and assistant cloud traffic is unchanged. The metrics SDK lives inside
   the main Station app, so removing that app removes Alice's interface too.
3. The web remote is one Java process bound to the Wi-Fi address; a watcher restarts
   it when the address changes, so a dead page after a network change means a new
   address. Cyrillic entry depends on the active app accepting paste.
4. The SmartTube build exists because this firmware ignores view transparency and
   left title lines on screen. It carries its own signature, so official updates do
   not install over it. Replacement needs free space above Android's low-storage
   threshold plus a margin; otherwise the installer changes nothing and says so.
5. The Debian installer first checks model, root, eMMC layout, firmware files, space,
   network and bootloader, lists every problem at once and changes nothing on
   failure. A downloaded Android update filling the cache partition is reported with a fix.
6. Debian boot modes: through Android (about three minutes, Android stays primary),
   direct (SSH in about 15 seconds, Android moves to the recovery slot; offered only
   when bootloader hashes match the tested ones) or Android by default with entry
   on command. Three failed boots in a row return the unit to Android by themselves.

## Preview and confirmation

- Blocking: show the host list, the bind mount and the added autostart line.
- TV kit: the owner sets the remote's passphrase, eight characters or more, through
  a one-time first-run link. Transport is plain HTTP, so passphrase and commands
  cross the LAN unencrypted: trusted Wi-Fi only, never port-forwarded. Booting into
  Couchy unloads the Yandex app and Alice may stop answering; the default stays Alice.
- Debian: it writes the recovery partition and, in direct mode, boot (the AliceBridge
  root image moves to recovery). It adds one line to the system startup script,
  renames the stock recovery-restore patch and uses the cache partition and the tail
  of misc. Bootloaders are untouched; each write is read back and reverted on
  failure. Without a known network Debian opens an unprotected setup access point
  after three minutes. A generated root secret is shown once.
- The upstream Debian command pipes a remote script into a root shell with certificate
  checks off. Treat it, like the other installers, as an owner-reviewed manual step.
  Partition writes are outside this project's MCP actions; stop at a written plan.

## Backup and rollback

- Blocking: restore mode removes the mount and autostart line and the original file
  is back at once. If another tool replaced the mounted file, it stops for manual review.
- TV kit: each update saves files under the remote's data directory with a pointer
  to the latest copy; the previous remote can be restored keeping the passphrase.
  Stopping it and deleting its autostart line keeps data and apps. Returning to
  official SmartTube means uninstalling the build, which resets settings.
- Debian: the installer's uninstall restores stock recovery, the previous boot with
  AliceBridge root and the startup script; originals sit in a backup directory on
  data. From Debian a helper command or a ten-second Alice-button hold returns to
  Android. An access mode issues a new secret or key when the old one is lost.

## Verification

After an owner-run change, confirm one ordinary voice command and normal Quasar
operation. For blocking, run its check mode and schedule the untested reboot case
explicitly. For the remote, test one key press and sign-out. For Debian, read the
installer's status and prove the return to Android before relying on it.

## Limitations

Every recipe is a single-unit report from late October 2026. Blocking covers a short
host list from one configuration, not all vendor domains, and telemetry findings from
other models do not transfer. Factory-reset and root-from-scratch paths were not part
of the TV kit test. Debian contains no Alice. No script, image, APK, host list or VPN
profile is bundled or executed here.

Sources: [analytics blocking on Station 1](https://hackalice.me/recipes/station1-privacy/),
[TV kit and web remote](https://hackalice.me/recipes/station1-tv/),
[Debian on Station 1](https://hackalice.me/recipes/station1-debian/),
[recipe index and model coverage](https://hackalice.me/recipes/).
