# Project-local Codex and Claude Code session bridge

`scripts/agent_sessions.py` is an optional, stdlib-only development utility for
Linux/WSL and Python 3.11+. It is separate from the Station MCP server: no device
access, network listener, additional MCP alias, or dependency is added.

For native OpenCode tools, load the
[OpenCode V2 server plugin](../integrations/opencode/agent-sessions/README.md).
It provides `agent_sessions_list`, `agent_sessions_send` and
`agent_sessions_messages`, executing the bounded bridge on the server rather than
inside an agent's isolated shell. It uses private server-user state; configure
the client hooks with that same `--state-dir` path.

It records official lifecycle-hook events and queues short operator messages for
registered main sessions in **this checkout**. It does not discover arbitrary
sessions, read client histories, extract credentials, simulate keystrokes, start
model requests, stop agents, or change their permission decisions.

## Connect clients

Run these commands from the repository, in the same Linux/WSL environment as
the clients. Only connect the clients you use:

```sh
.venv/bin/python scripts/agent_sessions.py install codex
.venv/bin/python scripts/agent_sessions.py install claude
```

The installer appends hooks to `.codex/hooks.json` or
`.claude/settings.local.json`, preserving existing hooks and unrelated settings.
It never changes `config.toml`, allowed tools, permissions, or hook-trust policy.
Both configuration paths are already ignored by Git. Avoid simultaneous edits
in other settings editors during installation: replacement is atomic, but the
concurrent-change check is optimistic, not a filesystem compare-and-swap.

For inspection or manual merging instead of installation:

```sh
.venv/bin/python scripts/agent_sessions.py config codex
.venv/bin/python scripts/agent_sessions.py config claude
```

Review/trust Codex hooks through `/hooks`; do **not** bypass hook trust. Ensure
hooks are enabled by your client's policy. Claude Code also loads these events
from project-local settings. If an already-open client has not loaded the new
hooks, follow that client's hook reload/review flow, or reopen/resume the session
after saving its work. The installer does not interrupt it. Confirm a real event
appears in `list` before claiming the integration works. A running, unconnected
session cannot be attached to or messaged by this script.

Commands contain absolute paths to this checkout and its Python interpreter.
Generate them again after moving the checkout or interpreter. Native Windows
hooks are deliberately rejected: Windows ACLs and Windows/WSL path translation
are not implemented. A Windows-hosted Codex/Claude client must not be reported
as connected merely because its files are visible from WSL.

## Inspect and send

```sh
.venv/bin/python scripts/agent_sessions.py list
```

Results contain the client, exact session ID, last event/tool identifier,
last-reported state, timestamp, age and pending-message count. No prompt, tool
arguments/results, assistant answer, transcript path, or session working path is
stored. `tool_pending` means a pre-tool hook ran, **not** that execution started;
`approval_pending` is a reported permission request, not permission to answer it.

`freshness: stale` means no hook event in two minutes; a long model request,
tool call, idle session, missing hooks and a crashed client can all look stale.
`liveness` is always `unknown`: neither recent events nor an idle/ended event
prove a process is alive, dead, blocked, or has completed background work.

To queue a message, select both the client and its exact session ID from `list`.
Message text comes from stdin, not process arguments:

```sh
.venv/bin/python scripts/agent_sessions.py send codex SESSION_ID <<'MESSAGE'
Please report your current task and run the focused regression tests.
MESSAGE
.venv/bin/python scripts/agent_sessions.py messages codex SESSION_ID
```

Use `claude` instead for a Claude Code session. Messages are plain text, at most
2048 UTF-8 bytes, with at most 32 pending messages per session. Unknown sessions
and sessions whose last event is `SessionEnd` are rejected. Messages expire after
10 minutes. `messages` returns the latest 100 metadata-only receipts:

| Status | What it establishes |
| --- | --- |
| `queued` | Stored locally, awaiting an eligible hook |
| `claimed` | A hook transaction took the message for output; **not** proof of stdout delivery, model receipt, reading, acknowledgment or execution |
| `expired` | Its queue lifetime elapsed; it will not be injected |

One message is claimed per `SessionStart`, `UserPromptSubmit`, `PreToolUse` or
`PostToolUse` event and returned as quoted `additionalContext`. A `Stop` hook
never continues the turn. Idle sessions are not awakened. No message grants
permissions, answers an approval prompt, or supersedes existing safety rules.
Subagent events with `agent_id` are ignored to avoid consuming a main session's
messages. Subagent-specific messaging is not implemented.

Claiming is serialized with SQLite transactions and committed **before** stdout.
There is no automatic retry: a crash, output error, another blocking hook, client
interruption or ignored hook output can lose a claimed message. Ask the recipient
for an explicit acknowledgment when delivery matters. Do not assume `claimed`
is an acknowledgment or automatically resend an uncertain instruction.

## Privacy, failures and checks

By default, state is stored in ignored `local/agent_sessions/state.sqlite3`, with an
owner-only directory/file on POSIX. Symlinks, hard-linked state files, unexpected
owners and unsafe permissions are rejected. This is not protection against a
compromised current account/root, a hostile checkout, backups, or arbitrary
content secrets. Do not send credentials, station captures or private logs.
Queued message bodies are cleared logically on claim or the next write after
expiry; this is **not** a secure-erasure guarantee. Session and receipt metadata
remain local until the operator removes the bridge state while clients are idle.

For a checkout on a Windows mount that exposes `777` modes, do not weaken these
checks or chmod unrelated private settings. Select a protected **Linux** state
directory explicitly, using the same argument for every command, including
`config`/`install` (which embed that path into hook commands):

```sh
.venv/bin/python scripts/agent_sessions.py --state-dir /tmp/opencode/alice-agent-sessions list
```

The supplied directory's parent must already exist and have trusted ownership,
permissions and no symlinks; the bridge creates its own owner-only child state.
Each checkout is isolated by a hash of its resolved path. Temporary state may
disappear after reboot/cleanup. For durable state, choose a private Linux directory
instead. This option does not make untrusted project settings safe: `install`
still refuses writable-by-others `.codex`/`.claude` directories. In that case use
`config` to inspect the hook fragment and have the owner merge/review it through
the client's trusted configuration workflow; the bridge does not bypass trust.

The hooks and the controller must see the **same filesystem namespace**. A path
under `/tmp` in an isolated command sandbox may be private to that invocation,
not the host's directory. Matching path strings do not prove shared state;
verify an actual client hook event from the controller environment. A separate
host connector is required when the sandbox cannot access that protected state.

Input is bounded and only allowlisted identity/event/tool fields are retained.
Hook failures emit `{}` and exit successfully, without an approval/denial or raw
input/exception details. Other hooks and the client's normal policies still
apply. Monitor missing/stale events rather than treating this fail-open observer
as an enforcement or security audit mechanism.

```sh
.venv/bin/python -m pytest -q tests/test_agent_sessions.py
.venv/bin/python -m ruff check scripts/agent_sessions.py tests/test_agent_sessions.py
.venv/bin/python -m mypy --no-incremental scripts/agent_sessions.py
```

Compatibility sources:
[Codex hooks](https://developers.openai.com/codex/hooks),
[Claude Code hooks](https://code.claude.com/docs/en/hooks).
Tests use synthetic hook payloads and local temporary state. No live Codex or
Claude session delivery, native Windows behavior, or device action is certified
by those tests. Existing private client settings are not installed or changed
by the test suite.
