# Capture alongside Quasar and physical-mute evidence

Status 2026-10-29: **design plus source-tested, not live-accepted, read-only tools.**
No station was accessed while this was written. Every positive branch below still
needs a live observation. Separately approved observations must not stop Quasar.

An independent activation phrase needs microphone audio (raw or VQE output) while
stock Alice keeps working. Hard rules: Quasar stays running and unmodified, so no
`q stop`/restart, no launch-profile edit, no ALSA config edit and no module load as
a prerequisite. The physical mute button must keep its meaning. No household audio
may be exported to make a decision.

## Evidence already available

| Fact | Evidence class | Source |
|---|---|---|
| Capture runs in `audiod` inside `maind --platformd`. The device is ALSA card 0 device 2 (`PDM-dummy`, called `dmicarray` in config) at `inRate` 48000, with 4 microphone channels, 2 speaker-reference channels and `outRate` 16000. | Community report, Quasar 3.21, Amlogic | [Voice pipeline](https://hackalice.me/internals/voice/), pinned revision in [SOURCES.md](SOURCES.md) |
| The channels are `raw_mic_0..3`, `raw_spk_0..1` and `vqe_0`. `audiosender` keeps a 10-second ring buffer of all 7. The page says simultaneous capture of `hw:0,2` "was not tested". | Community report | Same page |
| Mini 1 playback exists only on devices 0 and 1. Device 2 is PDM capture-only. | Owner-authorized read-only check | [NATIVE_AGENT.md](NATIVE_AGENT.md) |
| The VQE observer saw channel type 6 calls with finite data. It needed a temporary launch profile and a **Quasar stop/relaunch** (`vqe_experiment.py`). | Separately approved live experiment | [NATIVE_AGENT.md](NATIVE_AGENT.md) |
| The public schemas define `QuasarMessage.io_channel_request = 159` (`request_main`, `request_all`, `request_vqe`, `request_raw`, `request_com`, `unsubscribe`) and `io_audio_channels_data = 158`. Each channel carries `type`, `name`, `data`, `sample_rate` and `format`. Channel type `VQE_OMNI = 6` is *consistent with* the observed VQE output type 6, but the two enums are not proven identical. | Public schema, not a Mini 1 protocol test | [quasar_proto.proto](https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/quasar_proto.proto), [yandex_io.proto](https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/yandex_io.proto), [channel_types.proto](https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/voicetech/library/channel_types/proto/channel_types.proto) |
| Quasar's Alice IPC accepts a local client on loopback (`speech.listen`/`speech.say`). | Live, one firmware | [SPEECH_CLIENT.md](SPEECH_CLIENT.md) |

## Decision tree

```text
Q0  Is a capture PCM held by maind?  (capture_pcms[].quasar_holders)
    no  -> stop: unexpected (idle, muted or different model). Never open the device.
    yes -> Q1
Q1  Does maind contain the IOChannelRequest/IOAudioChannelsData type names,
    and does a Quasar role listen on loopback?   (io_channel_type_names, quasar_listeners)
    yes -> A: Quasar IPC channel subscription (preferred; next step is a live test)
    no  -> Q2
Q2  Is the held stream a dsnoop with Quasar-created shared memory?
    (alsa_config.pcms[type=dsnoop,in_quasar_cfg], sysv_shm.alsa_key_matches)
    yes -> B: dsnoop co-reader
    no  -> Q3
Q3  Is a second hardware substream free?   (subdevices_available > 0)
    yes -> C: parallel substream
    no  -> NOT FEASIBLE without stopping or reconfiguring Quasar on this firmware
           (D and F need a restart or module/config change; E is rejected)
```

`python -m alice_mcp.capture_probe` returns `capture_paths`, which maps the facts
onto this tree. Its values are `*_untested`, `excluded*` or `unknown`. None of them
means "works".

### A. Quasar IPC audio-channel subscription (preferred)

- **Mechanism:** a client on loopback sends `io_channel_request{request_vqe}` and
  receives `io_audio_channels_data` frames. This is the same length-prefixed
  `QuasarMessage` transport family that the existing ASR/TTS clients use.
- **Why preferred:** Quasar keeps the hardware. The VQE output already has
  speaker-reference echo cancellation, so the station's own speech and music are
  attenuated. No ALSA contention and no restart.
- **Prerequisites:** a Quasar role must handle field 159 for external clients on
  this firmware. The client must keep up with the stream.
- **Risks:**
  - The subscription is an IPC **write**. It is non-destructive, but it needs its
    own private opt-in, one-use preview and approval.
  - Backpressure behavior is unknown. A slow client could stall Quasar's audio
    thread or be dropped.
  - It creates a continuous household-audio stream, so processing must stay in
    RAM on the device or on an authenticated host, with no retention.
  - The cost is about 32–64 KB/s and some CPU.
- **Unknowns:** which role/port serves it, whether a handshake is required, the
  actual rate/format, and whether muting yields zeros, paused frames or nothing.
- **Probe fields:** `io_channel_type_names`, `quasar_listeners`,
  `quasar_config.ports`, `quasar_config.channels`.
- **Next step (not implemented):** a bounded 5-second subscriber. It reports only
  frame counts, channel names, `sample_rate` and `format`, with no audio export,
  similar to the VQE statistics collector.

### B. ALSA dsnoop co-reader

- **Mechanism:** open the same dsnoop definition (same `ipc_key` and slave).
  Each reader has its own application pointer over the shared ring.
- **Prerequisites:** Quasar opens capture through dsnoop, either from
  `/etc/asound.conf` or an in-memory config. The parameters must match exactly,
  and `ipc_perm` and SELinux must allow the reader.
- **Risks:** if Quasar opens `hw:0,2` directly (the community report says the
  device is occupied by `audiod`), dsnoop needs an ALSA config edit plus a Quasar
  restart. That is excluded. The generic `asound.conf` is known not to describe
  the live Mini 1 route ([NATIVE_AGENT.md](NATIVE_AGENT.md)).
- **Probe fields:** `alsa_config.pcms[].type/in_quasar_cfg`, `sysv_shm`,
  `maind_alsa_config_override`, `capture_pcms[].holders`.

### C. Second hardware substream

This works only if `subdevices_available > 0` while Quasar's stream is running.
Single-substream PDM drivers are typical, so this branch is expected to be
`excluded`. The probe answers it directly.

### D. VQE preload observer: excluded

ABI evidence exists, but the method requires a temporary launch profile and a
Quasar stop/relaunch, and a persistent version would need that on every boot.
It remains available only as the existing separately authorized bounded
statistics experiment, not as a capture path.

### E. Reading Quasar's memory (the `audiosender` ring buffer): rejected

This would need ptrace or `process_vm_readv` on Quasar, undocumented layouts and
SELinux exceptions. That is a security and stability risk with no source-backed
layout.

### F. Kernel loopback or ALSA `multi`/`tee` routing: excluded

This needs a module load and/or an `asound.conf` change plus a Quasar restart.
The probe only records whether `snd_aloop` is already loaded.

### G. External microphone (fallback, not "alongside Quasar")

A host or USB microphone is fully independent of Quasar. It does not share the
station's physical mute or AEC, so echo suppression would have to use the
station's speaking state. It is suitable for host-side wake experiments only.

### H. Stock transcripts (existing `speech.listen`)

This is not independent: activation stays "Алиса" and stock Alice also acts.

## Current verdict

Whether capture is feasible without stopping Quasar is **unknown on our firmware**.
Only branches A, B or C can be feasible, and the read-only probe decides which
of them applies.

If the probe reports no IO-channel type names, no Quasar-created dsnoop memory and
no free substream, the verdict becomes **not feasible without stopping or
reconfiguring Quasar**. In that case, record it here and stop.

Even branch A needs a separately approved live subscription test before any
detector work uses real audio.

## Owner-run read-only probe

```sh
ALICE_MCP_INVENTORY=/PRIVATE/inventory.json python -m alice_mcp.capture_probe --station ALIAS
```

The command prints one compact JSON object, `{"outcome":"complete","data":{...}}`,
and exits 1 with `blocked` if inventory or SSH trust fails. It uses explicit
pinned key-only SSH, including for native-configured stations, and never falls
back to native transport. The same facts are also available as
`station_probe(station_id, probe="capture")` on SSH-configured stations. The
resident agent does not implement this probe: its `microphone` probe is a subset,
and adding the probe there needs a resident upgrade.

**What it reads:**

- `/proc/asound/*/pcm*c/{info,sub0/status,sub0/hw_params}`
- descriptor link targets via `ls -l /proc/*/fd`
- `maind` command-line roles
- `/proc/net/tcp*` LISTEN entries owned by `maind`
- `/proc/sysvipc/shm`
- `/etc/asound.conf`: PCM names/types, the `ipc_key` count and a SHA-256
- `quasar.cfg`: a structural token scan for the `aliced`/`audiod`/`audiosender`/`glagold`
  ports, the `audiod` rates/channel counts and channel-name tokens
- `ALSA_CONFIG_PATH` presence in `maind`'s environment
- a NUL-split type-name scan of `maind`, skipped above 96 MiB

**What it never does:** open a PCM/control node, capture, load a library, attach
to a process, write, mount, or change Quasar.

**Output limits:** environment values, config values outside the allowlist,
remote addresses and raw lines never leave the parser. The JSON scan is a
heuristic: a brace inside a string can confuse it.

**Tests:** synthetic trees under dash/GNU tools. A one-off run under BusyBox
1.36.1 `sh`/`awk`/`grep` found and fixed a NUL-byte `grep` difference. Station
compatibility is unverified.

**What the owner observes:** nothing audible or visible. Report `capture_paths`
plus the `capture_pcms`, `quasar_listeners`, `quasar_config` and `sysv_shm` fields.

## Physical-mute evidence runner

What already exists:

- Yandex documents hardware disconnection of the microphones.
- The DeviceState IPC watcher returned `unknown`.
- The VQE level comparison requires a Quasar interruption and has not produced
  accepted phase coverage ([NATIVE_AGENT.md](NATIVE_AGENT.md)).

The new runner needs none of these:

```sh
ALICE_MCP_INVENTORY=/PRIVATE/inventory.json python -m alice_mcp.mute_evidence --station ALIAS
```

It requires an interactive terminal beside the station. Each phase starts only
after an exact typed word, and the snapshot is taken after the owner has set the
physical state. This avoids the earlier fixed-window timing failure.

**Phases:**

1. `UNMUTED` with red off.
2. An automatic 10-second quiet control (do not touch the station).
3. Short-press, then `MUTED` with red on.
4. Short-press, then `UNMUTED` with red off.

**Each snapshot** (read-only, over pinned SSH) contains:

- `/proc/interrupts` totals
- `/sys/kernel/debug/gpio` values, only if debugfs is already mounted
- `/sys/class/leds/*/brightness`
- regulator name/state
- each capture PCM's state, and whether `hw_ptr` advanced over one second
- the `maind` PIDs

**Interpretation:**

| Output | Meaning | Not meaning |
|---|---|---|
| `gpio_mute_candidates` (stable while unmuted, different while muted) | The SoC can read a mute-correlated line, so a pipeline can gate itself on it | That power is cut |
| `button_irq_candidates` (no change in control, 1–8 per press) | The button press reaches the SoC | Software controls mic power |
| `led_mute_candidates`, `regulator_mute_candidates` | Software-visible indicator or supply state follows mute | Microphone isolation |
| `capture_stream_continues_while_muted=true` | The PDM stream keeps running while muted, so a co-reader would still get data. The acoustic level test is still needed | Data is non-zero or zero |
| `capture_stream_continues_while_muted=false` | The stream pauses or stops while muted | A failed mute |
| `quasar_unchanged` | The `maind` PID set was stable across phases | Quasar is healthy |

Absence of all candidates is reported as `mute_signal_observable=false`. That
means "not observed through these interfaces", not "mute ineffective".
`electrical_power_cut_verified`, `physical_effects_verified` and `audio_captured`
are always `false`.

**Remaining gap:** the decisive acoustic test is muted versus unmuted signal level
on the actual capture branch. It is blocked on branch A, B or C delivering frames.
When that exists, add per-phase statistics (no audio export) to this runner.

## Model and firmware gaps

- **Mini 1 (`station-mini`):** device-2 facts come from live checks. The schema
  and port facts are unverified.
- **Mini 3 (Bergamot) and Lite:** no capture observation exists. The probe is
  model-agnostic, but the community voice page is mostly Lite and Quasar 3.21.
- No station run of either tool has been performed. These are synthetic tests
  only.

## Mini 1 probe result (2026-10-29)

The first owner-authorized read-only run on Mini 1 found a probe defect: station
BusyBox 1.26.2 awk parsed `roles (…)` as an undefined function call and dropped
the busy capture PCM. That was fixed with a regression test; the rerun completed.

- The only microphone PCM (PDM, 8 channels, S32_LE, 48 kHz) has one subdevice,
  running and held by the Quasar `platformd` process: **second substream excluded**.
- Quasar opens ALSA `default` of type `hw`, not dsnoop, and no SysV segment
  matches an ALSA IPC key: **dsnoop excluded without Quasar reconfiguration**.
- `snd_aloop` is not loaded: **loopback excluded**. VQE preload remains excluded.
- `maind` contains the `IOChannelRequest`/`IOAudioChannelsData` schema names, and
  the Quasar config declares `vqe_0`, `raw_mic_0`, `raw_spk_0` channels. Quasar
  listens on loopback IPC ports. **The IO-channel subscription is the only remaining
  path; it is untested.** No audio was opened, captured or exported.
