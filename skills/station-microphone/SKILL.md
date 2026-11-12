---
name: station-microphone
description: Assess the processed VQE microphone channel and third-party taps that forward it off the station, with consent and reversibility first; triggers include микрофон Станции, обработанный микрофон, VQE, колонка как микрофон для ПК.
---

# Processed microphone: consent before capture

Require owner authorization and the informed consent of everyone who may be heard
in the room. Never suggest covert, hidden or unattended recording.
Keep Quasar enabled. Installing a tap, preloading a module into Quasar, forwarding
audio to another machine and autostart are guidance-only: describe them, do not
perform them. This project's own listening tools are routed in
[station-control](../station-control/SKILL.md).

## Prerequisites

Identify exact model, firmware build and existing secure access. The recipe's
evidence is one **Lite 2 (YNDX-00028/Plum)** unit, firmware 0.369.1.50, kernel
4.9.113 aarch64 with a 32-bit userspace. Agree with the owner where audio will go,
who can open it there and how long anything recorded is kept. Tell the household
whenever forwarding is active.

The tap is the "bridge" mode of a third-party project (Alisa Voice 0.3.3): a Linux
PC with PipeWire-Pulse or PulseAudio, Python 3.10+ and Chromium, and on the station
stock Quasar plus `socat` and `aplay`. Its ChatGPT sign-in can be skipped when only
the microphone is wanted.

## Diagnosis

1. `vqe_0` is the output of the vendor voice-quality stage, the channel configured
   as Alice's own input, not a raw microphone. On the tested unit two microphone
   channels and one speaker reference enter at 48 kHz; the processed output is mono
   16 kHz. Channel counts on other models need their own check.
2. The tap copies the result of one exported VQE library call, for channel type 6,
   channel 0 only. The original call still runs and its buffer, return value and
   error state are preserved. Normalised float samples become signed 16-bit PCM
   with saturation and handling of invalid values.
3. Copies leave as non-blocking local datagrams of at most 160 samples (10 ms), so
   a slow or missing receiver loses audio instead of delaying Alice. The bridge
   then carries PCM over SSH to a virtual source on the PC, about 32,000 bytes/s.
4. Loading needs a separate temporary launch profile in the project's data
   directory and a Quasar restart with the module preloaded. The system launch
   profile and the VQE library are not rewritten. The restart interrupts voice
   and playback.
5. Channel types and the library interface are undocumented by the vendor. Since
   project version 0.3.0 an unknown maind/VQE hash pair is admitted with a warning;
   being allowed to install does not confirm the format on that build.
6. A good `vqe_0` capture is not stock far-field recognition. In a Lite 1 test the
   vendor's cloud ASR recognised speech at very low digital level while an external
   recogniser did worse on louder PCM, so a fixed gain or AGC is no equivalent.
7. Station 2's native client uses the processed channel on the station (adaptation
   author's data), not PC forwarding. Only one voice mode may own the microphones
   and speaker at a time.

## Preview and confirmation

Before the owner acts, state: the model/build match, that Quasar restarts, that a
module is preloaded into a vendor process, that live room audio leaves the station
for a named PC and appears there as a source any application can open, and that
autostart is optional. Stop when the build is unverified and the owner has not
accepted that, when someone present has not agreed, when another voice mode is
running, or when no recovery path exists. Test with the owner's own short speech,
not household recordings. Ask before any restart; never stop Quasar as an implied step.

## Backup and rollback

Upstream rollback: turn the bridge autostart off and stop the bridge. That removes
the temporary PC audio devices, returns Quasar to its system profile and restarts
it. Modules are removed through the project's maintenance menu; root, SSH and the
ChatGPT login remain and no reset is needed. Delete captured audio according to
the retention the owner chose.

## Verification

The owner confirms the bridge status reports the VQE source at 16000 Hz, the
virtual source `alice_station_mic` exists on the PC and stock Alice still answers.
After rollback the virtual source is gone and Alice works. The recipe does not say
whether the hardware mute silences the forwarded stream: have the owner check
before relying on it. Upstream observed the stream for 45 seconds (about 16 kHz,
non-zero level, Quasar process unchanged).

## Limitations

One unit and one build. The Mini 1 and Mini 2 model pages list the processed
microphone as not tested. No recognition or echo-cancellation gain was measured
for this bridge. Evidence is pinned to public revision
`520fbccd68cb90a8c4f01d06bf38d9a7db915304`; no station test was performed here and
no scripts, binaries, addresses or recordings are distributed.

Source: [processed microphone on Lite 2](https://hackalice.me/recipes/microphone/).
