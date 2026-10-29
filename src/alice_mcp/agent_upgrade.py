"""Operator-only guarded upgrade of an existing pinned Mini 1 installation."""

from __future__ import annotations

import hashlib
import importlib
import io
import ipaddress
import json
import math
import re
import secrets
import shlex
import tarfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from alice_mcp import agent_provision, pcm_pilot
from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller, SafetyError, SSHInput, Station, _unique
from alice_mcp.speech_capture import CLIENT_SHA256 as ASR_CLIENT_SHA256
from alice_mcp.speech_capture import TTS_CLIENT_SHA256
from alice_mcp.vqe_experiment import _ssh

AGENT_SHA256 = "ec43eb7ada625616dc04c5c6f58ab619ac1ce758bcec5d5f7a92780c17303806"
UPGRADE_NAMES = ("agent", "pcm-player", "config.json", "start")
BASE_NAMES = agent_provision.NAMES


@dataclass(frozen=True)
class UpgradeBundle:
    package: bytes = field(repr=False)
    sha256: str
    layout: str
    old_hashes: tuple[str, ...]
    hashes: tuple[str, ...]
    old_config: bytes = field(repr=False)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hex(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def _configuration(station: Station, old: bytes, hashes: tuple[str, ...],
                   layout: str) -> dict[str, Any]:
    root = agent_provision.destination(layout)
    if (station.model != "station-mini" or type(old) is not bytes or len(old) > 65536
        or type(hashes) is not tuple or len(hashes) != len(BASE_NAMES)
        or not all(_hex(h) for h in hashes)
        or hashes[:3] != (agent_provision.AGENT_SHA256, TTS_CLIENT_SHA256, ASR_CLIENT_SHA256)
        or hashes[3] != _sha(old)):
        raise ValueError
    # The reviewed existing startup may predate this source template. Its live
    # digest is preserved/bound by the transaction; the helper never executes it.
    value = json.loads(old, object_pairs_hook=_unique)
    expected = {"station_id": station.id, "model": station.model,
                "certificate_file": root + "/certificate.pem",
                "private_key_file": root + "/private-key.pem", "token_file": root + "/credential",
                "firmware_sha256": station.controls["firmware_sha256"],
                "tts_client": root + "/tts-once", "asr_client": root + "/asr-once",
                "preauthorize_all_actions": True}
    actions = value.get("allowed_actions", rt.ALL) if type(value) is dict else None
    if (type(value) is not dict
        or set(value) - {"allowed_actions"} != set(expected) | {"listen"}
        or any(value[k] != v for k, v in expected.items())
        or not _hex(value["firmware_sha256"])
        or value["preauthorize_all_actions"] is not True
        or actions != rt.ALL and (
            type(actions) is not list
            or any(type(a) is not str or a not in {"speech.say", "speech.listen"}
                   for a in actions)
            or len(set(actions)) != len(actions))
        or not rt.allows(value, "speech.say")
        or type(value["listen"]) is not str):
        raise ValueError
    address = urlsplit("//" + value["listen"])
    ip = ipaddress.ip_address(address.hostname or "")
    if (address.port != 18443 or not ip.is_private or ip.is_unspecified
        or address.username is not None or address.password is not None
        or address.path or address.query or address.fragment):
        raise ValueError
    return dict(value)


def _archive(content: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in UPGRADE_NAMES:
            info = tarfile.TarInfo(name)
            info.size = len(content[name])
            info.mode = 0o600 if name == "config.json" else 0o500
            archive.addfile(info, io.BytesIO(content[name]))
    return output.getvalue()


def _content(station: Station, agent: bytes, player: bytes, old: bytes,
             hashes: tuple[str, ...], alsa: str, library: str, layout: str) -> dict[str, bytes]:
    value = _configuration(station, old, hashes, layout)
    if (type(agent) is not bytes or type(player) is not bytes
        or _sha(agent) != AGENT_SHA256 or _sha(player) != pcm_pilot.PLAYER_SHA256
        or not _hex(alsa) or not _hex(library)):
        raise ValueError
    value["local_pcm"] = {"client": agent_provision.destination(layout) + "/pcm-player",
                          "alsa_config_sha256": alsa, "library_sha256": library}
    return {"agent": agent, "pcm-player": player,
            "config.json": json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=True).encode(),
            "start": agent_provision.start_script(layout).encode()}


def bundle(station: Station, agent: bytes, player: bytes, old_config: bytes,
           old_hashes: tuple[str, ...], alsa_config_sha256: str, library_sha256: str,
           *, layout: str) -> UpgradeBundle:
    try:
        if len(agent) + len(player) > agent_provision.MAX_PACKAGE:
            raise ValueError
        content = _content(station, agent, player, old_config, old_hashes,
                           alsa_config_sha256, library_sha256, layout)
        data = _archive(content)
        if len(data) > agent_provision.MAX_PACKAGE:
            raise ValueError
        return UpgradeBundle(data, _sha(data), layout, old_hashes,
                             tuple(_sha(content[n]) for n in UPGRADE_NAMES), old_config)
    except Exception:
        raise SafetyError("Native-agent upgrade bundle invalid; no transfer") from None


def _verify(station: Station, package: UpgradeBundle) -> None:
    try:
        if (type(package.package) is not bytes or len(package.package) > agent_provision.MAX_PACKAGE
            or _sha(package.package) != package.sha256):
            raise ValueError
        content: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(package.package), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != len(UPGRADE_NAMES):
                raise ValueError
            for member in members:
                if (member.name not in UPGRADE_NAMES or member.name in content
                    or not member.isfile() or member.pax_headers
                    or not 0 <= member.size <= agent_provision.MAX_PACKAGE):
                    raise ValueError
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError
                with stream:
                    content[member.name] = stream.read(agent_provision.MAX_PACKAGE + 1)
        config = json.loads(content["config.json"], object_pairs_hook=_unique)
        pcm = config["local_pcm"]
        expected = _content(station, content["agent"], content["pcm-player"], package.old_config,
                            package.old_hashes, pcm["alsa_config_sha256"],
                            pcm["library_sha256"], package.layout)
        if (content != expected or _archive(expected) != package.package
            or package.hashes != tuple(_sha(content[n]) for n in UPGRADE_NAMES)):
            raise ValueError
    except Exception:
        raise SafetyError("Native-agent upgrade bundle invalid; no transfer") from None


def _script() -> str:
    script = importlib.import_module("alice_mcp.agent_upgrade_script").SCRIPT
    if not isinstance(script, str) or "\0" in script or len(script.encode()) > 120 * 1024:
        raise SafetyError("Upgrade helper unavailable")
    return script


def _command(station: Station, package: UpgradeBundle, nonce: str, script: str,
             expected: str = "") -> str | SSHInput:
    args = ["apply" if expected else "inspect", nonce, station.controls["firmware_sha256"],
            package.sha256, expected, package.layout,
            str(3 * math.ceil(len(package.package) / 1024) + 4096),
            *package.old_hashes, *package.hashes]
    source = script.encode()
    if len(source) + len(package.package) > agent_provision.MAX_PACKAGE:
        raise SafetyError("Native-agent upgrade transfer exceeds reviewed bounds")
    arguments = " ".join(shlex.quote(a) for a in args)
    if not expected:
        return SSHInput("sh -s -- " + arguments, source)
    # Fixed reviewed source, not a shell/upload API. Exact byte reads prevent
    # consuming archive bytes; a sentinel preserves trailing source newlines.
    # Verify source before eval, including truncated/shifted binary streams.
    header = ("LC_ALL=C; export LC_ALL; "
              f"program=$(dd bs=1 count={len(source)}; printf .); program=${{program%.}}; "
              f'[ "${{#program}}" = {len(source)} ] && '
              f'[ "$(printf %s "$program" | sha256sum | cut -d \' \' -f 1)" = "{_sha(source)}" ] '
              '|| exit 1; eval "$program"')
    return "sh -c " + shlex.quote(header) + " sh " + arguments


def _permitted(controller: Controller, station: Station) -> None:
    if (controller.writes is not True or station.preauthorize_all_actions is not True
        or not station.native_mcp or station.model != "station-mini"
        or not rt.allows(station.controls, "speech.say")):
        raise SafetyError("Native-agent upgrade not authorized")


def _trust(station: Station, ssh: Station) -> str:
    return _sha(repr((station, ssh)).encode())


def _inspect(controller: Controller, ssh: Station, command: str | SSHInput) -> str:
    try:
        response = json.loads(controller.runner(ssh, command), object_pairs_hook=_unique)
        if (type(response) is not dict or set(response) != {"outcome", "state_sha256"}
            or response["outcome"] != "ready" or not _hex(response["state_sha256"])):
            raise ValueError
        return str(response["state_sha256"])
    except Exception:
        raise SafetyError("Native-agent upgrade state unavailable or changed") from None


def preview(controller: Controller, station_id: str, package: UpgradeBundle) -> dict[str, Any]:
    station = controller.station(station_id)
    _permitted(controller, station)
    _verify(station, package)
    ssh = _ssh(station)
    script, nonce = _script(), secrets.token_hex(16)
    state = _inspect(controller, ssh, _command(station, package, nonce, script))
    request = {"bundle_sha256": package.sha256, "layout": package.layout,
               "old_hashes": package.old_hashes, "hashes": package.hashes,
               "helper_sha256": _sha(script.encode()), "nonce": nonce,
               "trust_sha256": _trust(station, ssh)}
    digest = _sha(repr((request, state)).encode())
    identifier = controller.store_plan(station, "native_agent.upgrade", digest,
                                       request=request, remote_sha256=state)
    return {"plan_id": identifier, "expected_sha256": digest, "expires_in_seconds": 300,
            "impact": "Upgrade existing reviewed agent and enable local PCM; preserve Quasar, "
                      "credentials, SSH and existing speech permissions."}


def _fresh(controller: Controller, expires: float) -> None:
    now = controller.clock()
    if not math.isfinite(expires) or not math.isfinite(now) or now >= expires:
        raise SafetyError("Native-agent upgrade preview expired")


def apply(controller: Controller, plan_id: str, expected_sha256: str,
          package: UpgradeBundle) -> dict[str, Any]:
    try:
        plan = controller.take_plan(plan_id, expected_sha256)
        with controller._execution_lock:
            station = controller.station(plan.station.id)
            _permitted(controller, station)
            _verify(station, package)
            ssh, script = _ssh(station), _script()
            request = plan.request
            _fresh(controller, plan.expires)
            if (plan.action != "native_agent.upgrade" or station != plan.station
                or request.get("trust_sha256") != _trust(station, ssh)
                or request.get("bundle_sha256") != package.sha256
                or request.get("layout") != package.layout
                or request.get("old_hashes") != package.old_hashes
                or request.get("hashes") != package.hashes
                or request.get("helper_sha256") != _sha(script.encode())
                or not re.fullmatch("[a-f0-9]{32}", request.get("nonce", ""))):
                raise SafetyError("Native-agent upgrade preview changed")
            nonce = request["nonce"]
            state = _inspect(controller, ssh, _command(station, package, nonce, script))
            if state != plan.remote_sha256:
                raise SafetyError("Native-agent upgrade state changed")
            current = controller.station(station.id)
            _permitted(controller, current)
            if request["trust_sha256"] != _trust(current, _ssh(current)):
                raise SafetyError("Native-agent upgrade trust changed")
            _fresh(controller, plan.expires)
            try:
                command = _command(station, package, nonce, script, state)
                if not isinstance(command, str):
                    raise SafetyError("Native-agent upgrade command invalid")
                result = agent_provision._transfer(ssh, command, script.encode() + package.package)
                success = {"outcome": "installed", "quasar_changed": False,
                           "autostart_registered": True, "package_verified": True,
                           "staging_cleaned": True, "do_not_retry": True}
                if (type(result) is dict and result.keys() == success.keys()
                    and all(type(result[k]) is type(v) and result[k] == v
                            for k, v in success.items())):
                    return {**success, "operation": "guarded_upgrade", "local_pcm_enabled": True,
                            "production_native_route_verified": False}
                if type(result) is dict and result.get("outcome") == "blocked":
                    return {"outcome": "blocked", "do_not_retry": True}
            except Exception:
                pass  # An uncertain write is never retried and never exposes transport text.
            return {"outcome": "unknown", "do_not_retry": True,
                    "next_step": "Inspect the existing agent state before any further action"}
    except Exception:
        return {"outcome": "blocked", "error": "Native-agent upgrade preview invalid or changed"}
