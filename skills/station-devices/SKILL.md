---
name: station-devices
description: Identify Yandex Station platforms and source-linked LED/audio/GPIO unknowns before interpreting declared runtime controls; triggers include модель Станции, совместимость, Мини или Мини 3.
---

# Identify before acting

Require owner authorization for access. Keep Quasar enabled. This identification
skill is read-only. Installation, root, OTA, flash and unsupported/high-risk
operations remain guidance-only, never automatic. Implemented service/device
actions belong to their separate preview/approval workflows, not identification.

## Prerequisites

Ask for the retail model, desired outcome and firmware version, without addresses,
serials or account details. Existing authorized access is useful, not a prerequisite
to discussing compatibility. Read the [model matrix](references/models.md).

## Diagnosis

1. Match retail identity against platform evidence, not a similar internal name.
   Internal device-tree `station-midi` means Station 2; Cucumber means retail Midi;
   `yandexstation_2` means Android Max.
2. If access already exists, compare kernel architecture (`uname -m`), OS family,
   firmware version and ELF interpreter/class of the intended application.
   A 64-bit kernel does not establish a 64-bit system userspace.
3. Select Buildroot, Android or Yocto guidance. Treat Orion as unknown rather than
   borrowing a nearby model. Distinguish Mini 1, Bergamot and Pickle explicitly.
4. Record observed facts separately from wiki reports, estimates and missing data.
    Do not collect factory contents, full boot arguments or an unfiltered inventory.
5. Use `get_model_interfaces(model)` for local source-linked interface evidence and
   unknowns. With a configured private inventory, `get_station_controls(station_id)`
   lists declared action/asset/binding/option aliases, not paths, private values or
   verified hardware availability. Neither tool accesses the station. A declared
   model or action allowlist is not hardware detection or permission to write.

## Interface evidence and unsupported writes

- Lite: sysfs `micro:{red,green,blue}:ring1` through `ring3` brightness nodes.
  Mini 2: `mini2:{red,green,blue}:ring1` through `ring6`. Applicable triggers must
  be checked; do not infer Lite 2 support from Lite.
- Mini 3/Bergamot: 6×1, 24-bpp BGR framebuffer, 18 bytes. Station 2: 89×1,
  32-bpp RGBW, 356 bytes; index 88 is not visible. Runtime geometry/stride and
  exact locally reviewed `maind` firmware SHA-256 must match, not just model name.
- Mini 1 I2C ring protocol is unknown; Midi framebuffer channel order is unknown.
  Both have unsupported LED writes. Never guess an I2C payload or byte ordering.
- Full GPIO pinouts are unavailable. Lite's AO 497–511 and EE 411–496 ranges and
  named consumers do not identify safe writable pins. Midi's `zb-reset-gpios` and
  `zb-boot-gpios` lack line mappings. No arbitrary GPIO/I2C/memory/register controls.
- Volume requires a runtime-probed `Master` control on operator-reviewed Buildroot;
  the public exact command is documented only for Lite. Native maind/GStreamer
  playback evidence is not proof of every firmware's codecs, route or speakers.

## Preview and confirmation

Return a short compatibility decision: model/platform, exact evidence, proposed
topic, exclusions and confidence. Ask the owner to resolve an ambiguous model
before any change plan. A support badge or server profile is not permission.
The experimental runtime implements bounded LED/audio/media/sound/config actions,
but identification does not call apply. `preview_station_action` is a separate
read-only inspection under private policy gates; `apply_station_action` needs the
exact preview digest and native human elicitation within a one-use 300-second TTL.
It requires secure SSH and preinstalled Python 3.8+, never automatic installation,
rooting or ADB. Native Windows device writes are unavailable; unknown formats fail closed.

## Backup and rollback

Identification needs no device backup and changes nothing. For a later modification,
require that topic's exact file-level backup and a working recovery path first.
Do not create whole-partition dumps merely to identify hardware.
Later persistent runtime edits create owner-only records at
`/data/opt/alice-mcp/backups/<id>.json`. Record-specific `restore` requires a new
preview/approval and rejects changes observed by optimistic checks, not atomic
compare-and-swap. The helper lock does not lock external writers; a late update
can be overwritten or deleted. Keep target writers idle or do not apply; never
stop Quasar to obtain exclusivity. It is not factory recovery or a broad reset.
Config changes do not imply reload or a service restart.

## Verification

Cross-check at least two independent identity attributes when available: retail
label plus OS/SoC, or device-tree model plus firmware family. Report uncertainty
instead of claiming a device test. Leave Quasar running and end without writes.
File/control post-checks from another workflow do not prove visible/audible hardware
effects; declared controls, runtime checks and owner observations remain distinct.

## Limitations

The matrix covers all fourteen dedicated upstream model pages and the device
index, not fourteen validated integrations. Duo Max and Station Pro have no
dedicated pages at this revision. Firmware changes invalidate inferred persistence,
TLS behavior and binary compatibility. This repository has not tested stations.
Public evidence is pinned to revision `1ab048bf95c0e5e547a1ff6c801d2ed9deb193db`.
No station access or hardware test was performed; integration checks are
offline/synthetic. JSON endpoint/voice/model adapters are explicit operator bindings,
not manufacturer-certified schemas or backend installation; native `quasar.cfg`
bindings cover only `uniProxyUrl`/`uniProxyQuicUrl`, not arbitrary stock voice replacement.

Source: [upstream device index](https://hackalice.me/devices/), with exact model
links and conflicts in the local reference.
