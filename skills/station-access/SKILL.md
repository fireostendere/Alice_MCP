---
name: station-access
description: Assess owner-managed SSH and model-specific access, root and OTA options without executing them; triggers include SSH на Алисе, рут Станции, отключить обновления.
---

# Access assessment, not an unlock runner

Require owner authorization for the named station and any router or phone involved.
Keep Quasar enabled. Root, reset, OTA, flashing, CA changes and manual installations
are guidance-only; never execute an unlock flow, payload or downloaded installer.

## Prerequisites

Resolve retail model, OS family and exact firmware with
[station-devices](../station-devices/SKILL.md). Ask whether secure SSH already
exists; prefer using it over creating new access. Require an isolated trusted LAN,
physical recovery access and an owner-held backup before discussing changes.
Never request credentials, factory keys or private device files.

## Diagnosis

1. Existing SSH: require key-only authentication and a host key verified through an
   independent owner-controlled channel. Pin it; stop on mismatch. Do not silently
   accept a new key, discover peers or fall back to password/unauthenticated ADB.
2. Buildroot: distinguish older DNS-assisted setup from newer **Plum** and
   **Bergamot** factory routes. DNS setup can wipe data and affect other LAN clients.
   A local test stand has narrower reported coverage than the general table.
3. Bergamot requires a still-usable older factory recovery image; factory RAM and
   retail rootfs are different targets. Its documented data/Wi-Fi prerequisites
   are not the same as Plum's. A missing volume or trust-store file is a stop,
   never permission to guess a partition, flash boot or format UBI.
4. Pickle has only an existing-root persistence description. Max has no confirmed
   root; Orion's network route failed. Do not manufacture a supported path.
5. Android first-generation Station has a separate **boot-image write** route.
   AliceBridge's reported Lite 1/Mini 1/Midi/Lite 2 tests do not establish every
   server profile; Shizuku networking remains experimental. Phone-kernel rooting
   and any app's automatic downgrade/OTA stages are outside execution scope.
6. JBL Link Music is ARMv7 Yocto/systemd with a reported transient service-USB
   window, not a Buildroot catcher workflow. Persistent ADB exposes root to the LAN;
   ready-made OTA blocking and SSH binary compatibility are unresolved.

## Preview and confirmation

Provide a **manual plan only**: exact model/build, intended access boundary,
files/volumes affected, network scope, data-loss exposure and failure stop points.
Ask separate confirmation for reset, phone root, boot writes, trust-store changes
or OTA policy; consent to SSH does not cover them. Never turn a confirmation into
permission to run a high-risk step through another tool.

SSH recommendations deliberately differ from upstream empty-password examples:
disable password and empty-password logins, restrict reachability, verify the
selected Dropbear build supports those settings, pin its persistent host key,
and test a second key-only connection before retiring any existing recovery path.
Port 2222 is common; the first-generation AliceBridge route reports port 22.

## Backup and rollback

The owner keeps original configuration files, permissions and hashes outside public
reports. For CA work, retain the original **symlink target** and a proven way back
to that exact retail volume; do not use a different model's certificate bundle.
For a boot route, require a restorable exact-model stock image and a verified backup
before proceeding manually. Never extract factory private keys.

OTA policy is a separate risk tradeoff: blocking it loses vendor security fixes.
The wiki's Buildroot approach changes launcher membership, update application and
signature verification; it is not a portable security hardening recipe. A reversible
plan must restore all original pieces, not just restart an updater. Do not apply
it to Android, JBL or data-only-persistent Pickle by analogy. Restore only the
temporary owner-approved DNS changes; preserve unrelated router rules.

## Verification

Ask for a sanitized result, not raw logs: correct model/build, successful key-only
login, stable independently verified host-key fingerprint, rejected password login,
restricted exposure and Quasar still working. Reboot/persistence tests themselves
need owner scheduling. Recheck firmware after a reboot; report untested behavior.
If a manual write outcome is uncertain, stop and inspect—never retry it blindly.

## Limitations

No station access or root route is tested by this project. Third-party APKs may
perform writes before a user reaches optional installation buttons. Do not launch
them as a harmless diagnostic. No default passwords, unlock server addresses,
exploitable payloads or private-key extraction instructions are provided here.

## Sources

- [Model routing](https://hackalice.me/start/)
- [Historical DNS method and local-stand coverage](https://hackalice.me/start/root/)
- [Plum factory route](https://hackalice.me/start/retail-ca/)
- [Bergamot factory route](https://hackalice.me/start/retail-ca-mini3/)
- [AliceBridge, phone prerequisites and profile caveats](https://hackalice.me/start/root-android/)
- [JBL startup USB route and OTA gaps](https://hackalice.me/start/root-jbl-link-music/)
- [SSH and OTA examples, not this project's execution policy](https://hackalice.me/start/ssh/)
