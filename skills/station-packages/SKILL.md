---
name: station-packages
description: Assess yse, Entware and yap packages for architecture, storage and reversible installation; triggers include пакеты на Станцию, Entware, установить yse.
---

# One package, explicit source

Require owner authorization and existing secure access. Keep Quasar enabled.
Bootstrap, installation, upgrade and removal are guidance-only; do not run
downloaded installers or infer permission to execute package code.

## Prerequisites

Confirm Buildroot model, kernel architecture, data-volume free space, package
purpose and current installation ownership. Published .yap registry builds are
AArch64; Street and JBL need ARMv7 candidates, not renamed archives. Android
integration is not established by this recipe. Resolve the model first.

## Diagnosis

1. Inspect the existing package list and command resolution before adding anything.
   Decide whether a system tool or already installed package satisfies the task.
2. Distinguish Entware's own loader/libraries from self-contained .yap packages.
   A 32-bit system userspace can coexist with 64-bit Entware on a compatible kernel.
3. Review package metadata, exact version, archive size, unpacked size and dependencies.
   On Lite the writable partition is small; leave operating margin rather than
   equating free bytes with a safe installation budget.
4. Resolve duplicate command names and unrelated files in `/data/opt/bin`.
   Unprefixed yse selects Entware first by default; explicitly choose `ent:` or
   `yap:` in the owner's plan. PATH can select a different installed implementation.
5. For bootstrap, note the upstream HTTP transport limitation. Fetch/review artifacts
   on a trusted host with authenticated provenance where possible; do not normalize
   insecure download-and-run as an unavoidable automatic step.

## Preview and confirmation

Show the selected source/version, checksum provenance, destination, dependency and
space changes, command collisions, and expected process/startup effects. A checksum
from the same compromised index is integrity checking, not independent authenticity.
Request confirmation for this package only; do not perform a blanket upgrade.

## Backup and rollback

Retain prior package version, user configuration, ownership inventory and registry
configuration. Remove or downgrade only tracked package files after owner review.
Never copy the wiki's broad uninstall loop into an automatic cleanup; shared `/opt`
links, Entware state and manually installed tools can belong to other applications.
Do not install the separate apkg Buildroot client over an existing Entware layout.

## Verification

Have the owner confirm installed version, resolved executable path, compatible
loader, remaining space and the requested function with a harmless bounded check.
Recheck Quasar and unrelated commands. Package presence alone does not prove
functionality, persistence or safe service configuration.

## Limitations

The wiki's Android exclusion is not a universal statement that Android cannot
have root; first-generation Station has separate access evidence, not a validated
Entware recipe. Registry contents and download hosts are mutable. No package or
device operation was tested here; manual installation remains outside tool execution.

Source: [yse, Entware, registry rules and uninstall caveats](https://hackalice.me/start/packages/).
