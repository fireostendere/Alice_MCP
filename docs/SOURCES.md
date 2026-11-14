# Source coverage and evidence limits

## Provenance

- Public source: [HackAlice / Yandex Station Extended](https://hackalice.me/).
- Upstream repository: [Marker284/yandex-station-extended](https://github.com/Marker284/yandex-station-extended).
- Pinned revision: `520fbccd68cb90a8c4f01d06bf38d9a7db915304`.
- [Pinned source tree](https://github.com/Marker284/yandex-station-extended/tree/520fbccd68cb90a8c4f01d06bf38d9a7db915304/docs).
- Retrieval date: **2026-11-11**. The revision tracks **73** `docs/**/*.md` pages; the
  catalog holds **65** of them. **55** were re-read at this revision.
- **Not re-read:** ten access and package pages keep the summaries written at the
  earlier revision `1ab048bf95c0e5e547a1ff6c801d2ed9deb193db` (retrieved 2026-10-26). They are listed in the catalog as
  `upstream.carried.pages`, and their skills (`station-access`, `station-recovery`,
  `station-packages`, `station-yam`) still describe that revision. Eight new access
  pages (ADB, Max ADB, Mini 1 USB image, JBL Link Portable, UART shell, Max root,
  first-generation update blocking and Wi-Fi rollback) have no catalog entry and
  are listed as `upstream.carried.not_catalogued`. `docs/recipes/yam.md` was renamed
  upstream to `docs/recipes/packagemanagers.md`; only its path and URL were updated.
- The [public sitemap](https://hackalice.me/sitemap.xml), retrieved on the same date,
  lists the same **73 canonical page URLs**. Public page URLs are mutable; the Git
  revision fixes the evidence used here, not the future content of the live site.

Scope excludes the generated historical redirect aliases, site assets,
images, JavaScript/CSS, the two installer downloads and external repositories,
releases, issue threads or services linked from pages. Those links provide context,
not an audit of their implementation, safety, availability or license. The sitemap
is a canonical-page inventory, not proof that every downloadable artifact was read.
No recursive audit of external projects is claimed.

## Original summaries, not upstream redistribution

No explicit upstream documentation redistribution license was found at this
revision, and the site configuration states that all rights are reserved. Public
availability is not a redistribution grant. The catalog and
skills are original English operational summaries with exact source links, not
translations or copies of complete pages. No upstream shell scripts, binaries,
images, branded audio, certificates, private material or exploit payloads are
included. External projects may have separate licenses; their rights do not
automatically cover the wiki. Attribution is retained through the source links
and the [contributors page](https://hackalice.me/contributors/).

The `evidence: upstream_documentation` marker always means a community report,
not manufacturer support or an Alice MCP station test. Offline catalog tests,
schema checks and simulated checks are **not device tests**. “Verified” inside
an upstream report is attributed to that report, not independently revalidated.

## Coverage manifest

The machine-readable authority is
[`src/alice_mcp/data/catalog.json`](../src/alice_mcp/data/catalog.json): schema
`alice.catalog.v1`, one unique source path and canonical URL per page, mapped to
one or more of twenty skills. Home uses ID `index`; directory indexes use
`devices`, `recipes` and `start`. Other IDs omit the `docs/` prefix and `.md` suffix.
Every mapped page is cited by its skill or that skill's local reference.

| Source path | Canonical public page | Skills |
|---|---|---|
| `docs/contributing.md` | [Contributing to the wiki](https://hackalice.me/contributing/) | `station-community`, `station-bringup` |
| `docs/contributors.md` | [Community contributors](https://hackalice.me/contributors/) | `station-community` |
| `docs/devices/index.md` | [Device families and support matrix](https://hackalice.me/devices/) | `station-devices`, `station-bringup` |
| `docs/devices/fiero-hi.md` | [Fiero Hi: partner-branded Lite](https://hackalice.me/devices/fiero-hi/) | `station-devices` |
| `docs/devices/jbl-link-music.md` | [JBL Link Music with Alice](https://hackalice.me/devices/jbl-link-music/) | `station-devices` |
| `docs/devices/jbl-link-portable.md` | [JBL Link Portable with Alice](https://hackalice.me/devices/jbl-link-portable/) | `station-devices` |
| `docs/devices/station-2.md` | [Station 2](https://hackalice.me/devices/station-2/) | `station-devices` |
| `docs/devices/station-3.md` | [Station 3: Orion](https://hackalice.me/devices/station-3/) | `station-devices` |
| `docs/devices/station-gen1.md` | [Station first generation](https://hackalice.me/devices/station-gen1/) | `station-devices` |
| `docs/devices/station-lite.md` | [Station Lite: Micro](https://hackalice.me/devices/station-lite/) | `station-devices` |
| `docs/devices/station-lite2.md` | [Station Lite 2: Plum](https://hackalice.me/devices/station-lite2/) | `station-devices` |
| `docs/devices/station-max.md` | [Station Max](https://hackalice.me/devices/station-max/) | `station-devices` |
| `docs/devices/station-max-zigbee.md` | [Station Max with Zigbee](https://hackalice.me/devices/station-max-zigbee/) | `station-devices` |
| `docs/devices/station-midi.md` | [Station Midi: Cucumber](https://hackalice.me/devices/station-midi/) | `station-devices` |
| `docs/devices/station-mini.md` | [Station Mini first generation](https://hackalice.me/devices/station-mini/) | `station-devices` |
| `docs/devices/station-mini2.md` | [Station Mini 2](https://hackalice.me/devices/station-mini2/) | `station-devices` |
| `docs/devices/station-mini3.md` | [Station Mini 3: Bergamot](https://hackalice.me/devices/station-mini3/) | `station-devices` |
| `docs/devices/station-mini3-pro.md` | [Station Mini 3 Pro: Pickle](https://hackalice.me/devices/station-mini3-pro/) | `station-devices` |
| `docs/devices/station-street.md` | [Station Street: Mango](https://hackalice.me/devices/station-street/) | `station-devices` |
| `docs/devices/yandex-hub.md` | [Yandex Smart Home Hub: Saturn](https://hackalice.me/devices/yandex-hub/) | `station-devices` |
| `docs/edit.md` | [Wiki browser editor](https://hackalice.me/edit/) | `station-community` |
| `docs/glossary.md` | [Glossary](https://hackalice.me/glossary/) | `station-community` |
| `docs/index.md` | [Yandex Station Extended overview](https://hackalice.me/) | `station-community`, `station-control` |
| `docs/internals/architecture.md` | [Quasar firmware architecture](https://hackalice.me/internals/architecture/) | `station-internals` |
| `docs/internals/calls.md` | [Alice calls and port 5081](https://hackalice.me/internals/calls/) | `station-internals` |
| `docs/internals/directives.md` | [Directives and capabilities](https://hackalice.me/internals/directives/) | `station-internals` |
| `docs/internals/gotchas.md` | [Platform pitfalls](https://hackalice.me/internals/gotchas/) | `station-internals` |
| `docs/internals/network.md` | [Network interfaces and services](https://hackalice.me/internals/network/) | `station-internals` |
| `docs/internals/telemetry.md` | [Telemetry pipeline](https://hackalice.me/internals/telemetry/) | `station-internals` |
| `docs/internals/toolchain.md` | [Cross-compiling station software](https://hackalice.me/internals/toolchain/) | `station-internals` |
| `docs/internals/voice.md` | [Alice voice pipeline](https://hackalice.me/internals/voice/) | `station-internals`, `station-control` |
| `docs/internals/zigbee.md` | [Cloud-free Zigbee on Midi with quasard](https://hackalice.me/internals/zigbee/) | `station-internals` |
| `docs/recipes/index.md` | [Recipe index](https://hackalice.me/recipes/) | `station-community`, `station-streaming`, `station-gen1` |
| `docs/recipes/airplay.md` | [AirPlay with Shairport Sync](https://hackalice.me/recipes/airplay/) | `station-airplay`, `station-streaming` |
| `docs/recipes/bekend-self-hosted.md` | [Self-hosted backend overview](https://hackalice.me/recipes/bekend-self-hosted/) | `station-llm` |
| `docs/recipes/bt-proxy.md` | [MPRIS players as a Bluetooth source (bt-proxy)](https://hackalice.me/recipes/bt-proxy/) | `station-integrations` |
| `docs/recipes/fastfetch.md` | [Fastfetch station display](https://hackalice.me/recipes/fastfetch/) | `station-fastfetch` |
| `docs/recipes/home-assistant.md` | [Lite 2 as a Home Assistant voice satellite](https://hackalice.me/recipes/home-assistant/) | `station-integrations` |
| `docs/recipes/led.md` | [LED interfaces and animations](https://hackalice.me/recipes/led/) | `station-led` |
| `docs/recipes/llm.md` | [Custom LLM: Quasar Bridge and ChatGPT Voice](https://hackalice.me/recipes/llm/) | `station-llm` |
| `docs/recipes/microphone.md` | [Processed microphone on Lite 2](https://hackalice.me/recipes/microphone/) | `station-microphone` |
| `docs/recipes/midipult.md` | [MidiPult web panel for Station Midi](https://hackalice.me/recipes/midipult/) | `station-integrations` |
| `docs/recipes/packagemanagers.md` | [YAM and BetterYAM](https://hackalice.me/recipes/packagemanagers/) | `station-yam` |
| `docs/recipes/quasard.md` | [quasard replacement backend](https://hackalice.me/recipes/quasard/) | `station-llm` |
| `docs/recipes/sounds.md` | [Custom sounds and streams](https://hackalice.me/recipes/sounds/) | `station-sounds` |
| `docs/recipes/spotify.md` | [Spotify Connect on Station 2](https://hackalice.me/recipes/spotify/) | `station-streaming` |
| `docs/recipes/standalone.md` | [Standalone use without Yandex](https://hackalice.me/recipes/standalone/) | `station-standalone` |
| `docs/recipes/station1-debian.md` | [Debian on Station 1](https://hackalice.me/recipes/station1-debian/) | `station-gen1`, `station-standalone` |
| `docs/recipes/station1-privacy.md` | [Station 1 analytics endpoint blocking](https://hackalice.me/recipes/station1-privacy/) | `station-gen1` |
| `docs/recipes/station1-soundcloud.md` | [SoundCloud by voice on Station 1](https://hackalice.me/recipes/station1-soundcloud/) | `station-streaming` |
| `docs/recipes/station1-spotify.md` | [Spotify by voice on Station 1](https://hackalice.me/recipes/station1-spotify/) | `station-streaming` |
| `docs/recipes/station1-tv.md` | [Station 1 TV kit and web remote](https://hackalice.me/recipes/station1-tv/) | `station-gen1`, `station-streaming` |
| `docs/recipes/upravlenie-golosom-alisy.md` | [Voice web panel for spoken text](https://hackalice.me/recipes/upravlenie-golosom-alisy/) | `station-sounds` |
| `docs/recipes/usb-host.md` | [USB host peripherals](https://hackalice.me/recipes/usb-host/) | `station-usb` |
| `docs/recipes/v3rt3p.md` | [v3rt3p self-hosted stack](https://hackalice.me/recipes/v3rt3p/) | `station-llm` |
| `docs/recipes/youtube-music.md` | [YouTube Music with ytm-alice](https://hackalice.me/recipes/youtube-music/) | `station-streaming` |
| `docs/start/index.md` | [Getting started by model](https://hackalice.me/start/) | `station-access` |
| `docs/start/packages.md` | [Packages with yse](https://hackalice.me/start/packages/) | `station-packages` |
| `docs/start/recovery.md` | [Recovery and rollback](https://hackalice.me/start/recovery/) | `station-recovery` |
| `docs/start/retail-ca.md` | [Lite 2 factory-to-retail access assessment](https://hackalice.me/start/retail-ca/) | `station-access` |
| `docs/start/retail-ca-mini3.md` | [Mini 3 factory-to-retail access assessment](https://hackalice.me/start/retail-ca-mini3/) | `station-access` |
| `docs/start/root.md` | [DNS access assessment](https://hackalice.me/start/root/) | `station-access` |
| `docs/start/root-android.md` | [AliceBridge Android access assessment](https://hackalice.me/start/root-android/) | `station-access` |
| `docs/start/root-jbl-link-music.md` | [JBL USB startup access assessment](https://hackalice.me/start/root-jbl-link-music/) | `station-access` |
| `docs/start/ssh.md` | [SSH persistence and OTA assessment](https://hackalice.me/start/ssh/) | `station-access` |

## Risk and operational policy

- `read_only`: the mapped summary is identification, reference analysis or report
  preparation. A source page may still contain high-risk examples that are not
  execution instructions for this project.
- `manual_change`: the topic can alter application/configuration or visible/audio
  state; the skill requires a preview, narrow owner confirmation and rollback.
- `high_risk`: access/trust/firmware/kernel or privileged-service consequences,
  including undocumented recovery or service interruption. Classification is
  conservative and never grants execution permission.

All skills require owner authorization and preserve Quasar by default. High-risk
work requires explicit owner scope, exact-target evidence, verified prerequisites
and recovery planning. Unavailable Alice MCP actions remain guidance-only in that
API, not a prohibition on separately owner-authorized workflows through existing
permitted host tools. Knowledge never authorizes writes; requesting one feature
does not approve the wiki's bundled prerequisite chain. Obtain consent for scope
extensions, not again for already-covered actions. Runtime denials and TLS/SSH
trust checks still apply. SSH policy deliberately rejects upstream empty/default-
password examples in favor of pinned-host-key, key-only access. No arbitrary shell
executor, network discovery or private-key extraction is implied.

The local model reference retains all seventeen model pages, unsupported index
entries and conflicts: [model matrix](../skills/station-devices/references/models.md).
Key qualifications include Dropbear ABI disagreement, misleading retail/internal
names, Mini 3 versus Mini 3 Pro persistence, method-specific DNS test coverage,
newer TLS restrictions, Android package/access disagreement, build-scoped
Pickle and non-persistent Max access, unverified subscription-blocked reuse and
untested hardware/features marked in the source.

## Portability and checks

Skill-internal links stay inside the `skills/` tree. A packaging mapping from
that tree to `alice_mcp/data/skills` preserves sibling links and the devices
reference; no skill depends on the upstream checkout, this document or an
absolute workstation path. Packaging itself is maintained separately from these
summaries and still requires wheel/sdist inspection.

`tests/test_catalog.py` is an offline repository-file check. It pins the 65 paths,
schema/revisions/URLs, twenty skill names, source citations, required workflow
sections, portable relative links and absence of address/key literals or executable
download-and-run examples. It does not contact a station, validate third-party
binaries, prove exploit compatibility or replace the project's publication scanner.
