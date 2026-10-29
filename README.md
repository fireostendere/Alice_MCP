# Alice MCP

Local, privacy-first MCP server and source-linked skills for owner-managed
Yandex Stations. Development preview; not affiliated with Yandex or HackAlice.

See [the source catalog](docs/SOURCES.md), [experimental controls](docs/CONTROLS.md),
[security](SECURITY.md), and [contributing](CONTRIBUTING.md).
Third-party wiki claims are not device tests.
No private station data belongs in this repository.

## Install and run

Python 3.11+ and an OpenSSH client are required. Linux/WSL is the development
platform; Windows/macOS and real devices have not been qualified.

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/alice-mcp
```

The host router uses **stdio**, makes no outbound connection at startup, and
works as an offline knowledge/skills server without any station configuration.
There is no telemetry, discovery or arbitrary shell. The optional resident agent
has a separately authenticated TLS listener; see [native transport](docs/NATIVE_AGENT.md).
Do not launch it manually in a terminal expecting a chat interface: configure
your MCP client to start it.

### OpenCode V2

Merge this entry into your existing configuration; do not replace other servers.
Replace `ALICE_MCP_EXECUTABLE` locally with the installed executable's absolute
path. Keep the private inventory environment variable outside tracked files.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "alice": {
        "type": "local",
        "command": ["ALICE_MCP_EXECUTABLE"],
        "environment": {
          "ALICE_MCP_INVENTORY": "{env:ALICE_MCP_INVENTORY}"
        }
      }
    }
  },
  "permissions": [
    {"action": "alice_*", "resource": "*", "effect": "allow"}
  ]
}
```

The [OpenCode V2 MCP configuration](https://opencode.ai/v2/docs/mcp-servers/) puts the
server name under `mcp.servers`. Its [permission rules](https://opencode.ai/v2/docs/permissions/)
can allow `alice_*` calls without a client-side question. This scoped rule leaves
the server's private policy, previews and required native confirmations in force.
Merge it with existing rules; a later matching rule can override it.
Use one `alice` connection for knowledge, LEDs, speech and other supported actions;
these are features of the same server, not separate MCP connections. `alice_*`
matches all its tools; feature-specific permission patterns can miss other tools.
Client tool permission and the server's native action approval are separate layers.

For another stdio MCP client, use the same executable as `command` with no
arguments and supply the environment through that client's private settings.
Clients without native **form elicitation** can use read-only tools and explicitly
preauthorized actions; other device and service actions require
elicitation. A model-provided `approve=true` is not accepted
as a tool argument or a substitute for a human response.

### Private inventory (optional)

Create a JSON file **outside this checkout**, set POSIX mode `0600`, and point
`ALICE_MCP_INVENTORY` at its absolute path. On Windows, apply owner-only ACLs
yourself; the server does not attest to Windows ACLs. No password field exists.

Shape (synthetic placeholders; edit only in your private file):

```json
{
  "stations": [
    {
      "id": "desk",
      "model": "station-mini3",
      "host": "station.invalid",
      "port": 2222,
      "user": "root",
      "identity_file": "/PRIVATE/identity",
      "known_hosts_file": "/PRIVATE/hosts",
      "allow_service_actions": false
    }
  ]
}
```

`list_stations` exposes only the alias and declared model. Experimental controls
also expose declared action, asset, binding and option aliases, not their private
paths or values. Choose nonsensitive aliases. The model is operator input, not
hardware identification. Use a dedicated key
and independently verify/pin the device host key in the private hosts file.
If that file pins a logical name instead of the endpoint, set the optional
`host_key_alias` field to that exact name (OpenSSH `HostKeyAlias`). Only literal
names of 1–253 ASCII letters, digits, dots, underscores and hyphens are accepted,
starting with a letter or digit; no patterns or SSH expansion tokens are allowed.
On POSIX, the SSH files and their ancestors must be owned by the current user or
root, without group/world write access (trusted sticky temporary directories
are permitted). SSH files/ancestors cannot be symlinks; the private identity
must have no group/other access. OpenSSH path-expansion characters are rejected.
The server never enrolls a host key, reads a private key into Python, accepts
passwords, or uses the user's SSH config/agent. Provision key-only SSH yourself
before using station tools; the wiki's empty-password setup is **not** sufficient.

## Tools and safety boundary

The experimental [`speech.listen` action](docs/SPEECH_CLIENT.md) can temporarily
transfer and run one exact reviewed ASR client on Mini 1 with private opt-in,
firmware/state-bound preview and native approval. It returns one new final text,
not audio. A build or successful transfer is not a readiness signal.
`speech.say` adds literal-text native TTS with a separately pinned ARM32 client.
One corrected Mini 3 pilot has owner-confirmed audible output; native completion
events remain unverified. On one tested Mini 1, the owner accepted normal-tempo
**local Piper → resident TLS → PCM** speech after a separately guarded upgrade,
with SSH disabled for that audition. No cloud fallback or microphone capture is
involved; reboot persistence and other firmware remain unqualified. Host tests
and an ARM build alone do not prove sound. See [native local voice](docs/NATIVE_AGENT.md)
and [speech clients](docs/SPEECH_CLIENT.md).

The server exposes **19 tools**. See the generated [MCP contract](docs/TOOLS.md)
and the [runtime control contract](docs/CONTROLS.md).

| Tools | Behavior |
|---|---|
| `get_capabilities`, `list_stations` | Local capability/alias inventory; no station traffic |
| `search_knowledge`, `get_knowledge` | Original summaries, compatibility/risk metadata, source links |
| `list_skills`, `read_skill` | Bundled skills and references; not arbitrary local file access |
| `station_status` | Every station at once: online flag, read-only `health` metrics, writes and private-policy summary |
| `station_probe` | Fixed `health`, `storage`, `services`, `audio`, `reboot`, `logs`, `led`, `i2c_trace`, `network` probes |
| `preview_service_action` | Read state and create a five-minute one-use preview |
| `apply_service_action` | Native human approval, fresh-state validation, Quasar `start`/`restart`, post-check |
| `get_model_interfaces(model)` | Local source-linked LED/audio/GPIO facts and explicit unknowns; no station access |
| `get_station_controls(station_id)` | Declared private-policy action/asset/option aliases; not verified hardware availability |
| `preview_station_action(station_id, action, parameters)` | Read-only runtime inspection and a 300-second one-use device preview |
| `apply_station_action(plan_id, expected_sha256)` | Private preauthorization or native human approval, fresh-state validation, bounded device action, post-check |
| `run_station_action(station_id, action, parameters)` | Preview and apply in one call: fresh one-use preview, then private preauthorization or one native approval form |
| `get_capture_status(station_id, capture_id)` | Read fresh capture readiness, bounded LED records or one approved ASR text; never starts capture |
| `start_job`, `list_jobs`, `cancel_job` | Owned host timers, declarative presets and a bounded generated-tone siren; cancel by owned ID; lost when this process exits ([jobs](docs/JOBS.md)) |

### Fewer approval prompts and bounded diagnostics

Within an owner-authorized task, the agent can inspect capabilities, run read-only
diagnostics and prepare previews without asking again. Apply presents the native
confirmation itself; a separate chat confirmation is unnecessary. Client-side
permission dialogs are controlled by the client, not by MCP tool annotations.

To delegate simple actions, add `"preauthorized_actions": ["led.effect", "audio.volume",
"audio.play"]` to that station's private `controls` object, choosing only the
actions you want to delegate. Each must also be in `allowed_actions`; global write
opt-in and reviewed firmware are still required. This allows supported bounded
LED effects, volume within the private ceiling and short playback of registered
assets without native elicitation. The setting persists until removed from the
private inventory. Keep its media and volume choices suitable for unattended use.
`get_station_controls` reports this list and each device preview reports
`approval_required`. Default policy still requires native approval for every write.

To delegate **all already-permitted actions**, set `"preauthorize_all_actions": true`
on the private **station entry**, alongside `controls` (not inside it). Default is
`false`. This skips native forms for service start/restart and all device actions,
including capture, arbitrary-text TTS and persistent edits, within authorized tasks.
It persists until removed or set to `false`; choose it only for a trusted local agent.
It does not enable writes, add allowed actions/assets/options, authorize unrelated
tasks, or implement unsupported operations. Revocation invalidates outstanding previews.
Every apply still consumes its state-bound preview once within 300 seconds,
rechecks live policy/trust/state and never retries uncertain writes. The server
does not edit private inventory or enable writes on the owner's behalf.

Native-agent writes additionally require the state-bound contract described in
[NATIVE_AGENT.md](docs/NATIVE_AGENT.md). Older resident builds remain readable but
cannot bypass one-use previews; writes fail closed until a separately guarded
upgrade. Source changes do not update an installed station automatically.

Read-only probes use at most two concurrent transports per server. No polling runs
in the background. Reuse results within a task; when checking a running capture,
wait at least two seconds between status calls and stop after completion or an
uncertain result. This bounds diagnostic bursts, but does not establish the cause
of an OpenCode UI freeze or profile the client's terminal.

An OpenCode confirmation form remains unusable if navigation or submission fails,
even when Esc responds. Terminal redraw is not a verified fix: the freeze can
recur. Testing a `mini` demo does not establish main-TUI compatibility; verify
the actual MCP form and received answer. The cause remains under investigation.
A cancelled or expired preview needs a new preview, and uncertain writes must
never be retried automatically.

Log inspection returns only error/warning line counts from a bounded sample of
`maind.log`; raw log text is never sent to the host. These are heuristics, not a
diagnosis. `services.updatesd` is a process-name check, **not proof** that OTA is
enabled/disabled (roles may run as `maind`). Audio reports only device-directory
presence; it is not an audio or microphone test. Kernel architecture does not
establish userspace ABI. Missing or incompatible fields fail closed or report
`unknown`. Buildroot probes may be unavailable on Android/JBL devices.

`station_probe(station_id, probe="led")` checks fixed device-node and library
paths, LED-class entries, the driver links at `0-003c` and `1-001f`, and whether
`maind` is running. It returns booleans and allowlisted driver names only. It does
not open LED devices, scan I2C, read registers, load libraries, inspect private
logs, stop Quasar, or change files. A false flag means the check did not observe
the object, not proof of absence under all permission/firmware conditions. Node
or library presence establishes neither a usable control protocol nor library
ABI or RGB order. Arbitrary Mini 1 RGB control remains unsupported; `led.replay`
only repeats a recorded update, while `led.pulse` and `led.animate` are bounded
low-brightness tests requiring recent captured idle evidence and owner confirmation.
This probe has offline
synthetic coverage only; restart the MCP process to load a newly added probe.

`station_probe(station_id, probe="i2c_trace")` inspects the two standard tracefs/
debugfs locations for I2C event metadata, write-payload format, instance/control
permissions and the current tracing-on flag. Only booleans leave the station.
It never mounts filesystems, enables events, reads trace buffers, changes the
tracer, attaches to processes or issues I2C requests. False flags can mean an
unmounted/inaccessible interface, not necessarily a kernel without tracing.
Availability is not proof that LED traffic reaches these tracepoints. The separate
`led.capture` action requires private opt-in, reviewed firmware, a fresh preview
and native human approval. It uses only its own trace instance for 5–60 seconds,
then stops and removes that instance. Global tracing is never changed. Check a
fresh `get_capture_status` response: only `capture_ready=true` means the filtered
capture is currently running, not merely that tracepoints exist. Discovery and
capture have synthetic coverage only; real station compatibility is unverified.

The passive `network` probe reports Wi-Fi interface/carrier and IPv4 default-route
counts, process-presence flags, and on-device classification of at most the last
400 kernel-log lines / 64 KiB. Only counts and boot-relative event seconds leave
the station, never SSIDs, addresses, interface names or raw logs. It sends no
packets, changes no network or power setting and does not clear the kernel log.
Missing markers are not proof that no disconnect, suspend or driver fault happened;
the bounded ring-buffer window and keyword classification cannot establish cause
alone. Process absence does not establish a DHCP or association failure.

### Service actions

Service changes are disabled unless BOTH `ALICE_MCP_ALLOW_WRITES=1` and the
station's `allow_service_actions=true` are set locally. They additionally require
`quasar_script_sha256`: the SHA-256 of that station's **locally inspected**
`/etc/init.d/S99quasar`, whose `start`/`restart` semantics you have verified for
that firmware. Do not fill this field from a random online hash. The server
consumes the preview once, elicits approval, then rechecks the live script hash,
state and private configuration/trust metadata against the preview's
`expected_sha256`, serializes execution, and checks the launcher afterwards.
The result reports command success and launcher presence, **not proof that a
restart changed the running instance** (`restart_verified=false`) or that voice
service is ready. Identity change detection binds POSIX file metadata including
ctime; it does not extract keys or protect against a compromised local account
or root. Service writes are disabled on native Windows pending equivalent
file-trust/ACL checks; WSL uses POSIX checks and suitably protected Linux files.

Service actions do not edit configuration files and have no file rollback.
Restart interrupts voice/playback and cannot undo interrupted audio. A restart
is a separate, explicit service action, never an implied configuration reload.

### Experimental device actions

The runtime implements `led.effect`, `led.capture`, `led.replay`, `led.pulse`, `led.animate`,
`speech.listen`, `speech.say`,
`audio.volume`, `audio.play`, `media.upload`,
`sound.replace`, `config.select`, and narrow backup-record `restore`.
`led.show` (source-tested, not live-accepted) adds bounded reference-effect equivalents
and a channel-calibration runner; see [LED parity](docs/LED_PARITY.md).
They require `ALICE_MCP_ALLOW_WRITES=1`, a private per-station `controls` policy
allowing the action, and a locally reviewed SHA-256 of
`/system/vendor/quasar/maind`. This digest binds the reviewed firmware, not a
universal model profile. Helpers use the same pinned, key-only SSH transport.
Mini 3 `led.effect` uses stock shell, `dd`, `base64`, `flock` and core utilities,
not device Python. It checks the Bergamot identity, exact geometry and firmware,
then restores and reads back the captured 18 bytes; physical visibility is not verified.
`led.capture` uses the preinstalled shell, `flock`, `setsid` and core utilities;
it requires neither Python nor `stat`/`timeout`. Mini 1 replay/pulse/animate use that
shell helper plus preinstalled `i2cset`. Pulse uses one second at fixed PWM 16/255,
then the observed idle zero frame; current state and restoration cannot be read back.
Other actions use Python 3.8+.
No helper installs packages, roots the station or uses ADB. Native
Windows device writes are unavailable.
Bundled helper source is sent through bounded SSH stdin, keeping the SSH exec
request short; this is not a user-supplied shell/script interface.

Mini 1 `led.animate` provides only these fixed, dim presets (PWM at most 16/255):

| `mode` | Nominal show |
|---|---|
| `blink` | Six logical colors, each 0.5 s on / 0.5 s idle; 6 s total. |
| `rainbow` | Six colors across six logical zones, held for 3 s. |
| `rainbow_swirl` | Twelve half-second steps of the logical six-zone palette; 6 s total. |

Example parameters: `{"capture_id": "<opaque completed capture id>", "mode": "rainbow"}`.
Mode aliases appear in `get_station_controls().led_modes`, not as permissions.
Each show needs a fresh preview and native approval unless explicitly preauthorized;
the ring must be idle/dark. Same-boot,
same-firmware idle evidence must be no older than 30 minutes. It returns to that
observed idle frame, without hardware readback. RGB order, physical zone order
and swirl direction are not calibrated. These presets have synthetic coverage,
not verified visual playback. No show daemon, autostart or firmware-voice command
is installed; operator chat requests are routed through the existing MCP action.

Device actions reuse the service controller's one-use preview/approval registry,
trust/configuration binding, fresh-state checks and serialized execution. A
preview performs no device writes; it still requires the action's policy gates.
Apply requires the exact returned `plan_id` and `expected_sha256` within 300 seconds,
plus native human elicitation unless that bounded action is privately preauthorized.
Cancellation also consumes the preview.
Private policy lists are declarations, not proof that a driver, mixer or backend
is present. Unknown drivers/formats or mismatched firmware fail closed.

RGB brightness is limited to 0–32, effects to 1–3 cycles of 500–1000 ms, playback
to 1–10 seconds, and volume to the private ceiling (default 30%). Uploads accept
only an approved asset alias with matching preregistered SHA-256 and at most
49,152 decoded bytes. Config selection accepts only approved binding/option
aliases, never a supplied URL, path or secret. Exact parameters, model limits,
private JSON policy and guarded rootfs opt-in are in [CONTROLS.md](docs/CONTROLS.md).

Keep Quasar enabled. Transient effects attempt to restore captured state; persistent
edits create owner-only restore records first. `restore` rechecks the recorded
post-edit state of one such record, not factory recovery or a broad reset.
Hash checks are optimistic, **not atomic compare-and-swap**: the helper lock only
serializes cooperating helpers. External file/mixer writers must be idle; a write
after the final comparison can still be overwritten or deleted. If writers cannot
be kept idle, do not apply; never stop Quasar to obtain exclusivity.
File/control post-checks
do **not** establish visible LEDs, audible output, backend readiness or config
reload. No station access or hardware test was performed; integration checks are
offline and synthetic only.

An SSH timeout can occur after execution; an `unknown` outcome is **not a failure
safe to retry**. Inspect locally first. There is no automatic retry, Quasar stop,
implicit reboot, flash, root, OTA change or installer endpoint. Those unsupported/high-risk
recipes remain guidance-only, with prerequisites and recovery planning. Quasar is
preserved even for AirPlay/standalone requests.

## Skills and source coverage

The catalog maps every documentation page in a pinned upstream revision to one
or more skills. It is a source-linked, original workflow adaptation, **not a
mirror** of every script, asset or external linked project. Upstream material
was not vendored because a redistribution license was not identified. Consult
the linked current page when executing a manual procedure and check exact
model/firmware compatibility. No private experiences or station results were
imported. Documentation claims and offline tests do not certify a physical unit.

Skills are in `skills/` for manual installation into your agent's skill directory
(copy only the skill directories, not private inventory files). The wheel also
bundles them, accessible through `read_skill`. Installation does not register
them automatically in a client's global skill catalog.

## Development checks

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check --no-cache src tests scripts
.venv/bin/python -m mypy --no-incremental src/alice_mcp
PYTHONPATH=src .venv/bin/python scripts/snapshot_tools.py --check
.venv/bin/python scripts/check_public.py
.venv/bin/python -m build
.venv/bin/python scripts/check_public.py --archive dist/PACKAGE.whl
.venv/bin/python scripts/check_public.py --archive dist/PACKAGE.tar.gz
```

Before publishing, also scan the staged Git snapshot, resulting commit history,
and distribution payloads with `gitleaks --redact`. Never use raw scanner reports
as public attachments. Scanners reduce risk; they cannot prove that arbitrary
text contains no secrets. No license has been selected for this initial project;
public visibility alone does not grant redistribution rights.
