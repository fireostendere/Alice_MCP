---
name: station-community
description: Navigate HackAlice topics and prepare source-linked privacy-safe community reports with honest evidence limits; triggers include вики HackAlice, отправить отчёт, дополнить документацию.
---

# Share findings, not station identity

Require owner authorization before gathering device evidence. Keep Quasar enabled.
This workflow is read-only preparation; installations and device changes are
guidance-only. Drafting a contribution does not authorize publishing it or sending
private diagnostics to a community channel.

## Prerequisites

State the question or finding, public source page, model/firmware scope and whether
it comes from direct observation, simulation or third-party documentation. Use the
[home overview](https://hackalice.me/) and [recipe index](https://hackalice.me/recipes/)
to choose the narrow topic, not to impose an automatic root/OTA/package sequence.

## Diagnosis

1. For a support question, identify the missing prerequisite or contradictory claim
   and link its exact page. Do not turn an old “needs data” example into a claim
   that the current model matrix lacks that model.
2. For a new model report, collect only enough authorized metadata to distinguish
   OS, kernel/application architecture, storage family and relevant peripherals.
   Prefer an allowlisted summary; full dumps and logs may contain identifiers,
   credentials, removed private data or biometric/audio information.
3. Keep model-general architecture in internals and chassis-specific evidence in
   a device page. Put an operational recipe with the recipe index; record what
   was actually verified rather than importing a working claim from another model.
4. Link the [contributors page](https://hackalice.me/contributors/) for attribution
   and provenance. Credit is not independent review or validation of a recipe.

## Preview and confirmation

Draft a short report: expected behavior, sanitized observation, exact model/build,
minimal reproduction **without payloads**, relevant source links, limitation and
proposed documentation location. Let the owner inspect every text/image/attachment
before publication and request explicit confirmation of destination and contents.
Do not auto-open issues, upload archives or post to chat.

## Backup and rollback

Keep the original private evidence outside the repository and share only a new
redacted derivative; never overwrite the only original to redact it. Preserve a
local draft/diff before editing documentation. Publishing cannot be reliably undone
by deletion, so privacy review is the rollback boundary, not post-publication cleanup.

## Verification

Check that the draft contains no addresses, serials, account/device IDs, hostnames
derived from them, credentials, wireless network names, coordinates or raw audio.
Check source URLs, model/build conditions and qualifiers such as “untested.”
For a proposed new page, mention the index/navigation update needed without editing
unrelated files. Confirm the device remained unchanged and Quasar operational.

## Limitations

This catalog covers the 42 Markdown pages at a pinned revision, including home,
indexes and contributors. The same-day sitemap also has 42 canonical URLs;
redirect aliases, downloads, media and linked external projects are not additional
audited pages. Public links may later change. No explicit upstream documentation
redistribution license was found: write original summaries with attribution, not
copied pages, assets or shell scripts. Local report checks are not device tests.

Sources: [contribution structure and privacy rules](https://hackalice.me/contributing/),
[contributors and public change provenance](https://hackalice.me/contributors/),
[project overview](https://hackalice.me/), [recipe navigation](https://hackalice.me/recipes/).
