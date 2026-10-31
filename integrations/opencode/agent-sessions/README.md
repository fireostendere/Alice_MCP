# OpenCode V2: Codex / Claude Code session tools

Local server plugin for the [project session bridge](../../../docs/AGENT_SESSIONS.md).
Uses the OpenCode V2 `tool.transform` and `permission.assert` APIs, Node built-ins,
and the existing stdlib Python bridge. No npm install, new MCP connection, agent
launch, shell endpoint, model request, polling or transcript access is added.

## Load

Merge this into your existing project OpenCode configuration without replacing
other plugins, MCP servers or permissions:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["./integrations/opencode/agent-sessions"]
}
```

Alternatively, create `.opencode/plugins/agent-sessions/index.ts` with this
single project-local loader (do not enable both methods):

```ts
export { default } from "../../../integrations/opencode/agent-sessions/index.js"
```

OpenCode reloads project plugins/configuration after changes; `/reload` forces a
reload. Do not restart the service just for this plugin. The effective tools are
`agent_sessions_list`, `agent_sessions_send`, and `agent_sessions_messages`.
They check the invoking OpenCode session's current project and use its native
permission flow. No permission rules are changed, and rejections propagate.

The default interpreter is this checkout's `.venv/bin/python`; default state is
the OpenCode server user's private `.alice-agent-sessions` directory. Optional
operator configuration uses absolute Linux paths:

```json
{
  "plugins": [{
    "package": "./integrations/opencode/agent-sessions",
    "options": { "python": "/PRIVATE/python", "stateDir": "/PRIVATE/session-state" }
  }]
}
```

Configure Codex/Claude hooks with the **same** `--state-dir` value, following the
bridge guide. For the default state location, run in the clients' Linux/WSL user
environment (unsafe mount permissions can require the guide's manual merge):

```sh
.venv/bin/python scripts/agent_sessions.py --state-dir "$HOME/.alice-agent-sessions" install codex
.venv/bin/python scripts/agent_sessions.py --state-dir "$HOME/.alice-agent-sessions" install claude
```

Their clients must actually load and trust those hooks. The plugin
does not install or trust them, attach to an unconnected conversation, wake an idle
agent, or control native Windows apps. `claimed` is not a delivery acknowledgment.
Only send an instruction requested by the owner; tool permission is not task
authorization. An uncertain send is never retried automatically.

The server plugin executes a fixed Python argv directly on the server, **not**
inside the agent shell sandbox. No executable, project/state path or arbitrary
operation is accepted as a tool argument. Message bodies use stdin. Output is
bounded to 256 KiB and execution to five seconds; raw process errors/stderr are
never returned. Stopping a tool cancels only its bridge subprocess, not Codex or
Claude. A cancellation after dispatch can leave a queued message: inspect receipts.

## Check

```sh
node --test integrations/opencode/agent-sessions/lib.test.js
.venv/bin/python -m pytest -q tests/test_agent_sessions.py
```

Tests use fake OpenCode APIs and real Python against synthetic temporary hook
state. This does not certify live plugin activation or delivery into a running
Codex/Claude session. Verify the loaded native tools and a real hook event before
claiming a client is connected. Private loaders/settings are not public artifacts.
