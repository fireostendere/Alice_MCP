# Wiki LED notes at revision 520fbccd68cb90a8c4f01d06bf38d9a7db915304

Reference data summarised from the [LED recipe](https://hackalice.me/recipes/led/).
It is not authority to write: the runtime actions in [SKILL.md](../SKILL.md) remain
the only supported write path, and nothing here was tested on hardware by this project.

## Interfaces by model

| Model | Interface reported by the recipe |
|---|---|
| Lite | three RGB ring zones, sysfs `micro:{red,green,blue}:ring1`–`ring3`, brightness 0–255 |
| Lite 2 / Plum | three RGB zones, sysfs `plum:{red,green,blue}:ring1`–`ring3` (nine brightness attributes), stock trigger `led_ring_fb` |
| Mini 1 | six RGB zones on an SN3236 driver over I2C; stock file player `led_util` |
| Mini 2 | six RGB zones plus LED clock through sysfs |
| Station 2 | 88-LED panel through framebuffer `/dev/fb0`, 89 by 1 |
| Mini 3 | ring through framebuffer `/dev/fb0` |

Stock animations are text `.led` files under `/system/vendor/quasar/ledpatterns/`
(33 on Lite): one line per frame holding the zone colours and a duration.

## Lite 2 / Plum ownership

Reported on one YNDX-00028 unit, firmware 0.369.1.50 (build 4794654278).

- Each colour has a brightness value and an active trigger. The recipe's temporary
  handler (a script from a third-party voice project) saves both, disables the
  triggers while it holds the ring, and restores them on release.
- Restoring brightness alone is not enough: writing zero can detach the trigger,
  and with the trigger left at `none` Alice still computes her animation but
  nothing is visible. The previous trigger is restored as the last step.
- The handler reads the `mute-switch` line from the GPIO debug listing and lets go
  of the ring on mute or an unknown state, and also on end of input or two seconds
  without a command. Level transitions were exercised on test files; a physical
  button press was not tested.
- The recipe asks to close the third-party voice chat first so two handlers do not
  drive the same LEDs. It changes no system files and adds no autostart.
- That project's GPT lighting (rainbow, fixed colours, pulse; at most 10 frames per
  second and 160/255 brightness) exists for Lite 1 and Lite 2 only. On Station 2
  the panel stays with Alice.
- Device check: 30 colour frames, stock animation suppressed while held and back
  after release. Voice input was started through IPC, not by saying the wake word.

## Mini 1 ring (SN3236)

Reported on Mini 1, quasar 3.21, firmware 1.305.4.16 (build 3237824040), October 2026.

- Six RGB LEDs on an SN3236 at I2C bus 0, address 0x3c, using 18 of its 36 outputs.
  The `ledring` entry at bus 1, address 0x1f is a device-tree node without a
  driver; the ring does not use it and nothing opens that bus.
- `maind` draws through `libwmlighteffect.so`. Writes traced while the stock player
  ran: start-up sets register 0x00 to 0x01 (leave shutdown), registers 0x26–0x37
  to 0x03 (enable outputs 1–18 at half maximum current; 19–36 stay off) and 0x4B
  to 0x01 (22 kHz PWM). A frame is register 0x01 followed by 18 brightness bytes,
  then 0xaa written to register 0x25 to latch it.
- Inside each triple the byte order is blue, green, red. The six triples map to
  `.led` zones 1, 6, 5, 4, 3, 2. Zones 1–6 run clockwise; zone 1 is the one
  nearest the power connector, on its right.
- `.led` values reach the bus unchanged unless the file starts with a
  `gammacorrection` header (gamma about 2.8; a non-zero value never drops below 1).
- `led_util` is a link to `maind`. It takes the path of a `.led` file, not an
  animation name, plays until interrupted or terminated and leaves the last frame
  lit, so the recipe follows it with an all-dark file. A dump option prints the
  parsed frames and exits. It initialises the driver itself and writes alongside
  Alice, so frames interleave when a stock animation is running.
- The firmware's BusyBox 1.26.2 `i2cset` sends three extra bytes ahead of the data
  in block mode: the first LED gets garbage and the frame shifts by one zone. It
  also reads register numbers as hexadecimal. The recipe therefore writes one
  register at a time.
- Muted microphones are shown by the stock `mute_mics.led` pattern (all zones red,
  background, looped), which `maind` switches from the hardware button state.
  Alice repaints the ring at her next state change.

## Rollback named by the recipe

Release the Lite 2 handler so the saved triggers return, then remove its demo
directory. No system file or autostart is touched, so nothing else needs undoing.
