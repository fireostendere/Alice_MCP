---
name: station-recovery
description: Triage station boot loops and failed customizations with bounded evidence and reversible recovery guidance; triggers include Алиса не загружается, бутлуп, откат.
---

# Recover the last change first

Require owner authorization and physical access. Keep Quasar enabled whenever it
is running. Reset, flash, rooting, service shutdown and repair writes are
guidance-only; never execute them or repeat an uncertain repair automatically.

## Prerequisites

Identify model/firmware, last known working state, last change and whether existing
key-only pinned-host-key SSH still works. Ask whether power, network or firmware
changed at the same time. Do not treat an inaccessible host as permission to scan
the network, enable ADB or reset settings.

## Diagnosis

1. No boot indication: check the owner's normal supply and cable before software
   explanations. For a powered USB hub, insufficient supply can break Wi-Fi even
   before USB host switching. Do not bypass electrical protections.
2. Reboot loop on supported Amlogic: a sanitized reboot-reason classification can
   distinguish watchdog from normal restart. Watchdog is a clue, not proof of
   a bad kernel module. Compare with the last added startup/module change.
3. Collect only a short owner-redacted error excerpt. The cited Lite startup
   clears pstore, so its absence does not prove no crash; raw kernel/daemon logs
   can contain identifiers and must stay private. Do not deliberately reproduce
   a kernel panic solely to obtain a log.
4. Existing access but no custom SSH on Pickle may be expected: rootfs startup
   changes do not persist there. Android and Yocto need their own service map.
5. Map the regression to one object: custom startup entry, OTG enable state,
   audio route, package, or previously changed OTA files. Do not disable Quasar
   or unrelated startup entries to simplify the investigation.

## Preview and confirmation

Name one reversible repair, its exact precondition, affected file and expected
outage. The owner confirms the target and performs it manually. For OTG, explain
the documented disable/boot-pending guards before considering startup removal.
For audio, restore the speaker route before changing kernel drivers.

## Backup and rollback

Preserve the suspect custom entry and known-good originals before moving or
restoring anything. Do not delete the only recovery access. Revert one recorded
change at a time, maintaining hashes and permissions. A factory reset clears
data but **does not undo rootfs edits or restore a modified boot image**.
Private backups and removed files must not be attached to a public issue.

## Verification

After the owner's scheduled recovery, check normal boot, secure access, Quasar,
audio or hub function as applicable, and absence of the original symptom. Confirm
the suspected startup component is inactive without assuming all custom software
must be removed. If the device is still unstable, stop before flash escalation.

## Limitations

The generic recovery page does not establish reliable USB-burning entry or a safe
image source. Its UART unlock idea is untested; UART rates differ by model, so
do not generalize its Lite settings to JBL or Midi. No blind partition writes,
bootloader edits, generic rollback scripts or factory-reset execution are offered.

Sources: [recovery and reset semantics](https://hackalice.me/start/recovery/),
[known platform pitfalls](https://hackalice.me/internals/gotchas/),
[OTG guards](https://hackalice.me/recipes/usb-host/).
