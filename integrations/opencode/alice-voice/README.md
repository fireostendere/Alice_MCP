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
