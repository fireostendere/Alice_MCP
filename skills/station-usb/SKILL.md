---
name: station-usb
description: Plan bounded USB-host storage, Ethernet and audio experiments on supported AXG stations; triggers include USB хост, OTG, USB наушники на Алисе.
---

# USB host without losing recovery

Require owner authorization and physical recovery access. Keep Quasar enabled.
Hardware-role changes, module loads, installs and audio-route writes are
guidance-only; never execute register writes or copied switching scripts.

## Prerequisites

Limit the documented experiment to Lite Micro or retail Station 2, AXG/kernel
4.9.68. Identify the exact build and compatible module provenance. Establish
working pinned-host-key, key-only **Wi-Fi SSH** first: USB-gadget/ADB disappears
after switching the port. Require an appropriate externally powered PD/pass-through
hub; the station's sink port does not supply peripheral power.

## Diagnosis

1. With no changes, classify hub power, USB enumeration, driver binding and filesystem
   support separately. Wi-Fi association failure with a hub can indicate insufficient
   power, not a need for PHY edits. Reported bus operation is USB 2, not USB 3.
2. `HC died` can match the documented gadget/host ownership problem, but does not
   authorize a generic rebinding sequence. Do not unbind the parent dwc3 driver.
3. Compare exact kernel/config/module hashes. Matching `vermagic` is necessary but
   insufficient: vendor kernel structure differences caused crashes in earlier modules.
   Do not experiment with generic ext4/exfat/NTFS kernel modules; the source instead
   uses userspace FUSE where supported.
4. Station 2 storage/Ethernet results do not prove USB audio there. Its card reader
   can fail to signal insertion; first distinguish an empty/zero-size slot from
   filesystem corruption. Do not format it as a troubleshooting step.
5. For existing USB audio, distinguish card enumeration, supported PCM format,
   ALSA default routing and `Master` control. A hub's empty headphone jack can
   still present an active sound card and silently receive all output.

## Preview and confirmation

Present one staged owner-run experiment: enumerate a hub, then one peripheral,
then optional persistence. List exact files/modules, loss of gadget access,
power requirements and the stop condition for a watchdog reboot. Show the documented
boot-pending and disable safeguards before proposing autostart.

Audio routing needs separate confirmation. Begin with low source volume and safe
attenuation before connecting headphones; preserve the owner's calibration setting.
The wiki's attenuation is an example, not a measured hearing-safety guarantee.

## Backup and rollback

Retain prior startup files, module inventory, ALSA and hotplug configurations.
The documented runtime-only switch returns to factory role after a reboot **without
the startup script**; an installed persistent script changes that assumption.
Plan disabling OTG and restoring speaker routing through independent access.
Never change U-Boot OTG variables to work around an unsupported model.

## Verification

Ask for a sanitized outcome for one peripheral: stable power/Wi-Fi, enumeration,
correct driver, read-only test-file access or Ethernet link as appropriate. Never
write to an unidentified disk. For audio, verify both speaker return and volume
buttons; stale ALSA configuration and a second-card `Master` can explain failures.
Check Quasar, remove media only after the owner safely unmounts it, and schedule
any reboot test explicitly. Stop on kernel errors instead of repeated module loads.

## Limitations

No live module or USB test was performed here. ASIX entries in the source are built
but not hardware-verified; do not interpret a check mark as a device test. No
register addresses, role-switch payloads or unreviewed kernel modules are supplied.

Sources: [USB host recipe and kernel ABI warnings](https://hackalice.me/recipes/usb-host/),
[Station 2-specific evidence](https://hackalice.me/devices/station-2/).
