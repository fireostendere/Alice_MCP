# Model evidence matrix

Original summary of all model pages at upstream revision
`1ab048bf95c0e5e547a1ff6c801d2ed9deb193db`, retrieved 2026-10-26.
Every status below means **reported by the wiki**, not tested by Alice MCP.
The [device index](https://hackalice.me/devices/) lists fourteen dedicated pages.
No row authorizes rooting, probing a network or writing hardware registers.

| Retail model / exact source | Platform and executable compatibility | Access and persistence evidence | Important exclusion or unknown |
|---|---|---|---|
| [Lite / Micro v1.2](https://hackalice.me/devices/station-lite/) | AXG/A53; kernel 4.9.68 AArch64; Buildroot 2020.02.1 **armhf**; NAND/UBI. Static AArch64 or compatible armhf dependencies. | DNS root and persistent writable-rootfs changes reported for the sampled older build; data maps to bkupgrade. | Three sysfs RGB zones. Optional chip probe failures can be normal revision differences. Do not generalize to Plum. |
| [Lite 2 / Plum](https://hackalice.me/devices/station-lite2/) | AXG/A113X; kernel 4.9.68 AArch64; armhf Buildroot; NAND/UBI. | After the 21 October 2026 change, a separate factory-to-retail CA route is documented; older DNS route is a fallback only. | AliceBridge root is reported but exact tested firmware is unclear. Factory and retail partition identity must be verified, not guessed. |
| [Mini 1](https://hackalice.me/devices/station-mini/) | AXG/A53; kernel 4.9.68 AArch64; older Buildroot 2017.02; NAND/UBI; SELinux enforcing. | Page reports A/B boot and writable rootfs. DNS/AliceBridge reports do not prove compatibility on every build. | PCM5122, gesture sensor and I2C ring; not Lite sysfs or Mini 3 framebuffer. A/B claim plus abbreviated single-system MTD table is insufficient to select a write target. |
| [Mini 2](https://hackalice.me/devices/station-mini2/) | AXG/A53; kernel 4.9.68 AArch64; Buildroot 2020.02.1 armhf; NAND/UBI with vendor variants. | Writable-rootfs changes reported as possible; DNS root reported with one test's version not recorded. | Six sysfs RGB zones and clock segments. Six-column .led format still needs confirmation. AliceBridge server profile is not a verified end-to-end run. |
| [Mini 3 / Bergamot](https://hackalice.me/devices/station-mini3/) | AXG/A53; kernel 4.9.68 AArch64; armhf Buildroot; NAND/UBI. | Newer builds use a separate factory recovery/trust-store/data-marker procedure. It requires the older usable factory image. Retail rootfs changes are reported persistent across reboot, not immune to OTA. | Six-zone **BGR**, 24-bpp framebuffer, 18-byte frame; optional clock. Not Pickle. Factory RAM changes are not retail persistence. |
| [Mini 3 Pro / Pickle](https://hackalice.me/devices/station-mini3-pro/) | A4/A53; kernel 5.4.242 AArch64; Buildroot 2023.11.1; NAND/UBI; SELinux enforcing. | Only `/data` changes are reported to survive reboot. Existing-root ADB persistence is described; current root acquisition is **not** documented. | Sixteen ring and six logo zones plus clock. Rootfs init.d autostart does not persist. A server profile does not close the acquisition gap. |
| [Station 2](https://hackalice.me/devices/station-2/) | AXG/A53; kernel 4.9.68 AArch64; Buildroot 2020.02.1; larger NAND/UBI. | General DNS route reported. USB storage and RTL8153 Ethernet reported on this exact model; independent Wi-Fi access is essential during USB-role changes. | Internal `yandex,station-midi` is **not** retail Midi. Gowin framebuffer is 89 logical pixels, 88 visible LEDs, 32-bpp **RGBW**, 356-byte frame; USB audio untested here. |
| [Midi / Cucumber](https://hackalice.me/devices/station-midi/) | A5/A55; kernel 5.4.180 AArch64; Buildroot 2023.11.1; **eMMC**, no UBI. | DNS and AliceBridge root reported. Durable access and OTA behavior on this platform remain listed research topics. | Not AXG station-midi. Shared PDM capture runs at **96 kHz** in the report; other rates can silence Alice. Ring/logo framebuffers exist but channel order is not established. |
| [Street / Mango](https://hackalice.me/devices/station-street/) | A1/A35; kernel 5.15.137 and userspace **ARMv7/armv7l**, Buildroot 2023.11.1; NAND/UBI. | General DNS table reports access, but durable access/OTA specifics remain open. | Battery-powered, RGBW logo and charge segments. AArch64 .yap, YAM and the published AArch64 fastfetch do not run. Do not change charge-controller settings. |
| [Max](https://hackalice.me/devices/station-max/) | Amlogic TV-class/A53; Android 9/YaOS; kernel 4.9.113 AArch64; eMMC; exact SoC unresolved. | Network ADB can be an unprivileged production shell. **No confirmed root route**; AVB, dm-verity and SELinux enforce boundaries. | `yandexstation_2` means Max, not Station 2. Buildroot maind/init.d/rootfs recipes are inapplicable. Profile availability is not root support. |
| [Station first generation / Tulip](https://hackalice.me/devices/station-gen1/) | Allwinner R18/A53; Android 6.0.1; kernel 3.10.65; arm64-v8a ABI; eMMC. | Model-specific AliceBridge boot-image procedure is documented, including original boot backup and post-write hash. Factory reset does **not** restore stock boot. | DNS Buildroot route is excluded. Signed system/dm-verity differs from Buildroot. Poweroff may require physical reconnection; generic PSCI reset may hang. |
| [JBL Link Music with Alice](https://hackalice.me/devices/jbl-link-music/) | MediaTek MT8516, DT says MT8167; A35; kernel 4.4.22 and userspace **ARMv7**; Yocto/systemd; NAND/UBI, A/B boot/rootfs. | Transient service Micro-USB root ADB reported on firmware dated 19 December 2025; data-volume network ADB persistence reported. | Not Buildroot. ARMv7 SSH/Entware need verification; OTA blocking unresolved. Factory storage is not an ordinary application directory. No AArch64 packages. |
| [Yandex Smart Home Hub / Saturn](https://hackalice.me/devices/yandex-hub/) | AXG/A53; kernel 4.9.68 AArch64; Buildroot 2020.02.1; NAND/UBI. | General platform access is reported, but detailed durable-access behavior needs confirmation. | **Not a speaker:** no sound cards or microphones; single status RGB LED and beeper. Zigbee/BT/IR hub; generic Zigbee coordinator reuse is unverified. |
| [Station 3 / Orion](https://hackalice.me/devices/station-3/) | SoC, OS, architecture, storage and LED/audio hardware **unknown**. Network-derived platform identity only. | No root, SSH or ADB route documented. DNS method rejects certificate chains in the report. Glagol handshake does not establish older-command support. | Never infer it is Amlogic or reuse an existing model's factory/flash recipe. No safe documented recovery path. |

## Unsupported entries and profiles

The device index lists **Duo Max** and **Station Pro** as awaiting data, with no
dedicated Markdown page. AliceBridge also lists Module, Module 2, JBL Link Portable,
Elari A98, Prestigio SmartMate, Lightcomm and Linkplay profiles without an established
full run. Additional named OTA profiles have unknown retail mapping or disabled
downgrade. None extends this matrix's support. An available image is not a safe image.

## Conflicts and scope boundaries

- **Dropbear architecture conflict:** [SSH](https://hackalice.me/start/ssh/) describes
  supplied binaries as armhf; [toolchain](https://hackalice.me/internals/toolchain/)
  calls them AArch64. Inspect the actual selected release's ELF metadata and
  dependencies; do not choose a binary from either label alone.
- **TLS claims conflict in scope:** [older pitfalls](https://hackalice.me/internals/gotchas/)
  describes configurable plaintext endpoints and no pinning on a sampled Lite.
  [Plum](https://hackalice.me/start/retail-ca/),
  [Bergamot](https://hackalice.me/start/retail-ca-mini3/) and Orion describe stricter
  trust behavior. Lack of certificate pinning does not mean lack of CA validation.
  Do not weaken TLS as a diagnostic shortcut.
- **General versus local-stand DNS tests:** [root guide](https://hackalice.me/start/root/)
  lists more models in its general support table than its local-stand test table.
  Some tests omit exact firmware. Keep method and build attached to every claim.
- **Android package statements lag access evidence:**
  [packages](https://hackalice.me/start/packages/) groups Android models as lacking
  usable root, while [AliceBridge](https://hackalice.me/start/root-android/) documents
  a first-generation boot route. That does not validate Entware on Android.
- **Mini 3 persistence is not Mini 3 Pro persistence.** Bergamot retail-rootfs changes
  reported to survive reboot can still be overwritten by OTA; Pickle reports
  data-only persistence. Immutable file attributes do not protect a reflashed volume.
- **AirPlay is not transparent by default:** [recipe](https://hackalice.me/recipes/airplay/)
  includes hooks stopping Quasar and separate Station 2 ownership logic. Neither
  is approved implicitly by a request to install AirPlay.

Hardware limits here are community observations, not manufacturer specifications.
No firmware, boot image, kernel module, exploit, certificate or private key is
distributed with this reference.
