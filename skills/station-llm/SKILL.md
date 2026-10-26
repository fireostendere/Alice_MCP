---
name: station-llm
description: Compare hybrid Quasar Bridge and replacement quasard backends with model, privacy and rollback boundaries; triggers include своя LLM на Алисе, Quasar Bridge, quasard.
---

# Choose the voice boundary first

Require owner authorization and consent from people whose speech may be processed.
Keep Quasar enabled. Endpoint/DNS changes, trust-store work, installation, rooting
and OTA policy are guidance-only; never deploy or intercept traffic automatically.

## Prerequisites

Ask which outcome matters: retain cloud Alice voice/music/home functions, or replace
ASR/dialogue/TTS. Identify model/build and existing secure access. Decide where
audio, transcripts, API credentials and dialogue history may go. Keep secrets in
owner-controlled configuration outside this repository and chat.

## Diagnosis

1. Native stations generally perform wake-word/local commands on device, not full
   ASR. Losing the cloud does not automatically provide recognized text locally.
2. **Quasar Bridge** is the documented hybrid: own LLM/Home Assistant routing with
   Yandex cloud ASR/TTS and fallback functions. Reported evidence is **Station 2,
   Quasar 3.21**. Running a proxy on the station does not run the LLM or HA there.
3. **quasard** replaces activation/backend and uniproxy, including its own ASR/TTS.
   The source describes older JSON and 3.x protobuf AAPI, not universal firmware
   compatibility. Cloud smart-home/scenarios and Alice's original voice are not
   preserved by this route. External LLM/music services can still require internet.
4. Separate routing, speech recognition, model latency, tool execution and playback
   errors. A .yap installation does not itself redirect the speech endpoint;
   changing only the general backend URL does not replace uniproxy.
5. Check current TLS constraints for the actual build. The older “no pinning” claim
   does not override newer trust-store restrictions. Do not disable TLS validation,
   import leaked credentials or use private factory keys to make a proxy work.

## Preview and confirmation

Return a decision table for the owner's required functions and data destinations.
For a proposed manual deployment, list exact endpoint/config changes, host resources,
network scope, expected lost features, log-retention policy and original-file recovery.
Start with synthetic text/audio tests on the host using the external project's
reviewed simulators; do not use household recordings as fixtures.

Separate approval for Home Assistant actuation from conversation routing. Keep
tool permissions narrow and verify target/action previews; a model-generated tool
call is not authority. Ask before any voice redirection, daemon restart, DNS or
OTA policy change. Do not stop Quasar as an implied LLM setup step.

## Backup and rollback

The owner keeps the original endpoint configuration, DNS rules, permissions and
hashes privately. Retain a secure path independent of the replacement backend.
Rollback restores the exact original keys/rules, including alternate speech
transports changed by that deployment—not a broad network reset. Preserve unrelated
router configuration and never upload account/device files for debugging.

## Verification

After separately approved owner-run changes, test synthetic chat, one quiet spoken
request, cancellation and each required retained function (music, timer, HA where
authorized). Confirm actual data destinations and disabled transcript logging.
A working response is not proof of offline isolation: DNS fallbacks and other
services may still contact the vendor. Report such isolation as unverified unless
the owner supplies scoped, sanitized evidence. Restore normal behavior on failure.

## Limitations

GPU estimates, API quotas and latency in the recipe are one project's observations,
not guaranteed capacity. Voice cloning requires rights and consent to the sample.
No credentials, certificates, interception payloads or external server code are
distributed here; this project has not tested either backend on a station.

Sources: [LLM alternatives and compatibility](https://hackalice.me/recipes/llm/),
[voice pipeline](https://hackalice.me/internals/voice/),
[TLS scope caveats](https://hackalice.me/internals/gotchas/),
[Orion exclusions](https://hackalice.me/devices/station-3/).
