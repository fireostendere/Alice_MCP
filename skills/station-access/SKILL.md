---
name: station-access
description: Plan and carry out owner-authorized SSH and model-specific access, root and OTA work when project policy and tools permit; triggers include SSH на Алисе, рут Станции, отключить обновления.
---

# Owner-authorized station access

Require owner authorization for the named station and any router or phone involved.
Keep Quasar enabled. Use existing tools for explicitly requested actions when
project policy and runtime permissions permit them. This skill cannot override
those limits or add capabilities that are not implemented. Operations lacking an
implemented, authorized tool remain guidance-only; name the missing capability or
prerequisite immediately rather than implying that preparation is running.
Reviewed, integrity-pinned owner-authorized workflows may use existing permitted
tools; never blindly download and run an installer.

## Prerequisites

Resolve retail model, OS family and exact firmware with
[station-devices](../station-devices/SKILL.md). Prefer configured aliases and
already-authorized read-only checks over repeated questions. Reuse existing secure
SSH; ask only for facts that cannot be established through permitted checks.
Require an isolated trusted LAN and physical recovery access before changes, with
backup requirements proportional to the operation below. A full flash backup may
require the access being established;
do not make it a universal prerequisite for initial access/setup. Read-only
diagnosis and discussing options do not require a backup.
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
4. Pickle has only an existing-root persistence description. At the pinned
   revision Max has no confirmed root and Orion's network route failed; the live
   wiki has since added Max ADB, Mini 1 own-image-over-USB and Orion DNS routes
   that this skill has not reviewed. Read the current page for those models and
   do not manufacture a supported path from this summary.
5. Android first-generation Station has a separate **boot-image write** route.
   AliceBridge's reported Lite 1/Mini 1/Midi/Lite 2 tests do not establish every
   server profile; Shizuku networking remains experimental. Phone-kernel rooting
   and an app's downgrade/OTA stages are separate operations, not implied by
   permission to connect over SSH.
6. JBL Link Music is ARMv7 Yocto/systemd with a reported transient service-USB
   window, not a Buildroot catcher workflow. Persistent ADB exposes root to the LAN;
   ready-made OTA blocking and SSH binary compatibility are unresolved.

## Preview and confirmation

Provide a bounded plan: exact model/build, intended access boundary,
files/volumes affected, network scope, data-loss exposure and failure stop points.
Perform permitted steps already covered by the owner's request, using the tool's
state-bound previews and required approval flow. Preserve standing preauthorization;
do not ask redundant chat confirmations for the same scope. Get explicit approval
for additional reset, phone root, boot writes, trust-store changes or OTA policy
not covered by that request; consent to SSH alone does not cover them. Never use
another tool to bypass a policy or runtime denial.

If the owner must perform a manual step, say preparation is ready only after its
prerequisites have actually been verified; otherwise identify the unfinished step.

SSH recommendations deliberately differ from upstream empty-password examples:
disable password and empty-password logins, restrict reachability, verify the
selected Dropbear build supports those settings, pin its persistent host key,
and test a second key-only connection before retiring any existing recovery path.
Port 2222 is common; the first-generation AliceBridge route reports port 22.

## Backup and rollback

Preserve affected originals, permissions and hashes before persistent edits when
access exists; keep them outside public reports. If required originals cannot be
collected, name that blocker rather than treating the edit as reversible. Never
claim an unavailable backup exists.
Reset requires an explicitly accepted data-loss scope, not an assumed full flash
backup. For CA work, retain the original **symlink target** and a proven way back
to that exact retail volume; do not use a different model's certificate bundle.
For boot/partition writes, require a restorable exact-model stock image and a verified backup
appropriate to the affected volumes/data before writing. Never extract factory private keys.

OTA policy is a separate risk tradeoff: blocking it loses vendor security fixes.
The wiki's Buildroot approach changes launcher membership, update application and
signature verification; it is not a portable security hardening recipe. A reversible
plan must restore all original pieces, not just restart an updater. Do not apply
it to Android, JBL or data-only-persistent Pickle by analogy. Restore only the
temporary owner-approved DNS changes; preserve unrelated router rules.

## Verification

Run permitted read-only verification when access exists; ask the owner only for
observations that tools cannot establish. Report sanitized results, not raw logs:
correct model/build, successful key-only
login, stable independently verified host-key fingerprint, rejected password login,
restricted exposure and Quasar still working. Reboot only on an explicit owner
request, not as an implicit test. Recheck firmware after a reboot; report untested
behavior. If any write outcome is uncertain, stop and inspect—never retry it blindly.

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
