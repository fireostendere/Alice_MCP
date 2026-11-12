"""Model registry: one profile per retail device. Reference data, not hardware detection.

This is the scaffold to fill when bringing a model up (docs/MODELS.md): `os` selects the
adapter family, `dt` lets the read-only `identify` probe recognise the board, `todo` names
what still blocks an action. Which actions have an adapter is derived from the real gates
in `device_runtime`, never restated here. Host-only: the station helper must stay small.
"""

from __future__ import annotations

from typing import Any

from alice_mcp import device_runtime

DEVICES = "https://hackalice.me/devices/"
# Buildroot actions with no model gate: availability is probed on the station at preview.
GENERIC_ACTIONS = frozenset({"audio.volume", "audio.play", "media.upload", "sound.replace",
                             "config.select", "restore"})
SOUND_ACTIONS = frozenset({"audio.volume", "audio.play", "sound.replace", "speech.say",
                           "speech.listen", "speech.openai", "quasar.volume",
                           "alice.activation"})


def _profile(title: str, codename: str, os: str, userspace: str, led: str, *todo: str,
             dt: str = "", evidence: str = "none", page: bool = True,
             speaker: bool = True) -> dict[str, Any]:
    return {"title": title, "codename": codename, "os": os, "userspace": userspace, "dt": dt,
            "speaker": speaker, "led": led, "evidence": evidence, "todo": list(todo),
            "source": page}


# `os`: buildroot (native maind; the only family with write adapters), android, yocto, unknown.
# `dt`: device-tree compatible token quoted by the model's wiki page; "" where none is specific.
# `evidence`: live_pilot / partial_live (owner-observed on one unit), synthetic (offline tests
# only), none (no model-specific adapter). Facts are community reports, not our device tests.
PROFILES: dict[str, dict[str, Any]] = {
    "station-lite": _profile(
        "Станция Лайт", "Micro v1.2", "buildroot", "armhf",
        "sysfs micro:{red,green,blue}:ring1-3, three zones",
        "led.effect and led.show have never run on a real Lite",
        "No pinned TTS, ASR or Quasar IPC client build for this firmware",
        dt="yandex,station-micro", evidence="synthetic"),
    "fiero-hi": _profile(
        "Fiero Hi", "Micro v1.2 (blackthorn)", "buildroot", "armhf",
        "sysfs micro:{red,green,blue}:ring1-3, three zones (the Lite adapter)",
        "Shares Lite's board, device-tree token and LED adapter; nothing has run on a real "
        "Fiero Hi",
        "identify cannot tell it from Lite: the inventory model is the owner's statement",
        dt="yandex,station-micro", evidence="synthetic"),
    "station-lite2": _profile(
        "Станция Лайт 2", "Plum", "buildroot", "armhf",
        "sysfs plum:{red,green,blue}:ring1-3 driven by an active led_ring_fb trigger",
        "The sysfs LED adapter refuses nodes with an active trigger; a Plum adapter must "
        "restore the trigger last or Alice's own animation stays invisible",
        dt="yandex,station-lite2"),
    "station-mini": _profile(
        "Станция Мини", "Mini 1", "buildroot", "armhf",
        "I2C SN3236 ring at bus 0 / 0x3c, six zones, write-only PWM (no readback)",
        "The pilot model: every pinned client, the resident agent and voice are built for it",
        "Its device tree carries no model-specific token, so identify cannot confirm it",
        "A LinkPlay board variant exists upstream and is untested here",
        "Stop-phrase ring colour and reboot persistence remain unqualified",
        evidence="live_pilot"),
    "station-mini2": _profile(
        "Станция Мини 2", "Mini 2", "buildroot", "armhf",
        "sysfs mini2:{red,green,blue}:ring1-6, six zones, plus white clock segments",
        "led.show: add the six-zone sysfs order to LED_ZONES and led_layout",
        "Clock segments are redrawn by firmware; the six-column .led format is unconfirmed",
        dt="yandex,station-mini2", evidence="synthetic"),
    "station-mini3": _profile(
        "Станция Мини 3", "Bergamot", "buildroot", "armhf",
        "framebuffer /dev/fb0, 6x1, 24 bpp BGR, 18 bytes; optional white clock",
        "speech.say was audible on one pilot; completion events are unverified",
        "led.show is source-tested, not live-accepted; the clock has no adapter",
        "No ASR, Quasar IPC, live-voice or resident-agent build for Bergamot",
        dt="yandex,bergamot", evidence="partial_live"),
    "station-mini3-pro": _profile(
        "Станция Мини 3 Про", "Pickle", "buildroot", "aarch64",
        "sysfs pickle:{red,green,blue}:ring0-15 (ledfb-ring trigger), logo0-5, white clock",
        "Root is reported only on one factory image; later builds persist only /data",
        "Kernel 5.4 / Buildroot 2023.11: verify the ABI before reusing any pinned ARM32 client",
        "Opening the loopback capture device while Quasar holds it crashes its platform role",
        dt="yandex,pickle"),
    "station-midi": _profile(
        "Станция Миди", "Cucumber", "buildroot", "aarch64",
        "sysfs cucumber:* ring0-23 / logo0-5 / clock; /dev/fb0 ring 24x1 and /dev/fb1 logo "
        "6x1 at 24 bpp, byte order not investigated",
        "Shared loopback capture runs at 96 kHz: opening it at another rate silences Alice",
        "eMMC without UBI: storage and persistence notes for AXG do not apply",
        dt="yandex,cucumber"),
    "station-2": _profile(
        "Станция 2", "device-tree station-midi", "buildroot", "armhf",
        "framebuffer /dev/fb0, 89x1 (88 visible), 32 bpp RGBW, 356 bytes",
        "led.effect is synthetic only; led.show needs an RGBW layout",
        "Device-tree name yandex,station-midi is this model, not retail Midi",
        dt="yandex,station-midi", evidence="synthetic"),
    "station-3": _profile(
        "Станция 3", "Orion", "buildroot", "aarch64",
        "sysfs orion:* ring0-35 and ambilight; /dev/fb2 ring 36x1 24 bpp, /dev/fb0 ambilight "
        "12x1 48 bpp, /dev/fb1 display 21x12",
        "The framebuffer LED adapter assumes the ring is /dev/fb0; here that is the "
        "ambilight, so no LED adapter applies until one targets /dev/fb2",
        "Userspace bitness and the framebuffer byte order are not stated upstream",
        dt="yandex,orion"),
    "station-street": _profile(
        "Станция Стрит", "Mango", "buildroot", "armv7",
        "sysfs mango:{red,green,blue,white}:logo0-2 with ledfb triggers, plus charge segments",
        "32-bit ARMv7 kernel and userspace: AArch64 builds do not run",
        "Battery powered: never touch charge-controller settings",
        "Its device tree carries no model-specific token, so identify cannot confirm it"),
    "yandex-hub": _profile(
        "Умный хаб Яндекса", "Saturn", "buildroot", "armhf",
        "one status LED, sysfs saturn:{red,green,blue}:main",
        "Not a speaker: no sound cards or microphones, so no audio or speech action applies",
        dt="yandex,saturn", speaker=False),
    "station-gen1": _profile(
        "Станция (1-го поколения)", "Tulip", "android", "arm64-v8a",
        "ring of 24 RGB on two I2C drivers; untested upstream, no adapter",
        "Android 6 on Allwinner R18: no adapter family; Buildroot recipes do not apply"),
    "station-max": _profile(
        "Станция Макс", "yandexstation_2", "android", "aarch64",
        "LED screen and ring, protocol unknown; no adapter",
        "Android 9: no adapter family; yandexstation_2 is Max, not Station 2",
        "Reported elevated ADB lasts until reboot: no persistent root or SSH to build on"),
    "station-max-zigbee": _profile(
        "Станция Макс с Zigbee", "yandexstation_2 (YNDX-00052)", "android", "aarch64",
        "framebuffers for a 25x16 screen and a 24x1 ring, sysfs station2:* ring; no adapter",
        "Software identity equals Max: tell them apart by the SKU label or the zigbee "
        "device-tree node"),
    "jbl-link-music": _profile(
        "JBL Link Music", "MT8516", "yocto", "armv7", "no LED class devices; no adapter",
        "Yocto/systemd on ARMv7: no adapter family and no AArch64 packages"),
    "jbl-link-portable": _profile(
        "JBL Link Portable", "MT8516 (aud8516)", "yocto", "armv7",
        "sysfs status LEDs, mapping untested; no adapter",
        "Yocto/systemd on ARMv7: no adapter family",
        "Only the Alice edition is described upstream"),
    "station-duo-max": _profile(
        "Станция Дуо Макс", "?", "unknown", "unknown", "unknown",
        "No upstream data: identify the platform before anything else", page=False),
    "station-pro": _profile(
        "Станция Про", "?", "unknown", "unknown", "unknown",
        "No upstream data: identify the platform before anything else", page=False),
}
for _model, _item in PROFILES.items():
    _item["source"] = DEVICES + (_model + "/" if _item["source"] else "")


def actions(model: str) -> dict[str, str]:
    """adapter: model-specific code; generic: runtime-probed Buildroot path; none: no adapter."""
    profile = PROFILES[model]
    result = {}
    for action in sorted(device_runtime.ACTIONS):
        if action == "led.effect":  # Gated on the station by the interface profile.
            specific = device_runtime.interfaces(model)["led"]["supported"]
        else:
            specific = model in device_runtime.ACTION_MODELS.get(action, ())
        result[action] = (
            "none" if profile["os"] != "buildroot"
            or not profile["speaker"] and action in SOUND_ACTIONS
            else "adapter" if specific else "generic" if action in GENERIC_ACTIONS else "none")
    return result


def supports(model: str, action: str) -> bool:
    return actions(model).get(action, "none") != "none"


def lineup() -> dict[str, dict[str, str]]:
    return {model: {key: profile[key] for key in ("title", "os", "evidence")}
            for model, profile in PROFILES.items()}


def describe(model: str) -> dict[str, Any]:
    """Public reference facts for one model; never a hardware or firmware attestation."""
    facts = device_runtime.interfaces(model)  # Rejects an unknown model.
    profile = PROFILES[model]
    led = dict(facts["led"], description=profile["led"])
    if not led["supported"]:
        # `supported` is only the generic led.effect path; see `actions` for the rest.
        led["limitation"] = "no_generic_led_effect_adapter"
    return {**facts, "led": led, "actions": actions(model),
            "profile": {key: profile[key] for key in (
                "title", "codename", "os", "userspace", "speaker", "evidence", "todo")},
            "limitations": ["operator_firmware_review_required", "no_native_voice_control",
                            "sound_reload_unknown", "declared_model_is_not_detection"],
            "sources": [profile["source"], "https://hackalice.me/recipes/led/"]}
