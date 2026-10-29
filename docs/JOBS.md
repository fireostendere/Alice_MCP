# Owned jobs, siren, presets and reboot

**Status: source-tested, not live-accepted.** Every test uses synthetic station
responses. No station was contacted while building this, and no tone, LED, timer
or reboot has been observed on hardware.

## Where jobs run and what survives a restart

Jobs run in the host `alice` process (`src/alice_mcp/owned_jobs.py`). That
process is a stdio server launched by the MCP client, so:

| Event | Effect on owned jobs |
|---|---|
| MCP client closes or the server restarts | All jobs are lost. Nothing is persisted, resumed, re-fired or recreated. `list_jobs` starts empty; old job IDs are unknown. |
| Station reboots | A pending timer still fires on the host. Its steps run fresh previews against the new boot, and the usual firmware/state checks apply. |
| Host suspends | The monotonic clock can pause, so a timer fires late, never early. |
| Private inventory edited or trust changed | Pending and running jobs stop at their next step as `blocked`. |

A scheduler on the resident would survive host restarts. It would also need Go
changes, a resident state store, a guarded upgrade and its own cancel/restart
contract, and on its own it would not survive a station reboot either. The
host-side design was chosen because it is the smallest one that is honest about
this: it adds no device daemon, and each step reuses an existing typed action with
its guards. The same step path covers SSH stations and native stations: on a native
station `audio.volume` and `audio.play` steps go through the typed resident adapters
(`native_typed.py`) and `led.show` through the native LED adapter, with the same
bounds as a direct native preview. A resident scheduler is not implemented.

There is no display countdown: remaining time is reported only by `list_jobs`.
A clock/display adapter is a separate W2 feasibility item.

## Private policy

Declare jobs in the private inventory, as a `jobs` object on the station entry,
next to `controls`. If a station has no `jobs` object, it has no presets and no
siren.

```json
"jobs": {
  "presets": {
    "timer-done": [
      {"action": "speech.say", "parameters": {"text": "Время вышло"}},
      {"action": "led.effect",
       "parameters": {"red": 0, "green": 16, "blue": 0, "count": 2, "period_ms": 500}}
    ]
  },
  "siren": {"asset": "siren", "volume": 10,
            "led": {"action": "led.effect",
                    "parameters": {"red": 32, "green": 0, "blue": 0, "count": 1,
                                   "period_ms": 500}}}
}
```

- Presets: at most 16 aliases (`[a-z][a-z0-9-]{0,31}`), each with 1–8 steps. A step
  is exactly `{"action", "parameters"}`. Its parameters are checked against the
  bounds in [CONTROLS.md](CONTROLS.md) when the inventory loads.
- These actions cannot be preset steps: `led.capture`, `speech.listen`,
  `media.upload` and `restore`. Reboot and service actions are not device actions,
  so they can never be steps. There is no raw command, path, URL or process field,
  and no "repeat last" tool.
- An invalid `jobs` object makes the whole inventory invalid, like any other policy
  error. `get_station_controls` shows only the preset aliases, `siren_declared` and
  `allow_reboot`; it never shows step values.
- Permission is checked again when a job starts and before every step. That covers
  global write opt-in, `allowed_actions` and standing preauthorization: either
  `preauthorize_all_actions`, or `controls.preauthorized_actions` covering every
  step. Native stations always need `preauthorize_all_actions`. Background steps
  cannot show an approval form, so a job that would need one is refused at start.

## Tools

| Tool | Parameters | Boundary |
|---|---|---|
| `start_job` | `timer` `{seconds: 5–86400, preset}`; `preset` `{preset}`; `siren` `{mode: full\|sound_only\|test\|off, repeats: 1–5}` | Returns a `job_id`. At most 8 unfinished jobs; the 32 most recent are retained. |
| `list_jobs` | — | Lists jobs this process started: state, remaining seconds, per-step outcomes. No station access. |
| `cancel_job` | `job_id` or `"all"` | Sets cancel on unfinished jobs this process started. Never matches processes by name and never claims all sound stopped. |

Each step creates a fresh preview, consumes it once, and applies it only if
`approval_required` is false and the station's private configuration and trust
are unchanged since the job was created. Step outcomes are handled as follows:

- `applied`: the job continues with the next step.
- `blocked` or `conflict`: the job stops as `blocked`. The step itself did not write.
- Anything else, including transport loss: the job stops as `unknown` with
  `do_not_retry=true`.

When a job stops, its remaining steps are skipped, and no step is ever repeated.

Cancellation has these limits:

- A cancelled scheduled job never fires.
- A running job stops before its next step. The step already in progress (at most
  about 10 s of playback, or one bounded LED effect or utterance) is not
  interrupted. The siren's volume restore still runs after a cancel.
- `speech.listen` and LED capture sessions are not jobs. They end on their own
  deadlines.

## Siren

The tone is generated in code with integer arithmetic only. It is a triangle sweep
600 → 1400 → 600 Hz lasting 1 s: mono, S16, peaking at most 8191 (−12 dBFS), with
50 ms fades. It contains no third-party asset.

- SSH stations: 8 kHz, 16,044-byte WAV, SHA-256
  `2b6527b444c68e40e611bef553adbdfa06c3a3bebaed5bd8cb507ab21acf1aca`. The policy
  `siren.asset` must name a `.wav` entry in `controls.media` with exactly this
  digest. Upload it once with the `tone` acceptance command; that is a normal
  `media.upload` with a backup record, leaving at least 16 KiB of helper-input
  headroom.
- Native stations: the local-PCM player needs 16–48 kHz, so the same sweep is
  rendered at 16 kHz, 32,044 bytes, SHA-256
  `aa02430e8b1ea2f2fe1099c5f23763d3b18037f18d13d7fbc89653a8fa6e23b9`. `siren.asset`
  must name a `controls.local_media` entry with exactly this digest. The `tone`
  command with `--out` writes the file create-only (mode 0600) on the host; nothing
  is uploaded to the station.

No other clip can act as the siren.

| Mode | Steps |
|---|---|
| `full` | volume → speak the fixed text `Внимание! Тревога!` → `repeats` × (tone, declared LED step) → restore volume |
| `sound_only` | volume → `repeats` × tone → restore volume |
| `test` | volume → one tone and one declared LED step → restore volume |
| `off` | Cancels this station's unfinished owned siren jobs |

- Volume: the siren first sets `audio.volume` to `siren.volume`. The default is 10,
  and the value must not exceed the private `max_volume`. The siren plays only if
  that step returns a verified `backup_id` (SSH) or a verified `previous_percent`
  (native). The restore is then attempted after completion, cancellation, `blocked`
  and `unknown` alike: SSH replays the backup record; native sets the previous level
  with one more `audio.volume`. A previous native level above the private
  `max_volume` is refused by the same ceiling, so the report says
  `volume_restored=false` instead of claiming a restore. On SSH a failed restore
  also reports `volume_backup_id`, so the owner can run a manual `restore` preview.
- The tone uses `audio.play` for 2 s per repeat (`repeats` defaults to 3), so a
  siren has at most 5 tones. Tone and LED steps alternate one after the other; they
  never run at the same time.
- Required actions: `audio.volume`, `audio.play` and (SSH only) `restore`, plus
  `speech.say` for `full` and the action of any declared LED step. `restore` cannot
  be selectively preauthorized, and native stations always need it, so in practice
  a siren needs `preauthorize_all_actions`.
- Only one siren can be active per station.

## Reboot

Reboot is a separate disruptive action, controlled by the boolean `allow_reboot` on
the station entry. It defaults to `true` (owner decision, 2026-10-29); set it to
`false` to disable reboot for that station. That flag is independent of
`allow_service_actions`, `allowed_actions` and presets. Reboot also requires all
of the following:

- `ALICE_MCP_ALLOW_WRITES=1`.
- An SSH station on a Buildroot model.
- `controls.firmware_sha256` matching the live `maind`.
- A `reboot` utility on the station.
- No unfinished owned jobs on that station.

On native stations, reboot is refused: the resident agent has no reboot action, and
there is no SSH fallback. Adding a resident reboot would need Go changes and a
guarded upgrade.

1. `preview_service_action(station_id, "reboot")` reads `boot_id` and the firmware
   digest. Neither value is returned. It stores a 300-second plan bound to the
   station configuration, trust and boot.
2. `apply_service_action(plan_id, expected_sha256)` consumes the plan. It shows a
   native form unless `preauthorize_all_actions` is set, then rechecks everything.
3. In that same command, the station rechecks the boot id and firmware, then
   detaches `reboot` after 2 s so the reply can still arrive.

The result is `requested` with `reboot_verified=false` and `do_not_retry=true`. A
lost or unexpected reply is reported as `unknown`, and the reboot is never sent
again.

Reboot cannot run through `run_station_action`, presets or jobs. The server
instructions tell the agent to reboot only on an explicit owner request, never as a
fix.

To confirm a reboot afterwards, use `station_probe` `health` (uptime) and `reboot`
(reason: `normal`, `watchdog_reboot`, `cold_boot`, `software_reboot` or
`unknown`). The reason comes from an Amlogic sysfs node and can be unavailable on
other firmware.

## Owner-run acceptance (batch session)

Run these beside the station, with `ALICE_MCP_INVENTORY` set privately and
`ALICE_MCP_ALLOW_WRITES=1`.

- Without `--live`, each command only previews. It prints compact JSON with
  per-step read-only preview outcomes and consumes every plan.
- With `--live`, each command needs an interactive terminal and the exact typed
  word, and it acts once.

Never rerun a command whose result was `unknown`; inspect locally first.

1. **Timer and owned cancel**, word `TIMER`:
   `python -m alice_mcp.owned_jobs timer --station ALIAS --preset timer-done --seconds 10 --live`.
   This starts two identical timers and cancels the second by its ID. You should
   observe exactly one signal about 10 s later; answer `yes`/`no`. Expect
   `timer.state=complete` and `cancelled_timer.state=cancelled` with no step
   outcomes.
2. **Siren tone upload** (once), word `UPLOAD`:
   `python -m alice_mcp.owned_jobs tone --station ALIAS --live`. On a native station
   add `--out /PRIVATE/siren-16k.wav`: the tone is written there and then registered
   under `controls.local_media`; nothing is uploaded.
3. **Siren test**, word `SIREN`:
   `python -m alice_mcp.owned_jobs siren --station ALIAS --mode test --live`.
   You should hear one short tone at low volume, plus an LED flash if one is
   declared, and the previous volume should come back. Answer `yes`/`no`. Expect
   `volume_restored=true`.
4. **Preset**, word `PRESET`:
   `python -m alice_mcp.owned_jobs preset --station ALIAS --preset NAME --live`.
   Choose a preset whose steps restore themselves (LED effect, short playback,
   speech). Answer whether exactly the declared effects happened once.
5. **Reboot** (last, optional), word `REBOOT`:
   `python -m alice_mcp.owned_jobs reboot --station ALIAS --live`.
   Press Enter once the station has booted. The output adds the `health` and
   `reboot` probes and `reboot_observed` (uptime is shorter than the time since
   dispatch).

## Known gaps

- None of this is live-accepted. The audibility and loudness of the tone at 10 %
  (8 kHz through `maind --gst-launch`, 16 kHz through the native player) are
  unmeasured.
- Jobs end with the host process. There is no persistence and no resident
  scheduler.
- Native volume restore is limited to levels at or below the private `max_volume`.
- Mini 1 over SSH has no `led.effect`. Its siren LED step should use a W2 effect
  once one is available.
- An in-flight step cannot be interrupted, and the siren alternates tone and light
  instead of running them together.
- There is no display countdown.
