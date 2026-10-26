---
name: station-devices
description: Identify Yandex Station platforms and firmware limits before selecting an access or modification workflow; triggers include модель Станции, совместимость, Мини или Мини 3.
---

# Identify before acting

Require owner authorization for access. Keep Quasar enabled. This skill is read-only; installation, root, OTA, flash and service changes are guidance-only, never automatic.

## Prerequisites

Ask for the retail model, desired outcome and firmware version, without addresses,
serials or account details. Existing authorized access is useful, not a prerequisite
to discussing compatibility. Read the [model matrix](references/models.md).

## Diagnosis

1. Match retail identity against platform evidence, not a similar internal name.
   `station-midi` means Station 2; Cucumber means retail Midi;
   `yandexstation_2` means Android Max.
2. If access already exists, compare kernel architecture (`uname -m`), OS family,
   firmware version and ELF interpreter/class of the intended application.
   A 64-bit kernel does not establish a 64-bit system userspace.
3. Select Buildroot, Android or Yocto guidance. Treat Orion as unknown rather than
   borrowing a nearby model. Distinguish Mini 1, Bergamot and Pickle explicitly.
4. Record observed facts separately from wiki reports, estimates and missing data.
   Do not collect factory contents, full boot arguments or an unfiltered inventory.

## Preview and confirmation

Return a short compatibility decision: model/platform, exact evidence, proposed
topic, exclusions and confidence. Ask the owner to resolve an ambiguous model
before any change plan. A support badge or server profile is not permission.

## Backup and rollback

Identification needs no device backup and changes nothing. For a later modification,
require that topic's exact file-level backup and a working recovery path first.
Do not create whole-partition dumps merely to identify hardware.

## Verification

Cross-check at least two independent identity attributes when available: retail
label plus OS/SoC, or device-tree model plus firmware family. Report uncertainty
instead of claiming a device test. Leave Quasar running and end without writes.

## Limitations

The matrix covers all fourteen dedicated upstream model pages and the device
index, not fourteen validated integrations. Duo Max and Station Pro have no
dedicated pages at this revision. Firmware changes invalidate inferred persistence,
TLS behavior and binary compatibility. This repository has not tested stations.

Source: [upstream device index](https://hackalice.me/devices/), with exact model
links and conflicts in the local reference.
