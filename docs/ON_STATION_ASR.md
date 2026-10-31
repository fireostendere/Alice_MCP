# On-station Russian recognition experiment

The target is speech recognition on the Mini 1 itself. Host recognition is not
acceptance of this task. `native/station_asr.c` is a bounded ARM32 Vosk client;
the `voice.source="station_asr"` route connects it to `voice_listen_start`.
The existing `voice.source="station"` remains wake-only. The ASR source sends
only partial/final text to the host; microphone PCM stays in the station's RAM.
The host still runs MCP, OpenCode/Alice and cloud response synthesis. This is
local recognition, not a fully offline conversational model.

## Observed resources

The owner's live Mini 1, with Quasar and the resident MCP agent running, reported
242,028 KiB total RAM, 135,328 KiB available, four CPUs, no swap, and an unused
zram device. `overcommit_memory` was already 1. The data volume had 55,192 KiB
available; the system volume had 9,668 KiB. Squashfs was not listed by the kernel.
These are one device's measurements, not model-wide guarantees.

`station_probe(..., probe="asr_resources")` returns typed resource metadata over
explicit pinned, key-only SSH, including for a station configured with native
TLS. This named maintenance probe is not an automatic fallback after TLS failure.
It does not initialize zram, change memory policy, install files or capture audio.

## Why this experiment

The [Mini 3 author's report](https://habr.com/ru/articles/1100268/) describes Vosk
small-ru-0.22 running on the device with compressed RAM swap. Their platform had
the stock voice stack removed. Its speed and memory measurements do not qualify
our Mini 1 with Quasar retained. No register writes, stock-software removal or
third-party install scripts from that project are used here.

The [Vosk model catalog](https://alphacephei.com/vosk/models) describes the Russian
small model and its Apache-2.0 license. The pinned ZIP expands to 91,289,240 bytes,
which exceeds our free data volume. Independently gzipping its original files
reduces them to 46,103,484 bytes with the same decompressed contents. The C client
exports process-local `fopen`/`fopen64` adapters for its model directory only.
They decompress one file into a seekable anonymous `memfd`, released when Vosk
closes it. This avoids writing an expanded model to flash. It still requires
memory during loading; compression is not evidence that the runtime fits.

The client loads the ARM Vosk library with `dlopen`, accepts mono PCM16 at 16 kHz
on stdin, and emits bounded JSON partial/final results with `trusted:false`.
It does not open microphone hardware, run shell commands, choose actions or make
network requests. Input is limited to one hour per session and the process has a wall-clock
alarm. A 1 MiB input pipe absorbs short initial decode stalls; without it the
reviewed microphone client correctly exits on its one-second output deadline. Core dumps are disabled and its own OOM score is raised before model load.
The operator must additionally watch available memory and preserve Quasar.

## Reproducible inputs

| Input | SHA-256 |
| --- | --- |
| Official Vosk 0.3.45 ARMv7 ZIP | `10b795ae478ef1d530fcbfbbea9ccbbbf3b7e7c244bd5fd3176f4a6af32f3c8c` |
| ARM `libvosk.so` | `50e9e705777774ce3f33636939c10cfa2c27d2d2c6441466146f35dacd4e9be4` |
| Russian small-ru-0.22 ZIP | `961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4` |
| zlib 1.3.2 source tar.gz | `bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16` |

Vosk pins are locally observed downloads from the
[official release](https://github.com/alphacep/vosk-api/releases/tag/v0.3.45).
The zlib digest was checked against its [publisher's listing](https://zlib.net/).
Only C/H files were extracted for compilation; downloaded scripts were not run.
No library, model, key, inventory or generated executable is bundled in Git.

Build with Zig 0.15.1, target `arm-linux-gnueabihf.2.23`, `-O2`,
`-DHAVE_UNISTD_H`, `-Wl,--export-dynamic`, `-s`, and `-ldl`. Link the client with
zlib's `adler32`, `crc32`, `inffast`, `inflate`, `inftrees`, `trees`, `zutil`,
`deflate`, `compress`, `uncompr`, `gzclose`, `gzlib`, `gzread`, and `gzwrite` C
translation units, using the same source directory for headers. This statically
includes compression support without replacing any system library. The current
92,888-byte result has SHA-256
`79b42c44d65248b305eb376f3fb19be04209e3cd40d55ae4c08968c76b950fed`.
The station has glibc 2.23 and Linux 4.9. The client calls the kernel memfd syscall
because that glibc lacks its wrapper. The upstream Vosk library imports `expf`,
`logf`, and `powf` at GLIBC_2.27. The qualified pilot rebinds exactly those three
ELF version entries to the existing GLIBC_2.4 libm entry and removes only the
now-unused GLIBC_2.27 version requirement. The [glibc change](https://sourceware.org/legacy-ml/libc-alpha/2017-09/msg00574.html)
introduced versions without the old SVID wrapper. This compatibility build is
not the untouched upstream binary; speech tests below qualify this limited use.
Its SHA-256 is `d1e4e352232d394c18117fcd0e1872d5cc7a9caf2347ded228daaa0b2168565d`.
No system library is replaced. The library is also gzipped, inflated into an
anonymous memfd and loaded through `/proc/self/fd`; no expanded copy hits flash.
All installed file pins, including the reviewed VQE source, are in
`station_asr.MANIFEST`.

## Hardware evidence and limits

A synthetic arithmetic phrase was decoded exactly by the ARM client on the Mini 1
with Quasar unchanged: 22.287 s cold model load, 9.171 s processing for 6.015 s
input, and 113,332 KiB peak RSS. Those are startup/batch measurements, not live
end-of-command response latency. During a run, a separate resource probe saw
18,812 KiB available RAM and about 100 MiB of logical zram swap occupied.

A later 45 s microphone test used Realtek playback into the actual room. All
microphone PCM flowed from the station's `vqe_0` client into its local decoder.
The model loaded in 10.282 s, decoded 44.976 s in 45.308 s, and peaked at
108,324 KiB RSS. The conversational synonym was exact; the volume request had
an extra word, and the first arithmetic phrase included extraneous speech.
After the initial backlog the decoder caught up to within roughly 0.2–0.6 s
of audio delivery. Both child processes exited successfully, the source verified
unsubscribe, and Quasar identity was unchanged. This is not an accuracy benchmark.

The continuous source was separately started and stopped on the device, with
verified cleanup and unchanged Quasar. It keeps the model loaded across 60 s
capture windows and uses a fresh state-bound plan for each hourly session.
A small capture gap at each window boundary remains; no overlap is implemented.
An unknown session result stops listening without an automatic retry. State and
SSH-trust checks remain active; a RAM guard stops the decoder below 12 MiB free.

An unrestricted long-lived decoder was OOM-killed during a later conversation
test (exit 137, confirmed by the kernel); cleanup and unchanged Quasar were
verified. The client now frees and recreates the recognizer at a natural endpoint
after 15 seconds, or flushes it at 30 seconds of continuous input. The model stays
loaded, queued microphone input is retained, and freed heap pages are returned to
the system. This bounds the growing decoder state; long utterances can be split.
The upstream [recognizer implementation](https://github.com/alphacep/vosk-api/blob/v0.3.45/src/recognizer.cc)
otherwise retains frontend history much longer. Synthetic coverage checks both
model reuse and the forced boundary on continuous input.

With this bound, a 340.6 s microphone session including conversation playback
ended with verified cleanup and unchanged Quasar. Eleven resource samples over
five minutes kept the same decoder process alive, with 14,928–44,856 KiB available
RAM (13,740 KiB minimum from the station's faster guard). At 333.3 s the decoder
was 137 ms behind audio delivery. This is a short stability test, not an uptime guarantee.

`voice.source="station_asr"` additionally requires `voice.asr_directory` pointing
to the qualified `/bkupgrade/data/opt/alice-local-asr-<12 hex digits>` installation.
It requires the existing 160 MiB zram setup. It does not initialize swap or
change overcommit while listening. The operator installation enabled zram only
for the current boot; restart persistence has not yet been qualified.

The initial Vosk-only source lit the ring roughly 2–3 seconds after the wake word.
A passive check measured 2.424 seconds for pattern reading and firmware hashing,
versus 63 ms for a warm resident state check. The host now prepares a state-bound
indicator preview while idle; the optional independent keyword detector below
removes the wait for a Vosk partial. See [voice routes](VOICE_ARCHITECTURE.md).
The old 15-second no-command deadline played an error phrase. An isolated wake
now ends normally three seconds after the remote LED launch acknowledgement.
In the combined acoustic check on 2026-10-31, launch was acknowledged at 477 ms,
normal timeout occurred at 3,518 ms, and the Logitech microphone measured silence
after the generated wake phrase. These are launch timings, not camera evidence.
Final local text takes the
existing OpenCode Alice route or becomes Realtime user text with manual response
creation, as described in the [Realtime protocol](https://developers.openai.com/api/docs/guides/realtime-conversations).
There is no cloud STT and no separate mode-classifier request in this source.
Transcript text remains untrusted and station actions retain live policy checks.
Conversation input is suppressed during playback and briefly afterward to avoid
echo. Short stop phrases (including `стоп`, `хватит`, `замолчи`) are checked on
nonempty local partials before that echo/playback gate, without a new wake word.
They cancel the current response and queued speech. With the updated resident's
`pcm_cancellation` capability, closing the owned playback request propagates
SIGINT to its reviewed PCM helper, which uses ALSA `snd_pcm_drop` on interruption.
The host checks fresh resident state after cancellation; uncertain cleanup latches
playback off. This cancels the assistant's own PCM, not unrelated Quasar playback.
During reasoning, response generation and playback, the same loaded Vosk model
switches to a small grammar of stop phrases and `[unk]`; it returns to unrestricted
recognition when waiting for a command. Only the owned decoder receives USR1/USR2.
No second model or classification request is involved. Separate typed interrupt
partials/finals can cancel PCM, but cannot dispatch ordinary commands. Unknown
markers are discarded only in that stop channel. The decoder is renewed every
three seconds in stop mode. Synthetic tests verify switching without model reload,
continuous clocks and the separation from ordinary command execution.
Generated «стоп» and «хватит» then interrupted two audibly playing Realtime replies
in one ASR session, each with confirmed cancellation and cleanup. Cancellation
completed about 114 and 96 ms after the first recognized stop partial, respectively.
These are recognition-to-cancellation timings, not a household accuracy benchmark.

Installing the pinned personalized `wake-detector` in the restricted ASR directory
enables the local keyword path. One capture stream is copied through RAM pipes
to microWakeWord and Vosk. Keyword events use a separate SSH channel and can
activate the ring before Vosk produces text; Vosk then supplies the command.
Missing detector retains the explicit Vosk source; a corrupt/untrusted detector
blocks startup rather than falling back. `voice_status.asr.wake_source` reports
`station_mww` or `station_vosk`. An owner-run keyword-only pilot reported visible
activation within one second, 336 KiB detector peak RSS and unchanged Quasar.
The combined voice path subsequently passed an on-device startup/cleanup check
and the generated-phrase acoustic trials below.

Startup diagnostics on 2026-10-31 initially rejected a 19-byte first line on
SSH stderr while the detector exited zero. Further inspection reproduced the
loader's `dd` statistics on that channel, including a 19-byte first line. The
loader now suppresses those diagnostics; a regression test runs the real loader
and checks its silent hash-rejection path. The helper stream also has an explicit
boundary: at most 4 KiB of ASCII transport preface may precede it, while model
identity and every event after it remain strictly validated. Missing boundaries,
binary/oversized prefaces and malformed events fail closed. A repaired combined
live session loaded Vosk in 9.446 seconds and stopped after the bounded ready
window with verified cleanup, detector exit zero and unchanged Quasar. A later
MCP autostart loaded in 21.522 seconds and processed the acoustic trials.

A later acoustic regression caught a command arriving after the three-second
window had already closed: the first nonempty Vosk command partial reached the
host 3.8 seconds after keyword detection. A separate, small `od`/`awk` branch now
measures 10 ms PCM blocks on the station and reports only bounded activity
metadata. Three blocks above 45 RMS units qualify, with reports at most every
100 ms. The host ignores the first 600 ms after keyword detection and uses later
activity to keep the initial transcript window open and refresh an open conversation. It is an energy gate,
not semantic speech recognition. The EOF count is checked against the keyword
stream, allowing its incomplete final 10 ms block. Synthetic coverage checks
silence, activity, separate decoder clocks, truncated activity and owned cleanup.
A decoder reset during the wake word can also produce an unrelated short fragment.
The fast source waits for post-wake activity before accepting such non-keyword text,
so that fragment cannot start reasoning before the real command. Independent
activity and keyword channels are compared by audio time, including activity that
arrives before its keyword callback. A new isolated-wake acoustic trial remained silent and timed out normally at
4.021 seconds after detection, including a 977 ms LED launch acknowledgement.

An open Realtime conversation closes normally after seven seconds without input
while waiting for the next user turn. Response generation and playback do not count
as silence. New nonempty local partials refresh activity. The isolated-wake timeout
remains three seconds from LED acknowledgement. Normal silence/cancellation plays
no error phrase. Local text and energy activity refresh the waiting-conversation
timer before a delayed transcript arrives.

The repaired combined source answered a generated arithmetic command with
verified native PCM output: all 38,400 input frames were written at 48 kHz,
ALSA returned zero, and the Logitech microphone detected the reply. Indicator
launch was acknowledged at 769 ms; playback dispatch was 12.125 seconds after
wake. A generated «давай попиздим» request selected conversation mode through
the existing OpenCode answer request, produced native PCM, and closed normally
with `silence_timeout`, without a spoken error. Short local requests such as
`поговорим` take the direct Realtime route. One earlier run dispatched playback
2.122 seconds after its final local transcript (4.473 seconds after wake).
Dispatch is not acoustic onset; a two-second audible-response target has not
been demonstrated. Quieter synthetic wake trials sometimes missed the word.

The owner reported phrases cutting off during the live work, then temporarily
disconnected the speaker. Ready audio chunks are now coalesced up to four seconds
without waiting for additional audio, but the reported truncation is not considered
resolved or acoustically qualified. Conversation still has a hard 90-second maximum;
its deadline can interrupt a reply. One earlier PCM policy block was not reproduced
after the memory fix; its precise original cause remains unconfirmed.

Subsequent offline regressions reproduced two additional failures: a server
disconnect after a complete response discarded queued audio, and consecutive local
texts could request overlapping responses. The host now drains complete replies and
serializes these requests. Synthetic tests verify both fixes, explicit stop and
deadline behavior, and sanitized error reasons. These host changes are now
loaded into the running MCP. The short arithmetic
reply and conversation completed with verified PCM results in the acoustic
trials; varied human phrases and longer replies remain unqualified.

Synthetic tests and build checks cover compressed model/library IO, old-glibc client imports,
PCM and event bounds, explicit source configuration, local wake/final routing,
and text-only Realtime turns. An hour-long session rollover, reboot behavior,
and reliable household wake recognition remain unqualified. The subsequently freed
C922 camera enabled a synchronized A/V qualification on this Mini 1.

The first visual checks exposed two gaps hidden by launch acknowledgements: cold
`led_util` startup delayed visible red, and a cold off-player could be stopped before
writing its frame. The indicator now prepares the stock player on a private FIFO,
activates only after fresh state/policy/trust checks, and prepares an off-player while
the command window is open. Unactivated preparation never receives a coloured frame.
Completion wakes the indicator worker directly; lingering owned players get KILL
100 ms after TERM. Quasar remains enabled.

A three-trial generated-phrase check measured acoustic word-end to visible red at
856, 783 and 591 ms; the C922 frame interval was 66.7 ms. Visible spans were
3.27, 3.33 and 3.47 seconds, followed by sustained dark, without a spoken error.
Microphone PCM stayed in RAM; only metrics and cropped station video were retained.
These results cover synthetic Realtek playback on one device, not household voices
or a latency percentile. The cold fallback retains full checks and has no subsecond guarantee.

An isolated wake with noise activity or an unfinished local partial now also ends
silently at the collection limit. Energy refreshes the silence timer separately
from recognized partials, so a brief noise burst cannot latch a command indefinitely.

The final lifecycle check retained the same prepared SSH process beyond its original
240-second idle deadline. Fresh checks renew its timer every 220 seconds, without
restarting unchanged station/trust/animation assets. After 245 seconds the C922
measured 538 ms from generated word-end to visible red and a 3.40-second visible span,
followed by sustained dark and no spoken error. Preparation now waits for exclusive
ASR readiness, so an additional MCP context that cannot acquire the source cannot
touch the active listener's player. A repeated Realtime conversation completed and
closed on silence; a separate stop trial verified native PCM cancellation 113 ms
after the first recognized stop. These results do not establish word-to-stop latency.
