# Alice MCP

Local, privacy-first MCP server and source-linked skills for owner-managed
Yandex Stations. Development preview; not affiliated with Yandex or HackAlice.

See [the source catalog](docs/SOURCES.md), [security](SECURITY.md), and
[contributing](CONTRIBUTING.md). Third-party wiki claims are not device tests.
No private station data belongs in this repository.

## Install and run

Python 3.11+ and an OpenSSH client are required. Linux/WSL is the development
platform; Windows/macOS and real devices have not been qualified.

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/alice-mcp
```

The server uses **stdio only**, makes no outbound connection at startup, and
works as an offline knowledge/skills server without any station configuration.
There is no telemetry, discovery, cloud API, HTTP listener or arbitrary shell.
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
  }
}
```

For another stdio MCP client, use the same executable as `command` with no
arguments and supply the environment through that client's private settings.
Clients without native **form elicitation** can use all read-only tools, but
cannot execute service actions. A model-provided `approve=true` is not accepted
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

Only the alias and declared model are exposed to MCP. Choose nonsensitive aliases.
The model is operator input, not hardware identification. Use a dedicated key
and independently verify/pin the device host key in the private hosts file.
On POSIX, the SSH files and their ancestors must be owned by the current user or
root, without group/world write access (trusted sticky temporary directories
are permitted). SSH files/ancestors cannot be symlinks; the private identity
must have no group/other access. OpenSSH path-expansion characters are rejected.
The server never enrolls a host key, reads a private key into Python, accepts
passwords, or uses the user's SSH config/agent. Provision key-only SSH yourself
before using station tools; the wiki's empty-password setup is **not** sufficient.

## Tools and safety boundary

See the generated [MCP contract](docs/TOOLS.md).

| Tools | Behavior |
|---|---|
| `get_capabilities`, `list_stations` | Local capability/alias inventory; no station traffic |
| `search_knowledge`, `get_knowledge` | Original summaries, compatibility/risk metadata, source links |
| `list_skills`, `read_skill` | Bundled skills and references; not arbitrary local file access |
| `station_probe` | Fixed `health`, `storage`, `services`, `audio`, `reboot`, `logs` probes |
| `preview_service_action` | Read state and create a five-minute one-use preview |
| `apply_service_action` | Native human approval, fresh-state validation, Quasar `start`/`restart`, post-check |

Log inspection returns only error/warning line counts from a bounded sample of
`maind.log`; raw log text is never sent to the host. These are heuristics, not a
diagnosis. `services.updatesd` is a process-name check, **not proof** that OTA is
enabled/disabled (roles may run as `maind`). Audio reports only device-directory
presence; it is not an audio or microphone test. Kernel architecture does not
establish userspace ABI. Missing or incompatible fields fail closed or report
`unknown`. Buildroot probes may be unavailable on Android/JBL devices.

Service changes are disabled unless BOTH `ALICE_MCP_ALLOW_WRITES=1` and the
station's `allow_service_actions=true` are set locally. They additionally require
`quasar_script_sha256`: the SHA-256 of that station's **locally inspected**
`/etc/init.d/S99quasar`, whose `start`/`restart` semantics you have verified for
that firmware. Do not fill this field from a random online hash. The server
compares the live script hash, state and private configuration/trust metadata
with the preview's `expected_sha256`, elicits approval, consumes the preview
once, serializes actions, and checks the launcher afterwards.
The result reports command success and launcher presence, **not proof that a
restart changed the running instance** (`restart_verified=false`) or that voice
service is ready. Identity change detection binds POSIX file metadata including
ctime; it does not extract keys or protect against a compromised local account
or root. Service writes are disabled on native Windows pending equivalent
file-trust/ACL checks; WSL uses POSIX checks and suitably protected Linux files.

No configuration files are edited by these tools: file backups/rollback are not
applicable. Restart interrupts voice/playback, and cannot undo interrupted audio.
An SSH timeout can occur after execution; an `unknown` outcome is **not a failure
safe to retry**. Inspect locally first. There is no automatic retry, stop,
reboot, flash, root, OTA change, installer or restore endpoint. All such recipes
are manual-only skills with prerequisites and recovery guidance. Quasar is
preserved by default, even for AirPlay/standalone requests.

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
