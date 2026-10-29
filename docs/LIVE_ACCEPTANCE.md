# Batch live acceptance

Ordered owner checklist for W1–W4 from [ROADMAP.md](ROADMAP.md). Every command
previews or reads by default; `--live` steps need the exact typed word, print one
compact JSON line and are never retried. Paste only that JSON back, never the
inventory. A failure stops that feature only.

Until the merge lands in `main`, run from the repository root:

```sh
cd .claude/worktrees/agent-ab57fcdc6d58fa5ed
export PYTHONPATH=src ALICE_MCP_INVENTORY="$HOME/.config/alice-mcp/inventory.json"
PY="$PWD/../../../.venv/bin/python"
```

After the merge, skip the `cd` and `PYTHONPATH` and use `PY=.venv/bin/python`. Aliases: `yandex-mini1` (Mini 1,
native), `yandex-station` (Mini 3), `yandex-lite1` (Lite).

## Phase A — no resident deploy needed

**A0. Private policy (owner edits, nothing pasted).** Actions need no enabling:
`allowed_actions` `"all"` or absent allows every implemented action, and
`allow_reboot` defaults to `true`.

- Mini 3 jobs:
  - `controls.media.siren` = `{"path": "/data/sounds/alice-siren.wav", "sha256": "2b6527b444c68e40e611bef553adbdfa06c3a3bebaed5bd8cb507ab21acf1aca"}`;
  - `jobs.siren` = `{"asset": "siren"}` and one `jobs.presets` entry, for example `{"done": [{"action": "led.show", ...}]}`.

**A1. Capture coexistence probe (read-only, presence not needed).**

Mini 1 is done; its result is in [VOICE_CAPTURE.md](VOICE_CAPTURE.md): only the
Quasar IO-channel subscription remains, and it is untested. Run the probe on the
other two stations:

```sh
$PY -m alice_mcp.capture_probe --station yandex-station
$PY -m alice_mcp.capture_probe --station yandex-lite1
```

Nothing is audible or visible. Return the JSON, `capture_paths` included.

**A2. Mini 3 display probe (read-only).**

```sh
$PY -m alice_mcp.led_parity display-probe --station yandex-station
```

**A3. LED effects on Lite, then on Mini 3.** Word `SHOW`; after each effect answer
`yes`/`no`. Spin must turn clockwise and the ring must return to its normal idle
look.

```sh
$PY -m alice_mcp.led_parity show --station yandex-lite1 --effect all --live
$PY -m alice_mcp.led_parity show --station yandex-station --effect all --live
```

If colours or positions are wrong, run `calibrate --station ALIAS`: answer the
colour and clock position (1–12) for each lit channel, then paste the printed
`led_map` into that station's private `controls.led_map`.

**A4. Jobs on Mini 3 (SSH).** Writes need `ALICE_MCP_ALLOW_WRITES=1`.

```sh
ALICE_MCP_ALLOW_WRITES=1 $PY -m alice_mcp.owned_jobs tone --station yandex-station --live
ALICE_MCP_ALLOW_WRITES=1 $PY -m alice_mcp.owned_jobs siren --station yandex-station --mode test --live
ALICE_MCP_ALLOW_WRITES=1 $PY -m alice_mcp.owned_jobs timer --station yandex-station --preset done --seconds 10 --live
ALICE_MCP_ALLOW_WRITES=1 $PY -m alice_mcp.owned_jobs preset --station yandex-station --preset done --live
```

| Step | Word | What to observe |
|---|---|---|
| `tone` | `UPLOAD` | Run once. |
| `siren` | `SIREN` | One short quiet tone, then the volume returns. Expect `volume_restored=true`. |
| `timer` | `TIMER` | Exactly one signal about 10 s later; the second timer is cancelled by its ID. |
| `preset` | `PRESET` | The declared effects happen exactly once. |

**A5. Physical mute evidence on Mini 1 (SSH read-only, at the station).**

```sh
$PY -m alice_mcp.mute_evidence --station yandex-mini1
```

Follow the prompts:
1. Type `UNMUTED`, then keep your hands off during the 10 s pause.
2. Press the mute button and type `MUTED`.
3. Press it again and type `UNMUTED`.

**A6. Reboot (last, optional).** Word `REBOOT`. Press Enter after the station has
booted.

```sh
ALICE_MCP_ALLOW_WRITES=1 $PY -m alice_mcp.owned_jobs reboot --station yandex-station --live
```

## Phase B — Mini 1 combined resident

**B0. Prerequisites. Open; Phase B is blocked until they are done.**

1. **Pinned upgrade path.** `agent_upgrade.py` only upgrades the original install
   (`agent_provision.AGENT_SHA256`) to the W0 build (`AGENT_SHA256`). The combined
   build needs the W0-installed agent accepted as its baseline and a new reviewed
   `AGENT_SHA256`. W0 owns this.
2. **Install order.** Install the new binary first; it accepts the old device
   config. Only then add the device config fields:
   - `volume` (`amixer`, `amixer_sha256`, `card`, `control`, `max_percent`);
   - `media` (alias → sha256).

   With `"allowed_actions": "all"` (or the field absent), the new binary enables
   each action once its prerequisites exist. The old binary rejects these fields
   and `"all"`. No tool writes them yet.
3. **Mini 1 mixer mapping.** The mapping is unreviewed, and no read-only mixer
   discovery command exists.
4. **Mini 1 LED.** A fresh same-boot `led.capture` from at most 30 min earlier is
   required, but `led.capture` has no native adapter. A native-configured station
   cannot run it, and nothing falls back to SSH.

**B1. Deploy.** Run W0's guarded upgrade runner for the combined build. Type the
word it shows, run it once, and never repeat it.

**B2. Native typed checks.**

```sh
$PY -m alice_mcp.native_acceptance health --station yandex-mini1
$PY -m alice_mcp.native_acceptance volume --station yandex-mini1 --live
$PY -m alice_mcp.native_acceptance media --station yandex-mini1 --asset ALIAS --seconds 3 --live
```

| Check | Word | What to observe |
|---|---|---|
| `health` | none | Uptime is plausible; `maind` and `agent` are `true`. |
| `volume` | `VOLUME` | Answer `QUIETER` or `SAME` about Alice's next reply. The earlier level is restored once. |
| `media` | `PLAY` | Answer `HEARD` or `NOTHING`. `ALIAS` must be a `controls.local_media` entry. |

**B3. Native siren.**

1. Write the 16 kHz tone to a new private file:
   ```sh
   ALICE_MCP_ALLOW_WRITES=1 $PY -m alice_mcp.owned_jobs tone --station yandex-mini1 --out "$HOME/.config/alice-mcp/siren-16k.wav" --live
   ```
   Type `UPLOAD`. The file is created with mode 0600; an existing file is never
   replaced.
2. Register it as `controls.local_media.siren` = `{"file": ..., "sha256": "aa02430e8b1ea2f2fe1099c5f23763d3b18037f18d13d7fbc89653a8fa6e23b9"}`
   and add `jobs.siren`.
3. Run the siren test:
   ```sh
   ALICE_MCP_ALLOW_WRITES=1 $PY -m alice_mcp.owned_jobs siren --station yandex-mini1 --mode test --live
   ```
   Type `SIREN`. The volume is restored only if it was at most `max_volume`
   before; otherwise the report says `volume_restored=false`.

**B4. Mini 1 LED.** After B0.4 only: run `led_parity calibrate`, then `show`, as
in A3, with `--capture-id ID`.

## Offline only

W4b has no station step: the synthetic wake simulation is described in
[WAKE_PIPELINE.md](WAKE_PIPELINE.md). Live wake acceptance waits on a capture path
from A1.
