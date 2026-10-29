---
name: station-yam
description: Assess YAM or BetterYAM deployments and least-permission plugins without exposing an unauthenticated root API; triggers include YAM, BetterYAM, плагины yap.
---

# Treat the plugin daemon as privileged software

Require owner authorization for the station and management host. Keep Quasar enabled.
Daemon installation, plugin execution, updates and network/service changes are
guidance-only; do not execute the wiki's remote installers or enable binaries silently.

## Prerequisites

Determine whether the owner needs simple packages (`yse`) or hardware/voice plugins
with a management API (YAM). Snapshot evidence is YAM 0.1.15 and BetterYAM 0.1.18.
YAM expects an AArch64 kernel; Street/JBL are excluded. BetterYAM has a distinct
ARMv7 daemon for **Mini 1 only**, not universal support for 32-bit stations.

Confirm host support separately: the documented installer targets Windows x64,
Linux x86_64 and Apple Silicon macOS, not every host architecture. Existing access
must be authorized and secured before discussing a manual install.

## Diagnosis

1. For an existing daemon, compare its exact version, model and reported features
   with the intended plugin. Installation success does not establish LED, button,
   voice or sensor capability. Do not override the model just to suppress errors.
2. Distinguish host CLI from station daemon; removal of the Windows CLI does not
   remove `yamd`. The upstream station data path uses `/data/quazar/yam` with a **z**;
   do not “correct” it to the unrelated Quasar data directory.
3. The Web UI/API on port 8099 is unauthenticated. Network reachability can permit
   root-level package operations; require owner-managed isolation and no internet
   forwarding. This is not repaired by trusting plugin manifests alone.
4. Inspect manifest permissions and launch mode. Prefer the minimum Lua capability;
   keep binary execution disabled. Speech/commands plus network permissions can
   export private transcripts; raw IPC control and exec can alter the station.
5. A missing daemon after reboot can be expected: the installer does not supply
   autostart. Generic rootfs startup is untested in the recipe and fails to persist
   on Pickle. Do not keep reinstalling automatically to hide that gap.

## Preview and confirmation

Show exact release/checksum source, CLI and daemon locations, API exposure, plugin
permissions, oneshot/service behavior and update policy. The daemon's automatic
updates can replace and restart it; same-release SHA sums are not signatures.
Choose owner-reviewed manual updates where required by deployment policy.
Ask separately before enabling a plugin, a background service, network permission,
binary execution or persistence. No consent to YAM implies consent to stop Quasar.

## Backup and rollback

Retain settings, plugin inventory, manifests, disabled/enabled state and prior daemon
version. For rollback, the owner disables the specific plugin or restores the prior
release/settings before removing anything. Never delete the shared data hierarchy
from an uninstall example. Keep host CLI removal and station daemon removal distinct.

## Verification

Start with an owner-performed device/features read and one harmless authorized
capability, not speech capture or arbitrary exec. Confirm unsupported features fail
closed, binaries remain disabled, exposure is constrained and Quasar still works.
Verify chosen update policy and disclosed restart limitations; no persistence claim
without an explicitly scheduled reboot test.

## Limitations

BetterYAM Bluetooth toggling is documented only with Quasar stopped: leave it
unavailable unless the owner separately requests that outage. Its hotspot feature
is hardware-unverified. YAM and BetterYAM feature APIs are third-party contracts,
not Alice MCP permissions. No installer, daemon or plugin is bundled or run here.

Source: [YAM/BetterYAM architecture, permissions and deployment caveats](https://hackalice.me/recipes/yam/).
