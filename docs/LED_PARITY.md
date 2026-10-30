# LED parity shows, calibration and display feasibility

**Status: source-tested, not live-accepted.** Every check below ran on synthetic
sysfs trees, framebuffers, fake `i2cset`/`dd` shims and fake I2C writers. No
station was contacted. Nothing here proves a visible colour, position or timing.

## `led.show`: bounded equivalents of the reference effects

The owner-supplied `rgb_effects.sh` helpers were read as a behaviour spec only;
no code was copied or run. Their defects (indefinite loops, full-white strobe,
GCC/current writes, PID killing, generated scripts, "restore" by replaying settings)
are not reproduced. `led.show` is one new action: disabled unless the private
policy lists it, previewed fresh for 300 seconds, bound to station/config/state,
consumed once and never retried after `unknown`.

| `effect` | Reference behaviour | Bounded equivalent |
|---|---|---|
| `rainbow` | Whole ring cycles 16 hues forever | Whole ring cycles 12 dim hues (temporal, not the spatial `led.animate` rainbow) |
| `breathe` | Colour scaled up/down forever | Colour scaled in 14 eighth-steps |
| `spin` | Lit head with dim tail moves around | Head (changing hue) plus one-third tail |
| `spin_color` | Same with a custom colour | Same with the requested colour |
| `static` | Colour held until changed | Colour held for `duration_seconds`, then restore |
| `off` | All zero until changed | Dark for `duration_seconds`, then restore |
| `notify` | Five green blinks, then replay old effect | Requested colour on/off, then restore the captured state |
| `police` | Red half / blue half with fast double flashes | Red half / blue half alternating; Lite shares ring 2 like the reference |
| `calibrate` | — | One raw physical channel at 16/255; used by the calibration runner |

Parameters (all integers inclusive; no other fields):

- `effect` (above) and `duration_seconds` 1–15 (`calibrate`: 1–3) — the hard deadline;
- `speed` 1–5, optional, default 3: frames of 500/400/300/250/200 ms;
- `red`, `green`, `blue` 0–16 — required for `breathe`, `spin_color`, `static`,
  `notify`, forbidden otherwise; `channel` — required for `calibrate` only;
- `capture_id` — required on Mini 1 only (same captured idle evidence as `led.animate`).

Bounds enforced by validation and the generator (tested for every effect on each
profile at speeds 1 and 5 with the 15 s maximum): at most 75
frames, at most 15 s, every channel value at most 16/255, frames at least 200 ms.
On/off flashing is therefore at most 2.5 per second, below the three-flashes-per-second
photosensitivity guideline. There is no strobe, no white flood above 16/255, no
loop, no daemon and no current/GCC, shutdown, reset or driver-force write. Each
helper also enforces its own wall-clock deadline (frames × interval + 2 s), so a
slow device stops instead of overrunning. For SSH stations `get_station_controls`
lists `led_show_effects`.

Example: `{"effect": "breathe", "red": 8, "green": 0, "blue": 8, "duration_seconds": 6}`.

## Profiles

| Model | Transport | Evidence and restore |
|---|---|---|
| Mini 1 | Stock-shell `i2cset` helper over SSH, or the resident agent (below) | Requires a cleaned, same-boot, same-firmware capture ≤30 min old ending in the observed zero frame. Writes PWM `0x01..0x12` + update `0x25` only, then the idle zero frame. Write-only: `restoration_verified` stays false. |
| Lite | Device Python runtime, sysfs `micro:{red,green,blue}:ring{1,2,3}` | Snapshot of all nine `brightness` values (triggers must be `none`), readback after restore. |
| Mini 3 / Bergamot | Stock-shell `dd` framebuffer helper (6×1, BGR) | Exact 18-byte snapshot, readback after restore. Source-backed geometry only. |
| Others | — | Fail closed (`unsupported_led`), including Mini 2, Mini 3 Pro, Station 2. |

**Indicators.** On Lite and Mini 3 the helper reads the ring back before every
frame and before restoring. If anything else wrote meanwhile (normally the
firmware's mute/listening indication) it stops at once, does **not** restore its
older snapshot over that state, and reports `outcome=applied, yielded=true`.
Mini 1's SN3236 PWM is write-only: a mute indicator drawn during a show can be
overwritten by later frames and by the final idle frame until the firmware redraws.
Approve Mini 1 shows only while idle and do not press mute during the ≤15 s show.
Quasar is never stopped or fought; a show is never replayed to overpower it.

## Calibration knob: private `led_map`

Hardware will not match the paper model. Mini 1's RGB order and zone order are
unqualified; Lite/Mini 3 orders are documented but unobserved. Add to the
station's private `controls` (never to Git):

```json
"led_map": {
  "channels": [[0, "red"], [0, "green"], [0, "blue"], [1, "red"], null, "..."],
  "gain_percent": {"red": 100, "green": 80, "blue": 100}
}
```

`channels` has one entry per physical channel in device write order (Mini 1: 18
PWM registers `0x01..`; Lite: red ring1–3, green ring1–3, blue ring1–3; Mini 3: 18
framebuffer bytes): `[logical zone, colour]`, or `null` if unused/invisible. Zone 0
is the first lit position clockwise from 12 o'clock. `gain_percent` (0–100, default
100) can only dim a colour, e.g. for white balance. Both are optional; omitted
fields use the paper map. Invalid lengths, zones or colours reject the whole
inventory. `calibrate` ignores the map because it measures it.

## Owner-run acceptance commands

Run in a terminal beside the station (no pipes, no agent), with
`ALICE_MCP_INVENTORY` set privately. Writes need `led.show` in the private
`allowed_actions`; native Mini 1 also needs standing `preauthorize_all_actions`
on host and device. The typed word is the human approval for each run; every
step still uses its own fresh one-use preview. Output is one compact JSON line.

1. **Display probe (read-only):**
   `python -m alice_mcp.led_parity display-probe --station ALIAS`
2. **Calibration (first, once per station):** on Mini 1 first complete the existing
   `led.capture` workflow and keep its id, then
   `python -m alice_mcp.led_parity calibrate --station ALIAS [--capture-id ID]`.
   Type `CALIBRATE`. Each channel lights alone for 2 s at 16/255. Answer `r`/`g`/`b`/`n`
   and, if seen, its clock position 1–12 (12 = power-socket side). Paste the printed
   `led_map` into the private policy, restart MCP. `inconsistent` means a missing or
   doubled zone/colour report; `unknown` stops at once — inspect, never repeat.
3. **Effects:** `python -m alice_mcp.led_parity show --station ALIAS [--capture-id ID]`
   previews all eight effects without writing. Add `--live` and type `SHOW` to run
   each once for 4 s (`--effect NAME`, `--speed 1-5`, `--seconds 1-15`,
   `--color R,G,B` 0–16 change that). After each, answer `yes` only if you saw
   the expected pattern and the normal idle ring afterwards. A non-applied result
   stops the batch; nothing is retried.

What to observe: `rainbow` — whole ring changes hue together; `breathe` — one colour
fades up/down; `spin`/`spin_color` — one lit zone with a dim trailing zone moving
**clockwise** (wrong direction or jumping = recalibrate); `static` — steady colour;
`off` — dark; `notify` — slow blink; `police` — red half then blue half; afterwards
the usual idle state, and mute/listening indications still working.

## Resident agent (Mini 1)

The Go agent gains `led.show` for `station-mini` only (`loadConfig` rejects it for
other models). The host maps the effect and private `led_map`, then sends only
`{capture_id, interval_ms, frames}`: 1–75 lowercase-hex frames of 18 bytes ≤ `0x10`,
≥200 ms, ≤15 s total. The agent re-validates those bounds, the same capture
evidence as the shell helper, the shared owner-only `control.lock` (`flock`), and
opens `/dev/i2c-0` with `I2C_FUNCS` + non-forcing `I2C_SLAVE` 0x3c, so a bound
kernel driver blocks it. Each frame is a raw write `01`+PWM, then `25 aa` — the same
bytes on the wire as the shell's `i2cset … i` / `… b`. Failure attempts the idle frame
once; agent termination mid-show can skip it (bounded by the 15 s limit). Replies
are typed (`command_succeeded`, `restoration_command_succeeded`,
`restoration_verified=false`, `quasar_running`, `do_not_retry`); anything else is
`unknown`. Not built for, deployed to, or run on a station.

## Display / clock feasibility

From the repository's source-backed model summaries (pinned upstream revision in
[SOURCES.md](SOURCES.md), `skills/station-devices/references/models.md`):

- **Mini 1:** I2C RGB ring, PCM5122, gesture sensor — no display or clock. N/A.
- **Lite:** three sysfs RGB zones — no display or clock. N/A.
- **Mini 3 / Bergamot:** "optional clock" is reported; its segment interface,
  naming, brightness scale and settings ownership are **not** documented there.

So only a read-only probe is provided: `bergamot_compatible` (device tree),
`clock_led_entries` (count of `/sys/class/leds/*clock*`, names never returned) and
`clock_settings_file` (presence only, never contents). It opens no LED device,
reads no value and writes nothing. `display_writes_supported` is always false.

Design note for a later, separate task: a clock adapter would need the probe's
positive result, a reviewed segment map, captured restoration of both segment
brightness and Quasar's clock settings, the same yield-to-firmware rule as above,
and owner acceptance. The reference's bind-mounted fake lux/scale inputs and
forced GCC=255 are forbidden and will not be ported.

## Known gaps

No live acceptance on any model. Mini 1 channel/colour/position mapping is
uncalibrated until the runner is used; Lite sysfs readback might not echo written
values on some drivers (that would yield early, failing safe). Mini 1 cannot see
or preserve an indicator drawn during a show. The resident adapter has no
signal-time restore. Mini 3 clock writes, Mini 2 and other models are out of scope.
