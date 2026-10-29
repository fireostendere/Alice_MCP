"""Synthetic operator upgrade checks: no device, trust-file or network access."""

import hashlib
import io
import ipaddress
import json
import shlex
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from alice_mcp import agent_provision, agent_upgrade, pcm_pilot
from alice_mcp.core import Controller, SafetyError, SSHInput, Station
from alice_mcp.speech_capture import CLIENT_SHA256 as ASR_CLIENT_SHA256
from alice_mcp.speech_capture import TTS_CLIENT_SHA256


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def setup(monkeypatch: Any) -> Any:
    script = ModuleType("alice_mcp.agent_upgrade_script")
    script.SCRIPT = "synthetic helper"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, script.__name__, script)
    monkeypatch.setattr(agent_upgrade, "AGENT_SHA256", sha(b"new agent"))
    monkeypatch.setattr(pcm_pilot, "PLAYER_SHA256", sha(b"player"))
    station = Station("desk", "station-mini", "station.invalid", 22, "root", Path("unused"),
                      Path("unused"), False, "", "config", "native trust",
                      {"firmware_sha256": "a" * 64, "allowed_actions": ["speech.say"]},
                      preauthorize_all_actions=True, native_mcp={"endpoint": "synthetic"})
    root = agent_provision.destination("direct")
    config = {"station_id": "desk", "model": "station-mini",
              "listen": str(ipaddress.IPv4Address(0x7F000001)) + ":18443",
              "certificate_file": root + "/certificate.pem",
              "private_key_file": root + "/private-key.pem", "token_file": root + "/credential",
              "firmware_sha256": "a" * 64, "tts_client": root + "/tts-once",
              "asr_client": root + "/asr-once", "allowed_actions": ["speech.say"],
              "preauthorize_all_actions": True}
    old = json.dumps(config).encode()
    hashes = (agent_provision.AGENT_SHA256, TTS_CLIENT_SHA256,
              ASR_CLIENT_SHA256, sha(old), *(["c" * 64] * 3),
              sha(agent_provision.start_script("direct").encode()))
    clock = [0.0]
    controller: Any = SimpleNamespace(writes=True, plans={}, _plan_lock=threading.Lock(),
                                      _execution_lock=threading.Lock(), clock=lambda: clock[0],
                                      station=lambda _: station)
    controller.store_plan = lambda *a, **kw: Controller.store_plan(controller, *a, **kw)
    controller.take_plan = lambda *a: Controller.take_plan(controller, *a)
    calls: list[str] = []

    def runner(ssh: Station, command: SSHInput) -> str:
        assert not ssh.native_mcp
        assert isinstance(command, SSHInput) and command.data == b"synthetic helper"
        args = shlex.split(command.command)
        assert args[:4] == ["sh", "-s", "--", "inspect"]
        assert len(args[3:]) == 19
        assert args[5:9] == ["a" * 64, package.sha256, "", "direct"]
        assert int(args[9]) >= 3 * ((len(package.package) + 1023) // 1024) + 4096
        assert tuple(args[10:18]) == package.old_hashes
        assert tuple(args[18:]) == package.hashes
        calls.append(command.command)
        return json.dumps({"outcome": "ready", "state_sha256": "b" * 64})

    controller.runner = runner
    monkeypatch.setattr(agent_upgrade, "_ssh", lambda s: replace(s, native_mcp={},
                                                               trust_sha256="ssh trust"))
    package = agent_upgrade.bundle(station, b"new agent", b"player", old, hashes,
                                   "d" * 64, "e" * 64, layout="direct")
    return controller, station, package, calls, clock


def test_canonical_preserves_policy_and_excludes_secrets(setup: Any) -> None:
    import tarfile
    controller, station, package, _, _ = setup
    agent_upgrade._verify(station, package)
    with tarfile.open(fileobj=io.BytesIO(package.package)) as archive:
        assert tuple(archive.getnames()) == agent_upgrade.UPGRADE_NAMES
        assert all(m.isfile() and m.uid == m.gid == m.mtime == 0
                   for m in archive.getmembers())
        stream = archive.extractfile("config.json")
        assert stream is not None
        config = json.load(stream)
    old = json.loads(package.old_config)
    assert {k: v for k, v in config.items() if k != "local_pcm"} == old
    assert config["allowed_actions"] == ["speech.say"]
    assert package.old_config not in repr(package).encode()
    assert controller.plans == {}


def test_long_helper_uses_short_exec_requests_not_the_ssh_command_argument(setup: Any) -> None:
    _, station, package, _, _ = setup
    script = "# reviewed helper\n" * 1000
    inspect = agent_upgrade._command(station, package, "a" * 32, script)
    assert isinstance(inspect, SSHInput)
    assert len(inspect.command.encode()) <= 4096 and inspect.data == script.encode()
    apply = agent_upgrade._command(station, package, "a" * 32, script, "b" * 64)
    assert isinstance(apply, str) and len(apply.encode()) <= 4096
    assert script not in apply and sha(script.encode()) in apply


def test_apply_transfers_the_exact_helper_prefix_before_the_canonical_package(
    setup: Any, monkeypatch: Any,
) -> None:
    controller, _, package, _, _ = setup
    preview = agent_upgrade.preview(controller, "desk", package)
    payloads = []

    def transfer(ssh: Any, command: str, payload: bytes) -> dict[str, Any]:
        payloads.append(payload)
        return {"outcome": "installed", "quasar_changed": False, "autostart_registered": True,
                "package_verified": True, "staging_cleaned": True, "do_not_retry": True}

    monkeypatch.setattr(agent_provision, "_transfer", transfer)
    result = agent_upgrade.apply(controller, preview["plan_id"], preview["expected_sha256"],
                                  package)
    assert result["outcome"] == "installed"
    assert payloads == [b"synthetic helper" + package.package]


def test_transfer_limit_includes_the_reviewed_helper_not_only_the_archive(
    setup: Any, monkeypatch: Any,
) -> None:
    controller, _, package, calls, _ = setup
    monkeypatch.setattr(agent_provision, "MAX_PACKAGE",
                        len(package.package) + len(b"synthetic helper") - 1)
    with pytest.raises(SafetyError):
        agent_upgrade.preview(controller, "desk", package)
    assert calls == []


def test_upgrade_preserves_a_reviewed_older_startup_instead_of_requiring_new_template(
    setup: Any,
) -> None:
    _, station, previous, _, _ = setup
    old_hashes = (*previous.old_hashes[:7], sha(b"older installed startup"))
    package = agent_upgrade.bundle(station, b"new agent", b"player", previous.old_config,
                                   old_hashes, "d" * 64, "e" * 64, layout="direct")
    agent_upgrade._verify(station, package)
    assert package.old_hashes == old_hashes
    assert package.hashes[-1] == sha(agent_provision.start_script("direct").encode())


@pytest.mark.parametrize("change", ["agent", "config", "old_config", "trailer"])
def test_forged_hashes_do_not_authorize_content(setup: Any, change: str) -> None:
    import tarfile
    controller, _, package, calls, _ = setup
    with tarfile.open(fileobj=io.BytesIO(package.package)) as archive:
        content = {}
        for m in archive.getmembers():
            stream = archive.extractfile(m)
            assert stream is not None
            content[m.name] = stream.read()
    if change == "agent":
        content["agent"] = b"unreviewed"
    elif change == "config":
        value = json.loads(content["config.json"])
        value["allowed_actions"].append("speech.listen")
        content["config.json"] = json.dumps(value).encode()
    elif change == "old_config":
        package = replace(package, old_config=b"{}")
    data = agent_upgrade._archive(content) + (b"extra" if change == "trailer" else b"")
    package = replace(package, package=data, sha256=sha(data),
                      hashes=tuple(sha(content[n]) for n in agent_upgrade.UPGRADE_NAMES))
    with pytest.raises(SafetyError):
        agent_upgrade.preview(controller, "desk", package)
    assert not calls


@pytest.mark.parametrize("change", ["local_pcm", "preauth", "actions", "missing_say",
                                    "station", "model", "path", "listen", "unknown_key",
                                    "old_agent", "tts", "asr", "hash_count", "hash_format"])
def test_rejects_nonbaseline_config_and_pins(setup: Any, change: str) -> None:
    _, station, package, _, _ = setup
    value = json.loads(package.old_config)
    hashes = list(package.old_hashes)
    if change == "local_pcm":
        value["local_pcm"] = {}
    elif change == "preauth":
        value["preauthorize_all_actions"] = 1
    elif change == "actions":
        value["allowed_actions"].append("system.reboot")
    elif change == "missing_say":
        value["allowed_actions"] = ["speech.listen"]
    elif change in {"station", "model", "path", "listen", "unknown_key"}:
        key = {"station": "station_id", "model": "model", "path": "token_file",
               "listen": "listen", "unknown_key": "extra"}[change]
        value[key] = "unapproved"
    elif change in {"old_agent", "tts", "asr"}:
        hashes[{"old_agent": 0, "tts": 1, "asr": 2}[change]] = "f" * 64
    elif change == "hash_count":
        hashes.pop()
    else:
        hashes[4] = "not a digest"
    old = json.dumps(value).encode()
    hashes[3] = sha(old)
    with pytest.raises(SafetyError):
        agent_upgrade.bundle(station, b"new agent", b"player", old, tuple(hashes),
                             "d" * 64, "e" * 64, layout="direct")


@pytest.mark.parametrize("change", ["writes", "preauth", "native", "say", "model"])
def test_preview_requires_existing_authorized_native_route(setup: Any, change: str) -> None:
    controller, station, package, calls, _ = setup
    if change == "writes":
        controller.writes = False
    else:
        fields: dict[str, dict[str, Any]] = {
            "preauth": {"preauthorize_all_actions": False}, "native": {"native_mcp": {}},
            "say": {"controls": {"allowed_actions": []}}, "model": {"model": "other"}}
        controller.station = lambda _: replace(station, **fields[change])
    with pytest.raises(SafetyError):
        agent_upgrade.preview(controller, "desk", package)
    assert not calls


@pytest.mark.parametrize("change", ["ok", "expire", "revoke", "native", "ssh", "state",
                                    "during_inspect", "unknown", "incomplete", "script"])
def test_fresh_bound_consumed_once(setup: Any, monkeypatch: Any, change: str) -> None:
    controller, station, package, calls, clock = setup
    plan = agent_upgrade.preview(controller, "desk", package)
    transfers = []

    def transfer(*args: Any) -> dict[str, Any]:
        transfers.append(args)
        if change == "unknown":
            raise RuntimeError("SECRET_SENTINEL")
        result = {"outcome": "installed", "quasar_changed": False,
                  "autostart_registered": True, "package_verified": True,
                  "staging_cleaned": True, "do_not_retry": True}
        if change == "incomplete":
            result.pop("staging_cleaned")
        return result

    monkeypatch.setattr(agent_provision, "_transfer", transfer)
    if change == "expire":
        clock[0] = 301
    elif change == "revoke":
        controller.station = lambda _: replace(station, preauthorize_all_actions=False)
    elif change == "native":
        controller.station = lambda _: replace(station, trust_sha256="changed")
    elif change == "ssh":
        monkeypatch.setattr(agent_upgrade, "_ssh", lambda s: replace(s, trust_sha256="changed"))
    elif change == "state":
        controller.runner = lambda *_: json.dumps({"outcome": "ready", "state_sha256": "f" * 64})
    elif change == "during_inspect":
        def expire(*args: Any) -> str:
            clock[0] = 301
            return json.dumps({"outcome": "ready", "state_sha256": "b" * 64})
        controller.runner = expire
    elif change == "script":
        monkeypatch.setattr(sys.modules["alice_mcp.agent_upgrade_script"], "SCRIPT", "changed")
    result = agent_upgrade.apply(controller, plan["plan_id"], plan["expected_sha256"], package)
    assert "SECRET_SENTINEL" not in str(result)
    assert result["outcome"] == ("installed" if change == "ok" else
                                 "unknown" if change in {"unknown", "incomplete"} else "blocked")
    if change == "ok":
        assert result["operation"] == "guarded_upgrade"
        assert result["local_pcm_enabled"] is True
        assert result["production_native_route_verified"] is False
    assert len(transfers) == int(change in {"ok", "unknown", "incomplete"})
    assert agent_upgrade.apply(controller, plan["plan_id"], plan["expected_sha256"],
                               package)["outcome"] == "blocked"
    assert len(transfers) <= 1


@pytest.mark.parametrize("flag", ["quasar_changed", "autostart_registered", "package_verified",
                                  "staging_cleaned", "do_not_retry"])
@pytest.mark.parametrize("value", [None, 1, "true"])
def test_installed_flags_must_be_complete_exact_booleans(
    setup: Any, monkeypatch: Any, flag: str, value: Any,
) -> None:
    controller, _, package, _, _ = setup
    plan = agent_upgrade.preview(controller, "desk", package)
    result = {"outcome": "installed", "quasar_changed": False, "autostart_registered": True,
              "package_verified": True, "staging_cleaned": True, "do_not_retry": True}
    if value is None:
        result.pop(flag)
    else:
        result[flag] = value
    monkeypatch.setattr(agent_provision, "_transfer", lambda *_: result)
    applied = agent_upgrade.apply(controller, plan["plan_id"], plan["expected_sha256"], package)
    assert applied["outcome"] == "unknown" and applied["do_not_retry"] is True


@pytest.mark.parametrize("outcome", ["blocked", "unknown"])
def test_remote_failure_never_exposes_reason_or_retries(
    setup: Any, monkeypatch: Any, outcome: str,
) -> None:
    controller, _, package, _, _ = setup
    plan = agent_upgrade.preview(controller, "desk", package)
    calls = []

    def transfer(*args: Any) -> dict[str, Any]:
        calls.append(1)
        ssh, command, payload = args
        assert not ssh.native_mcp and payload == b"synthetic helper" + package.package
        argv = shlex.split(command)
        assert argv[4] == "apply" and argv[8] == "b" * 64
        return {"outcome": outcome, "error_code": "SECRET_SENTINEL"}

    monkeypatch.setattr(agent_provision, "_transfer", transfer)
    applied = agent_upgrade.apply(controller, plan["plan_id"], plan["expected_sha256"], package)
    assert applied["outcome"] == outcome and applied["do_not_retry"] is True
    assert "SECRET_SENTINEL" not in str(applied)
    assert agent_upgrade.apply(controller, plan["plan_id"], plan["expected_sha256"],
                               package)["outcome"] == "blocked"
    assert calls == [1]
