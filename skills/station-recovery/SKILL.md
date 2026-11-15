---
name: station-recovery
description: Triage station boot loops and failed customizations with bounded evidence and reversible recovery guidance; triggers include Алиса не загружается, бутлуп, откат.
---

# Recover the last change first

Require owner authorization and physical recovery access. Keep Quasar enabled
whenever it is running. One bounded, explicitly authorized reversible repair may
use an actually available permitted tool and its required approval flow. Operations
lacking such a tool remain guidance-only: name the missing capability, prerequisite
or manual physical requirement. Respect runtime denials; never retry an uncertain
repair automatically. Reboot or Quasar shutdown requires an explicit owner request,
never an implicit recovery step.

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
outage. Verify the target and backups before using a permitted tool within the
owner's explicit scope; do not ask again for already-covered actions. Obtain consent
for scope extensions. If execution is unavailable, name the concrete blocker and
the necessary owner-performed step. For OTG, explain
the documented disable/boot-pending guards before considering startup removal.
For audio, restore the speaker route before changing kernel drivers.

## Backup and rollback

Preserve the suspect custom entry and known-good originals before moving or
restoring anything. Do not delete the only recovery access. Revert one recorded
change at a time, maintaining hashes and permissions. A factory reset clears
data but **does not undo rootfs edits or restore a modified boot image**.
Private backups and removed files must not be attached to a public issue.

## Verification

After the authorized repair, use permitted checks for normal boot, secure access, Quasar,
audio or hub function as applicable, and absence of the original symptom. Confirm
the suspected startup component is inactive without assuming all custom software
must be removed. Ask the owner only for observations tools cannot establish.
Do not reboot as an implicit test. If still unstable, stop before flash escalation.

## Limitations

Project recovery tests are offline/synthetic, not evidence of a successful device
repair. The generic recovery page does not establish reliable USB-burning entry or a safe
image source. Its UART unlock idea is untested; UART rates differ by model, so
do not generalize its Lite settings to JBL or Midi. No blind partition writes,
bootloader edits or generic rollback scripts are offered. Reset/flash is not an
Alice MCP action. Reset requires an explicitly accepted data-loss scope and the
exact-model procedure, not a full flash backup that itself requires root. A
separately owner-authorized boot/partition write requires verified exact-model
firmware/artifact, image source, appropriate backups and recovery access.

Sources: [recovery and reset semantics](https://hackalice.me/start/recovery/),
[known platform pitfalls](https://hackalice.me/internals/gotchas/),
[OTG guards](https://hackalice.me/recipes/usb-host/).
