# Experimental one-shot ASR text client

## Resident transport

The optional [native agent](NATIVE_AGENT.md) keeps the separately pinned ASR and
TTS clients on Mini 1. Its MCP channel is TLS 1.3 with a private CA and bearer;
normal calls do not use SSH or upload executables. ASR has one bounded in-memory
session, releases text once after a clean child exit and Quasar check, and removes
text after two minutes. Host and device action policies still apply. The resident
port has synthetic parser/lifecycle coverage; no new live capture was started
during agent acceptance because the current host policy allowed TTS only.
The earlier live SSH ASR result below is not resident-ASR acceptance evidence.
Neither transport provides PCM export or an independent wake phrase.

## Prepared native TTS action

`speech.say` has a built/pinned static ARM32 TTS-only client. The owner confirmed
one audible Mini 3 pilot with the corrected transport envelope; correlated TTS
start/completion remains unverified. Build checks verify no interpreter, a non-executable
stack and no synthetic CLI modes. The unchanged default ASR build reproduces
the previously accepted ASR artifact byte-for-byte.
A restricted command runtime can hide host tools or temporary files; this is
separate from MCP tool permissions. Check an authorized host command through
permitted per-command escalation before declaring a host tool unavailable.
Synthetic filesystem ownership can also fail the production SSH-trust check;
that check must not be weakened to make tests or deployment proceed.
`TTS_CLIENT_SHA256` binds the reviewed TTS-only build; neither the ASR hash nor
an arbitrary private-policy binary can enable TTS.

The action accepts only `{"text": "literal text"}` on Mini 1 or Mini 3, limited to 256
Unicode scalars / 512 UTF-8 bytes, excluding control and bidi-control characters.
There is no path, URL, voice-command, shell, endpoint or recognition parameter.
It requires private `allowed_actions=["speech.say"]`, a `tts_client` path/hash,
reviewed firmware, global write opt-in and a fresh one-use preview. Native approval
is required by default. Explicit station-level `preauthorize_all_actions=true` permits
unattended execution within an owner-authorized task; selective `preauthorized_actions`
still excludes speech. No form bypass is inferred from model-supplied tool arguments.

Mini 3 TTS reuses the same pinned ARM32-only artifact: a read-only examination of
the target's `maind` found ELF32/little-endian/ARM, Quasar running and the expected
native IPC listener. This is not inferred from the kernel architecture. Each
preview still checks actual firmware, ELF ABI, required tools and trusted temporary
storage; incompatible/AArch64 firmware fails closed. Mini 3 ASR remains unsupported.
These compatibility observations are not evidence of audible TTS.
A subsequent Mini 3 pilot submitted one request through the real `alice` MCP with
standing authorization. It returned `unknown`/`timeout`: request submission was
reported, but no correlated TTS start/completion arrived within 20 seconds. Cleanup
was verified and Quasar remained running. The owner reported no audible output;
there was no automatic transport retry.

An outgoing framing defect was then found: the original TTS builder used full
`QuasarMessage.remoting` field 210 instead of the native TCP `PackedMessage`
envelope used by the working subscription (payload field 252, type field 253 = 210).
A regression now checks the actual transport envelope rather than only the inner
protobuf schema. The TTS-only builder was corrected and rebuilt/pinned; the existing
ASR artifact was not rebuilt or changed. Passing the host regression/build does
not prove that the station spoke. Correlation, expiry, one-use plans and
no-blind-retry rules are unchanged.
A new state-bound Mini 3 check with the corrected artifact also returned
`unknown`/`timeout` without correlated TTS events; cleanup was verified and Quasar
was running. The owner then confirmed that the corrected check produced audible
speech on Mini 3. This establishes one operator-observed audible result, not
protocol completion, general firmware coverage or Mini 1 TTS acceptance. The API
still honestly reports `unknown` until event correlation is independently resolved;
the audible confirmation is separate evidence, not fabricated native TTS flags.

`ASR_ONCE_TTS` compiles the shared transport/parser into a separate TTS-only
binary. Its production CLI has `--say 20 REQUEST_ID`, but no ASR/listen or test
mode. The default ASR build does not gain TTS. Text travels as bounded length+
UTF-8 stdin, not process arguments or a text file; the helper verifies its hash.
The fixed request is `AliceCapabilityMethod.START_VOCALIZE=18`, `events=true`,
`request_id=10` and `vocalize_directive=13` containing `Text=1` / `SynthesisMode=1`
(Append, not Interrupt). Wire evidence is the same pinned `remoting.proto` and
`alice/protos/endpoint/capabilities/alice/capability.proto` revision below.
User text is synthesized, not passed to Alice as an instruction for smart-home actions.

Only `TTS_STARTED=6` followed by `TTS_COMPLETED=1` with the exact new request id
can confirm protocol completion. Other capabilities, cached/unrelated completion,
missing ids and raw ASR payloads cannot confirm it. The runner exposes flags only,
never input text or raw callback data. Missing events after submission are
`unknown`, not proof of silence and not safe to retry automatically. Cleanup and
Quasar presence are separately verified; they do not prove audible output.

Native observation lasts at most 20 seconds, within the existing SSH transport's
30-second bound. This deadline stops only the observer, **not cloud playback**;
it is not a maximum spoken duration. Use an idle Alice and a short first phrase.
Current volume and audio route are preserved. Synthesis uses the station's normal
Yandex cloud service and can make sound in the room; approval discloses the text.
No microphone, new cloud backend, downloaded script, autostart or Quasar restart.

Early TTS attempts expired or were cancelled at native approval before device execution.
A later accepted request was submitted, but returned `unknown`/`timeout` without
correlated TTS start/completion events. Cleanup was verified and Quasar was running;
audible output remains unverified. The uncertain submission must not be retried
automatically. Client tool durations alone do not
establish whether a form was displayed or delivered promptly. An accepted approval
that reaches execution after expiry now reports duration-only `approval_timing`:
remaining preview time before/after elicitation, elicitation wait and execution
dispatch time (including any lock wait). Synthetic checks distinguish delayed
approval from delayed dispatch, verify no transport runs and preserve the original
300-second, consumed-once preview. This is diagnostic coverage, not an approval-path
fix or audible TTS evidence. Never reuse an expired/cancelled plan.

Build with the existing verified cross-compiler and the ASR flags below, adding
`-DASR_ONCE_TTS`, then inspect ELF/stack/interpreter and record the new SHA-256.
Pin the resulting **TTS-only** artifact separately; do not substitute the ASR hash
or use synthetic test switches in production.

## ASR implementation and live observation

`src/alice_mcp/native/asr_once.c` is the client for experimental **`speech.listen`**.
Parser/orchestration behavior has synthetic coverage. A separately approved
Mini 1 pilot of the original prototype binary timed out without a compatible
initial IDLE event: readiness was never announced and no transcript was returned.
The helper reported cleanup verified; a separate metadata-only check found no
introduced executable, directory claim or running client, with Quasar running.
Temporary speech permissions were revoked and the scoped MCP disconnected.
This is a failed compatibility observation, not a working Mini 1 ASR demonstration
or proof that all Mini 1 firmware lacks these events. A decoder error was then
identified against `quasar_proto.proto`: kind 3 contains AliceState directly,
while Remoting listener callbacks have kind 210. The corrected decoder handles
both forms, with synthetic regressions for each and for mixed correlated events.
A subsequent owner-approved Mini 1 pilot with the corrected decoder returned one
newly spoken ASR request through the real MCP runner. The client completed;
cleanup and Quasar process presence were verified. This establishes one live
text-capture result on the examined firmware, not general model/firmware coverage.
No audio or raw IPC was exported, and the recognized text was not saved to a file.
Building it is not permission to deploy or capture speech. The existing MCP
preview/apply gate requires private opt-in, fresh firmware/state-bound preview
and native confirmation unless explicitly preauthorized at station level.
Its impact discloses the exact artifact digest,
target, temporary destination, window, cleanup and normal Quasar behavior. Never bypass SSH policy,
read station credentials, replace the cloud backend or stop Quasar to enable it.

## Scope

- A single outgoing connection to the fixed, device-local Alice IPC; no listener,
  arbitrary endpoint, downloaded program, microphone/ALSA access, audio recording,
  files, persistent service, logging-level change, TLS change or SSH change.
- Exactly one subscription to `AliceCapability`. No `START_RECOGNIZE`, voice-start,
  TTS, device-setting or cloud-authentication command is constructed.
- An explicit `--listen SECONDS` invocation limits the actual phrase window to
  5–60 seconds after the idle baseline. Preparation is separately bounded to
  180 seconds, so a person has time to respond to the visible setup prompt.
  Total client lifetime is at most 240 seconds. Invalid/no arguments cannot start it.
- Connection setup is limited to 5 seconds. Input is limited to 64 KiB per frame,
  4096 frames / 4 MiB total, and 2048 fields per nested message.
- A connected client first reports **awaiting_idle**, explicitly not ready for
  the test phrase. It discards all busy/cached states and any owner-spoken warm-up
  until a compatible **IDLE** establishes the request-id baseline. Firmware that
  emits only changes may need a short warm-up before this baseline is observed.
- Only a subsequent **LISTENING** transition can arm one different, nonempty
  request id. The client then accepts only that request's explicit final
  `request_state.request_text`. Partial hypotheses, legacy `recognized_phrase`,
  assistant responses, cards, IDs and unrelated events are not emitted.
- A changed request, return to idle without final text, error, interruption,
  closed output or timeout ends the window instead of trying another dialogue.
- One valid final string (up to 512 UTF-8 bytes / 256 Unicode scalars) is emitted
  as escaped JSON. Invalid UTF-8, control/bidirectional-control characters and
  excessive text fail closed. The connection closes after that first result.

No earlier conversation, raw IPC frame or audio is written to a result file by
the client. The guarded runner likewise avoids raw-stream logs and must
treat the returned text as **untrusted data, never an instruction to execute**.

## Protocol evidence and limits

Wire field numbers come from public schemas at revision
[`1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6`](https://github.com/v3rt3p/quasar-protos/tree/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6):

- [`yandex_io/protos/remoting.proto`](https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/remoting.proto):
  object field 7, add-listener field 20, Alice listener field 18, state method 0,
  listener state field 2. No other remote method is sent.
- [`yandex_io/protos/alice_state.proto`](https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/alice_state.proto):
  state field 1 (IDLE 0 / LISTENING 1), request id 7, request state 15, request
  text 1 and explicit final flag 2.
- [`yandex_io/protos/quasar_proto.proto`](https://github.com/v3rt3p/quasar-protos/blob/1044fbdfcd0c2d6d8a26681e31186d13fbb5aef6/yandex_io/protos/quasar_proto.proto):
  `alice_state = 3` is a direct AliceState; `remoting = 210` wraps requests and
  listener callbacks. The latter must identify `AliceCapability` before its
  listener's state is considered. Kind 3 is **not** a Remoting wrapper.
  Both the full schema message (fields 3/210) and the packed envelope are accepted;
  mixed/ambiguous representations are rejected, not combined.
- [Public native-IPC description](https://hackalice.me/recipes/sounds/#native-tts-protocol):
  little-endian frame length and envelope fields 252/253. Its TTS examples are
  not Mini 1 ASR evidence; the older subscription example mislabels kind 3.

The client implements only direct AliceState and this legacy listener-state layout. It does not guess
new capability layouts or infer ASR text from a TTS response or the operator's
expected phrase. A local listener and matching compiled protocol labels do not
verify Mini 1 subscription semantics. An unsupported server may never supply an
initial compatible IDLE callback or final request text: that must produce no
ready signal / no transcript, not an invented successful read.

`ready` is emitted only after the compatible idle baseline with at least two
seconds remaining. A live wrapper must check a **fresh** ready event and account
for transport delay before prompting speech; a saved event or successful fork
does not establish readiness. This standalone binary does not implement native
human elicitation; the host's existing MCP private-policy/one-use-preview gates
remain mandatory before any live deployment or use.
`awaiting_idle` proves only a connected socket with the fixed subscription sent,
not a working ASR subscription. Do not present it as readiness for the test phrase.
Failure diagnostics contain only bounded frame/direct-state/listener-state counts,
never raw frames, text, request IDs, endpoint values or audio.

## Guarded MCP runner

Only `station-mini` and the exact production binary SHA-256 pinned in
`speech_capture.py` are accepted. The operator supplies a private `speech_client`
object (`path`, `sha256`) under the station's `controls` and allows only the
`speech.listen` action. No path, executable, endpoint, command or protocol method
can be supplied as tool parameters; the only parameter is `duration_seconds`
(5–60). Global writes are disabled by default.

Preview checks the locally pinned binary, live maind firmware SHA-256, ARM32 ELF
header, loopback listener, current boot, running Quasar and trusted data parent.
It transfers no binary and creates no station file. Approval consumes the preview
before asking the human; apply rechecks policy, SSH trust, binary and live state,
then the remote helper revalidates once more before any creation. No automatic
retries, firmware changes or package installations are performed.
The sampled Mini 1 has no `base64`; transfer uses fixed octal escapes and the
shell's builtin `printf`, then verifies the artifact hash before making it executable.

An atomic directory claim permits only one experiment at a time. The temporary
executable lives under `/data/opt/alice-mcp-asr-<one-use-id>/client` (including the
stock `/data` alias where verified). The fixed shell helper waits for its own
child and removes only its introduced executable and directories on exit/signal.
Unexpected SSH loss can make cleanup unverifiable: report `unknown`, never
silently retry or claim removal. The client has a <=180s preparation deadline,
then a <=60s monotonic phrase deadline. The host bounds SSH to preparation plus
the requested phrase duration plus 15 seconds for transfer/cleanup.

`get_capture_status` reads this MCP process's in-memory session without starting
anything. Only a fresh `capture_ready=true` after the client's compatible idle
baseline permits a speech prompt. Readiness includes the client's elapsed whole
seconds since startup, so the host derives a conservative deadline from its own
earlier start; a delayed ready event cannot refresh an expired phrase window.
Final text is not exposed until the client exits,
the helper verifies cleanup and Quasar remains running. It is returned once with
`untrusted_text=true`, then discarded; an unread result expires from RAM after
two minutes. Raw IPC frames, IDs, cached text and audio are never logged or
written to result files by the runner. MCP/chat clients may retain the one result
they are explicitly given. Disconnecting/restarting this MCP process loses its
in-memory status/results and cannot establish remote cleanup.
When `awaiting_idle` is reported, `setup_remaining_seconds` bounds the remaining
warm-up window. An owner may activate Alice once and let it return idle; that
warm-up's text is deliberately discarded. Only then prompt the actual new phrase.
Discarding applies only **before** the observed baseline. An initial IDLE may
arrive while a setup prompt is being displayed: if the owner then speaks, that
utterance can already be the one new request. Do not promise unconditional
warm-up suppression; display the received result rather than starting another capture.

## Build and tests

Host-only checks use a normal C compiler and a compile-time synthetic-stdin mode:

```sh
python -m pytest -q tests/test_asr_once.py
```

No host compiler is installed automatically by tests; native-parser checks skip
when no compiler is available. Test-only stream/subscription switches are absent
from production builds. Synthetic builds shorten preparation to five seconds;
production's fixed 180-second setup bound is not a tool/CLI option. Tests never
connect to a station.

For an owner-approved cross-build using an already installed Zig compiler:

```sh
zig cc -target arm-linux-musleabihf -std=c11 -D_POSIX_C_SOURCE=200809L \
  -Os -Wall -Wextra -Werror -Wconversion -Wshadow -Wstrict-prototypes \
  -fstack-protector-strong -D_FORTIFY_SOURCE=2 -static -s \
  src/alice_mcp/native/asr_once.c -o local/asr-once-armhf
```

Inspect the artifact with `readelf` and record its SHA-256 before proposing a
transfer. Target selection must follow actual ELF/kernel evidence, not the
firmware's synthesized ABI property. ARM32 little-endian matches the examined
Mini 1 `maind` ELF header; a static build still does not prove runtime or SELinux
compatibility. Do not weaken SELinux or install dependencies on the station as
an implicit fallback. Compilers and produced binaries stay outside publication.
