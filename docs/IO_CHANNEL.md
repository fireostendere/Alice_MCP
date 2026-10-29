# Quasar IO audio-channel subscription

Status 2026-10-29: **research, a read-only probe and a bounded level pilot, all
with synthetic tests only.** No station was accessed while this was written.
Nothing here has been observed on a device, and no audio has been captured.

Read-only checks on Mini 1 left one way to get room audio without stopping or
reconfiguring Quasar: subscribe to Quasar's own audio channels over its local IPC.
The other routes are ruled out. The microphone PCM has a single running substream
held by `platformd`, Quasar opens `default` as type `hw` rather than `dsnoop`, and
the VQE preload hook needs a restart. See the capture-coexistence notes (W4) for
that evidence.

## Sourced facts

Schema links point to [v3rt3p/quasar-protos @ `1044fbd`][protos]. That repository
says its schemas were extracted from the **Station 2** `maind`, not Mini 1. Its
LICENSE reads "Copyright (c) Yandex LLC … All rights reserved", so it is used as
reference only and is not vendored. Community links point to
[Marker284/yandex-station-extended @ `930a0ff`][wiki], which is newer than the
catalog pin in [SOURCES.md](SOURCES.md), and to
[derangedabbot/yandex-station-2-airplay @ `3ed9bfe`][abbot].

| # | Fact | Source |
|---|---|---|
| S1 | `QuasarMessage.io_audio_channels_data = 158` (`IOAudioChannelsData`) and `io_channel_request = 159` (`IOChannelRequest`). For comparison, `alice_state = 3` and `remoting = 210`. | [quasar_proto.proto L645-646][qp645], [L585][qp585], [L716][qp716] |
| S2 | `IOChannelRequest` has six `EmptyMessage` fields: `request_main=1`, `request_all=2`, `request_vqe=3`, `request_raw=4`, `request_com=5`, `unsubscribe=6`. | [yandex_io.proto L343][yio343] |
| S3 | `IOAudioChannelsData` has `repeated IOAudioChannelData channels = 1`. Each `IOAudioChannelData` has: `type=1` (`voicetech.AudioInputChannelType`), `name=2`, `data=3` (bytes), `is_for_recognition=4`, `doa_angle=6` (double), `sample_rate=7` (int32), `format=8` (`SoundFormat.Type`), `record_start_timepoint_ms=9`, `duration_ms=10` and `tags=11`. | [yandex_io.proto L317-333][yio317] |
| S4 | `AudioInputChannelType` values: `UNKNOWN 0`, `RAW_MIC 1`, `RAW_MAIN_MIC_SYNC 2`, `RAW_AUXILIARY_MIC_SYNC 3`, `RAW_SPEAKER 4`, `RAW_FEEDBACK_SYNC 5`, `VQE_OMNI 6`, `VQE_BEAMFORMING 7`, `VQE_BACKGROUND_NOISE_REDUCER 8`, `VQE_OMNI_WITH_GAIN 9`, `VQE_STATE 10`. | [channel_types.proto][types] |
| S5 | `SoundFormat.Type` values: `UNKNOWN 0`, `PCM 1`, `OPUS 2`, `RAW 3`, `HEADERLESS_OPUS_STREAM 4`, `HEADERLESS_OPUS_STREAM_BYTE_SIZE 5`. Bit depth and endianness inside `PCM` are **not** stated. | [remoting.proto L37][remoting] |
| S6 | The native local IPC is TCP on loopback. Each frame is a 4-byte little-endian length followed by protobuf. Field `253` (varint) is the message kind, and `252` carries the nested message. Kind `3` is AliceState and kind `210` is a Remoting call. Port 9875 is `aliced`. | [abbot 09-voice.md L17][abbot17], [station-2.md L232][st2], [sounds.md][sounds] |
| S7 | This repository's own clients use S6 live: Mini 1 ASR and Mini 3 TTS. Packed `252/253` is required for outgoing requests; the full-`QuasarMessage` form failed for TTS. | [SPEECH_CLIENT.md](SPEECH_CLIENT.md) |
| S8 | Community Mini 3 port table: 9875 `aliced`, 9891 `external_commandsd`, 9899 `glagold`, 9900 `mediad`, 9906 `audioclient`, 9910 `iohub_clients`, 9922 `volume_manager`. Internal ports are configured in `quasar.cfg`. There is no `audiod` row. | [station-mini3.md L117][mini3], [network.md][network] |
| S9 | `audiod` runs inside `maind --platformd`. The channels listed in `audiosender.availableChannels` are `raw_mic_0..3`, `raw_spk_0..1` and `vqe_0`. `vqe_0` is 16 kHz mono and feeds the spotters. `audiosender` keeps a 10 s ring buffer. This is from an Amlogic/Quasar 3.21 report, not Mini 1. | [voice.md L44-58][voice], [L130][voice130] |
| S10 | The only public working `vqe_0` tap (Lite 2 / Plum) hooks `YandexVqeC_GetOutputChannelData` (type 6, channel 0, float32) through `LD_PRELOAD` and **restarts Quasar**. It does not use IO channels, and it is excluded here. | [microphone.md L47-58][mic] |
| S11 | [AlexxIT/YandexStation @ `4e8567b`][yst] has no IO-channel or local audio IPC code; it uses glagol and the cloud. A web search on 2026-10-29 found no public description of IO-channel serving. Both are negative results. | searches |
| S12 | Mini 1 W4 live read-only run: `maind` contains the `IOChannelRequest`/`IOAudioChannelsData` type names; `quasar.cfg` declares `vqe_0`, `raw_mic_0`, `raw_spk_0` and an available-channels key, plus `aliced`/`audiod`/`audiosender`/`glagold` sections; Quasar roles listen on many loopback ports. | W4 report (parent session) |

## Assumptions (unverified)

- **A1, framing.** The packed kind equals the `QuasarMessage` field number. That
  holds for 3 and 210 (S1, S6), so a subscription is assumed to be
  `{252: IOChannelRequest{3: {}}, 253: 159}`, sent as
  `09 00 00 00 e2 0f 02 1a 00 e8 0f 9f 01`, with data arriving as kind 158. The
  helper also accepts a full `QuasarMessage{158: …}` and rejects frames that mix
  both forms.
- **A2, server.** Which service answers field 159 is **unknown**. Ranked
  candidates:
  1. `audiod`: it owns capture and has its own config section.
  2. `iohub_services` / `iohub_clients`: the `IO*` message family is shared with
     `IOHubMessage`.
  3. `audiosender`: it owns `availableChannels`.
  4. `aliced` on 9875: an IPC known to work, and a consumer of `vqe_0`.

  The idea that YandexIO's audio-source client connects to `audiod` is a
  recollection, not a source. The probe ranks only services that a Quasar process
  is actually listening on.
- **A3, lifetime.** A subscription is assumed to last for the connection.
  `unsubscribe=6` or closing the socket is assumed to end it.
- **A4, access.** No handshake or authentication is assumed on loopback. This
  holds for 9875 and is unknown for other ports.
- **A5, payload.** `PCM` data is assumed to be mono S16LE per channel entry, and
  `vqe_0` 16 kHz. The helper reports `bytes_per_sample_estimate` from
  `duration_ms` to test this. A value of 4 would mean float32 or S32, and then the
  S16 levels are invalid.
- **A6, channel selection.** `request_vqe` is assumed to yield type 6
  (`VQE_OMNI`, the type also seen in the VQE experiment), and `request_raw` to
  yield `raw_mic_*`/`raw_spk_*`. Whether the two type enums are identical is not
  proven.
- **A7, backpressure.** Quasar is assumed to drop or buffer frames for a slow
  client. The helper reads continuously and does no heavy work.
- **A8, physical mute.** What a subscription delivers while muted (zeros, gaps or
  nothing) is untested.

## Step 1: read-only probe

```sh
ALICE_MCP_INVENTORY=/PRIVATE/inventory.json python -m alice_mcp.io_channel_probe --station ALIAS
```

The probe prints one JSON object, `{"outcome":"complete","data":{...}}`, or exits
1 with `blocked`. It always uses explicit pinned key-only SSH and never falls back
to native transport.

**What it reads** (`src/alice_mcp/io_channel_probe.py`):

- `maind` PIDs and roles.
- Socket inodes from `ls -l /proc/<maind>/fd`.
- `/proc/net/{tcp,tcp6,unix}`.
- `/sys/fs/selinux/enforce`.
- A token scan of `quasar.cfg`, limited to 1 MiB, for the numeric `port` directly
  inside each top-level section.
- Counts of `maind` C strings ending in `.cpp` that contain `audiod`,
  `audio_source`, `iohub` or `channel`. These are split on NUL and capped at
  96 MiB / 4 MiB.

**What it never does:** connect to a socket, open a PCM, attach to a process,
load a library or write anything. Addresses, paths, configuration values and raw
lines never leave the parser.

**Output:**

- `services[]`: each config service with `listen` (`loopback|any|other|none`),
  `owner` (Quasar role or `foreign`), `accepted_connections`, `client_roles` (the
  Quasar roles already connected to it) and `foreign_clients`.
- `unmapped_quasar_listeners`.
- `unix_quasar_listeners` and `unix_quasar_audio_named`.
- `maind_cpp_tokens`.
- `io_channel_candidates`: A2's order, filtered to services that Quasar is
  listening on.

**Decision rule:** pilot the first candidate first. A strong signal is a service
whose `client_roles` include the spotter host (`aliced`) or `audiosender`: they
consume audio, so the service they connect to is probably serving it. If no
candidate exists, report the `services` list and stop. Do not guess a port.

## Step 2: bounded level pilot

```sh
ALICE_MCP_ALLOW_WRITES=1 ALICE_MCP_INVENTORY=/PRIVATE/inventory.json \
  python -m alice_mcp.io_channel_pilot --station ALIAS \
  --helper /PRIVATE/io-channel-levels-armhf --port PORT --channel vqe --seconds 5 \
  --preamble none
```

`--preamble` picks one fixed, schema-backed opening. It never takes caller
bytes. The variants are listed under [round 2](#round-2-frame-metadata-and-fixed-preambles).

**Gates.** Before anything runs, all of these must hold:

- The station is `station-mini`.
- Station-level `preauthorize_all_actions=true`.
- `speech.listen` is in the private `allowed_actions`, as existing microphone
  consent. No new policy key is added.
- The firmware digest is pinned.
- Global writes are enabled.
- An interactive terminal is present.

The pilot creates a read-only, state-bound preview first. The owner must then type
**`LEVELS`** after reading the prompt. Any other answer, including EOF, consumes
the preview.

**On the device** (a fixed shell helper over bounded SSH stdin):

1. Re-check the firmware digest, the ARM32 ELF, the boot ID and the Quasar
   identity (each `maind` PID with its start time).
2. Check that the port is a single `0A` listener on `0100007F`/`00000000` whose
   inode a `maind` process owns.
3. Check that no other `alice-mcp-*-active` claim exists.
4. Write the octal-encoded helper. Its SHA-256 is pinned:
   `818b3f1fc09f429c4f8359504ecc1b22a60c1b66591aef69e9223df005f271fb`.
5. Run `--levels PORT CHANNEL SECONDS PREAMBLE` with seconds in 1–5.

**The helper** (`src/alice_mcp/native/io_channel_levels.c`):

1. Connects once to the loopback port. The connect is bounded to 2 s, with no
   retry or reconnect.
2. Sends the selected fixed opening (nothing for `listen_only`).
3. Reads frames until the window ends. Limits: 256 KiB per frame, 32 MiB and
   20,000 frames per run, 16 channels.
4. Reduces each sample immediately into peak and mean-square, interpreting the
   data as S16LE (A5), in a reused buffer.
5. Sends `unsubscribe` in the same envelope, bounded to 0.5 s, even after errors
   or SIGTERM, and closes the socket. `listen_only` sends nothing.

It prints one JSON line with counts, levels and numeric frame metadata
(`other[]`) only, including `audio_exported:false`. The wrapper then deletes its files, removes the claim
and reports `cleanup_verified`, `quasar_running` and `quasar_unchanged`.

**Outcomes.**

- `complete` means a full window, at least one audio frame, unsubscribe sent and
  every trailer flag true.
- `blocked` means nothing was sent.
- Everything else is `unknown` with `do_not_retry:true`.

Never rerun an `unknown` automatically. Inspect the station read-only first.

**What the owner observes:** nothing audible or visible. Speech near the station
is processed in station RAM for up to 5 s. Only these values return: frame
counts, channel type/name/rate/format, `bytes_per_sample_estimate`, `peak_s16`,
`mean_square_s16`, and the host-derived `rms_dbfs`/`peak_dbfs`.

**Reading the result.**

| Result | Meaning | Not meaning |
|---|---|---|
| `complete`, a `vqe_0`/type 6 channel, estimate 2 | Quasar serves VQE PCM to a loopback subscriber: path A is viable for this firmware | Wake-word quality, or mute behaviour |
| `unknown` / `no_audio_frames` with `quasar_unchanged` | This port ignored the request, or needs a handshake or another kind (A1–A4) | That IO channels are impossible; try the next candidate in a **new** approved run |
| Estimate 4 | Float32 or S32 payload; the S16 levels are invalid | A broken stream |
| `quasar_unchanged:false` | Quasar restarted or changed during the run; stop and inspect | Proof that the subscription caused it |

**Build and pin.** The artifact was built with the toolchain the parent session
provided (zig 0.15.1). Two clean-cache builds were byte-identical: 19,128 bytes,
ELF32 ARM EABI5 hard-float, static, no `INTERP`, non-executable stack. The
round-1 build (`c066c8ff…`, 29,532 bytes, with stdio) is retired. A fixed JSON
writer replaced `printf`, which made room for the frame metadata.

```sh
zig cc -target arm-linux-musleabihf -std=c11 -D_POSIX_C_SOURCE=200809L \
  -Os -Wall -Wextra -Werror -Wconversion -Wshadow -Wstrict-prototypes \
  -fstack-protector-strong -D_FORTIFY_SOURCE=2 -static -s \
  src/alice_mcp/native/io_channel_levels.c -o local/io-channel-levels-armhf
```

Octal transfer limits the helper to about 29.6 KB inside the 120 KiB `SSHInput`
bound. A larger helper must switch to `pcm_pilot`'s binary-stdin transfer.
Changing the C source requires a rebuild and a new pin.

## Round 1 live result (Mini 1, 2026-10-29)

These results were reported by the parent session after an owner-authorized run.
Each run used `request_vqe` for 5 s, one run per port. Every run reported
`connected`, `subscribe_sent`, `unsubscribe_sent`, `cleanup_verified` and
`quasar_unchanged`, and **0 audio frames**.

| Port | Service | Non-audio frames | Bytes (incl. 4-byte headers) |
|---|---|---|---|
| 9850 | `yio_audio` | 0 | 0 |
| 9903 | `iohub_services` | 6 | 84 (≈10-byte bodies) |
| 9910 | `iohub_clients` | 3 | 255 |
| 9875 | `aliced` | 3 | 865 |

Three services answer with length-prefixed frames, but none sends
`IOAudioChannelsData`. Round 1 did not record what those frames were.

## Round 2: frame metadata and fixed preambles

### Helper output: numeric metadata

For up to 16 non-audio frames, the helper adds one entry each to `other[]`.
Each entry contains only:

- `length`
- `top`: the top-level `[field, wire]` pairs, at most 8
- `kind`: the packed `253` value, or -1
- `payload`: one of `message`, `string_like`, `unparsed`, `empty` or `absent`
- `paths`: field-number paths at most two levels into the payload, at most 16.
  The payload is the `252` bytes, or the single field of a full `QuasarMessage`.

Bytes that look like text are never parsed: fewer than 10 % of them are below
0x20. Their content therefore cannot leak as field numbers. Payload bytes,
strings and varint values other than `kind` are never returned.

### Hypotheses, ranked

1. **H1: the IO channel API runs the other way (audio ingress).** Quasar may send
   `IOChannelRequest` to an external audio *producer* (a partner or host IO app)
   and receive `IOAudioChannelsData` from it.
   - Schema evidence: `request_*` reads as asks from the consumer side. The
     producer-side `ProducedChannelsInfo` exists, but no `QuasarMessage` field
     carries it. `is_for_recognition`, `doa_angle`, `record_start_timepoint_ms`
     and `SoundBufferTag` look like metadata the producer supplies. The `IO*`
     family is the IO SDK surface: `IOEvent` goes to the app, `IOControlCommand`
     comes from the app, and so does `IOHubMessage.retrieve_state`.
   - Live evidence: `iohub_services` pushed six frames of about 10 bytes. A packed
     `IOChannelRequest{request_*}` is 9 bytes.
   - Test: `listen_only` on 9903 and 9910 sends nothing. Look for `kind:159`.
   - If H1 holds, this API cannot give Mini 1 room audio without acting as an
     audio *source* to Quasar. That injects input into Alice, which is rejected.
2. **H2: envelope mismatch on `yio_audio`.** Port 9850 stayed silent after a
   packed request. It may parse only the full `QuasarMessage` form. Test:
   `full_envelope` on 9850.
3. **H3: the hub needs a handshake.** iohub may expect
   `IOHubMessage.retrieve_state` (field 183) before other requests. Test:
   `hub_retrieve_state` on 9903.
4. **H4: channel selection.** Only `request_main` (or `raw`) may be honoured.
   Test: `none` with `--channel main` on 9850.
5. **H5: audio flows only inside the `platformd` process.** If H1–H4 are all
   negative, no TCP consumer API exists, and the verdict is: not feasible without
   stopping or reconfiguring Quasar.

Rejected preambles:

- `service_name_ready` (157) would impersonate a peer service's readiness.
- `ProducedChannelsInfo` has no envelope field.
- Sending audio of our own would inject input into Alice.
- Free-form bytes are never allowed.

### Exact bytes per `--preamble`

Every variant ends with `unsubscribe` (field 6) in the same envelope, except
`listen_only`.

| Value | Sent before reading | Schema basis |
|---|---|---|
| `none` | `09000000 e20f 02 XX00 e80f 9f01` (packed `IOChannelRequest`, kind 159) | S1, S2, A1 |
| `listen_only` | nothing | — |
| `full_envelope` | `05000000 fa09 02 XX00` (`QuasarMessage{159: IOChannelRequest}`) | S1, S2 |
| `hub_retrieve_state` | `09000000 e20f 02 0a00 e80f b701` (packed `IOHubMessage{retrieve_state}`, kind 183), then `none` | `quasar_proto.proto` L644, `yandex_io.proto` L352 |

`XX` is `0a` for `main`, `1a` for `vqe` and `22` for `raw`.

### Next live round (same bounds: 5 s, levels only, identity check, never retried)

Use the new pinned helper, `818b3f1f…71fb`. The round-1 artifact now fails the
pin check before anything is transferred.

| # | Port | `--channel` | `--preamble` | Tests | Writes to Quasar |
|---|---|---|---|---|---|
| 1 | 9903 | vqe | `listen_only` | H1 | none |
| 2 | 9910 | vqe | `listen_only` | H1 | none |
| 3 | 9903 | vqe | `none` | compare with #1: what the request changes | request + unsubscribe |
| 4 | 9850 | vqe | `full_envelope` | H2 | request + unsubscribe |
| 5 | 9850 | main | `none` | H4 | request + unsubscribe |
| 6 | 9903 | vqe | `hub_retrieve_state` | H3 | retrieve_state + request + unsubscribe |

Stop the round on any `quasar_unchanged:false`, `cleanup_verified:false` or
`transport_uncertain` result. Do not repeat a run automatically.

How to read the results:

- `kind:159` frames in #1 or #2 support H1.
- `kind:158` with channels anywhere means audio arrived, so path A is open.
- `unparsed`/`string_like` payloads on 9850 would mean `yio_audio` speaks
  another protocol.

## Later: resident and wake pipeline (planned, not implemented)

**Resident (Go).**

- Port `observe`/`add_channel` to `native/agent/iochannel.go`, with the same
  bounds and the same packed and full-message parsing.
- Discover the port on the device with the probe's `/proc` ownership check,
  bound to the resident's state stamp (boot, firmware, `maind` identity).
- Run one goroutine on `net.Dial("tcp", loopback:PORT)`, with no reconnect loop.
  After a disconnect, re-subscribe only after a fresh Quasar-identity check.
- A persistent subscription is a standing household-audio stream, so it needs
  its own explicit private policy action and owner approval. The resident exposes
  only status and levels over MCP and never PCM.

**W4b wake pipeline, `feed(pcm, station_speaking)`.**

- The reader decodes the chosen `vqe_0` entries to `int16`. If the estimate is 4,
  it converts float32 to int16 with saturation.
- Frames go into a bounded ring of about 2 s. On overflow the ring drops the
  oldest frames; it never blocks the socket reader.
- The detector goroutine calls `feed(pcm []int16, stationSpeaking bool)`.
- `stationSpeaking` comes from AliceState on 9875 (kind 3, the SPEAKING/TTS
  state) or from `raw_spk_*` energy when `request_raw` is subscribed.
- If frames stop or muted state is observed, the detector goes idle. Audio never
  leaves the process.

## Gaps and limits

- Only Mini 1 and ARM32 are covered. Mini 3, Lite and AArch64 userspace are not.
- The pilot connects over IPv4 only. A `tcp6`-only listener is reported by the
  probe but blocked by the pilot.
- BusyBox 1.26.2 is not available to host tests. The scripts are tested under
  `dash` with GNU awk and mawk, and reviewed for the known BusyBox 1.26 awk
  pitfall (`name (expr)` parsed as a function call). Only a station run can
  confirm compatibility.
- The `quasar.cfg` scan is heuristic: a brace or colon inside a string can
  confuse it.

**Tests:**

- `tests/test_io_channel_probe.py`: a synthetic `/proc` tree, with
  sentinel-leak, read-only and parser-rejection checks.
- `tests/test_io_channel_pilot.py`:
  - a synthetic loopback IPC server that checks the exact subscribe and
    unsubscribe frames, levels, malformed streams, window bounds and refusal;
  - the real shell wrapper end-to-end under `dash` with gawk and mawk;
  - Quasar-identity change, policy gates, a strict result parser and the
    typed-word flow.

[protos]: https://github.com/v3rt3p/quasar-protos/tree/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6
[qp585]: https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/quasar_proto.proto#L585
[qp645]: https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/quasar_proto.proto#L645-L646
[qp716]: https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/quasar_proto.proto#L716
[yio317]: https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/yandex_io.proto#L317-L333
[yio343]: https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/yandex_io.proto#L343-L350
[types]: https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/voicetech/library/channel_types/proto/channel_types.proto
[remoting]: https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/remoting.proto#L37-L47
[wiki]: https://github.com/Marker284/yandex-station-extended/tree/930a0ffa947d82545089470670609fd20b201a68/docs
[st2]: https://github.com/Marker284/yandex-station-extended/blob/930a0ffa947d82545089470670609fd20b201a68/docs/devices/station-2.md#L232
[sounds]: https://github.com/Marker284/yandex-station-extended/blob/930a0ffa947d82545089470670609fd20b201a68/docs/recipes/sounds.md#L45-L50
[mini3]: https://github.com/Marker284/yandex-station-extended/blob/930a0ffa947d82545089470670609fd20b201a68/docs/devices/station-mini3.md#L113-L123
[network]: https://github.com/Marker284/yandex-station-extended/blob/930a0ffa947d82545089470670609fd20b201a68/docs/internals/network.md#L15-L28
[voice]: https://github.com/Marker284/yandex-station-extended/blob/930a0ffa947d82545089470670609fd20b201a68/docs/internals/voice.md#L44-L58
[voice130]: https://github.com/Marker284/yandex-station-extended/blob/930a0ffa947d82545089470670609fd20b201a68/docs/internals/voice.md#L130
[mic]: https://github.com/Marker284/yandex-station-extended/blob/930a0ffa947d82545089470670609fd20b201a68/docs/recipes/microphone.md#L45-L58
[abbot]: https://github.com/derangedabbot/yandex-station-2-airplay/tree/3ed9bfe3b31f9ef993497e38ac6d72cdeb6fd8c7
[abbot17]: https://github.com/derangedabbot/yandex-station-2-airplay/blob/3ed9bfe3b31f9ef993497e38ac6d72cdeb6fd8c7/docs/09-voice.md#L17-L27
[yst]: https://github.com/AlexxIT/YandexStation/tree/4e8567b8dd06679d233233ebb17f9d56a9b2003a
