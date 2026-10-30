"""Operator-only guarded upgrade of an existing pinned Mini 1 installation."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import ipaddress
import json
import math
import os
import re
import secrets
import shlex
import sys
import tarfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alice_mcp import agent_provision, native_transport, pcm_pilot
from alice_mcp import device_runtime as rt
from alice_mcp.agent_provision import AGENT_SHA256
from alice_mcp.core import (
    Controller,
    SafetyError,
    SSHInput,
    Station,
    _check_ssh_file,
    _unique,
    confirm,
)
from alice_mcp.speech_capture import CLIENT_SHA256 as ASR_CLIENT_SHA256
from alice_mcp.speech_capture import TTS_CLIENT_SHA256
from alice_mcp.vqe_experiment import _ssh

BASE_AGENT_SHA256 = "ec43eb7ada625616dc04c5c6f58ab619ac1ce758bcec5d5f7a92780c17303806"
UPGRADE_NAMES = ("agent", "pcm-player", "config.json", "start")
BASE_NAMES = (*agent_provision.NAMES, "pcm-player")
CONFIG_FIELDS = frozenset({"volume", "media", "led_map"})


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
        or hashes[:3] != (BASE_AGENT_SHA256, TTS_CLIENT_SHA256, ASR_CLIENT_SHA256)
        or hashes[3] != _sha(old) or hashes[-1] != pcm_pilot.PLAYER_SHA256):
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
    # W0 requires its legacy list. "all" is only introduced after the TLS barrier.
    actions = value.get("allowed_actions") if type(value) is dict else None
    if (type(value) is not dict
        or set(value) - {"allowed_actions"} != set(expected) | {"listen", "local_pcm"}
        or any(value[k] != v for k, v in expected.items())
        or not _hex(value["firmware_sha256"])
        or value["preauthorize_all_actions"] is not True
        or type(actions) is not list
        or any(type(a) is not str or a not in {"speech.say", "speech.listen"} for a in actions)
        or len(set(actions)) != len(actions)
        or not rt.allows(value, "speech.say")
        or type(value["listen"]) is not str):
        raise ValueError
    pcm = value["local_pcm"]
    if (not rt._keys(pcm, {"client", "alsa_config_sha256", "library_sha256"})
        or pcm["client"] != root + "/pcm-player"
        or not _hex(pcm["alsa_config_sha256"]) or not _hex(pcm["library_sha256"])):
        raise ValueError
    address = urlsplit("//" + value["listen"])
    ip = ipaddress.ip_address(address.hostname or "")
    if (address.port != 18443 or not ip.is_private or ip.is_unspecified
        or address.username is not None or address.password is not None
        or address.path or address.query or address.fragment):
        raise ValueError
    return dict(value)


def _updates(station: Station, fields: dict[str, Any]) -> dict[str, Any]:
    if type(fields) is not dict or fields.keys() - CONFIG_FIELDS:
        raise ValueError
    result = dict(fields)
    if "volume" in fields:
        v = fields["volume"]
        if (not rt._keys(v, {"amixer", "amixer_sha256", "card", "control", "max_percent"})
            or not rt._safe_path(v["amixer"]) or not _hex(v["amixer_sha256"])
            or not rt._integer(v["card"], 0, 7)
            or not rt._integer(v["max_percent"], 0, station.controls.get("max_volume", 30))
            or type(v["control"]) is not str
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,42}", v["control"])
            or v["control"].strip() != v["control"]
            or any(word in v["control"].lower() for word in
                   ("capture", "mic", "adc", "boost", "gain", "input", "loopback"))):
            raise ValueError
    if "media" in fields:
        media = fields["media"]
        registered = station.controls.get("local_media", {})
        if (type(media) is not dict or not 1 <= len(media) <= 32
            or any(not rt._match(rt.ALIAS, alias) or not _hex(digest)
                   or registered.get(alias, {}).get("sha256") != digest
                   for alias, digest in media.items())):
            raise ValueError
    if "led_map" in fields:
        result["led_map"] = rt.validate_led_map(station.model, fields["led_map"])
    return result


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
              hashes: tuple[str, ...], alsa: str, library: str, layout: str,
              config_updates: dict[str, Any]) -> dict[str, bytes]:
    value = _configuration(station, old, hashes, layout)
    if (type(agent) is not bytes or type(player) is not bytes
        or _sha(agent) != AGENT_SHA256 or _sha(player) != pcm_pilot.PLAYER_SHA256
        or value["local_pcm"]["alsa_config_sha256"] != alsa
        or value["local_pcm"]["library_sha256"] != library):
        raise ValueError
    value.update(_updates(station, config_updates))
    value.update(allowed_actions=rt.ALL, preauthorize_all_actions=True)
    return {"agent": agent, "pcm-player": player,
            "config.json": json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=True).encode(),
            "start": agent_provision.start_script(layout).encode()}


def bundle(station: Station, agent: bytes, player: bytes, old_config: bytes,
           old_hashes: tuple[str, ...], alsa_config_sha256: str, library_sha256: str,
           *, layout: str, config_updates: dict[str, Any] | None = None) -> UpgradeBundle:
    try:
        if len(agent) + len(player) > agent_provision.MAX_PACKAGE:
            raise ValueError
        content = _content(station, agent, player, old_config, old_hashes,
                           alsa_config_sha256, library_sha256, layout,
                           {} if config_updates is None else config_updates)
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
                            pcm["library_sha256"], package.layout,
                            {k: config[k] for k in CONFIG_FIELDS if k in config})
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
            str(len(package.package)), *package.old_hashes, *package.hashes]
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
            "impact": "Upgrade pinned W0, verify new resident over TLS, then migrate to all "
                      "actions and supplied reviewed fields; preserve Quasar, PCM, credentials "
                      "and SSH. No audio, LED or reboot acceptance is performed."}


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

            def verify_tls() -> None:
                current = controller.station(station.id)
                _permitted(controller, current)
                if current != station or _trust(current, _ssh(current)) != request["trust_sha256"]:
                    raise SafetyError("Native-agent upgrade trust changed")
                native_transport._state(controller, current, "speech.say")
                fresh = controller.station(station.id)
                _permitted(controller, fresh)
                if fresh != station or _trust(fresh, _ssh(fresh)) != request["trust_sha256"]:
                    raise SafetyError("Native-agent upgrade trust changed during TLS check")
                _fresh(controller, plan.expires)

            try:
                command = _command(station, package, nonce, script, state)
                if not isinstance(command, str):
                    raise SafetyError("Native-agent upgrade command invalid")
                result = agent_provision._transfer(ssh, command, script.encode() + package.package,
                                                   verify_tls=verify_tls)
                success = {"outcome": "installed", "quasar_changed": False,
                           "autostart_registered": True, "package_verified": True,
                           "staging_cleaned": True, "do_not_retry": True}
                if (type(result) is dict and result.keys() == success.keys()
                    and all(type(result[k]) is type(v) and result[k] == v
                            for k, v in success.items())):
                    verify_tls()
                    return {**success, "operation": "guarded_upgrade", "local_pcm_enabled": True,
                            "production_native_route_verified": True}
                if type(result) is dict and result.get("outcome") == "blocked":
                    return {"outcome": "blocked", "do_not_retry": True}
            except Exception:
                pass  # An uncertain write is never retried and never exposes transport text.
            return {"outcome": "unknown", "do_not_retry": True,
                    "next_step": "Inspect the existing agent state before any further action"}
    except Exception:
        return {"outcome": "blocked", "error": "Native-agent upgrade preview invalid or changed"}


def operator_upgrade(controller: Controller, station_id: str, package: UpgradeBundle, *,
                     ask: Callable[[str], str] = input) -> dict[str, Any]:
    plan = preview(controller, station_id, package)
    try:
        answer = ask("Upgrade W0 once, verify TLS, then migrate policy; preserve Quasar. "
                     "Type UPGRADE: ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if confirm(answer, "UPGRADE") is not None:
        try:
            controller.take_plan(plan["plan_id"], plan["expected_sha256"])
        except SafetyError:
            pass
        return {"outcome": "cancelled", "do_not_retry": True}
    return apply(controller, plan["plan_id"], plan["expected_sha256"], package)


def _read(path: Path, limit: int, *, private: bool = False) -> bytes:
    _check_ssh_file(path, private=private)
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise SafetyError("Upgrade input exceeds reviewed bounds")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded W0 upgrade; preview by default, no retry")
    parser.add_argument("--station", required=True, help="Private station alias")
    parser.add_argument("--agent", required=True, type=Path, help="Pinned combined ARM32 agent")
    parser.add_argument("--player", required=True, type=Path, help="Pinned W0 PCM player")
    parser.add_argument("--old-config", required=True, type=Path, help="Exact private W0 config")
    parser.add_argument("--old-hashes", required=True, type=Path, help="Private SHA256 manifest")
    parser.add_argument("--config-updates", type=Path, help="Reviewed volume/media/led_map JSON")
    parser.add_argument("--layout", required=True, choices=("direct", "bkupgrade"))
    parser.add_argument("--live", action="store_true", help="Ask for UPGRADE, then apply once")
    args = parser.parse_args(argv)
    if args.live and not sys.stdin.isatty():
        parser.error("Live CLI requires a terminal; preauthorized agents use preview()/apply()")
    inventory = os.environ.get("ALICE_MCP_INVENTORY")
    if not inventory:
        parser.error("Set ALICE_MCP_INVENTORY privately")
    try:
        controller = Controller(Path(inventory),
                                writes=os.environ.get("ALICE_MCP_ALLOW_WRITES") == "1")
        station = controller.station(args.station)
        old = _read(args.old_config, 65536, private=True)
        hashes = json.loads(_read(args.old_hashes, 65536, private=True), object_pairs_hook=_unique)
        if type(hashes) is not dict or set(hashes) != set(BASE_NAMES):
            raise ValueError
        pcm = json.loads(old, object_pairs_hook=_unique)["local_pcm"]
        updates = ({} if args.config_updates is None else json.loads(
            _read(args.config_updates, 65536, private=True), object_pairs_hook=_unique))
        package = bundle(station, _read(args.agent, agent_provision.MAX_PACKAGE),
                         _read(args.player, 65536), old, tuple(hashes[n] for n in BASE_NAMES),
                         pcm["alsa_config_sha256"], pcm["library_sha256"], layout=args.layout,
                         config_updates=updates)
        result = (operator_upgrade(controller, args.station, package) if args.live
                  else {"outcome": "previewed", **preview(controller, args.station, package)})
    except Exception:
        result = {"outcome": "blocked", "error": "Upgrade inputs or preview unavailable; no retry"}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["outcome"] in {"installed", "previewed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
