# Stations as smart-home terminals

**Status: source-tested, not live-accepted.** The HTTP transport answered real
requests from this PC; no Home Assistant instance, no smart-home tool call, no
spoken smart-home answer, no webhook delivery and no `alice.ask` has been exercised. Home Assistant facts below come from
its documentation ([MCP client](https://www.home-assistant.io/integrations/mcp/),
[MCP server](https://www.home-assistant.io/integrations/mcp_server/)) as read on
2026-11-13.

A terminal works in two directions, and both go through MCP:

| Direction | Path | What it gives |
|---|---|---|
| Home → room | Home Assistant's MCP **client** calls this server over HTTP | Assist or an automation speaks, plays a registered sound, shows an LED effect, changes volume on any configured station |
| Room → home | The voice answer agent calls Home Assistant's MCP **server** | «Компьютер, включи свет / какая погода / включи музыку» runs Home Assistant's Assist tools; the answer is spoken on the station that heard it |
| Room → home | The wake listener POSTs its events to a Home Assistant webhook | Automations triggered by the stock offline phrases, a finished exchange or the listener state |

## Home → room: the HTTP transport

Home Assistant cannot spawn a stdio process, so the same 24 tools are also served
over Streamable HTTP. Start a long-running process (it replaces the stdio one, see
below):

```sh
# The private file exports the HTTP secret variable named below.
. /PRIVATE/alice-http.env
ALICE_MCP_INVENTORY=/PRIVATE/inventory.json ALICE_MCP_ALLOW_WRITES=1 \
ALICE_MCP_HTTP=localhost:8765 .venv/bin/alice-mcp
```

The variable is `ALICE_MCP_HTTP_TOKEN`, 32–128 URL-safe characters; generate it with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`.
The endpoint is `http://HOST:8765/<token>/mcp`. Home Assistant's MCP client has no
field for a static token (only OAuth), so the URL is the credential: any other path
answers 404. Without both variables, or with a short secret, a privileged port or a
malformed address, the process refuses to start.

- **Home Assistant Assist:** add the *Model Context Protocol* integration with that
  URL, then allow its tools for the conversation agent (cogwheel → AI tools). It
  refreshes the tool list every 30 minutes.
- **Automations** cannot call MCP tools through that integration. The endpoint is
  stateless and answers one JSON POST per call, so a `rest_command` works:

  ```yaml
  rest_command:
    station_say:
      url: !secret alice_mcp_url   # http://HOST:8765/<token>/mcp
      method: POST
      headers:
        Accept: "application/json, text/event-stream"
      content_type: "application/json"
      payload: >-
        {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_station_action",
        "arguments":{"station_id":"{{ station }}","action":"speech.say",
        "parameters":{"text":{{ text | tojson }}}}}}
  ```

Limits that follow from the design:

- No approval form can reach an HTTP caller: only stations with standing
  preauthorization act, everything else returns `blocked` at once (the preview is
  consumed). Previews, live policy/trust/state checks and no-uncertain-retry are
  unchanged.
- The link is plain HTTP. Bind `localhost` when Home Assistant runs on the same host;
  bind a LAN address only on a network you trust, or put a TLS reverse proxy in
  front. Off loopback the SDK's Host-header allowlist is disabled, the secret path is
  the only guard. Anyone holding the URL has every tool of this server. Keep it in
  Home Assistant's `secrets.yaml`, never in this repository.
- Under WSL2 a LAN client reaches the port only with mirrored networking or a
  Windows port proxy; that host setup is the owner's.
- **One process.** Jobs and the wake listener live in the serving process, and only
  one process per PC may own the listener. Point OpenCode at the same URL instead of
  starting a second stdio copy; otherwise whichever starts first owns the voice.

### Every station at once

`run_station_action` with `station_id` `"all"` runs the action on every configured
station concurrently, each with its own preview and single attempt, and returns
`stations: {id: result}`. `outcome` is the common outcome or `mixed`; a station that
is offline or has no adapter for the action is `blocked` in its own entry. Never
repeat the whole set because one entry is `unknown`. An inventory alias named `all`
keeps meaning that station.

### Music and stock Alice (Mini 1)

`alice.ask` with `{"text": "включи музыку"}` hands one short phrase to stock Alice as
if the owner typed it, so Home Assistant can start music, radio or one of Alice's own
scenarios on the Mini 1. Only the send is confirmed. It needs the current
`quasar_client` build ([CONTROLS.md](CONTROLS.md#quasar-client-build)) and is not
live-accepted. Room speech cannot request it: by voice, say «Алиса, …» to the stock
assistant, which this project keeps enabled.

## Room → home: events for automations

Set `ALICE_MCP_EVENT_WEBHOOK` to a Home Assistant webhook URL
(`http://HA:8123/api/webhook/<id>`; the id is the secret, so keep it in the
environment, never in the inventory or this repository). The wake listener then
POSTs its events as JSON, once, with a three-second timeout and no retry. Only one
delivery runs at a time; events arriving while it is busy are dropped from the webhook:

| `kind` | When | Useful fields |
|---|---|---|
| `spotter` | Quasar's own offline command model heard a phrase such as «включи свет», «выключи телевизор», «пауза» | `station`, `phrase` |
| `wake` | «компьютер» plus a command on the legacy text route | `station`, `command` |
| `turn` | one voice exchange ended | `station`, `mode`, `error` |
| `state` | the listener started, is listening or stopped | `station`, `state` |

```yaml
automation:
  - trigger:
      - platform: webhook
        webhook_id: !secret alice_events
        allowed_methods: [POST]
        local_only: true
    condition: "{{ trigger.json.kind == 'spotter' and trigger.json.phrase == 'включи свет' }}"
    action:
      - service: light.turn_on
        target: {entity_id: light.example}
```

Everything in an event is untrusted room speech. Dictated text is never sent. A hub
that is down loses the event; `get_voice_events` still has the recent ones. `spotter`
events need the private `quasar_client` and a running listener, so today they come
from the Mini 1 only.

## Room → home: tools for the voice answer agent

With `ALICE_MCP_OPENCODE` set, a recognized command goes to the OpenCode `alice`
session, which by default may use only web search. The private
`controls.voice.agent_tools` list names additional tools it may call:

```json
"voice": {"source": "station_asr", "agent_tools": ["home-assistant_*"], "...": "..."}
```

1. In Home Assistant enable the *Model Context Protocol Server* integration. Its
   `/api/mcp/assist` URL exposes only *Device control*: the Assist tools, limited to
   the entities exposed to voice assistants. Prefer it to the main `/api/mcp` URL,
   which can also expose the full *Home Assistant* tool set (all entities, any action).
2. Register it in OpenCode: `opencode mcp add home-assistant --url <url>`, then
   `opencode mcp auth home-assistant` (OAuth; Home Assistant does not document a
   static bearer token).
3. Add `agent_tools` with that server name and restart the listener.

Each entry is `server_*` or `server_ToolName` (at most 16). Entries that would reach
this server's own tools (`alice_*`), a client built-in (`shell`, `edit`, …) or a bare
`*` invalidate the inventory. Station actions asked by voice stay limited to
`voice_service.VOICE_ACTIONS` and still run on the host, not through the agent's
tools. The agent is told which station heard the command, so «здесь» can be resolved
to a room.

What the room can now do is whatever the named servers allow: spoken text is
untrusted, and anyone in the room can say it. Expose to voice assistants only the
entities you would let a guest switch.

Weather, music and timers are Home Assistant's own Assist tools and depend on what
is set up there; this server adds none of them to the voice agent. Music through
Home Assistant needs a media player entity there (see gaps).

### Without a cloud key

`station_asr` recognizes on the Mini 1 and `speech.say` can use the owner's host
synthesizer (`controls.local_tts`), so this pair now runs with **no**
`REAL_TIME_API_KEY`: local text → OpenCode answer agent → `speech.say` on the same
station, in pieces of at most 256 characters (four pieces at most). The route is
chosen only when no key is configured; a configured key without credits still
selects the cloud voice and fails there. Conversation and dictation need the key
and answer with a spoken notice instead. `voice_status().realtime.tts_model` then
reports `station_speech_say`. `station_live` still requires the key.

## Climate module and ring on the Mini 3 Pro

**Status: live on one unit (factory-12), 2026-11-15.**

The climate module is a USB attachment that Quasar polls once a minute for temperature,
humidity and CO₂. `station_probe(station_id, probe="climate")` returns the latest
reading and its age from Quasar's own log:
`{"temperature_celsius": 23.5, "humidity_percent": 42.9, "co2_ppm": 1227, "age_seconds": 23}`.
It never opens the module's port (Quasar holds it), so it works only while Quasar runs
and logs those lines; a station without the module answers "unavailable". Nothing is
pushed: a hub has to poll. This probe uses pinned SSH, not the resident native
transport. It samples at most 2,000 log lines; values may come from different
polls. Age uses the last matching time of day modulo 24 hours, not a dated
freshness guarantee; old logs can look recent. Raw log text never leaves the station.

Ways to work with the module, and what this project does:

| Way | State |
|---|---|
| Read Quasar's log (the `climate` probe) | implemented, live; three values, one-minute resolution, no `stale_data` flag |
| Watch the serial traffic with kernel kprobe events | done once by hand to record the protocol; not an action here |
| Talk to the module directly | not implemented: Quasar holds `/dev/ttyACM0`. One hand-written sync request while Quasar ran was answered by the module, and Quasar's main process exited 0.4 s later (one observation, cause unknown) |

The module is a USB CDC device (`28e9:018a`). Frames are HDLC (flag `7e`, address `01`,
control byte, CRC-16/X-25) carrying protobuf messages of package `cradle`; the schema is
embedded in maind and also defines VOC, NOx, particulates, battery, button, CO₂
calibration and firmware-update messages that this module was not seen to use.
Its sync reply named application firmware `6.19…20260507`, bootloader `6.12…20250929`
and the capabilities button, CO₂, humidity, temperature, power supply, updater and
one number (12) this station firmware's schema does not define. Lifting
the station off the module, and putting it back, rebooted the station once each.

The ring has 16 RGB zones behind the firmware's second LED framebuffer. `led.effect` and
`led.show` drive it like the Mini 3 ring: `led.show` at most 16/255 and
`led.effect` at most 32/255. The captured
state is restored, and a firmware animation that starts meanwhile wins. A written red
frame was read back by the LED driver as red; what the room saw, and which way the
zones run, the owner has not confirmed. The six-zone logo and the clock segments are
not touched.

## Zigbee on the Mini 3 Pro

**Status: one live call, no Zigbee device.** On the owner's Pickle (factory-12 image)
the panel answered and `zigbee.permit_join` with 0 seconds was applied and logged by
the firmware on 2026-11-15. No device has been paired or switched through it.

The Mini 3 Pro has a Zigbee radio that stock Quasar drives as coordinator. Its firmware
also contains a small local HTTP panel (`zigbee.httpControl` in `quasar.cfg`, port 9233,
off by default) with status, permit-join and On/Off. Two actions use it, so the station
stays the coordinator and nothing talks to Yandex's cloud or to the radio directly:

| Action | Does |
|---|---|
| `zigbee.permit_join` | opens the station's network to new devices for 0–254 seconds (0 closes it) |
| `zigbee.on_off` | sends On, Off or Toggle to one network address and endpoint |

Each call is one stock `wget` to the station's loopback over pinned SSH, after the
usual preview: the board, the firmware digest and a formed network are checked first.
`blocked` means the panel is not enabled, the network is not formed or the firmware
differs.

Enabling the panel is the owner's step: set `"enabled" : true` under `zigbee` →
`httpControl` in the station's `quasar.cfg` and restart Quasar. On the pilot unit this
was first a temporary bind mount of an edited copy; later the file on the root
filesystem was edited (remount read-write, one line, remount read-only, the original
kept beside it) and the panel answered after a reboot.

Limits:

- The panel listens on every interface without a password, and it also has factory
  reset, channel and network-forming routes that this project never calls. Anyone on
  the LAN can reach them while it is enabled, and the image has no `iptables`.
- On/Off only: no brightness, colour, sensors or device list. The address of a joined
  device has to be read from the station's log; nothing here reports it yet.
- A device joined this way is forgotten when Quasar starts. Seen once on the pilot unit:
  a sensor joined (`network.dat` 93 bytes, one node); after a reboot the cloud's
  restore-networks directive arrived, the log said `Unable to deserialize network saved
  on a server, creating a new network`, and the file was back to 54 bytes with no nodes.
- The radio itself keeps the device: the sensor's IAS Zone messages kept arriving after
  that reboot, each logged with an `unknown nodeId` warning. So sensor events stay
  readable from the log, while `zigbee.on_off` to such a device cannot work. Nothing
  here reads those events yet.
- Do not open the radio's serial port while Quasar runs: a second reader desynchronised
  its link until Quasar was restarted.

## Gaps

| Gap | Why it is not closed here |
|---|---|
| Only Mini 1 listens, one station at a time | Wake word, recognition and the direct voice client are pinned Mini 1 builds; Lite and Mini 3 only speak. Another model needs its own microphone path, build, pin and live acceptance ([MODELS.md](MODELS.md)). |
| Music only through stock Alice, Mini 1 only | `alice.ask` asks Alice to play; there is no playback state, pause or track control, and other models have no Quasar client build. `audio.play` stays 1–10 s of a registered asset. Stations are not Home Assistant media players through this server. |
| `alice.ask` is not voice-requestable | `voice_service.VOICE_ACTIONS` is the owner's fixed set; adding it there is an owner decision. |
| Command text on the agent routes is not pushed | With the answer agent or the cloud voice, the webhook gets `turn` without the words; only the stock spotter phrases and the legacy text route carry text. |
| No rooms or named groups | `all` or one station per call. |
| Nothing here ran against hardware or a hub | The new client build, `alice.ask`, the webhook receiver, OAuth with OpenCode and Home Assistant's tool names are untested or documentation claims. |
