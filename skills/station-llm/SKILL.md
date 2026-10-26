---
name: station-llm
description: Compare Quasar Bridge/quasard boundaries and preview/apply existing private JSON endpoint, voice or model bindings without installing a backend; triggers include своя LLM на Алисе, Quasar Bridge, quasard.
---

# Choose the voice boundary first

Require owner authorization and consent from people whose speech may be processed.
Keep Quasar enabled. Experimental `config.select` implements selection among
explicit operator-reviewed JSON bindings, not backend deployment. DNS changes,
trust-store work, installation, rooting, OTA policy and unsupported/high-risk
operations remain guidance-only; never deploy or intercept traffic automatically.

## Prerequisites

Ask which outcome matters: retain cloud Alice voice/music/home functions, or replace
ASR/dialogue/TTS. Identify model/build and existing secure access. Decide where
audio, transcripts, API credentials and dialogue history may go. Keep secrets in
owner-controlled configuration outside this repository and chat.

Runtime selection needs secure pinned-host-key, key-only SSH, preinstalled Python
3.8+, global write opt-in, a private `config.select` allowlist entry and a locally
reviewed `maind` firmware SHA-256. No automatic installation or native Windows
device writes are available. A working backend must already exist independently.

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
6. `get_model_interfaces(model)` reports source evidence;
   `get_station_controls(station_id)` reports declared binding kinds and option
   aliases, not private values or verified backend/hardware availability. JSON
   adapters bind existing known leaf keys; they are not manufacturer-certified
   schemas. Native `quasar.cfg` supports only `uniProxyUrl`/`uniProxyQuicUrl`
   endpoint bindings, not arbitrary stock Alice voice replacement.

## Preview and confirmation

Return a decision table for the owner's required functions and data destinations,
using nonsensitive aliases rather than publishing private URLs. For a proposed
manual deployment, list scope, host resources, expected lost features, log-retention
policy and original-file recovery; keep exact private values in owner-held records.
Start with synthetic text/audio tests on the host using the external project's
reviewed simulators; do not use household recordings as fixtures.

Separate approval for Home Assistant actuation from conversation routing. Keep
tool permissions narrow and verify target/action previews; a model-generated tool
call is not authority. Ask before any voice redirection, daemon restart, DNS or
OTA policy change. Do not stop Quasar as an implied LLM setup step.

For an already registered binding, call
`preview_station_action(station_id, "config.select", {"binding": alias, "option": alias})`.
This is read-only inspection. Both aliases must match `[a-z][a-z0-9-]{0,31}` and
exist in private policy. Do not supply a URL, value, path, shell command or key.
Review the data/voice destination change privately with the owner, then pass the
returned `plan_id` and `expected_sha256` to `apply_station_action`. Native human
elicitation is required; previews are single-use with a 300-second TTL, consumed
even on cancellation, and revalidate configuration/trust/state before execution.

Private bindings use JSON-only `.json`/`.cfg` files under `/data/opt/`, or the
limited native `quasar.cfg` endpoint fields. `pointer` is an array of existing
leaf keys; `kind` is `endpoint`, `voice` or `model`; `choices` maps public aliases
to private string values. HTTPS/WSS is the default. HTTP/WS needs explicit
per-binding `allow_insecure=true`; credentials, query strings and fragments remain
forbidden. `voice`/`model` choices only select an existing reviewed adapter's fields,
not an arbitrary native voice or a newly installed LLM.

## Backup and rollback

Before persistent JSON edits, the helper saves owner-only records at
`/data/opt/alice-mcp/backups/<id>.json`. Preview `restore` with `backup_id` (32
lowercase hexadecimal characters), then request separate native approval. Restore
refuses changes observed by optimistic state checks; it is not atomic
compare-and-swap. External file/mixer writers must be idle throughout apply/restore:
an update after the final check can be overwritten or deleted, and the helper lock
does not lock external writers. If exclusivity cannot be ensured, do not apply;
never stop Quasar to obtain it. Only a file's own record may delete a created file.
Never substitute a broad rollback/reset.

The owner separately keeps original DNS rules, configuration, permissions and hashes
privately, with a secure recovery path independent of the backend. Runtime restore
does not undo external deployment/DNS changes. Preserve unrelated router configuration
and never upload account/device files for debugging. Read-only rootfs edits fail by
default; explicit private per-station `allow_rootfs_remount=true` permits only guarded
temporary read-write access and restoration of the original read-only mount state.

## Verification

Config selection does not imply reload. A service restart requires its own explicit
service preview/approval and may interrupt voice/playback; it is not proof of config
reload. File/control post-checks are not proof of audible effects or backend readiness;
`effect_observed` and `physical_effects_verified` remain false. Unknown write/cleanup
outcomes need local inspection, never automatic retry.

After separately approved changes, test synthetic chat, one quiet spoken
request, cancellation and each required retained function (music, timer, HA where
authorized). Confirm actual data destinations and disabled transcript logging.
A working response is not proof of offline isolation: DNS fallbacks and other
services may still contact the vendor. Report such isolation as unverified unless
the owner supplies scoped, sanitized evidence. Restore normal behavior on failure.

## Limitations

GPU estimates, API quotas and latency in the recipe are one project's observations,
not guaranteed capacity. Voice cloning requires rights and consent to the sample.
No credentials, certificates, interception payloads or external server code are
distributed here. Public evidence is pinned to revision
`1ab048bf95c0e5e547a1ff6c801d2ed9deb193db`. No station access or hardware test was
performed; integration checks are offline/synthetic. Neither backend is qualified
by policy declarations or synthetic checks.

Sources: [LLM alternatives and compatibility](https://hackalice.me/recipes/llm/),
[voice pipeline](https://hackalice.me/internals/voice/),
[TLS scope caveats](https://hackalice.me/internals/gotchas/),
[Orion exclusions](https://hackalice.me/devices/station-3/).
