---
name: station-llm
description: Compare Quasar Bridge, ChatGPT Voice and self-hosted backend (quasard, v3rt3p) boundaries and preview/apply existing private JSON endpoint, voice or model bindings without installing a backend; triggers include своя LLM на Алисе, Quasar Bridge, quasard, свой бэкенд, ChatGPT на колонке.
---

# Choose the voice boundary first

Require owner authorization and consent from people whose speech may be processed.
Keep Quasar enabled. Experimental `config.select` implements selection among
explicit operator-reviewed JSON bindings, not backend deployment. DNS changes,
trust-store work, installation, rooting, OTA policy and unsupported/high-risk
operations remain guidance-only; never deploy or intercept traffic automatically.

## Prerequisites

Ask which outcome matters: retain cloud Alice voice/music/home functions, hand the
microphones and speaker to a ChatGPT voice session, or replace ASR/dialogue/TTS
with an own server. Identify model/build and existing secure access. Decide where
audio, transcripts, API credentials and dialogue history may go. Keep secrets in
owner-controlled configuration outside this repository and chat.

Runtime selection needs secure pinned-host-key, key-only SSH, preinstalled Python
3.8+, global write opt-in, a private `config.select` allowlist entry and a locally
reviewed `maind` firmware SHA-256. No automatic installation or native Windows
device writes are available. A working backend must already exist independently.

## Diagnosis

1. Native stations generally perform wake-word/local commands on device, not full
   ASR. Losing the cloud does not automatically provide recognized text locally.
2. **Quasar Bridge** is the documented hybrid: per request it routes to an own
   OpenAI-compatible model, Home Assistant (through an MCP server) or Yandex,
   while Yandex cloud keeps doing ASR/TTS, so the account binding and Alice's voice
   stay. It works by replacing both uniproxy endpoint keys in the system-partition
   `quasar.cfg`. At v0.6.2 its project reports install, update and autostart on
   **Station 2 and Midi** and operation only on Lite; the ARMv7 build is untested
   on any 32-bit model, and the recipe's open-questions list still names Station 2
   alone. It now installs onto the station's data partition with its own autostart,
   or on a separate host; the YAM package is legacy and no longer updated. Running
   it on the station does not run the LLM or HA there. Optional model tools (off
   by default) add voice-triggered self-update and web-page reading; a built-in
   AirPlay receiver must not run beside a separately installed one. A Lite 1
   variant keeps Yandex only as far-field ASR and passes the final text to an own LLM.
3. **ChatGPT Voice (Alisa Voice 0.3.3)** leaves the endpoint alone and connects the
   processed microphone and speaker to a ChatGPT account session: natively on
   Lite 1, Lite 2 or Station 2, or through a browser on an always-on Linux PC
   (Lite 2 only). Both modes stay cloud; Alice's replies are suppressed and music
   pauses during a chat, and the open/close phrases are still recognised by Yandex.
   The recipe verified one Lite 2/Plum build; Lite 1 and Station 2 results come
   from adaptation authors, retail Midi is unsupported, and an unknown build of a
   supported model is admitted with a warning only. The session lives on the
   station (native) or in the PC browser profile (bridge). For the audio tap see
   [station-microphone](../station-microphone/SKILL.md).
4. **Self-hosted backends** replace the vendor's server side (protocol layer, STT,
   request processor, optional LLM, TTS) on an always-on machine; stock functions
   are not guaranteed and some projects still use cloud parts. **quasard** answers
   activation itself, blocks firmware updates, speaks the older JSON (around 1.56)
   and 3.x protobuf voice formats, and needs router redirection of vendor domains,
   forced DNS, a certificate the station trusts and an NVIDIA GPU. Its page marks
   status and model compatibility as unchecked; Yandex Music works, cloud smart
   home, scenarios and Alice's voice do not, and its default model key is for a
   cloud provider. **v3rt3p** is a container stack with Home Assistant, timers
   and reminders, no music or multiroom and no GPU requirement; its page leaves
   model compatibility undefined and defers installation to project documentation.
   Two further listed projects are still in development.
5. Separate routing, speech recognition, model latency, tool execution and playback
   errors. Installing the bridge does not itself prove the endpoint was switched:
   its installer asks before switching and reboots the station;
   changing only the general backend URL does not replace uniproxy.
6. Check current TLS constraints for the actual build. The older “no pinning” claim
   does not override newer trust-store restrictions. Do not disable TLS validation,
   import leaked credentials or use private factory keys to make a proxy work.
7. `get_model_interfaces(model)` reports source evidence;
   `get_station_controls(station_id)` reports declared binding kinds and option
   aliases, not private values or verified backend/hardware availability. JSON
   adapters bind existing known leaf keys; they are not manufacturer-certified
   schemas. Native `quasar.cfg` supports only `uniProxyUrl`/`uniProxyQuicUrl`
   endpoint bindings, not arbitrary stock Alice voice replacement.

## Preview and confirmation

For an owner-requested action, call `run_station_action(station_id, action, parameters)`: it previews and applies in one call
(no form when preauthorized). Use the separate preview/apply tools only to show a plan before acting.

Return a decision table for the owner's required functions and data destinations,
using nonsensitive aliases rather than publishing private URLs. For a proposed
manual deployment, list scope, host resources, expected lost features, log-retention
policy and original-file recovery; keep exact private values in owner-held records.
Start with synthetic text/audio tests on the host using the external project's
reviewed simulators; do not use household recordings as fixtures. Stop when the
model or build lies outside the reported evidence, when an install route executes
a remotely fetched script unreviewed, when a required trust or DNS change is
unclear, or when the original `quasar.cfg` is not yet saved off the station.
A Home Assistant voice satellite that replaces Alice is covered in
[station-integrations](../station-integrations/SKILL.md).

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
elicitation is required unless the preview reports `approval_required=false` under
explicit station-level `preauthorize_all_actions=true`. Previews are single-use with a 300-second TTL, consumed
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
lowercase hexadecimal characters), then apply with native approval unless explicitly
preauthorized (`approval_required=false`). Restore
refuses changes observed by optimistic state checks; it is not atomic
compare-and-swap. External file/mixer writers must be idle throughout apply/restore:
an update after the final check can be overwritten or deleted, and the helper lock
does not lock external writers. If exclusivity cannot be ensured, do not apply;
never stop Quasar to obtain it. Only a file's own record may delete a created file.
Never substitute a broad rollback/reset.

Upstream rollback, for the owner to run: an on-station Quasar Bridge is first
returned to the original endpoint with its own control script and a reboot, Alice
is checked, and only then is it uninstalled (removal refuses before rollback;
settings, keys and logs stay in its data directory). A separate-host bridge, a
legacy YAM install or quasard needs the saved original `quasar.cfg` put back on
the system partition and a reboot. ChatGPT Voice is undone by turning its
autostart off and stopping the mode; its login and SSH access remain.

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
`520fbccd68cb90a8c4f01d06bf38d9a7db915304`. No station access or hardware test was
performed; integration checks are offline/synthetic. Neither backend is qualified
by policy declarations or synthetic checks.

Sources: [LLM alternatives and compatibility](https://hackalice.me/recipes/llm/),
[self-hosted backend overview](https://hackalice.me/recipes/bekend-self-hosted/),
[quasard](https://hackalice.me/recipes/quasard/),
[v3rt3p](https://hackalice.me/recipes/v3rt3p/),
[voice pipeline](https://hackalice.me/internals/voice/),
[TLS scope caveats](https://hackalice.me/internals/gotchas/),
[Orion exclusions](https://hackalice.me/devices/station-3/).
