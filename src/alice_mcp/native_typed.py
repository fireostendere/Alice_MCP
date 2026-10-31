"""Typed resident equivalents of SSH volume, registered playback and health probes.

Host and resident each enforce their own private policy. Registered media cross
TLS as hash-pinned canonical WAV for the pinned local-PCM player; no file or
executable is uploaded and nothing falls back to SSH.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp import local_tts
from alice_mcp.core import SafetyError, Station, _check_ssh_file

ACTIONS = frozenset({"audio.volume", "audio.play"})
PROBES = frozenset({"storage", "services", "audio"})
SERVICES = ("maind", "quasar_launcher2", "dropbear", "shairport-sync", "updatesd")
MAX_MEDIA_WAV = 44 + 48000 * 2 * 10


def probe_data(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Accept only the fixed typed shape; never relay unexpected resident fields."""
    if name == "storage":
        ok = (set(data) == {"total_kib", "used_kib", "free_kib"}
              and all(rt._integer(data[k], 0, 1 << 50) for k in data)
              and max(data["used_kib"], data["free_kib"]) <= data["total_kib"])
    elif name == "services":
        ok = (set(data) == {*SERVICES, "agent"} and all(type(v) is bool for v in data.values())
              and data["agent"] is True)
    else:
        ready = data.get("volume_control") == "ready"
        ok = (set(data) == {"sound_devices", "volume_control", "volume_percent"}
              and type(data["sound_devices"]) is bool
              and data["volume_control"] in {"ready", "unconfigured", "unavailable", "busy"}
              and (rt._integer(data["volume_percent"], 0, 100) if ready
                   else data["volume_percent"] is None))
    if not ok:
        raise SafetyError("Native probe metadata incompatible")
    return dict(data)


def _media(controls: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    item = controls["local_media"][params["asset"]]
    path = Path(item["file"])
    _check_ssh_file(path)
    with path.open("rb") as source:
        audio = source.read(MAX_MEDIA_WAV + 1)
    if len(audio) > MAX_MEDIA_WAV or hashlib.sha256(audio).hexdigest() != item["sha256"]:
        raise ValueError("asset_mismatch")
    wire = local_tts.wav_parameters(audio)
    if len(audio) - 44 > int.from_bytes(audio[24:28], "little") * 2 * params["duration_seconds"]:
        raise ValueError("asset_too_long")
    return {"asset": params["asset"], "duration_seconds": params["duration_seconds"], **wire}


def parameters(station: Station, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Host-side bounds; the resident independently rechecks its own policy."""
    return check(station.controls, station.model, action, params)


def check(controls: dict[str, Any], model: str, action: str,
          params: dict[str, Any]) -> dict[str, Any]:
    """The same bounds from a private policy alone, e.g. while loading declared job steps."""
    try:
        if action == "audio.volume":
            ceiling = controls.get("max_volume", 30)
            if rt._keys(params, {"percent"}):
                rt._require(rt._integer(params["percent"], 0, ceiling))
            else:
                rt._require(rt._keys(params, {"step"}) and params["step"] in {"quieter", "louder"})
            return dict(params)
        rt._require(action == "audio.play" and model == "station-mini"
                    and rt._keys(params, {"asset", "duration_seconds"})
                    and rt._match(rt.ALIAS, params["asset"])
                    and params["asset"] in controls.get("local_media", {})
                    and rt._integer(params["duration_seconds"], 1, 10))
        _media(controls, params)  # Fail before any resident state is observed.
        return dict(params)
    except (ValueError, TypeError, KeyError, OSError, SafetyError):
        raise ValueError("invalid_parameters") from None


def wire(station: Station, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Re-read registered media at dispatch time; a changed file blocks, never substitutes."""
    try:
        if action == "audio.play":
            return _media(station.controls, params)
        if "step" in params:
            return {"step": params["step"], "max_percent": station.controls.get("max_volume", 30)}
        return dict(params)
    except (ValueError, TypeError, KeyError, OSError, SafetyError):
        raise SafetyError("Registered native media changed or unavailable") from None


def result(station: Station, action: str, raw: dict[str, Any],
           sent: dict[str, Any]) -> dict[str, Any]:
    if raw.get("outcome") == "blocked" and set(raw) <= {"outcome", "error"}:
        return {"outcome": "blocked", "error": "Native station policy or runtime blocked action"}
    if action == "audio.play":
        from alice_mcp.native_transport import _pcm_result

        audio = base64.b64decode(sent["wav_base64"])
        return _pcm_result(raw, expected_frames=(len(audio) - 44) // 2,
                           expected_rate=int.from_bytes(audio[24:28], "little"))
    before, after = raw.get("previous_percent"), raw.get("percent")
    ceiling = station.controls.get("max_volume", 30)
    # ALSA floors to a representable level; the resident verifies its exact raw readback.
    if (set(raw) != {"outcome", "previous_percent", "percent", "quasar_running", "do_not_retry",
                     "evidence_class", "physical_effects_verified"}
        or raw["outcome"] not in {"applied", "unknown"} or not rt._integer(before, 0, 100)
        or after is not None and not rt._integer(after, 0, 100)
        or type(raw["quasar_running"]) is not bool or raw["do_not_retry"] is not True
        or raw["evidence_class"] != "analytical" or raw["physical_effects_verified"] is not False
        or raw["outcome"] == "applied" and (after is None or (
            after > sent["percent"] if "percent" in sent
            else not before < after <= ceiling if sent["step"] == "louder"
            else not after < before or after > ceiling))):
        raise SafetyError("Native volume result unverified; do not retry")
    return dict(raw)
