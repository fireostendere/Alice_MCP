# Supplied controller: behavior map and MCP migration gates

This is an original static analysis of the owner-supplied `Telegram_Control_Bot`
tree and the separate root `rgb_effects.sh`, not imported implementation code.
Three independent read-only reviews covered general entrypoints, voice flows and
RGB. No supplied program, dependency installer, shell helper or asset was run,
imported, deployed or contacted. Configuration/history contents were not imported.
Telegram control is not part of the requested replacement.

## Coverage and provenance

The inventory contained 43 files. Review covered all 19 command-dispatch branches,
71 callback branches, 16 menu/history builders, 13 HTTP GET routes, the POST route,
recording HEAD handling and five stored/default custom actions. UI aliases do not
constitute separate hardware capabilities. Static coverage is not operational proof.

The reference README claims MIT, but no complete LICENSE/COPYING/NOTICE was supplied;
bundled executables/media and their redistribution rights are unverified. Do not
copy the source/assets into package distributions on that claim alone. The supplied
tree, ZIP, private settings/history and root shell script are not publication files.
Code references below identify the supplied snapshot, which is not bundled with MCP.

## The important architectural distinction

`bot.py` controls the Station from the computer using ADB. Its “brain” is a rule
engine, not an LLM or stock-Alice conversation API. Incoming voice is played, not
transcribed. Removing a name from supplied text is not acoustic wake detection.
The soundpad hotkeys play through the Station, not a PC virtual microphone.

Our one `alice` connection remains the interface. Normal resident operations use
authenticated TLS, never an implicit SSH/ADB fallback or per-call executable
upload. Existing SSH-only implementations do not become native capabilities by
adding permissions. Client conversation covers ordinary writing, facts, arithmetic
and web research without adding duplicate Station tools or the reference evaluator.

## Capability-by-capability migration map

**Existing** means implemented, still subject to model, firmware, private policy
and transport. **Partial** is not a claim of full parity. **Gate** names concrete
missing implementation, evidence or authorization. No missing feature is advertised
as operational merely because its menu or configuration can be reproduced.

| Reference behavior | Current MCP equivalent / gap | Required migration gate |
|---|---|---|
| Menus, refresh, model/control inventory | Existing `get_capabilities`, `list_stations`, `get_model_interfaces`, `get_station_controls`, `station_status` | Keep alias-only output; declared model and source heuristics are not hardware identification. |
| Literal TTS | Existing `speech.say`; local Piper/PCM source and accepted bounded owner SSH pilot | Separately guarded resident update, host local-backend binding and actual native/TLS playback acceptance. No cloud fallback. |
| Text questions, date/time, calendar, jokes, science facts, stories, poems, random choices | Client agent supplies answers; `speech.say` can vocalize approved text | Do not copy canned responses or filtered `eval`; answer text/transcripts are not action permissions. |
| Weather / encyclopedia | Client research tools, not project `search_knowledge` | Queries leave the host when external services are chosen; no fabricated weather on failure. |
| Independent arbitrary activation name | Only Unicode profile validation and untrusted transcript-prefix routing exist | Quasar-compatible capture, physical-mute evidence, endpointing/echo/retrigger protection and acoustic positive/negative qualification. The reference supplies none of these. |
| Route activation to MCP / Alice | Profile returns an untrusted `mcp` / `alice` label | Implement actual destination adapters. Speaking text is not submitting it to stock Alice's dialogue service; avoid duplicate stock/MCP actuation. |
| Soundboard / notification assets | Existing registered-asset `media.upload` and bounded `audio.play` on supported SSH profiles | Native typed media/volume adapters remain absent; preserve alias/hash/duration/private-volume limits. No arbitrary files or executable upload. |
| Volume, quieter/louder, mute-style intents | Existing bounded `audio.volume`, model-specific mixer and restore policy | Native adapter and reviewed Mini 1 mixer mapping; no implicit full-volume or microphone-gain changes. |
| Global stop / owned cancellation | No generic native stop for arbitrary playback, guard and timer jobs | Track only owned bounded sessions and cancel those IDs; never kill all matching processes or claim all sound stopped. |
| Intercom: incoming audio then room reply | Playback primitives only; `speech.listen` is one fresh stock transcript, not raw room audio | Explicit capture/export consent, duration/destination/retention, safe capture alongside Quasar and physical mute. Never record automatically after playback. |
| Room listening / WAV export | Not implemented | The supplied recorder stops/kills Quasar and boosts capture gain; that method must not be used. |
| Persistent dialogue history and notifications | One-shot approved transcript lifecycle only | Separate explicit session/retention/deletion design. No continuous raw-log scraper or import of saved household dialogue. |
| Acoustic security guard, sensitivity and escalation | No continuous monitoring or siren composite | Explicit monitoring policy, level calibration, mute behavior and false-alarm tests; no surprise room/video capture or loud escalation. |
| Bounded LED effects | Existing `led.effect`, Mini 1 captured replay/pulse and fixed animation presets on SSH profiles | No native LED implementation. Mini 1 channel/color/position calibration and status-indicator preservation remain unqualified. |
| Temporal rainbow, breathe, spin/custom spin, static color/off, notify, police, strobe, speed/restore | Current presets are not equivalent to these reference algorithms | Implement bounded low-brightness equivalents on qualified profiles; no indefinite loops, GCC changes, 10-Hz full-white strobe or persistent command replay as “restore.” |
| Clock/display short text, faces, segments and clear | Missing; ordinary ring effects are not a clock adapter | Reviewed display model/protocol plus captured setting restoration. Supplied helper overwrites clock settings and fakes sensor input through bind mounts; do not port that mechanism. |
| Display brightness / adaptive mode | Missing | Real model-specific scale/trigger/current evidence and bounded transactional restoration; no fake 50,000-lux input or forced GCC=255. |
| Countdown, timer/alarm presets and cancel | Bounded playback/LED primitives only, not a scheduler/display workflow | Owned job lifecycle, bounded durations, restart/cancel semantics, output limits and distinct display adapter. Referenced alarm helper is absent. |
| Full/sound-only/test siren and off | Missing composite | Reviewed assets, bounded safe volume/duration, owned cancellation and restoration. Missing source helper prevents even static verification. |
| Host soundpad keyboard shortcuts | Registered-asset actions supply underlying playback | Optional separately permissioned client integration; no default global keyboard hook or broad player killing. |
| Health, memory, storage, uptime, services/audio/network summaries | Existing fixed probes, depending on transport; native has `health`/`microphone` only | Return typed allowlisted values, not raw process/mixer/filesystem output or private factory metadata. Passive network probe is not ping. |
| Reboot | Read-only reboot-reason probe only | Separate explicit disruptive-action policy and state-bound implementation; never an implicit natural-language side effect. |
| Custom buttons and repeat | No raw-command registry | Only declarative presets referencing already-permitted typed actions; fresh preview/approval each time, no uncertain-write repeat button. |
| Arbitrary terminal / browser terminal / quick shell commands | Deliberately unsupported | Do not expose root shell, raw ADB, unauthenticated HTTP execution or raw terminal history. Typed bounded functions are the replacement. |
| Script/program/config upload and execute/register | Only pinned media upload, narrow configuration bindings, reviewed operator provisioning | No generic executable/script uploader, downloaded-script execution or automatic second execution method after failure. |
| Setup / dependency self-repair / process cleanup / bot lifecycle | Private inventory plus client-launched stdio `alice`; guarded resident provisioning/update is separate | Do not import installer, broad process matching, automatic package installs, volume/light side effects or a second background Telegram daemon. |
| Host login autostart | Not required for client-launched MCP | Separate explicit persistence decision if later needed; no hidden host startup mutation. |
| Support links, suggestions, Telegram forwarding | Telegram-only UI, not Station control | Do not port Telegram identities, submissions or forwarding. An advertised update link is not an implemented update mechanism. |

## Host-camera functionality is a separate target

All reference cameras belong to the **computer**, not Mini 1. Station standing
authorization does not authorize an arbitrary PC camera, room stream or keyboard
monitor. No camera or host-input action was enabled by this review.

| Reference host behavior | What must exist before a legitimate MCP equivalent |
|---|---|
| Enumerate/select/switch/persist camera | Separate host-device aliases/policy and exact device identity; no supplied filesystem device path. |
| Photo / short clip | Explicit capture, bounded current acquisition, private artifact/destination/retention limits; no automatic external send. |
| Live stream / stop | Authenticated transport and actual worker/viewer shutdown. The reference's flag-only stop is not a privacy barrier. |
| Status, motion HUD/overlay | Sanitized status and explicit analysis settings; pixel-change boxes are not person identification. |
| Manual recording and download/metadata | Owned session, duration/quota/retention/deletion and authenticated artifact access. The reference's ten-minute cutoff is not a retention policy. |
| Motion alerts / combined guard / sensitivity / dismiss/disarm | Separate explicit continuous-surveillance policy; no implicit photo/clip capture in sound-only mode. |
| Input detection and screen lock | Separate host permission and disruptive-action confirmation; no covert keyboard/activity collection. |
| Fullscreen, wide view, clock and reload UI | Client presentation only, not new Station actions. |

The reference HTTP server exposes camera/recording/terminal operations without
adequate authentication, binds broadly and can remain alive after “stop.” None of
that listener or its file-serving routes is a safe TG-free transport replacement.

## RGB mapping and restoration findings

- The root helper assumes `micro:{red,green,blue}:ring{1,2,3}`. Our source-backed
  profile assigns that layout to **Lite**, not Mini 1. It provides no Mini 1 pin map.
- The bundled helper guesses among several model prefixes, otherwise defaults to
  Bergamot/six indices, and broadcasts to RGB-looking entries. Its first matching
  GCC control is not a verified model/current mapping.
- Reference `rainbow` is a whole-display temporal palette; existing Mini 1 `rainbow`
  is one spatial six-zone frame. Same names do not imply behavioral equivalence.
- Root/bundled `spin`, `spin_color`, `notify` and `police` differ; bundled notify
  without GCC does not actually blink, and police's broad writes override grouping.
- TTS calls `color`; both supplied dispatchers implement `static`, not `color`.
- Neither helper validates RGB/delay bounds, enforces a deadline, preserves actual
  hardware state/current, verifies success or protects mute/listening/timer signals.
  Predictable temporary files, unchecked PID killing and generated executable text
  must not be transplanted. “Restore” replays script settings, not captured state.

Existing Mini 1 writes remain constrained to captured same-boot/firmware idle
evidence and fixed reviewed operations, without arbitrary registers, force/unbind,
shutdown/current writes or hardware readback claims. The auxiliary LED candidate
is metadata, not permission to write it. Missing software mute state is unknown,
not evidence that the physical hardware button is ineffective.

## Source anchors and migration order

Within the supplied `Telegram_Control_Bot/Telegram_Control_Bot` snapshot:

- `bot.py:134–345`: ADB wrappers, model heuristics, display and soundboard.
- `bot.py:348–828`: cloud synthesis chain, rule “brain,” weather/wiki and recording.
- `bot.py:852–1124,2004–2597`: host input/camera, HTTP, recordings and motion.
- `bot.py:2603–2928`: siren, guards and timers.
- `bot.py:2935–3181`: raw-log dialogue collection and history/navigation.
- `bot.py:3696–4183,4196–4933`: complete command and callback dispatch.
- `speaker_scripts/record_helper.sh:5–33`: gain/Quasar interruption during capture.
- `speaker_scripts/rgb_effects.sh:6–75,78–433`: heuristic mapping and effects.
- `speaker_scripts/led_helper.sh:39–138`: clock, sensor overrides and face patterns.
- `setup_bot.py:125–156`: deployment/volume/light side effects; `pc_soundboard.py`
  implements host hotkeys triggering Station playback.

First qualify the already accepted local voice through the separately guarded
resident/TLS route. Then implement typed native parity behind the existing private
action policies. Independent capture/wake and Mini 1 RGB require their own hardware
evidence, not a copy of the reference recorder or its model guesses. Host camera,
monitoring, persistence and disruptive actions need separate target/consent gates.
This map records remaining work; it does **not** mark the requested full migration
or the complete voice-assistant goal as delivered.
