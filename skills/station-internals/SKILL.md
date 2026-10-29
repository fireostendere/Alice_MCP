---
name: station-internals
description: Diagnose Quasar startup, networking, voice and directive layers, then plan ABI-correct host builds; triggers include как устроена Алиса, maind, protobuf, кросс-компиляция.
---

# Trace one layer; do not rewrite the platform

Require owner authorization for station evidence. Keep Quasar enabled. Reading
public documentation and inspecting sanitized metadata is preferred; deployment,
service/config changes, module loads and manual installations are guidance-only.
Never collect private factory data, raw device-key files or household audio.

## Prerequisites

Resolve model, OS, firmware and the single failing function. The generic architecture
is mainly Lite/Amlogic Buildroot; Android APK stacks and JBL Yocto/systemd are
different. Use already-authorized scoped read-only access, not an arbitrary shell
executor, discovery sweep or a new root flow.

## Diagnosis

### Startup and filesystem

Map BusyBox rcS startup to Quasar onboot, launcher configuration and maind roles.
Different role names can be instances of one executable, not separate packages.
Inspect only relevant role/version metadata; do not call broad diagnostic helpers
that print device IDs or account configuration. Actual mount flags matter more
than BusyBox write-permission tests. RAM-backed paths do not establish persistence.

### Network and voice

The cited Lite map includes glagol 1961, optional ADB 5555, optional SSH 2222 and
mDNS 5353, plus local IPC/DNS. A port list is reference data, not a target list for
scanning or proof that an operation is authenticated. Internal service ports come
from the exact model/build configuration. Do not expose them to the LAN.

Separate microphone capture/VQE, local wake words, cloud ASR/dialogue, directive
routing and output playback. Text input skips ASR, so working text does not prove
microphone health. Missing cloud ASR is not fixed by replacing an LLM URL. Do not
record speech to diagnose by default; use owner-approved synthetic input.
Capture device/rate examples differ: Cucumber's shared PDM report requires **96 kHz**;
never apply a generic 48 kHz example to it or retune the hardware to experiment.

### Directives and state

The Mini 2 report distinguishes legacy `Payload` containing protobuf Struct from
typed `PayloadRaw` with serialized message bytes and matching `DirectiveType`.
An apparent JSON shape is not an accepted typed wire message on protobuf-lite.
Use matching protocol schemas and synthetic host-side encoding tests first.
Own-device volume, alarm, timer and IoT alarm capabilities are different targets.
Alarm-list state comes through its capability update, not necessarily the generic
device-state message. Do not send trial alarm or smart-home directives to learn
their meaning; those are state-changing operations with their own approval.

### Runtime pitfalls

Check tool support before suggesting flags: sampled BusyBox lacks expected timeout,
tar and awk features. ALSA includes and cached mdev rules can explain why a config
edit is not reflected in an existing process. Competing avahi/maind address
publication can cause discovery-name churn; do not infer it is a network attack.
Vendor-module ABI needs matching structure layout, not merely a version string.
The older TLS/no-pinning observation is model/build-scoped, not permission to use
plaintext endpoints or assume certificate checks are absent on newer firmware.

### Host toolchain workflow

1. Inspect kernel architecture and the actual target ELF class/interpreter; do not
   trust maind's synthesized `getprop` ABI. Lite-family kernels can be AArch64 with
   armhf userspace. Street/JBL need ARMv7 even though their CPU family supports more.
2. For an independent tool on a supported 64-bit kernel, the source recommends a
   static AArch64 musl target. For firmware-library integration use the actual armhf
   ABI and verify that model's glibc ceiling; Lite's 2.30 is not Mini 1's guarantee.
3. Build on the host with an existing cross-toolchain or Zig/CMake support, using
   a pinned source and dependency set. Inspect the artifact locally with ELF tools
   for interpreter, shared dependencies and minimum CPU features before deployment.
   Do not replace system loaders/libraries to satisfy a mismatched binary.
4. Treat the source's Dropbear architecture labels as conflicting: inspect the
   selected artifact. A documented download name does not establish its ABI.

## Preview and confirmation

Return the failing layer, evidence, one hypothesis and a bounded next observation.
For a build, show target triple, artifact hash, dependencies, proposed data-volume
destination and harmless smoke check. Ask separate owner confirmation for transfer,
execution or persistence; compilation approval is not deployment approval.
Do not restart Quasar as a default diagnostic or load an experimental module.

## Backup and rollback

Read-only analysis needs no station backup. Before a later owner-run deployment,
preserve the previous application/version/config and ownership manifest. Place new
applications and mutable state in the verified persistent data volume. Rollback
removes only introduced application files or restores the previous version, not
system directories. Keep build artifacts separate from private station evidence.

## Verification

Check the hypothesis against a second bounded observation or host-side synthetic
protocol/build test. For manual application testing, confirm exact binary/version,
expected output and unchanged Quasar functions, startup and audio parameters.
Report community observations, inferences, simulated checks and device tests as
different evidence classes. Unknown semantics remain unknown.

## Limitations

The wiki does not fully specify glagol or every maind diagnostic. Firmware role,
port and typed-directive maps are not stable public APIs. No live capture,
cross-build, station deployment or acceptance test was performed for these summaries.

## Sources

- [Startup, maind and synthesized properties](https://hackalice.me/internals/architecture/)
- [Network map and DNS fallbacks](https://hackalice.me/internals/network/)
- [Voice stages and labeled inferences](https://hackalice.me/internals/voice/)
- [Legacy/typed directives and state](https://hackalice.me/internals/directives/)
- [BusyBox, ALSA, mDNS, kernel and TLS pitfalls](https://hackalice.me/internals/gotchas/)
- [Host toolchains, ABI choices and ADB setup](https://hackalice.me/internals/toolchain/)
- [Cucumber microphone-rate constraint](https://hackalice.me/devices/station-midi/)
