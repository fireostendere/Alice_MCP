"""Explicit host-only Piper binding after a separately approved resident upgrade.

This is not an MCP tool. Concurrent inventory/trust writers must be idle: the
shared atomic writer compares before replacement but is not an OS-level CAS.
No audio, capture, configuration reload or device write is performed here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp import native_transport
from alice_mcp.core import Controller, SafetyError, Station, _check_ssh_file, _unique


def bind(controller: Controller, expected_station: Station,
         profile: dict[str, str]) -> dict[str, Any]:
    """Add only controls.local_tts, preserving policy and a private create-only backup."""
    try:
        path = controller.inventory
        if (os.name != "posix" or not controller.writes or path is None
            or expected_station.model != "station-mini" or not expected_station.native_mcp
            or not expected_station.preauthorize_all_actions
            or not rt.allows(expected_station.controls, "speech.say")):
            raise ValueError("gates")
        _check_ssh_file(path, private=True)
        if controller.station(expected_station.id) != expected_station:
            raise ValueError("station changed")
        info = path.stat()
        with path.open("rb") as source:
            original = source.read(65537)
        if (len(original) > 65536
            or hashlib.sha256(original).hexdigest() != expected_station.config_sha256):
            raise ValueError("inventory changed")
        data = json.loads(original, object_pairs_hook=_unique)
        entry = next(e for e in data["stations"] if e["id"] == expected_station.id)
        policy = entry["controls"]
        validated = rt.validate_policy(dict(policy, local_tts=profile), expected_station.model)
        local = validated["local_tts"]
        if "local_tts" in policy and policy["local_tts"] != local:
            raise ValueError("binding conflict")

        def state() -> str:
            remote = native_transport.call(expected_station, "get_station_controls",
                                           {"station_id": expected_station.id})
            stamp = remote.get("state_sha256")
            if (remote.get("station_id") != expected_station.id
                or remote.get("model") != expected_station.model
                or remote.get("tts_backend") != "local_pcm"
                or remote.get("state_contract") != "alice.native.state.v1"
                or not isinstance(stamp, str) or not re.fullmatch(r"[a-f0-9]{64}", stamp)):
                raise ValueError("resident mismatch")
            return stamp

        initial_state = state()
        if "local_tts" in policy:
            _check_ssh_file(path, private=True)
            if controller.station(expected_station.id) != expected_station:
                raise ValueError("station changed")
            return {"status": "unchanged"}
        policy["local_tts"] = local
        updated = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
        if len(updated) > 65536:
            raise ValueError("inventory too large")
        _check_ssh_file(path, private=True)
        if not controller.writes or controller.station(expected_station.id) != expected_station:
            raise ValueError("station changed")
        backup_id = secrets.token_hex(16)
        backup = path.with_name(path.name + ".local-pcm-backup-" + backup_id)
        rt._atomic(backup, original, 0o600, info.st_uid, info.st_gid,
                   expected={"original": None})
        if state() != initial_state:
            raise ValueError("resident changed")
        _check_ssh_file(path, private=True)
        if not controller.writes or controller.station(expected_station.id) != expected_station:
            raise ValueError("station changed")
        rt._atomic(path, updated, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid,
                   expected={"original": base64.b64encode(original).decode(),
                             "mode": stat.S_IMODE(info.st_mode),
                             "uid": info.st_uid, "gid": info.st_gid})
        return {"status": "bound", "backup_id": backup_id}
    except (OSError, ValueError, TypeError, KeyError, StopIteration, RecursionError, SafetyError):
        raise SafetyError("Local voice binding failed; inspect locally before retrying") from None
