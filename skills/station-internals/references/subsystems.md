# Voice, calls, telemetry and Zigbee notes

Original summaries of four internals pages at public revision
`520fbccd68cb90a8c4f01d06bf38d9a7db915304`. Reference data only: nothing here was
run on a station, and every action below is guidance-only for the owner. Server
addresses, account tokens, keys and payloads are deliberately omitted.

## Voice pipeline details

Source: [voice page](https://hackalice.me/internals/voice/) — a Quasar 3.21 Amlogic
profile, later notes from one Lite 1 0.369.1.46-family build, Plum and Station 2.
No Mini 1 measurement appears on the page.

- On the device only the wake word and about 25 short commands are recognized;
  full recognition and dialogue are cloud-side. Which command groups are active
  follows account settings, so stations differ.
- The original profile lists four raw microphone channels, two loudspeaker
  reference channels and `vqe_0`; the Lite 1 build adds one more processed channel
  of unknown purpose, and Plum has two microphones and one reference.
- `vqe_0` capture through the VQE interface is reported for Plum and Lite 1, on
  Lite 1 also during the station's own playback; echo-cancellation quality was not
  measured. Opening the stock ALSA capture device in parallel is not a general method.
- The stock uplink is Ogg/Opus, 16 kHz mono. Quiet digital levels were still
  recognized by the stock service, while an amplified external path recognized
  distant speech worse: fixed gain or AGC does not reproduce stock behaviour.
- Phrase end is decided by the server in the sampled config. Internal VAD
  capabilities exist in the binary, with no verified way for another process to
  subscribe. Interruption-related names are likewise research leads only.
- Station 2 spotter models are a Kaldi-style network with vendor layers. Its library
  has a text-defined keyword mode, but that build ships no models for it and no
  training tools exist: a custom wake word cannot be set there.
- Cumulative partial results and a client-approval hold that lets a proxy take the
  final text were verified on one Lite 1 through a transparent proxy. Audio still
  goes to the vendor cloud; timings are one measurement, not a guarantee.
- One Lite 1 config has Opus, PCM and buffered-PCM stream modes on the default ALSA
  device, loopback media and audio-client ports, an audio-focus arbiter and a smart
  volume switch. No stable external API is established for any of them.
- The activation sound is played locally on a spot, before any server reply, and
  follows an account-synced station flag; a second fast-feedback mechanism is
  configured on Lite 1 and which one is audible is unmeasured.
- A 10-second multi-channel ring buffer backs sound logs of activations; random
  logging is enabled for a beta group. See telemetry below for the consent layer.

## Calls and port 5081

Source: [calls page](https://hackalice.me/internals/calls/) — Midi (Cucumber),
0.369.1.50-family build, from config, binary strings, a call log and one capture;
several points are labeled inferences upstream.

- Calls run inside the platform role (a `calld` subsystem with loopback IPC) on an
  embedded SIP library. The station is an outbound SIP **client** of the vendor
  exchange over TLS; 5081 is open only while a call or registration is active.
  It is not a local control API and not a reason to probe the port.
- A call has separate planes: HTTPS session control authorized by the owner's
  account token, a persistent push channel that announces the incoming call, SIP
  signalling, and UDP Opus media under SRTP. In the captured call the media went
  through vendor relays even with phone and station on the same Wi-Fi.
- Server verification is enabled for the SIP transport, and with DTLS key agreement
  a passive capture does not expose speech.
- Static fallback addresses for telephony hosts mean a DNS override alone does not
  isolate calls.
- App calls, find-my-phone, carrier calls (beta, Russia only) and the baby monitor
  reuse the same stack through different providers.
- Stop: do not capture calls, extract tokens or session keys, or alter the call
  configuration to diagnose something else.

## Telemetry

Source: [telemetry page](https://hackalice.me/internals/telemetry/) — Midi
(Cucumber), 0.369.1.50-family build, from config, live logs and device traffic.
The page says the binary is shared across Amlogic models; other builds are untested.

- The telemetry role is an aggregator: other roles send events over local IPC, it
  queues them under the data volume and uploads through two batch queues (an
  analytics SDK and a product-event collector). Logs ship through the separate
  log role; a monitor samples system metrics about every 10 s.
- Each analytics report carries the device serial, a per-device analytics UUID,
  model, software version and the full kernel identification string. Treat the
  telemetry log, queue and metadata files as identifying: never paste them.
- The embedded application key is a shared client identifier, not a credential
  for reading anyone's data; that does not make sending with it acceptable.
- Event allow-lists live in the station config and are extended by online config.
  Covered areas: network and system health, requests, audio, voice-profile
  enrollment, accounts, smart home, Bluetooth, alarms, updates and crashes.
- Two layers are controlled differently. Diagnostic telemetry has no user toggle
  on the device. Voice-sample logging of activations uses the voice channel and
  follows the owner's consent in the vendor account; an empty on-disk cache does
  not show that it is off, because samples are streamed and only a share is sent.
- The supported way to stop voice-sample use is the account consent switch. The
  page also lists config keys (log shipping, allow-lists, sample parameters,
  limits) as facts, not a recommendation; online config can override local edits,
  and fallback hosts defeat DNS-only blocking.
- Stop: no config edits, traffic key logging or host blocking as a side effect of
  another task; each is a separate owner decision with its own backup.

## Zigbee on Midi through a local backend

Source: [Zigbee page](https://hackalice.me/internals/zigbee/) — Midi (Cucumber)
only, 26–27 October 2026, a custom extension to the quasard backend, pairing and
voice control checked with one Aqara lamp. Not a stock feature, not ZHA or
Zigbee2MQTT, and not coordinator reuse: stock Quasar keeps driving the radio.

- Pairing uses binary discovery directives over the station's existing protobuf
  connection to the local backend; the JSON route through glagol was rejected.
  Stages are start (time- and device-limited), finish and cancel.
- Finish needs the identifier of the **current** discovery callback plus the found
  endpoint; an address alone was rejected, and waiting too long timed out. Start
  and finish use different protocol enumerations, so check values against the schema.
- A sent directive is not a completed pairing: upstream confirmed a success code
  from the firmware and the device in both network databases.
- Names and rooms are separate local metadata in a partly reconstructed format.
  Resending an old full set can erase later household changes; removing a record
  does not remove the device from the radio network or the cloud account.
- The two network databases must agree. A mismatch made the firmware skip device
  handlers; the documented fix was specific to one stray record, not a recipe.
  A raw database is not a restore-directive payload: that attempt emptied the
  software database and needed the original files back.
- Upstream took its consistent snapshot while briefly suspending the platform
  role. That interrupts Quasar, so it is never an implicit step here; a live copy
  of changing files is not a consistent snapshot either. Take a new snapshot after
  any pairing or metadata change, since restoring an old one loses later bindings.
- Archives hold Zigbee network keys and household metadata: never publish them,
  the metadata contents, device identifiers or operator logs.
- Stop: no pairing, metadata or database action without a fresh owner-approved
  snapshot plan, and never retry an uncertain directive.
