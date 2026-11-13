# Model registry and bring-up

`src/alice_mcp/models.py` holds one profile per retail device. It is the single model
list (`core.MODELS`, the inventory `model` field) and the scaffold to fill when a new
speaker is brought up. A profile is reference data from community reports: it is not
hardware detection, not a device test and not permission to write.

## Lineup

Status is this project's evidence, not the upstream wiki's. Only Mini 1 is a live
pilot; everything else is offline-tested code or an empty slot.

| `model` | Retail device | OS family | Adapters today | Evidence |
|---|---|---|---|---|
| `station-mini` | Станция Мини (Mini 1) | Buildroot | I2C ring shows/capture, native and OpenAI speech, Quasar IPC, wake/voice, resident agent, generic audio/config | live pilot |
| `station-mini3` | Станция Мини 3 (Bergamot) | Buildroot | framebuffer `led.effect`/`led.show`, `speech.say`, generic audio/config | one audible TTS pilot |
| `station-lite`, `fiero-hi` | Станция Лайт, Fiero Hi (one board) | Buildroot | sysfs `led.effect`/`led.show`, generic audio/config, host speech | synthetic; one host-speech run on a Lite |
| `station-mini2` | Станция Мини 2 | Buildroot | sysfs `led.effect`, generic audio/config | synthetic |
| `station-2` | Станция 2 | Buildroot | RGBW framebuffer `led.effect`, generic audio/config | synthetic |
| `station-lite2`, `station-mini3-pro`, `station-midi`, `station-3`, `station-street` | Лайт 2, Мини 3 Про, Миди, Станция 3, Стрит | Buildroot | generic audio/config only | none |
| `yandex-hub` | Умный хаб | Buildroot | `config.select`, `media.upload`, `restore` (no speaker) | none |
| `station-gen1`, `station-max`, `station-max-zigbee` | Станция (1), Макс, Макс с Zigbee | Android | none: read-only probes | none |
| `jbl-link-music`, `jbl-link-portable` | JBL Link Music / Portable | Yocto | none: read-only probes | none |
| `station-duo-max`, `station-pro` | Дуо Макс, Станция Про | unknown | none: read-only probes | none |

`get_capabilities().models` lists the same registry. `get_model_interfaces(model)`
returns the profile, its open `todo` items and the per-action matrix:

- `adapter` — model-specific code exists (it still needs the private policy,
  reviewed firmware digest and a successful preview);
- `generic` — the model-agnostic Buildroot path (`audio.volume`, `audio.play`,
  `media.upload`, `sound.replace`, `config.select`, `restore`), probed on the
  station at preview and unverified on that model. `speech.say` is generic too:
  with a private `local_tts` the host synthesizes and the firmware's player speaks
  ([host speech](CONTROLS.md#host-speech-without-the-agent));
- `none` — nothing to call; the action is rejected before any station traffic.

The matrix is derived from the real gates (`device_runtime.ACTION_MODELS`, the LED
interface profile and the OS family), so it cannot disagree with what a preview
accepts. `get_station_controls(station_id).supported_actions` is the private
allowlist narrowed to that matrix; `allowed_actions` itself is not changed.

## Bringing up a station of another model

1. Add the private inventory entry with the closest `model` and no `controls`.
   Reads work without a write policy.
2. `station_probe(station_id, "identify")`: fixed device-tree tokens and file
   presence only. `declared_matches=false` means the entry names the wrong model;
   `null` means the board has no model-specific token (Mini 1, Стрит, Android,
   Yocto) and proves nothing either way. `os_family` is a heuristic from the
   Quasar init script, `build.prop`, systemd and native `maind`.
   `quasar_init_script=false` means `/etc/init.d/S99quasar` is absent, so Quasar
   start/restart service actions cannot work on that station.
3. Run `health`, `storage`, `services`, `audio`, `led`. A probe that reports
   unavailable on a non-AXG board is a gap to record, not a fault to fix remotely.
4. Read `get_model_interfaces(model)`. `generic` actions may work as they are:
   review `/system/vendor/quasar/maind` locally, put its SHA-256 in the private
   `controls.firmware_sha256`, then try one quiet bounded action through a preview.
5. Record what the owner actually saw or heard. A successful command is not an
   observed effect.

## Adding an adapter for a model

Write the failing synthetic test first, then:

1. `models.py`: correct the profile (`os`, `userspace`, `dt`, `led`) and replace
   resolved `todo` lines with what was observed and on which firmware.
2. `device_runtime.py`: add the model to `ACTION_MODELS[action]`. For LEDs also
   extend `LED_ZONES`/`led_layout` (shows) or `interfaces()` (the `led.effect`
   interface). This file is sent to the station with every request and shares one
   192 KiB budget with the largest upload, so keep additions small and put
   host-only logic in its own module.
3. `controls.invoke`: route the action to the adapter if it is not the generic
   on-station helper. Adapters built on pinned binaries (speech, Quasar IPC, live
   voice, resident agent) are Mini 1 ARM32 builds: a new model needs its own
   reviewed build, pin and policy key in `validate_policy`, never a reused pin.
4. `tests/test_models.py` proves the matrix still matches the gates. Add the
   adapter's own synthetic tests, then a live acceptance entry in
   [LIVE_ACCEPTANCE.md](LIVE_ACCEPTANCE.md) before raising `evidence`.

Hard limits do not move with a new model: no arbitrary shell, no factory reset or
flashing, Quasar stays enabled, an uncertain write is never retried.

## Live observations (2026-11-11)

Owner-requested checks on two powered stations; everything else stays synthetic.

| Check | Lite | Mini 1 |
|---|---|---|
| `identify` | `station-lite` and `fiero-hi` (shared board), Buildroot, declared model matches | no device-tree token, Buildroot by `maind`, no `S99quasar` init script |
| `reboot` probe | reason reported | `unknown` (the Amlogic node is absent) |
| SSH keepalive | a 100 s idle session stays up | a 100 s idle session stays up |
| `quasar.volume` step up and back | not applicable | applied both ways with the resident agent down |
| `station_live` listener | not applicable | 130 s listening, clean stop; after its transport is killed it ends in under 2 s and restarts |
| `speech.openai` | not applicable | refused by the provider (no API credits): nothing played, cleanup verified |

On 2026-11-12 the Mini 1 resident agent, absent since that boot, was started once by
its own init hook and answered native probes ([NATIVE_AGENT.md](NATIVE_AGENT.md)).
From the station, with the Quasar CA bundle the voice client uses, the Gemini API
host resolved and completed a verified TLS request (HTTP 403 without a key).

Also on 2026-11-12 `speech.say` ran through a host synthesizer program on both
powered stations: on the Mini 1 through the agent's PCM player and on the Lite over
SSH into the firmware player. Both reported `applied`; neither is owner-confirmed
as heard. Giving the Lite a `controls` entry showed that it has no Python, so the
Python-based generic actions cannot run on it as they are.

Not exercised on hardware: an upload near the 49,152-byte ceiling (no such asset is
registered), the volume ceiling below 100 %, the PC audio source, Mini 1 `led.show`
(needs a fresh capture), host speech on any model but Mini 1 and Lite, and anything
on Mini 3 (offline).

## Known gaps

- Profiles follow the wiki revision pinned in [SOURCES.md](SOURCES.md). The access
  (root, ADB, UART, OTA) pages were not re-read at that revision; see there.
- Station 3's ring is `/dev/fb2`; the framebuffer adapter and the `led` probe look
  only at `/dev/fb0`, which on that model is the ambilight.
- Policy keys for pinned artifacts (`quasar_client`, `speech_client`, `voice`) are
  accepted for Mini 1 only, and `tts_client` for Mini 1 and Mini 3. `local_tts` is
  host-side and accepted for any model.
- The Python-based generic actions need Python 3.8+ on the station; the one Lite
  checked has none. Host speech does not need it.
- The `reboot` probe reads an Amlogic 4.9 node and reports `unknown` elsewhere.
- Stations behind the resident agent answer only its own probe set; `identify`
  needs an SSH-configured entry.
