---
name: station-fastfetch
description: Plan a minimal private Fastfetch display with compatible binaries and optional USB rows; triggers include fastfetch на Алисе, системная информация Станции.
---

# A display, not an inventory upload

Require owner authorization for station access. Keep Quasar enabled. Installation,
configuration and startup changes are guidance-only. Prefer an existing read-only
summary instead of installing an information tool just to identify a model.

## Prerequisites

Confirm kernel architecture and binary provenance. The published Fastfetch is
AArch64, not for ARMv7 Street or JBL. Determine whether another package manager
already owns the executable. Use an original or licensed logo, not copied wiki art.

## Diagnosis

1. Check an existing installation's version and executable path before transferring
   another binary. A package-manager collision should be resolved explicitly.
2. If the program fails, inspect ELF class/interpreter and dependencies; “aarch64”
   in its OS display does not prove a 64-bit vendor userspace.
3. Separate core modules from optional command-backed USB rows. Those rows depend
   on a separately installed OTG helper and may be absent normally. Do not install
   USB host support merely to populate an information line.
4. Audit custom command modules as executable code. A display configuration is not
   automatically safe, especially when it runs with root access.

## Preview and confirmation

Show the exact binary/version, destination, minimal module list and configuration
diff. Omit local addresses and identifier-bearing host titles from shared output.
Use a per-user/data-volume config selected through the installed version's supported
options where possible rather than remounting rootfs just for presentation.
Ask before adding any command module; do not add shell-login autostart by default.

## Backup and rollback

Retain the original config and package ownership record. For manual installation,
record only the files introduced; rollback restores that config and removes only
those files, not a package manager's shared directory or someone else's binary.
Do not install over a conflicting unowned executable.

## Verification

Have the owner run one local display and compare architecture, memory and storage
labels against independent platform evidence. Missing optional rows are not a
device fault. Keep screenshots/output private or redact network, host and disk
identifiers before sharing. Confirm no startup or Quasar settings changed.

## Limitations

This is not a benchmark, a flash-health check or a compatibility test. The wiki's
sample figures are one device snapshot, not expected values for every station.
No binary, ASCII artwork or USB helper is redistributed or executed here.

Source: [Fastfetch recipe and optional OTG helper](https://hackalice.me/recipes/fastfetch/).
