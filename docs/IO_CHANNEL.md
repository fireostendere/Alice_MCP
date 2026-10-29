# Quasar IO audio-channel subscription

Status 2026-10-29: **research, a read-only probe and a bounded level pilot, all
with synthetic tests only.** No station was accessed while this was written.
Nothing here has been observed on a device, and no audio has been captured.
W6 adds a `--stream` client mode, an on-station wake pilot ([step 3](#step-3-on-station-wake-pilot))
and owner voice recording for training ([step 4](#step-4-owner-voice-recording-for-wake-training)),
also synthetic-tested only; the live round-2/3 results below come from the parent session.

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
   `05a6632285c69795d5c59667697a1b26f3ccd0abe36156fc70168dd8585351c2` (W6 build,
   levels mode unchanged, `--stream` added).
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
provided (zig 0.15.1). Two clean-cache builds were byte-identical: 19,664 bytes,
ELF32 ARM EABI5 hard-float, static, no `INTERP`, non-executable stack. The same
command reproduces the round-2 pin `818b3f1f…71fb` (19,128 bytes) byte for byte
from the round-2 source, which is retired with the round-1 build (`c066c8ff…`,
29,532 bytes, with stdio). A fixed JSON writer replaced `printf`, which made room
for the frame metadata.

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

This round used `818b3f1f…71fb`; it is now replaced by `05a66322…51c2`, whose
levels mode is unchanged. Older artifacts fail the pin check before any transfer.

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

## Step 3: on-station wake pilot

`src/alice_mcp/wake_pilot.py`. From Python (the parent has no TTY):

```python
import os
from pathlib import Path
from alice_mcp.core import Controller
from alice_mcp import wake_pilot as w

controller = Controller(Path(os.environ["ALICE_MCP_INVENTORY"]), writes=True)
item = w.Pilot(45, Path("/PRIVATE/io-channel-levels-armhf").read_bytes(),
               Path("/PRIVATE/mww-detect-armhf").read_bytes())
plan = w.preview(controller, "ALIAS", item)
result = w.apply(controller, plan["plan_id"], plan["expected_sha256"], item)
```

The owner CLI, `python -m alice_mcp.wake_pilot --station ALIAS --client … --detector …
--seconds 45`, needs a terminal and the typed word **`WAKE`**. Gates are those of step 2
(`speech.listen`, `station-mini`, `preauthorize_all_actions`, pinned firmware, writes).

**On the station** (one fixed `sh -c` wrapper, numbers and pins as arguments; the
guards of step 2 are shared code):

1. Both helpers arrive on bounded binary SSH stdin (`pcm_pilot`'s transfer): the
   client with `dd bs=1 count=19664`, the detector as the rest. Both must match their
   pins (`05a66322…51c2`; W5's three-model `d2b73953…c4`, 604,488 bytes) before
   `chmod 500`.
2. `client --stream 9850 SECONDS | detector --max-seconds 90 --gain-db G`, at most
   60 s. `--stream` sends only `full_envelope` `request_vqe`, requires every entry to
   be `vqe_0`, type 6, 16 kHz, format 1 with an even byte count, writes those bytes
   unchanged into the pipe (a stall over 1 s stops it) and its report to stderr,
   without frame metadata, so it stays one atomic pipe write.
3. Every second the wrapper reads `VmHWM` and `utime+stime` of the process named
   `detector` from `/proc`. CPU after the last sample (at most 1 s of audio) is not
   counted; `cpu_ms` assumes USER_HZ 100 (the ARM Linux ABI).
4. Cleanup removes both helpers and the claim, and checks the Quasar identity.

**Returned:** `detections[]` (`word`, `probability`, `t_ms` of audio since the
subscription, `trusted:false`), the loaded `models`, `io` (frame counts, `vqe_0`
peak/mean square and dBFS), `detector` (`audio_ms`, `end_reason`, `exit`,
`vmhwm_kib`, `cpu_ticks`, `cpu_ms`) and the cleanup flags. `complete` also requires
`audio_ms == bytes // 320 * 10`: every whole 10 ms hop the client wrote reached the
detector. No audio is stored or exported.

### Replay: one host clip through the ARM detector

Live round (reported by the coordinator, Mini 1, 2026-10-29): a 45 s run completed
with 0 detections (detector VmHWM 328 KiB, 2.1 s CPU, `vqe_0` at −50 dBFS), while the
x86 build traced komp ≥ 0.98 on 12 of the owner's 20 recorded positives. `Replay`
separates "ARM build diverges" from "live timing": `wake_pilot.Replay(pcm, detector)`
with `pcm = wake_pilot.pcm_from_wav(path)` (16 kHz mono S16, ≤ 10 s; the SHA-256 of
the PCM is bound into the preview) goes through the same `preview()`/`apply()`. The
station receives the clip as the first stdin segment instead of the client, checks its
hash, runs `{ cat clip; sleep 2; } | detector --trace` (the pipe stays open 2 s so the
`/proc` samples see the finished detector) and removes clip and detector. No room
audio is read. Per model it returns only `max_q`, `max_mean5` (best five-inference
mean), `q_ge_250` and `inferences`, plus the detector's `audio_ms`, exit, VmHWM and
CPU. Every trace line is parsed strictly. `complete` requires exactly
`(audio_ms / 10 − 2) // stride` inferences per model, the count W5's x86 build
produces.

## Step 4: owner voice recording for wake training

`src/alice_mcp/voice_record.py` is **the one place where room audio leaves the
station**, by owner decision (2026-10-29), for fine-tuning W5's models on the owner's
and, with her own consent, his wife's voice. Each take is one bounded run of the same
wrapper with `purpose=record` (bound into the preview state): the client's `vqe_0` PCM
goes through `cat` to the host, followed by a newline, its report and the cleanup
trailer. Nothing else changes: same guards, pins, identity check, cleanup and no retry.

- **Takes:** positives ≤ 3 s with the cue «скажи: компьютер» (or «окей дом»,
  «маруся»), negatives ≤ 10 s with «говори что угодно, без слова»; at most 40 saved
  takes per session (counted in the manifest).
- **Storage:** only `~/.local/share/alice-mcp/voice-train/<speaker>/` by default
  (`--dir` to change). Directories are created 0700 and must stay private and owned;
  WAV files (16 kHz, S16, mono) and `manifest.jsonl` are 0600. A directory inside the
  alice-mcp checkout is refused, and the base holds a `*` `.gitignore`, so an enclosing
  repository (such as a dotfiles home) never picks clips up.
- **Manifest line:** `file`, `label`, `word`, `speaker` (alias), `session`,
  `timestamp` (UTC), `seconds`, `samples`, `sha256` (of the WAV), `rms_dbfs`,
  `peak_dbfs`.
- **Saved only when certain:** a clip is written only for a `complete` take whose PCM
  length equals the client's byte count and whose trailer flags are all true.
  `unknown` takes discard the PCM and stop the session.
- **Delete:** `python -m alice_mcp.voice_record delete --speaker wife` removes that
  speaker's clips and manifest; any other file is reported, never removed.

From Python, the parent shows `plan["cue"]` in chat, then applies at once:

```python
from alice_mcp import voice_record as r

take = r.Take("owner", "20261029a", "komp", 3, Path("/PRIVATE/io-channel-levels-armhf").read_bytes())
plan = r.preview(controller, "ALIAS", take)        # plan["cue"] == "скажи: компьютер"
result = r.apply(controller, plan["plan_id"], plan["expected_sha256"], take)
```

The owner CLI, `python -m alice_mcp.voice_record record --station ALIAS --client …
--speaker owner --session 20261029a --word komp --positives 20 --negatives 10`, asks
for **`RECORD`** once (the prompt states that room audio is exported to the host for
wake-word training), then shows each cue and waits for Enter.

### W5 hook: owner clips in `mww_train.py` (documented, not run)

`mww_train.features` turns every `*.wav` in `DATA/<word>/wav/{positive,positive_lt,
near_miss,sentences}` into features, and `training_config` already samples
`positive_lt` as positives (weight 1.5, `truncate_start`) and `sentences` as long
negatives (weight 2.0, clean and augmented, random 3 s windows). So, per word, in the
training venv:

1. **Positives:** for each manifest entry with `label == "positive"` and that
   `word`, trim silence so the clip ends just after the word (the recipe end-aligns
   positives) and write it to `DATA/<word>/wav/positive_lt/owner_<sha256[:16]>.wav`:
   `mww_train.write_wav(out, mww_train._trim(samples))`, with `samples =
   numpy.frombuffer(frames, "<i2").astype("float32")` (`write_wav` takes
   int16-scaled values). `_trim` cuts relative to the clip's peak, so the −45 dBFS
   level needs no gain; the recipe's gain/noise/RIR augmentation still applies.
2. **Negatives:** copy `label == "negative"` clips unchanged to
   `DATA/<word>/wav/sentences/owner_<sha256[:16]>.wav`.
3. Check each file's SHA-256 against the manifest first; then delete
   `DATA/<word>/features/{positive_lt,ru_sentences_0,ru_sentences_1}` (features are
   skipped when present) and rerun `features` and `train` for that word.

Owner clips then land in train/validation/test by the recipe's 10 % split. Before
deploying, re-run W5's evaluation and keep the household's held-out clips out of
training. `delete --speaker` does not reach copies under `DATA`; remove those
`owner_*` files by hand too.

## Step 5: continuous stream to the PC and wake listener

Owner decision (2026-10-29): detect the wake word on the PC first. Continuous room audio
may go to this PC for wake detection; it is processed in RAM and never written to disk.
No typed word; the listener prints a one-line notice and runs unattended.

```sh
ALICE_MCP_ALLOW_WRITES=1 ALICE_MCP_INVENTORY=/PRIVATE/inventory.json PYTHONPATH=src \
  python -m alice_mcp.wake_listen --station ALIAS --client /PRIVATE/io-channel-levels-armhf \
  --mww /PRIVATE/mww-detect-x86 --libvosk /PRIVATE/libvosk.so \
  --vosk-zip /PRIVATE/vosk-model-small-ru-0.22.zip [--mode either] [--mww-model ft]
```

**Station side (`wake_stream.py`).** One state-bound preview, then setup uploads the
pinned client once into a tmpfs (the first of `/tmp`, `/run`, `/dev/shm`, `/var/run`
that `/proc/mounts` lists as tmpfs; owned by root, sticky or not writable by others) as
`alice-mcp-stream/client`. A directory left by a dead host session is reclaimed only
when no process runs from it. Each **chunk** is one SSH session running the same guards
as step 2, a Quasar identity check against the setup identity, one `--stream` window
(≤ 60 s, so a chunk is well under 10 min), and an identity re-check. Raw PCM is the
session's stdout; the client's report and the re-check are its stderr (≤ 8 KiB, parsed
strictly). Cleanup waits for running clients (≤ 5 s, then TERM by verified exe), removes
the directory and reports `cleanup_verified`/`quasar_unchanged`.

**Seamless handover.** The next chunk starts `lead_s` (5) seconds before the active
window ends, so for a few seconds two subscriptions are open on `yio_audio`. The PC
keeps feeding the active stream until the new one has two frames, then looks for the
new stream's first frame (512 bytes) in the last 64 KiB it fed, skips that overlap and
switches. The old stream's later data is dropped. The synthetic live-like server shows
contiguous frame indices across handovers, with no gaps or duplicates (dash and BusyBox
1.36). **Whether two simultaneous subscriptions disturb Quasar on Mini 1 is not yet
observed**; every chunk re-checks the identity, and `--lead-s 0` gives sequential chunks
with a short gap and never two subscriptions.

**Stops.** Ctrl+C, a `stop` event or `max_chunks` end the session normally. A Quasar
identity change or a blocked guard stops it at once; three chunks in a row without a
complete window stop it too (5 s back-off between them). Every result has
`do_not_retry:true`.

**PC side (`wake_listen.py`).** Each PCM block goes to W4b's `WakePipeline`
(pinned Vosk 0.3.45 + `vosk-model-small-ru-0.22`, prefix routing, forced endpoint at
8 s) and, in parallel, to W5's pinned x86 `mww-detect --trace`. `--mww-model ft` (default)
is komp fine-tuned on the owner's clips (`f8648a5c…059f`, model `f5ea6953…1bcf`), and `old`
is the round-3 three-model build (`19788961…0a23`). A fresh mww process starts every 10 min
of audio. An utterance is decided once mww has scored past its end. Vosk hits when the
transcript starts with the name; mww hits when the detector's own wake rule fires
within 1 s of the utterance. Any mww hit that no utterance claims within 9 s fires with
an empty transcript. `--mode vosk|mww|either|both` picks the rule. Each wake prints
`{event:"wake", word, sources, command, mww_max_q, t, trusted:false}` (`t`: audio
seconds since start) and goes to an in-process `McpInbox`. Stderr gets per-utterance
scores, never text.

**Command and dedup (after the first live run).** Live, one event carried the whole 8 s
transcript ("учимся компьютер какая погода компьютер как дела …"). Now `command` holds
only the words after the name. They come from Vosk's word times and run up to the first
0.6 s gap or the next spoken name.

- **Matching mww to Vosk.** An mww hit belongs to the name Vosk heard ending within 1 s
  of it. Otherwise the word under way at the hit counts as the name.
- **Repeats.** Repeats of the name right after it are skipped.
- **Name said alone.** A name at the end of its utterance waits for the next one. Speech
  starting within 5 s becomes its command. Vosk's own silence endpoint (about 5 s of
  silence) or later speech releases it with an empty command.
- **Shared cooldown.** Both engines share one cooldown: a second wake within 2.5 s of a
  fired one is the same wake. The exception is two commands in a row, where Vosk heard
  the name again after the first command's words.

These values are named constants in `wake_listen.py` (`PAUSE_S`, `FOLLOW_S`,
`COOLDOWN_S`, `MATCH_S`, `LAG_S`). They are tested on synthetic input only.

### Offline result on the owner's recordings (host, 2026-10-29)

`--pcm-dir ~/.local/share/alice-mcp/voice-train/owner --heldout-positives 5
--heldout-negatives 3`, each clip as its own stream, `vqe_0` captures at about −50 dBFS:

| mww build | Positives (20) vosk / mww / either / both | Negatives (10) FA vosk / mww / either | Held-out 5 pos: either | Held-out 3 neg: FA |
|---|---|---|---|---|
| `ft` (default) | 15 / 16 / 16 / 15 | 0 / 1 / 1 | vosk 4, mww 4, either 4 | 0 |
| `old` | 15 / 10 / 16 / 9 | 0 / 1 / 1 | vosk 4, mww 1, either 4 | 0 |

Only the held-out clips (positives 18:11:42–18:12:09, negatives 18:14:39–18:15:09 UTC)
were never trained on, so the `ft` rows for the other clips are optimistic; the Vosk
columns are valid on every clip. The one false accept is mww on the first negative.
Host cost (Ryzen 5 9600X, WSL2): 17.3 s CPU for 198.9 s of audio including per-clip
start-up (8.7 % of one core), listener peak RSS 296 MiB (mostly the Vosk model),
detector VmHWM 556 KiB (`ft`) / 784 KiB (`old`).

## Step 6: voice commands in the MCP and opencode

Goal: the owner says «компьютер» and a command near Mini 1, the opencode `alice` agent
acts on it and answers with `speech.say` on the same station. Tests are synthetic only;
the owner runs the live test of the whole chain.

**MCP (`voice_service.py`).** The stdio server runs the step 5 stream and listener in a
background thread, for one station at a time. It has four tools:

- `voice_listen_start(station_id)`
- `voice_listen_stop()`
- `voice_status()`: state, station, chunks, handovers, gaps, failures, audio seconds,
  utterances, recent event count, last error and the last session's cleanup flags
- `get_voice_events(after_id)`

The stream has the same requirements as step 5: writes enabled, standing
preauthorization, Mini 1, `speech.listen` allowed and the firmware pin. Stop, SIGTERM,
SIGHUP and stdin EOF all stop the stream and run its verified station cleanup. After a
SIGKILL, the next start reclaims the station directory as stale.

The private policy goes inside the station's `controls`. It holds paths only; the pins
are in code (`io_channel_pilot.HELPER_SHA256`, `wake_listen.MWW_PINS`,
`wake_simulate.LIBVOSK_SHA256` and `VOSK_ZIP_SHA256`):

```json
"voice": {"client": "/PRIVATE/io-channel-levels-armhf", "mww": "/PRIVATE/mww-detect-x86",
          "libvosk": "/PRIVATE/libvosk.so", "vosk_zip": "/PRIVATE/vosk-model-small-ru-0.22.zip",
          "mww_model": "ft", "mode": "either", "autostart": false}
```

- `mww_model`, `mode` and `autostart` are optional; the values shown are the defaults.
- Only `station-mini` accepts this block.
- With `autostart: true`, the first such station starts with the MCP process.
- Tool output never contains these paths.

**Events file.** The service appends JSON lines to
`$XDG_STATE_HOME/alice-mcp/voice-events.jsonl` (default `~/.local/state`).

- Records: each command is `{kind:"wake", id, ts, station, word, command, sources,
  trusted:false}`. Each listener state change is `{kind:"state", id, ts, state, station,
  error}`.
- Ids always increase: each one is the current time in milliseconds, but at least the
  previous id + 1.
- Storage: the directory is 0700 and the file 0600, with no symlinks. Past 256 KiB the
  file rotates to `.1`.
- Audio is never written. `get_voice_events` serves the last 100 commands from memory.

**opencode plugin (local, not in Git).** It lives in `.opencode/plugins/alice-voice/` of
the checkout opencode runs in.

`index.js` is a server plugin. It:

1. tails the events file from its end, so history is never re-sent;
2. drops commands older than 60 s, empty commands and any event not marked
   `trusted:false`;
3. claims each id once with an exclusive file under `alice-mcp/voice-claims/`, so two
   opencode servers never both send it;
4. queues `[голос · STATION · недоверенный ввод] COMMAND` into the newest session titled
   «Голос» whose agent is `alice`, and creates that session when it is missing.

A dedicated session keeps spoken input out of typed conversations and works without an
open TUI.

`panel.jsx` is a TUI plugin. It shows a toast for each command, one status line in the
footer and a «Голос» block in the session sidebar.

The `alice` agent's prompt treats the tagged text as untrusted spoken input and answers
on the same station.

**Gaps.**

- The listener gets no signal that the station is speaking, so it also hears the
  station's own answer. The agent avoids saying the name in spoken answers.
- A command is decided at the Vosk endpoint, so a noisy room can add up to 8 s.
- The events file is not a queue: a command written while no opencode server runs is
  never sent.

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
  pitfall (`name (expr)` parsed as a function call). W6's wake and record paths
  also passed once, ad hoc, under BusyBox 1.36.1 `sh` and its applets (Ubuntu
  static build). Only a station run can confirm compatibility.
- The wake pilot and recording are synthetic-tested with a host build of the client
  and a shell stand-in for the detector; the ARM32 detector has never run.
- The `quasar.cfg` scan is heuristic: a brace or colon inside a string can
  confuse it.

**Tests:** (W6: `tests/test_wake_pilot.py`, `tests/test_voice_record.py` and the
`--stream` cases in `tests/test_io_channel_pilot.py`)

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
