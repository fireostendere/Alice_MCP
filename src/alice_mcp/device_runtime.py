"""Self-contained, bounded POSIX controls. Sources describe interfaces, not device tests.

No network client, shell, service-stop, arbitrary executable or GPIO write interface.
The host owns authorization, expiry and single-use approval; this runtime rechecks state.
Hash comparisons are optimistic; the helper lock does not lock external writers.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ACTIONS = frozenset({"led.effect", "led.capture", "led.replay", "led.pulse", "led.animate",
                     "led.show",
                     "speech.listen", "speech.say",
                     "audio.volume", "audio.play", "media.upload", "sound.replace",
                     "config.select", "restore"})
PREAUTHORIZED_ACTIONS = frozenset({"led.effect", "audio.volume", "audio.play"})
ALL = "all"
ANIMATION_MODES = frozenset({"blink", "rainbow", "rainbow_swirl"})
# Bounded equivalents of the reference RGB effects: no loop, strobe, GCC/current or shutdown.
SHOW_COLOR_EFFECTS = frozenset({"breathe", "spin_color", "static", "notify"})
SHOW_EFFECTS = SHOW_COLOR_EFFECTS | {"rainbow", "spin", "off", "police", "calibrate"}
# Speed 1-5; >=200 ms frames keep on/off flashing at or below 2.5 Hz (WCAG: three per second).
SHOW_INTERVAL_MS = {1: 500, 2: 400, 3: 300, 4: 250, 5: 200}
SHOW_LEVEL = 16
COLORS = ("red", "green", "blue")
LED_ZONES = {"station-mini": 6, "station-lite": 3, "station-mini3": 6}
HUES = ((16, 0, 0), (16, 6, 0), (16, 16, 0), (6, 16, 0), (0, 16, 0), (0, 16, 6),
        (0, 16, 16), (0, 6, 16), (0, 0, 16), (6, 0, 16), (16, 0, 16), (16, 0, 6))
BREATH = (1, 2, 3, 4, 5, 6, 7, 8, 7, 6, 5, 4, 3, 2)  # Eighths of the requested colour.
SCHEMA = "alice.device.v1"
ALIAS = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
FIELDS = {
    "endpoint": {"uniProxyUrl", "uniProxyQuicUrl", "backendUrl", "base_url", "api_base",
                 "endpoint", "url"},
    "voice": {"voice", "voice_id", "tts_voice", "speaker", "speaker_id"},
    "model": {"model", "model_id", "llm_model", "tts_model"},
}
SLOTS = {"spotter_start", "spotter_cancel", "volume_sound", "notification_sound"}
MODELS = {"station-lite", "station-lite2", "station-mini", "station-mini2", "station-mini3",
          "station-mini3-pro", "station-2", "station-midi", "station-max",
          "station-3", "station-street", "station-gen1", "yandex-hub",
          "jbl-link-music"}
BASE = "/data/opt/alice-mcp"
CAPTURE_BASE = BASE + "/led-captures"
CAPTURE_ROOTS = ("/sys/kernel/tracing", "/sys/kernel/debug/tracing")
CAPTURE_FILTER = (
    "((adapter_nr == 0 && addr == 60) || (adapter_nr == 1 && addr == 31)) && len <= 256"
)
CAPTURE_LIMIT = 1024 * 1024
CAPTURE_MAX_SECONDS = 60
NATIVE = "/system/vendor/quasar/quasar.cfg"
MAX_MEDIA = 49152
ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/data/opt/bin:/opt/bin", "LANG": "C", "LC_ALL": "C",
       "LD_LIBRARY_PATH": "/system/vendor/quasar:/system/vendor/quasar/libs"}


def _require(ok: Any, code: str = "invalid_policy") -> None:
    if not ok:
        raise ValueError(code)


def _match(pattern: Any, value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _integer(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _keys(value: Any, required: set[str],
          optional: set[str] | frozenset[str] = frozenset()) -> bool:
    return type(value) is dict and required <= value.keys() <= required | optional


def _json(data: bytes) -> Any:
    def pairs(items: Any) -> dict[str, Any]:
        result = {}
        for key, value in items:
            _require(key not in result, "invalid_json")
            result[key] = value
        return result
    def constant(_: str) -> Any:
        raise ValueError("invalid_json")
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def _encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 200:
        return False
    parts = value.split("/")
    return value.startswith("/") and all(
        re.fullmatch(r"[A-Za-z0-9_.-]+", p) and p not in {".", ".."} for p in parts[1:])


def _config_path(value: Any) -> bool:
    if value == NATIVE:
        return True
    return (_safe_path(value) and value.startswith("/data/opt/")
            and not value.startswith(BASE + "/")
            and Path(value).suffix in {".json", ".cfg"}
            and not re.search(r"token|secret|credential|password|device|key|startup|init|autorun",
                              value, re.I))


def _endpoint(value: str, insecure: bool) -> bool:
    try:
        url = urlsplit(value)
        return (not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
                and "\\" not in value
                and url.scheme in ({"https", "wss", "http", "ws"} if insecure else {"https", "wss"})
                and bool(url.hostname) and not url.username and not url.password
                and "@" not in url.netloc and not url.query and not url.fragment
                and "?" not in value and "#" not in value and url.port != 0)
    except ValueError:
        return False


def allowed_actions(policy: dict[str, Any]) -> list[str]:
    """Owner default (2026-10-29): "all" or an absent list means every implemented action.

    An empty policy (no controls) still allows nothing; unknown names never match.
    """
    if not policy:
        return []
    actions = policy.get("allowed_actions", ALL)
    return sorted(ACTIONS) if actions == ALL else [a for a in actions if a in ACTIONS]


def allows(policy: dict[str, Any], action: str) -> bool:
    return action in allowed_actions(policy)


def preauthorized_actions(policy: dict[str, Any]) -> list[str]:
    value = policy.get("preauthorized_actions", [])
    allowed = allowed_actions(policy)
    return sorted(PREAUTHORIZED_ACTIONS.intersection(allowed)) if value == ALL else [
        a for a in value if a in allowed]


def validate_policy(policy: dict[str, Any], model: str) -> dict[str, Any]:
    """Validate private operator configuration; never include its values in errors."""
    try:
        _require(model in MODELS)
        if policy == {}:
            return {}
        _require(_keys(policy, {"firmware_sha256"},
                       {"allowed_actions", "max_volume", "allow_rootfs_remount", "media",
                        "configs", "speech_client", "tts_client", "local_tts", "local_media",
                        "led_map", "preauthorized_actions", "voice"}))
        _require(_match(HEX64, policy["firmware_sha256"]))
        actions = policy.get("allowed_actions", ALL)
        _require(actions == ALL or type(actions) is list and len(actions) <= len(ACTIONS)
                 and len(set(actions)) == len(actions)
                 and all(type(a) is str and a in ACTIONS for a in actions))
        actions = allowed_actions(policy)
        preauthorized = policy.get("preauthorized_actions", [])
        _require(preauthorized == ALL or type(preauthorized) is list
                 and len(preauthorized) <= len(PREAUTHORIZED_ACTIONS)
                 and len(set(preauthorized)) == len(preauthorized)
                 and all(type(a) is str and a in PREAUTHORIZED_ACTIONS and a in actions
                         for a in preauthorized))
        preauthorized = preauthorized_actions(policy)
        _require(_integer(policy.get("max_volume", 30), 0, 100))
        _require(type(policy.get("allow_rootfs_remount", False)) is bool)
        result = {"firmware_sha256": policy["firmware_sha256"], "allowed_actions": sorted(actions),
                  "max_volume": policy.get("max_volume", 30),
                  "allow_rootfs_remount": policy.get("allow_rootfs_remount", False),
                   "media": {}, "configs": {}}
        if preauthorized:
            result["preauthorized_actions"] = sorted(preauthorized)
        if "led_map" in policy:
            result["led_map"] = validate_led_map(model, policy["led_map"])
        if "voice" in policy:
            # Host-side artifact paths for voice_service; their pins live in code.
            voice = {"mww_model": "ft", "mode": "either", "autostart": False,
                     **policy["voice"]}
            _require(model == "station-mini"
                     and _keys(voice, {"client", "mww", "libvosk", "vosk_zip", "mww_model",
                                       "mode", "autostart"})
                     and all(_safe_path(voice[k]) for k in ("client", "mww", "libvosk",
                                                            "vosk_zip"))
                     and voice["mww_model"] in ("ft", "old")
                     and voice["mode"] in ("vosk", "mww", "either", "both")
                     and type(voice["autostart"]) is bool)
            result["voice"] = voice
        if "local_tts" in policy:
            local = policy["local_tts"]
            _require(model == "station-mini"
                     and _keys(local, {"model_file", "model_sha256", "config_sha256"})
                     and _safe_path(local["model_file"])
                     and _match(HEX64, local["model_sha256"])
                     and _match(HEX64, local["config_sha256"]))
            result["local_tts"] = dict(local)
        if "local_media" in policy:
            # Host-side WAV files for the resident local-PCM player, never station paths.
            local_media = policy["local_media"]
            _require(type(local_media) is dict and 1 <= len(local_media) <= 32)
            for alias, item in local_media.items():
                _require(_match(ALIAS, alias) and _keys(item, {"file", "sha256"})
                         and _safe_path(item["file"]) and item["file"].endswith(".wav")
                         and _match(HEX64, item["sha256"]))
            result["local_media"] = {alias: dict(item) for alias, item in local_media.items()}
        for client in ("speech_client", "tts_client"):
            if client in policy:
                item = policy[client]
                _require((model == "station-mini"
                          or client == "tts_client" and model == "station-mini3")
                         and _keys(item, {"path", "sha256"})
                         and _safe_path(item["path"]) and _match(HEX64, item["sha256"]))
                result[client] = dict(item)
        for group in ("media", "configs"):
            _require(type(policy.get(group, {})) is dict and len(policy.get(group, {})) <= 32)
            for alias, item in policy.get(group, {}).items():
                _require(_match(ALIAS, alias))
                if group == "media":
                    _require(_keys(item, {"path", "sha256"}))
                    path = item["path"]
                    _require(_safe_path(path) and re.fullmatch(
                        r"/data/sounds/[a-zA-Z0-9_-]+\.(opus|wav|mp3)", path))
                    _require(_match(HEX64, item["sha256"]))
                    result[group][alias] = dict(item)
                    continue
                _require(_keys(item, {"path", "pointer", "kind", "choices"}, {"allow_insecure"}))
                _require(_config_path(item["path"]))
                pointer, kind, choices = item["pointer"], item["kind"], item["choices"]
                _require(type(kind) is str and kind in FIELDS)
                _require(type(pointer) is list and 1 <= len(pointer) <= 16)
                _require(all(isinstance(p, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", p)
                             and not re.search(r"token|secret|password|credential|key", p, re.I)
                             for p in pointer))
                _require(pointer[-1] in FIELDS[kind])
                if item["path"] == NATIVE:
                    _require(kind == "endpoint"
                             and pointer[-1] in {"uniProxyUrl", "uniProxyQuicUrl"})
                _require(type(item.get("allow_insecure", False)) is bool)
                _require(type(choices) is dict and 1 <= len(choices) <= 32)
                for option, value in choices.items():
                    _require(_match(ALIAS, option) and isinstance(value, str)
                             and 1 <= len(value) <= 2048
                             and not any(ord(c) < 32 or ord(c) == 127 for c in value))
                    if kind == "endpoint":
                        _require(_endpoint(value, item.get("allow_insecure", False)))
                result[group][alias] = dict(item, pointer=list(pointer), choices=dict(choices),
                                           allow_insecure=item.get("allow_insecure", False))
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ValueError("invalid_policy") from None


def led_layout(model: str) -> list[list[Any]]:
    """Paper channel map in device write order: [logical zone, colour] per channel.

    Mini 1 is the unqualified SN3236 triplet assumption; Lite is the documented
    micro:{red,green,blue}:ring{1,2,3} node order; Mini 3 is its BGR framebuffer.
    """
    _require(model in LED_ZONES, "unsupported_led")
    if model == "station-lite":
        return [[j % 3, COLORS[j // 3]] for j in range(9)]
    order = (2, 1, 0) if model == "station-mini3" else (0, 1, 2)
    return [[j // 3, COLORS[order[j % 3]]] for j in range(18)]


def validate_led_map(model: str, value: Any) -> dict[str, Any]:
    """Private calibration knob: channel -> zone/colour (or unused) and per-colour gain."""
    layout = led_layout(model)
    _require(_keys(value, set(), {"channels", "gain_percent"}) and value)
    channels = value.get("channels", layout)
    _require(type(channels) is list and len(channels) == len(layout))
    for entry in channels:
        _require(entry is None or type(entry) is list and len(entry) == 2
                 and _integer(entry[0], 0, LED_ZONES[model] - 1)
                 and isinstance(entry[1], str) and entry[1] in COLORS)
    gains = value.get("gain_percent", {})
    _require(type(gains) is dict and all(isinstance(k, str) and k in COLORS
                                         and _integer(v, 0, 100) for k, v in gains.items()))
    return {"channels": [None if e is None else list(e) for e in channels],
            "gain_percent": {c: gains.get(c, 100) for c in COLORS}}


def _show_parameters(params: Any, model: str) -> dict[str, Any]:
    _require(model in LED_ZONES and type(params) is dict and isinstance(params.get("effect"), str)
             and params["effect"] in SHOW_EFFECTS)
    effect = params["effect"]
    required = {"effect", "duration_seconds"}
    if effect in SHOW_COLOR_EFFECTS:
        required |= set(COLORS)
    if effect == "calibrate":
        required.add("channel")
    if model == "station-mini":
        required.add("capture_id")  # Same captured same-boot idle evidence as led.animate.
    _require(_keys(params, required, {"speed"}))
    result = {"speed": 3, **params}
    _require(_integer(result["speed"], 1, 5) and _integer(
        params["duration_seconds"], 1, 3 if effect == "calibrate" else 15))
    _require(all(_integer(result.get(c, 0), 0, SHOW_LEVEL) for c in COLORS))
    if effect == "calibrate":
        _require(_integer(params["channel"], 0, len(led_layout(model)) - 1))
    if "capture_id" in params:
        _require(_match(HEX32, params["capture_id"]))
    return result


def _show_frame(effect: str, i: int, zones: int,
                color: tuple[int, ...]) -> list[tuple[int, ...]]:
    dark = (0, 0, 0)
    if effect == "rainbow":
        return [HUES[i % len(HUES)]] * zones
    if effect == "breathe":
        return [tuple(v * BREATH[i % len(BREATH)] // 8 for v in color)] * zones
    if effect == "notify":
        return [color if i % 2 == 0 else dark] * zones
    if effect == "police":
        red = i % 2 == 0
        lit = range((zones + 1) // 2) if red else range(zones // 2, zones)
        on = (SHOW_LEVEL, 0, 0) if red else (0, 0, SHOW_LEVEL)
        return [on if z in lit else dark for z in range(zones)]
    head = color if effect == "spin_color" else HUES[i * 2 % len(HUES)]
    tail = tuple(v // 3 for v in head)
    return [head if z == i % zones else tail if z == (i - 1) % zones else dark
            for z in range(zones)]


def show_frames(params: dict[str, Any], model: str,
                led_map: dict[str, Any] | None = None) -> tuple[int, list[list[int]]]:
    """Validated parameters -> (frame interval ms, physical channel frames); pure, <=15 s."""
    layout = led_layout(model)
    if params["effect"] == "calibrate":  # Raw physical channel: it measures, so bypasses the map.
        return params["duration_seconds"] * 1000, [
            [SHOW_LEVEL if j == params["channel"] else 0 for j in range(len(layout))]]
    channels = (led_map or {}).get("channels") or layout
    gains = (led_map or {}).get("gain_percent", {})
    zones, total = LED_ZONES[model], params["duration_seconds"] * 1000
    color = tuple(params.get(c, 0) for c in COLORS)
    interval = SHOW_INTERVAL_MS[params["speed"]]
    if params["effect"] in {"static", "off"}:
        interval = total
        logical = [[color if params["effect"] == "static" else (0, 0, 0)] * zones]
    else:
        logical = [_show_frame(params["effect"], i, zones, color)
                   for i in range(max(1, total // interval))]
    return interval, [[0 if e is None else frame[e[0]][COLORS.index(e[1])]
                       * gains.get(e[1], 100) // 100 for e in channels] for frame in logical]


def _media(data: bytes, path: str) -> bool:
    if not 0 < len(data) <= MAX_MEDIA:
        return False
    if path.endswith(".wav"):
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if path.endswith(".opus"):
        return data.startswith(b"OggS") and b"OpusHead" in data[:128]
    return path.endswith(".mp3") and (data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 255 and data[1] & 0xE0 == 0xE0))


def validate_parameters(action: str, params: dict[str, Any], policy: dict[str, Any],
                        model: str) -> dict[str, Any]:
    try:
        _require(model in MODELS and isinstance(action, str) and action in ACTIONS)
        if action == "led.show":
            return _show_parameters(params, model)
        fields = {
            "led.effect": {"red", "green", "blue", "count", "period_ms"},
            "led.capture": {"duration_seconds"},
            "speech.listen": {"duration_seconds"},
            "speech.say": {"text"},
            "led.replay": {"capture_id"},
            "led.pulse": {"capture_id", "channel"},
            "led.animate": {"capture_id", "mode"},
            "audio.volume": {"percent"}, "audio.play": {"asset", "duration_seconds"},
            "media.upload": {"asset", "content_base64"}, "sound.replace": {"asset", "slot"},
            "config.select": {"binding", "option"}, "restore": {"backup_id"},
        }
        _require(_keys(params, fields[action]))
        if action == "led.effect":
            _require(all(_integer(params[c], 0, 32) for c in ("red", "green", "blue")))
            _require(_integer(params["count"], 1, 3) and _integer(params["period_ms"], 500, 1000))
        elif action == "led.capture":
            _require(model == "station-mini"
                     and _integer(params["duration_seconds"], 5, CAPTURE_MAX_SECONDS))
        elif action == "speech.listen":
            _require(model == "station-mini" and "speech_client" in policy
                     and _integer(params["duration_seconds"], 5, 60))
        elif action == "speech.say":
            text = params["text"]
            _require(model in {"station-mini", "station-mini3"} and "tts_client" in policy
                     and isinstance(text, str) and 1 <= len(text) <= 256
                     and len(text.encode("utf-8")) <= 512 and bool(text.strip())
                     and not any(ord(c) < 32 or 127 <= ord(c) <= 159
                                 or 8234 <= ord(c) <= 8238 or 8294 <= ord(c) <= 8297 for c in text))
        elif action == "led.replay":
            _require(model == "station-mini" and _match(HEX32, params["capture_id"]))
        elif action == "led.pulse":
            _require(model == "station-mini" and _match(HEX32, params["capture_id"])
                     and _integer(params["channel"], 0, 2))
        elif action == "led.animate":
            _require(model == "station-mini" and _match(HEX32, params["capture_id"])
                     and isinstance(params["mode"], str) and params["mode"] in ANIMATION_MODES)
        elif action == "audio.volume":
            _require(_integer(params["percent"], 0, policy.get("max_volume", 30)))
        elif action in {"media.upload", "audio.play", "sound.replace"}:
            alias = params["asset"]
            _require(_match(ALIAS, alias) and alias in policy.get("media", {}))
            if action == "audio.play":
                _require(_integer(params["duration_seconds"], 1, 10))
            elif action == "sound.replace":
                _require(isinstance(params["slot"], str) and params["slot"] in SLOTS)
                _require(policy["media"][alias]["path"].endswith(".opus"))
            else:
                text = params["content_base64"]
                _require(isinstance(text, str) and len(text) <= 65536)
                data = base64.b64decode(text, validate=True)
                _require(_media(data, policy["media"][alias]["path"]))
                _require(_sha(data) == policy["media"][alias]["sha256"])
        elif action == "config.select":
            _require(_match(ALIAS, params["binding"]) and _match(ALIAS, params["option"]))
            _require(params["binding"] in policy.get("configs", {}))
            _require(params["option"] in policy["configs"][params["binding"]]["choices"])
        else:
            _require(_match(HEX32, params["backup_id"]))
        return dict(params)
    except (ValueError, TypeError, KeyError, binascii.Error, AttributeError):
        raise ValueError("invalid_parameters") from None


def interfaces(model: str) -> dict[str, Any]:
    """Public reference facts only, never a hardware or firmware attestation."""
    _require(model in MODELS, "unsupported_model")
    source_model = model
    led: dict[str, Any] = {"supported": False, "limitation": "interface_not_established"}
    if model in {"station-lite", "station-mini2"}:
        led = {"supported": True, "interface": "sysfs", "prefix": (
            "mini2" if model == "station-mini2" else "micro"),
               "zones": 6 if model == "station-mini2" else 3,
               "attributes": ["brightness", "max_brightness", "trigger"]}
    elif model in {"station-mini3", "station-2"}:
        mini = model == "station-mini3"
        led = {"supported": True, "interface": "framebuffer", "path": "/dev/fb0",
               "width": 6 if mini else 89, "height": 1, "bits_per_pixel": 24 if mini else 32,
               "stride": 18 if mini else 356, "channels": "BGR" if mini else "RGBW",
               "last_pixel_unlit": not mini}
    return {"model": model, "evidence_class": "document",
            "evidence": "public_documentation_only", "led": led,
            "gpio": ({"mode": "read_only_facts", "AO": [497, 511], "EE": [411, 496],
                      "consumers": ["pdn", "sdio_wifi", "bt_rfkill", "aw9110_shdn", "3V3 WLAN"],
                      "physical_pin_map": False} if model == "station-lite" else {
                          "mode": "read_only_facts", "line_mapping": "unknown",
                          "device_tree_properties": ["zb-reset-gpios", "zb-boot-gpios"]
                          if model == "station-midi" else []}),
            "audio": {"volume": "runtime_probed_Master", "documented_model": "station-lite",
                      "documented_lite_mixer_raw_range": [0, 255],
                      "documented_lite_hardware_rate_hz": 48000,
                      "audible_success_verified": False},
            "limitations": ["operator_firmware_review_required", "no_native_voice_control",
                            "sound_reload_unknown", "mini1_i2c_unknown", "midi_channels_unknown"],
            "sources": ["https://hackalice.me/devices/" + source_model + "/",
                        "https://hackalice.me/recipes/led/"]}


def _path(root: Path, name: str, kernel: bool = False) -> Path:
    # Kernel names are constructed exclusively from fixed profiles, never request strings.
    _require(_safe_path(name.replace(":", "_")) if kernel else _safe_path(name), "unsafe_path")
    if not kernel and (name == "/data" or name.startswith("/data/")):
        alias = root / "data"
        if alias.is_symlink():
            # The documented stock alias only; never follow an operator-selected symlink.
            _require(os.readlink(alias) in {"/bkupgrade/data", "bkupgrade/data"}, "unsafe_path")
            name = "/bkupgrade/data" + name[len("/data"):]
    path = root / name.lstrip("/")
    if not kernel:
        current = root
        for part in Path(name).parts[1:]:
            current = current / part
            _require(not current.is_symlink(), "unsafe_path")
    return path


def _read(path: Path, limit: int, device: bool = False) -> bytes:
    if device:
        fd = os.open(str(path), os.O_RDONLY)
    else:
        with _parent(path) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(fd)
        _require(device or stat.S_ISREG(info.st_mode), "unsafe_path")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        _require(len(data) <= limit, "size_limit")
        return data
    finally:
        os.close(fd)


def _firmware(root: Path) -> str:
    path = _path(root, "/system/vendor/quasar/maind")
    with _parent(path) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        _require(stat.S_ISREG(os.fstat(fd).st_mode), "unsafe_path")
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=False) as stream:
            total = 0
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                _require(total <= 128 * 1024 * 1024, "size_limit")
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


def _command(root: Path, args: list[str]) -> bytes:
    _require(root == Path("/"), "synthetic_command_disabled")
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=5, check=True,
                            env=ENV, cwd="/system/vendor/quasar")
    _require(len(result.stdout) <= 16384, "size_limit")
    return result.stdout


def _volume(root: Path) -> int:
    text = _command(root, ["amixer", "sget", "Master"])
    values = re.findall(rb"\[(\d{1,3})%\]", text)
    _require(values and len(set(values)) == 1, "unsupported_mixer")
    value = int(values[0])
    _require(0 <= value <= 100, "unsupported_mixer")
    return value


def _asset(root: Path, item: dict[str, Any]) -> bytes:
    data = _read(_path(root, item["path"]), MAX_MEDIA)
    _require(_sha(data) == item["sha256"] and _media(data, item["path"]), "asset_mismatch")
    return data


def _led_state(root: Path, model: str) -> list[dict[str, Any]]:
    profile = interfaces(model)["led"]
    _require(profile["supported"], "unsupported_led")
    nodes = []
    if profile["interface"] == "sysfs":
        for color in ("red", "green", "blue"):
            for ring in range(1, profile["zones"] + 1):
                name = f"/sys/class/leds/{profile['prefix']}:{color}:ring{ring}/"
                maximum = _read(_path(root, name + "max_brightness", True), 32, True)
                trigger_path = _path(root, name + "trigger", True)
                trigger = _read(trigger_path, 4096, True) if trigger_path.exists() else b""
                _require(not trigger or b"[none]" in trigger, "active_trigger")
                raw = _read(_path(root, name + "brightness", True), 32, True)
                _require(raw.strip().isdigit() and maximum.strip().isdigit(), "invalid_led_state")
                _require(0 <= int(raw) <= int(maximum) and int(maximum) >= 32, "invalid_led_state")
                nodes.append({"path": name + "brightness", "data": base64.b64encode(raw).decode(),
                              "maximum": int(maximum), "trigger": _sha(trigger), "color": color})
    else:
        base = "/sys/class/graphics/fb0/"
        for field, expected in (("virtual_size", f"{profile['width']},1"),
                                ("bits_per_pixel", str(profile["bits_per_pixel"])),
                                ("stride", str(profile["stride"]))):
            _require(_read(_path(root, base + field, True), 32, True).strip() == expected.encode(),
                     "unsupported_geometry")
        fd = os.open(str(_path(root, "/dev/fb0", True)), os.O_RDONLY)
        try:
            raw = os.read(fd, profile["stride"])
        finally:
            os.close(fd)
        _require(len(raw) == profile["stride"], "unsupported_geometry")
        nodes.append({"path": "/dev/fb0", "data": base64.b64encode(raw).decode()})
    return nodes


def _manifest(root: Path, backup_id: str, policy: dict[str, Any], model: str) -> dict[str, Any]:
    path = _path(root, BASE + "/backups/" + backup_id + ".json")
    info = path.stat()
    _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
             "invalid_backup")
    item = _json(_read(path, 3 * 1024 * 1024))
    _require(_keys(item, {"schema", "id", "policy_sha256", "model", "target", "original",
                         "mode", "uid", "gid", "post_sha256", "restored"}), "invalid_backup")
    _require(item["schema"] == SCHEMA and item["id"] == backup_id and item["model"] == model
             and item["policy_sha256"] == _sha(_encode(policy)) and item["restored"] is False,
             "invalid_backup")
    allowed = {m["path"] for m in policy.get("media", {}).values()}
    allowed.update(m["path"] for m in policy.get("configs", {}).values())
    allowed.update("/system/vendor/quasar/sounds/common/" + s + ".opus" for s in SLOTS)
    _require(item["target"] in allowed | {"mixer:Master"}, "invalid_backup")
    _require(_match(HEX64, item["post_sha256"]), "invalid_backup")
    _require(type(item["mode"]) is int and 0 <= item["mode"] <= 0o777
             and type(item["uid"]) is int and type(item["gid"]) is int, "invalid_backup")
    _require(item["original"] is None or isinstance(item["original"], str), "invalid_backup")
    if item["original"] is not None:
        _require(len(base64.b64decode(item["original"], validate=True)) <= 1024 * 1024,
                 "invalid_backup")
    return dict(item)


def _snapshot(root: Path, req: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
    policy, action, params = req["policy"], req["action"], req["params"]
    _require(_firmware(root) == policy["firmware_sha256"], "firmware_mismatch")
    state: dict[str, Any] = {"request": req, "firmware": policy["firmware_sha256"]}
    content = None
    if action in {"led.effect", "led.show"}:
        state["led"] = _led_state(root, req["model"])
    elif action == "led.capture":
        state["capture"] = _capture_profile(root)
        _require(not _path(root, CAPTURE_BASE + "/" + req["nonce"] + ".json").exists(),
                 "nonce_used")
    elif action == "audio.volume":
        state.update(target="mixer:Master", volume=_volume(root))
    elif action == "audio.play":
        state["asset"] = _sha(_asset(root, policy["media"][params["asset"]]))
    else:
        if action == "restore":
            manifest = _manifest(root, params["backup_id"], policy, req["model"])
            state["manifest"] = manifest
            target = manifest["target"]
            if target == "mixer:Master":
                state.update(target=target, volume=_volume(root))
                _require(_sha(str(state["volume"]).encode()) == manifest["post_sha256"],
                         "restore_conflict")
                return state, None
            content = (base64.b64decode(manifest["original"], validate=True)
                       if manifest["original"] is not None else None)
        elif action == "config.select":
            binding = policy["configs"][params["binding"]]
            target = binding["path"]
        elif action == "media.upload":
            target = policy["media"][params["asset"]]["path"]
            content = base64.b64decode(params["content_base64"], validate=True)
        else:
            target = "/system/vendor/quasar/sounds/common/" + params["slot"] + ".opus"
            content = _asset(root, policy["media"][params["asset"]])
        path = _path(root, target)
        exists = path.exists()
        _require(exists or action == "media.upload", "missing_target")
        limit = MAX_MEDIA if target.startswith("/data/sounds/") else 1024 * 1024
        raw = _read(path, limit) if exists else None
        info = path.stat() if exists else None
        _require(info is None or not info.st_mode & 0o7111, "unsafe_permissions")
        state.update(target=target, original=base64.b64encode(raw).decode() if raw is not None
                     else None, mode=stat.S_IMODE(info.st_mode) if info else 0o600,
                     uid=info.st_uid if info else os.geteuid(),
                      gid=info.st_gid if info else os.getegid())
        state["resolved_target"] = str(path.relative_to(root))
        if action == "config.select":
            document = _json(raw or b"")
            parent = document
            for key in binding["pointer"][:-1]:
                _require(type(parent) is dict and key in parent, "missing_field")
                parent = parent[key]
            leaf = binding["pointer"][-1]
            _require(type(parent) is dict and type(parent.get(leaf)) is str, "missing_field")
            parent[leaf] = binding["choices"][params["option"]]
            content = _encode(document)
            _require(len(content) <= 1024 * 1024, "size_limit")
        if action == "restore":
            _require(raw is not None and _sha(raw) == manifest["post_sha256"], "restore_conflict")
    return state, content


def _private_dir(root: Path, name: str) -> Path:
    path = _path(root, name)
    with _parent(path) as parent:
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
                     "unsafe_backup_directory")
        finally:
            os.close(fd)
    return path


@contextlib.contextmanager
def _parent(path: Path) -> Any:
    """Pin each non-kernel directory using no-follow descriptors, including at write time."""
    _require(path.is_absolute() and ".." not in path.parts, "unsafe_path")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory
    finally:
        os.close(directory)


def _assert_target(parent: int, name: str, expected: dict[str, Any]) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        _require(expected["original"] is None, "state_conflict")
        return
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and expected["original"] is not None, "state_conflict")
        with os.fdopen(fd, "rb", closefd=False) as source:
            data = source.read(1024 * 1024 + 1)
        _require(base64.b64encode(data).decode() == expected["original"]
                 and (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
                 == (expected["mode"], expected["uid"], expected["gid"]), "state_conflict")
    finally:
        os.close(fd)


def _atomic(path: Path, data: bytes, mode: int, uid: int, gid: int,
            expected: dict[str, Any] | None = None) -> None:
    # ponytail: optimistic comparison, not kernel CAS; all writers need a transactional
    # backend/shared lock for a hard guarantee against external check/commit races.
    with _parent(path) as parent:
        try:
            existing = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            _require(stat.S_ISREG(existing.st_mode), "unsafe_path")
        except FileNotFoundError:
            pass
        temp = "." + path.name + "." + os.urandom(8).hex()
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fchown(fd, uid, gid)
                os.fchmod(fd, mode)
                os.fsync(fd)
            if expected is not None:
                _assert_target(parent, path.name, expected)
            os.replace(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(fd)
            try:
                os.unlink(temp, dir_fd=parent)
            except FileNotFoundError:
                pass


def _save_manifest(root: Path, manifest: dict[str, Any]) -> None:
    path = _path(root, BASE + "/backups/" + manifest["id"] + ".json")
    _atomic(path, _encode(manifest), 0o600, os.geteuid(), os.getegid())


@contextlib.contextmanager
def _signals() -> Any:
    def interrupt(signum: int, frame: Any) -> None:
        raise InterruptedError("interrupted")
    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[number] = signal.signal(number, interrupt)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _node_write(root: Path, name: str, data: bytes) -> None:
    path = _path(root, name, True)
    fd = os.open(str(path), os.O_WRONLY)
    try:
        if root != Path("/") and stat.S_ISREG(os.fstat(fd).st_mode):
            # Synthetic sysfs emulator only; never truncate actual kernel nodes.
            os.ftruncate(fd, 0)
        _require(os.write(fd, data) == len(data), "short_write")
    finally:
        os.close(fd)


def _effect(root: Path, req: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    params = req["params"]
    result = {"status": "unknown", "restoration_verified": False}
    with _signals():
        try:
            profile = interfaces(req["model"])["led"]
            for _ in range(params["count"]):
                for node in state["led"]:
                    if "color" in node:
                        data = str(params[node["color"]]).encode()
                    else:
                        rgb = [params[c] for c in ("red", "green", "blue")]
                        if profile["channels"] == "BGR":
                            data = bytes(reversed(rgb)) * 6
                        else:
                            data = bytes(rgb + [0]) * 88 + bytes(4)
                    _node_write(root, node["path"], data)
                time.sleep(params["period_ms"] / 1000)
                for node in state["led"]:
                    _node_write(root, node["path"], base64.b64decode(node["data"]))
                time.sleep(params["period_ms"] / 1000)
            result["status"] = "applied"
        except Exception:
            result["error_code"] = "effect_interrupted"
        finally:
            restored = True
            for node in state["led"]:
                try:
                    original = base64.b64decode(node["data"])
                    _node_write(root, node["path"], original)
                    # Framebuffers require a bounded read, not a read-until-EOF.
                    fd = os.open(str(_path(root, node["path"], True)), os.O_RDONLY)
                    try:
                        restored = os.read(fd, len(original)) == original and restored
                    finally:
                        os.close(fd)
                except Exception:
                    restored = False
            result["restoration_verified"] = restored
            if not restored:
                result["status"] = "unknown"
                result["error_code"] = "restoration_failed"
    return result


def _levels(root: Path, nodes: list[dict[str, Any]]) -> list[int]:
    return [int(_read(_path(root, n["path"], True), 32, True).strip()) for n in nodes]


def _show(root: Path, req: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Sysfs show with a hard deadline; yields instead of overwriting a foreign writer."""
    interval, frames = show_frames(req["params"], req["model"], req["policy"].get("led_map"))
    nodes = state["led"]
    _require(all("color" in n for n in nodes) and len(nodes) == len(frames[0]), "unsupported_led")
    original = [int(base64.b64decode(n["data"]).strip()) for n in nodes]
    expected = original
    deadline = time.monotonic() + len(frames) * interval / 1000 + 2
    result: dict[str, Any] = {"status": "unknown", "restoration_verified": False,
                              "yielded": False}
    with _signals():
        try:
            for frame in [*frames, None]:
                # Another writer (normally the firmware's mute/listening indication) changed
                # the ring since our last frame: it owns it now; never restore over it.
                if _levels(root, nodes) != expected:
                    result.update(status="applied", yielded=True)
                    break
                if frame is None:
                    result["status"] = "applied"
                    break
                for index, node in enumerate(nodes):  # Device Python 3.8: no zip(strict=).
                    _node_write(root, node["path"], str(frame[index]).encode())
                expected = frame
                time.sleep(interval / 1000)
                _require(time.monotonic() <= deadline, "deadline_exceeded")
        except Exception:
            result.update(status="unknown", error_code="effect_interrupted")
        finally:
            if not result["yielded"]:
                restored = True
                for node in nodes:
                    try:
                        _node_write(root, node["path"], base64.b64decode(node["data"]))
                    except Exception:
                        restored = False
                try:
                    restored = restored and _levels(root, nodes) == original
                except Exception:
                    restored = False
                result["restoration_verified"] = restored
                if not restored:
                    result.update(status="unknown", error_code="restoration_failed")
    return result


def _capture_profile(root: Path) -> dict[str, Any]:
    """Metadata only. Never mount tracefs or fall back to the global trace buffer."""
    for address, driver in (("0-003c", "sn3236"), ("1-001f", "ledring")):
        link = _path(root, "/sys/bus/i2c/devices/" + address + "/driver", True)
        _require(link.is_symlink() and Path(os.readlink(link)).name == driver,
                 "unverified_led_address")
    cpus = os.cpu_count() if root == Path("/") else 4
    _require(cpus is not None and 1 <= cpus <= 16, "unsupported_trace_geometry")
    for name in CAPTURE_ROOTS:
        path = _path(root, name)
        if not path.is_dir() or not (path / "available_events").is_file():
            continue
        if root == Path("/"):
            mounts = _read(Path("/proc/mounts"), 65536).splitlines()
            _require(any(len(p) >= 3 and p[1] == name.encode() and p[2] == b"tracefs"
                         or len(p) >= 3 and p[1] == b"/sys/kernel/debug" and p[2] == b"debugfs"
                         and name == "/sys/kernel/debug/tracing"
                         for p in (line.split() for line in mounts)), "tracefs_unmounted")
        events = _read(path / "available_events", 128 * 1024).splitlines()
        _require(b"i2c:i2c_write" in events, "missing_tracepoint")
        fmt = _read(path / "events/i2c/i2c_write/format", 8192)
        for field in (rb"field:\s*int\s+adapter_nr;",
                      rb"field:\s*(?:__u16|u16|unsigned short)\s+addr;",
                      rb"field:\s*(?:__u16|u16|unsigned short)\s+len;",
                      rb"field:\s*__data_loc\s+(?:__u8|u8|unsigned char)\[\]\s+buf;"):
            _require(re.search(field, fmt), "unknown_trace_format")
        instances = path / "instances"
        with _parent(instances / "unused"):
            _require(instances.is_dir() and os.access(instances, os.W_OK), "no_trace_instances")
        return {"trace_root": name, "format_sha256": _sha(fmt), "cpus": cpus}
    raise ValueError("tracefs_unavailable")


def _capture_write(path: Path, data: bytes, synthetic: bool) -> None:
    with _parent(path) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        _require(stat.S_ISREG(os.fstat(fd).st_mode), "unsafe_trace_node")
        if synthetic:
            os.ftruncate(fd, 0)
        _require(os.write(fd, data) == len(data), "short_write")
    finally:
        os.close(fd)


def _capture_create_instance(path: Path) -> tuple[int, int]:
    with _parent(path) as parent:
        os.mkdir(path.name, dir_fd=parent)  # Never reuse or modify another instance.
        info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    _require(stat.S_ISDIR(info.st_mode), "unsafe_trace_instance")
    return info.st_dev, info.st_ino


def _capture_remove_instance(path: Path) -> None:
    with _parent(path) as parent:
        os.rmdir(path.name, dir_fd=parent)  # Tracefs owns its files; never recursively delete.


def _capture_worker_identity(root: Path, pid: int) -> list[Any]:
    _require(type(pid) is int and 0 < pid < 2 ** 31, "invalid_worker")
    fields = _read(_path(root, "/proc/" + str(pid) + "/stat"), 4096).rsplit(b")", 1)[1].split()
    _require(len(fields) > 19 and fields[0] not in {b"Z", b"X"} and fields[19].isdigit(),
             "worker_not_running")
    return [pid, fields[19].decode("ascii")]


def _capture_record_path(root: Path, capture_id: str) -> Path:
    _require(_match(HEX32, capture_id), "invalid_capture")
    return _path(root, CAPTURE_BASE + "/" + capture_id + ".json")


def _capture_save(root: Path, record: dict[str, Any]) -> None:
    _atomic(_capture_record_path(root, record["id"]), _encode(record),
            0o600, os.geteuid(), os.getegid())


def _capture_record(root: Path, req: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    directory = _private_dir(root, CAPTURE_BASE)
    # ponytail: retain at most 16 bounded results; operator archives/removes old results.
    _require(len(list(directory.iterdir())) < 16, "capture_storage_full")
    record = {"id": req["nonce"], "model": req["model"], "status": "starting",
              "policy_sha256": _sha(_encode(validate_policy(req["policy"], req["model"]))),
              "trace_root": state["capture"]["trace_root"], "worker": [], "stamp": [],
              "deadline_ns": time.monotonic_ns() + req["params"]["duration_seconds"] * 10 ** 9,
              "cleanup_verified": False, "records": [], "truncated": False}
    path = _capture_record_path(root, req["nonce"])
    with _parent(path) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
    with os.fdopen(fd, "wb") as output:
        output.write(_encode(record))
    return record


def _capture_instance(root: Path, record: dict[str, Any]) -> Path:
    _require(record["trace_root"] in CAPTURE_ROOTS and _match(HEX32, record["id"]),
             "invalid_capture")
    return _path(root, record["trace_root"] + "/instances/alice-led-" + record["id"])


def _capture_active(root: Path, record: dict[str, Any]) -> bool:
    try:
        instance = _capture_instance(root, record)
        info = instance.lstat()
        _require(stat.S_ISDIR(info.st_mode)
                 and list((info.st_dev, info.st_ino)) == record["stamp"], "instance_changed")
        _require(_capture_worker_identity(root, record["worker"][0]) == record["worker"],
                 "worker_changed")
        for name, expected in (("tracing_on", b"1"), ("current_tracer", b"nop"),
                               ("events/i2c/i2c_write/enable", b"1"),
                               ("events/i2c/i2c_write/filter", CAPTURE_FILTER.encode())):
            _require(_read(instance / name, 1024).strip() == expected, "capture_not_active")
        return True
    except (OSError, ValueError, IndexError, KeyError, TypeError):
        return False


def _capture_parse(raw: bytes) -> tuple[list[dict[str, Any]], bool]:
    records: list[dict[str, Any]] = []
    pattern = re.compile(
        rb"\bi2c_write: i2c-([01]) #\d+ a=([0-9a-f]{3,4}) f=[0-9a-f]{4} "
        rb"l=(\d{1,3}) \[([0-9a-f-]*)\]\s*$")
    for line in raw[:CAPTURE_LIMIT].splitlines():
        match = pattern.search(line)
        if not match:
            continue
        bus, address, length = int(match[1]), int(match[2], 16), int(match[3])
        if (bus, address) not in {(0, 60), (1, 31)} or length > 256:
            continue
        payload = match[4].split(b"-") if match[4] else []
        if len(payload) != length or any(not re.fullmatch(rb"[0-9a-f]{2}", p) for p in payload):
            continue
        if len(records) == 64:
            return records, True
        records.append({"bus": bus, "address": address,
                        "data_hex": b"".join(payload).decode("ascii")})
    return records, len(raw) > CAPTURE_LIMIT


def _capture_report(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    path = _capture_record_path(root, req["nonce"])
    info = path.lstat()
    _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
             "invalid_capture")
    record = _json(_read(path, 65536))
    _require(record["id"] == req["nonce"] and record["model"] == req["model"]
             and record["policy_sha256"] == _sha(_encode(validate_policy(
                 req["policy"], req["model"]))), "capture_policy_changed")
    _require(type(record["deadline_ns"]) is int, "invalid_capture")
    remaining = max(0, min(CAPTURE_MAX_SECONDS,
                           (record["deadline_ns"] - time.monotonic_ns()) // 10 ** 9))
    active = record["status"] == "capturing" and remaining >= 2 and _capture_active(root, record)
    complete = (record["status"] == "complete" and record["cleanup_verified"] is True
                and not _capture_instance(root, record).exists())
    return {"outcome": "capturing" if active else "complete" if complete else "unknown",
            "capture_id": record["id"], "capture_ready": active,
            "remaining_seconds": remaining if active else 0,
            "cleanup_verified": record["cleanup_verified"] is True,
            "records": record["records"] if complete else [],
            "truncated": record["truncated"] is True}


def _capture_worker(root: Path, req: dict[str, Any], record: dict[str, Any]) -> None:
    instance = _capture_instance(root, record)
    event = instance / "events/i2c/i2c_write"
    stamp: tuple[int, int] | None = None
    completed = False
    previous = signal.getsignal(signal.SIGALRM)

    def deadline(signum: int, frame: Any) -> None:
        raise TimeoutError("capture_deadline")

    try:
        with _signals():
            signal.signal(signal.SIGALRM, deadline)
            remaining = (record["deadline_ns"] - time.monotonic_ns()) / 10 ** 9
            _require(0 < remaining <= CAPTURE_MAX_SECONDS, "capture_expired")
            signal.setitimer(signal.ITIMER_REAL, remaining)
            record["worker"] = _capture_worker_identity(root, os.getpid())
            stamp = _capture_create_instance(instance)
            record["stamp"] = list(stamp)
            synthetic = root != Path("/")
            _capture_write(instance / "tracing_on", b"0", synthetic)
            _capture_write(instance / "events/enable", b"0", synthetic)
            _require(_read(instance / "current_tracer", 128).strip() == b"nop",
                     "unexpected_tracer")
            _capture_write(instance / "buffer_size_kb", b"16", synthetic)
            _capture_write(event / "filter", CAPTURE_FILTER.encode(), synthetic)
            _require(_read(event / "filter", 1024).strip() == CAPTURE_FILTER.encode(),
                     "filter_not_installed")
            _capture_write(event / "enable", b"1", synthetic)
            _capture_write(instance / "tracing_on", b"1", synthetic)
            _require(_capture_active(root, record), "capture_not_active")
            record["status"] = "capturing"
            _capture_save(root, record)
            time.sleep(max(0, (record["deadline_ns"] - time.monotonic_ns()) / 10 ** 9))
            completed = True
    except TimeoutError:
        completed = record["status"] == "capturing"
    except BaseException:
        pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if stamp is not None:
            try:
                info = instance.lstat()
                _require(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == stamp,
                         "instance_changed")
                try:
                    _capture_write(instance / "tracing_on", b"0", root != Path("/"))
                    _capture_write(event / "enable", b"0", root != Path("/"))
                    _require(_read(instance / "tracing_on", 32).strip() == b"0"
                             and _read(event / "enable", 32).strip() == b"0", "stop_failed")
                    if completed:
                        with _parent(instance / "trace") as parent:
                            fd = os.open("trace", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                        with os.fdopen(fd, "rb") as trace:
                            record["records"], record["truncated"] = _capture_parse(
                                trace.read(CAPTURE_LIMIT + 1))
                except BaseException:
                    completed = False
                finally:
                    _capture_remove_instance(instance)
                    record["cleanup_verified"] = not instance.exists()
            except BaseException:
                completed = False
        record["status"] = "complete" if completed and record["cleanup_verified"] else "unknown"
        try:
            _capture_save(root, record)
        except Exception:
            pass  # Live status checks must fail closed if the final record cannot be saved.


def _start_capture(root: Path, req: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    record = _capture_record(root, req, state)
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            os.umask(0o077)
            with open(os.devnull, "r+b", buffering=0) as null:
                for fd in (0, 1, 2):
                    os.dup2(null.fileno(), fd)
            # Inherits the control lock; does not inherit the SSH terminal/session.
            _capture_worker(root, req, record)
        finally:
            os._exit(0)
    until = time.monotonic() + 2
    while time.monotonic() < until:
        result = _capture_report(root, req)
        if result["capture_ready"] or os.waitpid(pid, os.WNOHANG)[0]:
            return result
        time.sleep(0.025)
    return _capture_report(root, req)  # Unknown is not permission to restart the capture.


def _play(root: Path, req: dict[str, Any]) -> dict[str, Any]:
    _require(root == Path("/"), "synthetic_command_disabled")
    item = req["policy"]["media"][req["params"]["asset"]]
    data = _asset(root, item)
    with _signals(), tempfile.TemporaryFile(dir=_path(root, BASE)) as media:
        # An unlinked snapshot binds the player to verified bytes, not a mutable asset path.
        media.write(data)
        media.flush()
        media.seek(0)
        pipeline = (f"filesrc location=/proc/self/fd/{media.fileno()}"
                    + " ! decodebin ! audioconvert ! audioresample ! alsasink device=default")
        args = ["/system/vendor/quasar/maind", "--gst-launch", pipeline]
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, cwd="/system/vendor/quasar",
                                   env=ENV, start_new_session=True, pass_fds=(media.fileno(),))
        try:
            # Do not reap the leader before group cleanup: a zombie reserves its PID,
            # and children may still be playing after their leader exits.
            time.sleep(req["params"]["duration_seconds"])
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            code = process.wait(timeout=3)
    return {"status": "applied" if code in (0, -signal.SIGKILL) else "unknown"}


@contextlib.contextmanager
def _writable(root: Path, target: str, policy: dict[str, Any]) -> Any:
    remounted = False
    try:
        if target.startswith("/system/") and root == Path("/"):
            readonly = bool(os.statvfs("/").f_flag & os.ST_RDONLY)
            if readonly:
                _require(policy["allow_rootfs_remount"], "rootfs_readonly")
                # Even a timed-out mount may have succeeded; always attempt to restore ro.
                remounted = True
                _command(root, ["mount", "-o", "remount,rw", "/"])
        yield
    finally:
        if remounted:
            _command(root, ["mount", "-o", "remount,ro", "/"])
            _require(os.statvfs("/").f_flag & os.ST_RDONLY, "remount_restore_failed")


def _mutate(root: Path, req: dict[str, Any], state: dict[str, Any],
            content: bytes | None) -> dict[str, Any]:
    action, params, policy = req["action"], req["params"], req["policy"]
    if action == "led.effect":
        return _effect(root, req, state)
    if action == "led.show":
        return _show(root, req, state)
    if action == "led.capture":
        captured = _start_capture(root, req, state)
        captured["status"] = captured.pop("outcome")
        return captured
    if action == "audio.play":
        return _play(root, req)
    restore = action == "restore"
    if restore:
        manifest = state["manifest"]
    else:
        path = _path(root, BASE + "/backups/" + req["nonce"] + ".json")
        _require(not path.exists(), "nonce_used")
        original = (base64.b64encode(str(state["volume"]).encode()).decode()
                    if action == "audio.volume" else state["original"])
        post = str(params["percent"]).encode() if action == "audio.volume" else content
        manifest = {"schema": SCHEMA, "id": req["nonce"], "policy_sha256": _sha(_encode(policy)),
                    "model": req["model"], "target": state["target"], "original": original,
                    "mode": state.get("mode", 0o600), "uid": state.get("uid", os.geteuid()),
                    "gid": state.get("gid", os.getegid()), "post_sha256": _sha(post or b""),
                    "restored": False}
        _save_manifest(root, manifest)
    target = manifest["target"]
    with _signals(), _writable(root, target, policy):
        if target == "mixer:Master":
            value = int(base64.b64decode(manifest["original"])) if restore else params["percent"]
            _require(_integer(value, 0, 100), "invalid_backup")
            _require(_volume(root) == state["volume"], "state_conflict")
            _command(root, ["amixer", "sset", "Master", str(value) + "%"])
            _require(_volume(root) == value, "verification_failed")
        else:
            if action == "media.upload":
                media_dir = _path(root, "/data/sounds")
                with _parent(media_dir) as directory:
                    try:
                        os.mkdir(media_dir.name, mode=0o700, dir_fd=directory)
                        os.fsync(directory)
                    except FileExistsError:
                        pass
            path = _path(root, target)
            if content is None:
                _require(restore and manifest["original"] is None, "invalid_backup")
                with _parent(path) as directory:
                    _assert_target(directory, path.name, state)
                    _require(stat.S_ISREG(os.stat(path.name, dir_fd=directory,
                                                 follow_symlinks=False).st_mode), "unsafe_path")
                    os.unlink(path.name, dir_fd=directory)
                    os.fsync(directory)
                _require(not path.exists(), "verification_failed")
            else:
                _atomic(path, content, manifest["mode"], manifest["uid"], manifest["gid"], state)
                _require(_sha(_read(path, 1024 * 1024)) == _sha(content), "verification_failed")
    if restore:
        manifest["restored"] = True
        _save_manifest(root, manifest)
    result: dict[str, Any] = {"status": "applied", "backup_id": manifest["id"],
                              "reload_required": action in {"sound.replace", "config.select"}}
    if restore:
        result["restoration_verified"] = True
        result["reload_required"] = target.endswith((".cfg", ".json", ".opus"))
    return result


def dispatch(request: dict[str, Any], root: Path = Path("/")) -> dict[str, Any]:
    """Inspect does no writes. Apply serializes, re-reads and compares before mutation."""
    started = False
    try:
        _require(_keys(request, {"schema", "mode", "model", "policy", "action", "params", "nonce"},
                       {"expected_sha256"}), "invalid_request")
        if request["mode"] == "capture_status":
            _require(request["schema"] == SCHEMA and request["action"] == "led.capture"
                     and request["model"] == "station-mini" and "expected_sha256" not in request
                     and _keys(request["params"], {"capture_id"})
                     and _match(HEX32, request["nonce"])
                     and request["params"]["capture_id"] == request["nonce"], "invalid_request")
            policy = validate_policy(request["policy"], request["model"])
            _require(allows(policy, "led.capture")
                     and _firmware(root) == policy["firmware_sha256"], "capture_disabled")
            return _capture_report(root, request)
        _require(request["schema"] == SCHEMA and request["mode"] in {"inspect", "apply"}
                 and _match(HEX32, request["nonce"]), "invalid_request")
        _require((request["mode"] == "apply" and _match(HEX64, request.get("expected_sha256")))
                 or (request["mode"] == "inspect" and "expected_sha256" not in request),
                 "invalid_request")
        req = {k: v for k, v in request.items() if k != "expected_sha256"}
        req["mode"] = "inspect"
        req["policy"] = validate_policy(req["policy"], req["model"])
        req["params"] = validate_parameters(req["action"], req["params"],
                                            req["policy"], req["model"])
        _require(allows(req["policy"], req["action"]), "action_disabled")
        if request["mode"] == "inspect":
            state, _ = _snapshot(root, req)
            result = {"status": "ready", "state_sha256": _sha(_encode(state))}
        else:
            import fcntl

            # ponytail: global lock; split only if independent controls need concurrency.
            _private_dir(root, BASE)
            _private_dir(root, BASE + "/backups")
            lock = _path(root, BASE + "/control.lock")
            with _parent(lock) as parent:
                fd = os.open(lock.name, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
            try:
                info = os.fstat(fd)
                _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                         and stat.S_IMODE(info.st_mode) == 0o600, "unsafe_lock")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state, content = _snapshot(root, req)
                digest = _sha(_encode(state))
                if digest != request["expected_sha256"]:
                    result = {"status": "conflict", "state_sha256": digest}
                else:
                    started = True
                    result = _mutate(root, req, state, content)
            finally:
                os.close(fd)
        result["outcome"] = result.pop("status")
        return result
    except Exception as error:
        if isinstance(error, ValueError) and error.args in {
            ("restore_conflict",), ("state_conflict",),
        }:
            return {"outcome": "conflict", "error_code": "restore_conflict"}
        return {"outcome": "unknown" if started else "blocked",
                "error_code": "operation_uncertain" if started else "request_blocked"}


def main() -> None:
    result: dict[str, Any] = {"outcome": "blocked",
                               "error_code": "invalid_request"}
    try:
        _require(sys.version_info >= (3, 8), "unsupported_python")
        _require(len(sys.argv) == 2 and len(sys.argv[1]) <= 160000, "invalid_request")
        payload = base64.b64decode(sys.argv[1], validate=True)
        _require(len(payload) <= 120000, "invalid_request")
        result = dispatch(_json(payload))
    except BaseException:
        pass
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
