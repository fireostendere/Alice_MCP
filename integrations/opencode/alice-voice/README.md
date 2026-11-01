# OpenCode voice integration

This is the source copy of the local Alice voice plugin. It contains no client
configuration, station inventory, credentials or saved events. It is included in
the source distribution, not the Python wheel.

Copy this directory to `.opencode/plugins/alice-voice` in the project and reload
OpenCode V2. Keep the existing single `alice` MCP entry and private settings.
The server entry queues fresh, untrusted wake commands into the `alice` agent's
`Голос` session. It claims each event once and does not replay stale/history
commands. The TUI entry shows listener state and recent legacy commands.

When the MCP has a Realtime key, it handles voice through its cloud pipeline and
does not dispatch duplicate commands to this plugin. The managed indicator then
follows actual playback/error completion rather than the legacy agent state.
See [the voice architecture](../../../docs/VOICE_ARCHITECTURE.md).

Run the dependency-free shared-logic tests with Node 20 or later:

```sh
node --test integrations/opencode/alice-voice/lib.test.js
```

The TUI JSX runs in OpenCode's plugin runtime; these unit tests do not establish
compatibility with every OpenCode version or physically verify LEDs/sound.

## Compact Alice profile

Set the plugin option `station` to an explicit owner-managed alias to bind the
`alice` agent to that speaker. Ordinary questions call `alice_ask_station`: time
comes from the PC clock with its UTC offset; news, weather and facts use an actual
web search, with citation links returned separately from spoken text. A weather
request without a city uses Belgrade, Serbia; an explicitly named city takes priority.
The successful tool result is already spoken; the agent must not speak it again.
Bound Alice tool executors reject a different station even if a question names
one; other agents and their existing permissions are not changed by that binding.
The final `context` hook also adds these instructions after config-agent transforms,
without deleting project/security system sources. Existing development tools are
removed from Alice's assembled request, not from other agents' requests.

The server plugin now applies a runtime-only compact system prompt to the existing
`alice` agent and denies development/file/skill/delegation tools for that profile.
It preserves station-action permissions, model selection, project/security
instructions, other agents and the single private MCP configuration. The runtime
transform replaces Alice's base system prompt; unload the plugin to restore it.
The existing `alice_run_station_action` executor is kept and exposed natively
(`codemode: false`); no duplicate tool or connection is registered. Other tools
keep their existing transport options. It asks for the action directly, without
an introductory message, and never reports unverified sound as audible success.

Copy the updated `index.js` and `lib.js` together to the existing plugin directory.
OpenCode reloads plugins/configuration after changes; do not start another MCP
alias or listener. Its private MCP `cwd` and `PYTHONPATH` must select this checkout
and its `src` directory, not an old worktree.

Some custom OpenCode builds still defer native tools behind `mcp_discover`.
`codemode: false` does not guarantee preloading in those builds; removing that
round-trip requires their tool-selection implementation, outside this repository.
The literal-speech bypass and timing fields are documented in
[voice architecture](../../../docs/VOICE_ARCHITECTURE.md#literal-speech-fast-path-and-latency-measurements).
