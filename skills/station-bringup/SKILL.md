---
name: station-bringup
description: Bring a station of any model into Alice MCP step by step - register it, identify the board read-only, read the model profile and action matrix, try one bounded action, then fill the profile or add an adapter; triggers include добавить колонку, новая Станция, другая модель, подключить Мини 3, поддержка модели.
---

# Identify, read the matrix, then one quiet action

Require owner authorization for the named station. Keep Quasar enabled. Root,
ADB, OTA, flashing and package installation are guidance-only prerequisites that
the owner completes separately ([station-access](../station-access/SKILL.md));
this workflow starts from existing pinned-host-key, key-only SSH.

Only Mini 1 is a live pilot. Every other model is offline-tested code or an empty
profile slot, so treat each first run as an experiment and report what was seen.

## Prerequisites

The owner adds the station to the private inventory with the closest `model` and
no `controls` object. Never ask for the address, key, serial or account. Do not
edit the inventory, enable the global write gate or invent a firmware digest on
the owner's behalf. `get_capabilities().models` lists every accepted `model`.

## Diagnosis

1. `list_stations`, then `station_probe(station_id, "identify")`. It returns only
   fixed device-tree tokens and file presence. `declared_matches=false`: stop and
   have the owner correct `model`. `null`: this board has no model-specific token
   (Mini 1, Street, Android, Yocto), which proves nothing either way.
   `device_tree_models` can hold two names: Lite and Fiero Hi are one board.
2. `get_model_interfaces(model)`: `profile.os` selects the adapter family,
   `profile.todo` lists what is still unknown, `actions` is the matrix.
   `adapter` is model-specific code, `generic` is the runtime-probed Buildroot
   path, `none` is rejected before any station traffic. Device-tree
   `yandex,station-midi` is retail Station 2; `yandexstation_2` is Max.
3. `station_probe` with `health`, `storage`, `services`, `audio`, `led`. An
   unavailable probe on a non-AXG board is a gap to record. `kernel_arch` does not
   establish userspace ABI; `profile.userspace` is a community report.
4. Android, Yocto and unknown-platform models stop here: read-only probes are the
   whole supported surface. Do not borrow a Buildroot recipe for them.

## Preview and confirmation

For a Buildroot model the owner reviews `/system/vendor/quasar/maind` locally and
puts its SHA-256 into the private `controls.firmware_sha256`. Then
`get_station_controls(station_id).supported_actions` is what this station can be
asked to do. Pick one `generic` action that is quiet and reversible, normally
`audio.volume` a few percent below the current level or a one-second `audio.play`
of a registered clip, and call `run_station_action(station_id, action, parameters)`.
It previews and applies in one call, with a native form unless preauthorized.

A blocked preview on a new model is the expected answer when the mixer control,
Python 3.8+ or the player is missing. Report it; do not look for another route,
and never retry an `unknown` or `do_not_retry` result.

When an action needs model-specific code, follow `docs/MODELS.md` in the source
repository: failing synthetic test first, profile and `ACTION_MODELS` entry next,
adapter last. Pinned clients (speech, Quasar IPC, live voice, resident agent) are
Mini 1 ARM32 builds; another model needs its own reviewed build and pin.

## Backup and rollback

Identification and probes change nothing. `audio.volume` leaves a restore record
(`restore` with its `backup_id`); `audio.play` leaves nothing. Do not start with
`sound.replace`, `config.select` or `media.upload` on an unqualified model: they
are persistent edits whose reload behaviour is unknown there. Remove the private
`controls` object to return the station to read-only.

## Verification

The owner states what was heard or seen. Record model, firmware build, action,
outcome and that observation, then update the profile's `todo` and `evidence`
only to the level actually reached: a passing command is `synthetic` at best, an
owner-observed effect on one unit is `partial_live`.

## Limitations

Profiles summarise community reports and are not device tests. `identify` cannot
tell Max from Max with Zigbee, or Lite from Fiero Hi, and needs an SSH-configured
entry. A new model gets no exemption from the hard limits: no arbitrary shell,
no flashing or factory reset, no Quasar stop.

Sources: [device index and minimal data set for a new model](https://hackalice.me/devices/),
[how to report a model](https://hackalice.me/contributing/).
