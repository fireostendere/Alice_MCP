# Morning live acceptance — one ordered checklist

Use the reviewed source checkout, its development Python (`$PY`), the existing
private `ALICE_MCP_INVENTORY`, and the existing single `alice` MCP connection.
Set `STATION` privately to the intended station alias. No inventory, endpoint,
recording, transcript log or credential is pasted into chat or committed.
These commands were **not run on a station overnight**.

Stop the affected feature after any `unknown`, lost transport or failed cleanup;
never repeat an uncertain operation. A new state preview does not make an
uncertain earlier write safe to repeat. Quasar stays enabled throughout.

1. **Read-only health.** From the selected source checkout:

   ```sh
   export PYTHONPATH="$PWD/src"
   "$PY" -m alice_mcp.native_acceptance health --station "$STATION"
   "$PY" -m alice_mcp.capture_probe --station "$STATION"
   ```

   Older W0 residents may report unsupported typed probes; record that as a build
   gap. This does not authorize an upgrade. The known working Mini 1 source remains
   the full QuasarMessage IOChannelRequest envelope on yio_audio:9850, vqe_0,
   16 kHz S16 mono; do not rediscover it or stop Quasar.

2. **Silent/dark detector replay.** This is the only device execution allowed by
   the overnight exception. It never reads room audio or drives LEDs/sound:

   ```sh
   "$PY" -m alice_mcp.wake_pilot --station "$STATION" \
       --detector "$REPLAY_DETECTOR" --replay "$REPLAY_WAV"
   # Apply exactly once only after the preview is ready:
   "$PY" -m alice_mcp.wake_pilot --station "$STATION" \
       --detector "$REPLAY_DETECTOR" --replay "$REPLAY_WAV" --live
   ```

   The first command only inspects and previews. The second makes a new bound,
   one-use preview and applies it once. Require complete, cleanup_verified,
   quasar_running and quasar_unchanged. The temporary clip/helper are removed.
   Compare model_sha256, inference count and trace_sha256 against the same x86
   model/input/gain. Max probabilities alone are insufficient.

   **Current blocker:** the replay pin is the older three-model ARM build; the
   supplied x86 default is fine-tuned `ft`. Do not use the command to claim personal
   ARM parity until the matching ARM build/pin is reviewed. Identify the five
   held-out clips by private content hashes before batching 5 + 15. Do not silently
   choose the first or last five. Stop the batch on the first unknown result.

3. **Physical mute (owner present).**

   ```sh
   "$PY" -m alice_mcp.mute_evidence --station "$STATION"
   ```

   Follow the physical-button prompts. A running socket or silent level is not
   proof of mute behavior. No microphone audio is saved by this runner.

4. **PC voice chain through the existing MCP connection.** Keep `voice.source`
   absent or `"PC"`. Issue these tools in order; say «компьютер» plus one benign
   command between start and get-events:

   ```text
   alice.voice_listen_start(station_id=STATION)
   alice.voice_status()
   alice.get_voice_events(after_id=0)
   alice.voice_listen_stop()
   ```

   Without a Realtime key, expect one untrusted command event and verify the
   OpenCode voice-session/plugin chain. With the project Realtime key configured,
   follow [VOICE_ARCHITECTURE.md](VOICE_ARCHITECTURE.md): a command uses cloud STT,
   Responses and mini TTS; «компьютер, давай поговорим» starts the ongoing Realtime
   conversation. Inspect `voice_status().realtime`; no duplicate command event is
   dispatched to the legacy agent. Require verified cleanup/unchanged Quasar at stop.
   Do not change or restart its active worktree configuration as a test side effect.
   `source="station"` currently emits wake-only events with empty commands and is
   not a replacement for this end-to-end command test.

5. **Audible local voice, daytime only.** On the existing MCP connection:

   ```text
   alice.run_station_action(station_id=STATION, action="speech.say",
                            parameters={"text":"Проверка локального голоса."})
   ```

   Confirm normal tempo and one playback. Software completion alone is not audible
   acceptance. Do not replay an unknown result. Registered-media acceptance, if
   its reviewed resident and asset are already present:

   ```sh
   "$PY" -m alice_mcp.native_acceptance media --station "$STATION" \
       --asset "$MEDIA_ASSET" --seconds 3 --live
   ```

6. **LEDs, daytime only, after recent idle evidence.** With a reviewed private
   channel map and a fresh same-boot capture (<=30 minutes):

   ```sh
   "$PY" -m alice_mcp.led_parity show --station "$STATION" \
       --effect all --capture-id "$CAPTURE_ID" --live
   ```

   Native led.capture is still unavailable; acquire the evidence only through the
   explicit reviewed SSH capture workflow, never an automatic fallback. Mini 1
   owner-confirmed mapping is B,G,R with zones 1,6,5,4,3,2. The SSH PWM writer now
   uses byte mode with hex registers because this BusyBox block mode appends junk.
   Check colors, direction and return to idle. Do not touch the separately active
   voice-indicator agent/worktree while checking these source changes.

7. **Owned timer, after its registered preset is qualified.**

   ```sh
   "$PY" -m alice_mcp.owned_jobs timer --station "$STATION" \
       --preset "$PRESET" --seconds 10 --live
   ```

   Confirm exactly one effect and owned cancellation. Skip siren/presets requiring
   Mini 1 volume until a real mapping is qualified. Mini 1 has no amixer; do not run
   the old amixer-based volume acceptance or invent ioctl control metadata.

No resident update, volume mapping installation, reboot or policy change is part
of this checklist. The composed source and reproduced target pin are recorded
in NATIVE_AGENT.md; deployment remains a separately authorized guarded upgrade. The new ALSA backend
and station-source helper configuration have synthetic tests and ARM builds only.

## Guarded combined-upgrade precondition

The integrated operator API and CLI are documented in
[NATIVE_AGENT.md](NATIVE_AGENT.md#operator-api-and-cli). They accept the pinned
installed W0 resident and PCM player, start the new binary with the exact old
configuration, verify the production TLS/state contract, then migrate the
action policy and optional reviewed `volume`, `media` and `led_map` fields.
The non-TTY API uses standing owner authorization; `--live` CLI requires `UPGRADE`.
Keep the five-minute one-use preview and no-uncertain-retry rules.
No guessed Mini 1 mixer, automatic wake configuration or device installation is
authorized by a source merge. Use the composed source target pin, never the
older PR #4-only build.
