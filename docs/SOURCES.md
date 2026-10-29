# Source coverage and evidence limits

## Provenance

- Public source: [HackAlice / Yandex Station Extended](https://hackalice.me/).
- Upstream repository: [Marker284/yandex-station-extended](https://github.com/Marker284/yandex-station-extended).
- Pinned revision: `1ab048bf95c0e5e547a1ff6c801d2ed9deb193db`.
- [Pinned source tree](https://github.com/Marker284/yandex-station-extended/tree/1ab048bf95c0e5e547a1ff6c801d2ed9deb193db/docs).
- Retrieval date: **2026-10-26**. All **42** tracked `docs/**/*.md` pages were read,
  including the home page, three directory indexes, contributing and contributors.
- The [public sitemap](https://hackalice.me/sitemap.xml), retrieved on the same date,
  lists the same **42 canonical page URLs**. Navigation in the pinned `mkdocs.yml`
  also includes these pages. Public page URLs are mutable; the Git revision fixes
  the evidence used here, not the future content of the live site.

Scope excludes the fourteen generated historical redirect aliases, site assets,
images, JavaScript/CSS, the two installer downloads and external repositories,
releases, issue threads or services linked from pages. Those links provide context,
not an audit of their implementation, safety, availability or license. The sitemap
is a canonical-page inventory, not proof that every downloadable artifact was read.
No recursive audit of external projects is claimed.

## Original summaries, not upstream redistribution

No explicit upstream documentation redistribution license was found at this
revision. Public availability is not a redistribution grant. The catalog and
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
one of fourteen topic skills. Home uses ID `index`; directory indexes use
`devices`, `recipes` and `start`. Other IDs omit the `docs/` prefix and `.md` suffix.
Every mapped page is cited by its skill or that skill's local reference.

| Source path | Canonical public page | Skill |
|---|---|---|
| `docs/index.md` | [Home](https://hackalice.me/) | `station-community` |
| `docs/contributing.md` | [Contributing](https://hackalice.me/contributing/) | `station-community` |
| `docs/contributors.md` | [Contributors](https://hackalice.me/contributors/) | `station-community` |
| `docs/devices/index.md` | [Device index](https://hackalice.me/devices/) | `station-devices` |
| `docs/devices/jbl-link-music.md` | [JBL Link Music](https://hackalice.me/devices/jbl-link-music/) | `station-devices` |
| `docs/devices/station-2.md` | [Station 2](https://hackalice.me/devices/station-2/) | `station-devices` |
| `docs/devices/station-3.md` | [Station 3](https://hackalice.me/devices/station-3/) | `station-devices` |
| `docs/devices/station-gen1.md` | [Station first generation](https://hackalice.me/devices/station-gen1/) | `station-devices` |
| `docs/devices/station-lite.md` | [Lite](https://hackalice.me/devices/station-lite/) | `station-devices` |
| `docs/devices/station-lite2.md` | [Lite 2](https://hackalice.me/devices/station-lite2/) | `station-devices` |
| `docs/devices/station-max.md` | [Max](https://hackalice.me/devices/station-max/) | `station-devices` |
| `docs/devices/station-midi.md` | [Midi](https://hackalice.me/devices/station-midi/) | `station-devices` |
| `docs/devices/station-mini.md` | [Mini 1](https://hackalice.me/devices/station-mini/) | `station-devices` |
| `docs/devices/station-mini2.md` | [Mini 2](https://hackalice.me/devices/station-mini2/) | `station-devices` |
| `docs/devices/station-mini3-pro.md` | [Mini 3 Pro](https://hackalice.me/devices/station-mini3-pro/) | `station-devices` |
| `docs/devices/station-mini3.md` | [Mini 3](https://hackalice.me/devices/station-mini3/) | `station-devices` |
| `docs/devices/station-street.md` | [Street](https://hackalice.me/devices/station-street/) | `station-devices` |
| `docs/devices/yandex-hub.md` | [Smart Home Hub](https://hackalice.me/devices/yandex-hub/) | `station-devices` |
| `docs/internals/architecture.md` | [Architecture](https://hackalice.me/internals/architecture/) | `station-internals` |
| `docs/internals/directives.md` | [Directives](https://hackalice.me/internals/directives/) | `station-internals` |
| `docs/internals/gotchas.md` | [Pitfalls](https://hackalice.me/internals/gotchas/) | `station-internals` |
| `docs/internals/network.md` | [Network](https://hackalice.me/internals/network/) | `station-internals` |
| `docs/internals/toolchain.md` | [Toolchain](https://hackalice.me/internals/toolchain/) | `station-internals` |
| `docs/internals/voice.md` | [Voice](https://hackalice.me/internals/voice/) | `station-internals` |
| `docs/recipes/index.md` | [Recipe index](https://hackalice.me/recipes/) | `station-community` |
| `docs/recipes/airplay.md` | [AirPlay](https://hackalice.me/recipes/airplay/) | `station-airplay` |
| `docs/recipes/fastfetch.md` | [Fastfetch](https://hackalice.me/recipes/fastfetch/) | `station-fastfetch` |
| `docs/recipes/led.md` | [LED](https://hackalice.me/recipes/led/) | `station-led` |
| `docs/recipes/llm.md` | [LLM alternatives](https://hackalice.me/recipes/llm/) | `station-llm` |
| `docs/recipes/sounds.md` | [Sounds](https://hackalice.me/recipes/sounds/) | `station-sounds` |
| `docs/recipes/standalone.md` | [Standalone](https://hackalice.me/recipes/standalone/) | `station-standalone` |
| `docs/recipes/usb-host.md` | [USB host](https://hackalice.me/recipes/usb-host/) | `station-usb` |
| `docs/recipes/yam.md` | [YAM/BetterYAM](https://hackalice.me/recipes/yam/) | `station-yam` |
| `docs/start/index.md` | [Getting started](https://hackalice.me/start/) | `station-access` |
| `docs/start/packages.md` | [Packages](https://hackalice.me/start/packages/) | `station-packages` |
| `docs/start/recovery.md` | [Recovery](https://hackalice.me/start/recovery/) | `station-recovery` |
| `docs/start/retail-ca-mini3.md` | [Mini 3 factory route](https://hackalice.me/start/retail-ca-mini3/) | `station-access` |
| `docs/start/retail-ca.md` | [Lite 2 factory route](https://hackalice.me/start/retail-ca/) | `station-access` |
| `docs/start/root-android.md` | [AliceBridge](https://hackalice.me/start/root-android/) | `station-access` |
| `docs/start/root-jbl-link-music.md` | [JBL service USB](https://hackalice.me/start/root-jbl-link-music/) | `station-access` |
| `docs/start/root.md` | [Historical DNS route](https://hackalice.me/start/root/) | `station-access` |
| `docs/start/ssh.md` | [SSH and OTA](https://hackalice.me/start/ssh/) | `station-access` |

## Risk and operational policy

- `read_only`: the mapped summary is identification, reference analysis or report
  preparation. A source page may still contain high-risk examples that are not
  execution instructions for this project.
- `manual_change`: the topic can alter application/configuration or visible/audio
  state; the skill requires a preview, narrow owner confirmation and rollback.
- `high_risk`: access/trust/firmware/kernel or privileged-service consequences,
  including undocumented recovery or service interruption. Classification is
  conservative and never grants execution permission.

All skills require owner authorization and preserve Quasar by default. Rooting,
OTA changes, flashing, reset, Quasar shutdown and manual installations are
guidance-only, never automatic execution. Requesting one feature does not approve
the wiki's bundled prerequisite chain. SSH policy deliberately rejects upstream
empty/default-password examples in favor of pinned-host-key, key-only access.
No arbitrary shell executor, network discovery or private-key extraction is implied.

The local model reference retains all fourteen model pages, unsupported index
entries and conflicts: [model matrix](../skills/station-devices/references/models.md).
Key qualifications include Dropbear ABI disagreement, misleading retail/internal
names, Mini 3 versus Mini 3 Pro persistence, method-specific DNS test coverage,
newer TLS restrictions, Android package/access disagreement, absent current
Pickle/Max/Orion root routes, unverified subscription-blocked reuse, missing base
AirPlay installation and untested hardware/features marked in the source.

## Portability and checks

Skill-internal links stay inside the `skills/` tree. A packaging mapping from
that tree to `alice_mcp/data/skills` preserves sibling links and the devices
reference; no skill depends on the upstream checkout, this document or an
absolute workstation path. Packaging itself is maintained separately from these
summaries and still requires wheel/sdist inspection.

`tests/test_catalog.py` is an offline repository-file check. It pins the 42 paths,
schema/revision/URLs, fourteen skill names, source citations, required workflow
sections, portable relative links and absence of address/key literals or executable
download-and-run examples. It does not contact a station, validate third-party
binaries, prove exploit compatibility or replace the project's publication scanner.
