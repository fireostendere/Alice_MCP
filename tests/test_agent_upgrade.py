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

from alice_mcp import agent_provision, agent_upgrade, native_transport, pcm_pilot
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
    config["local_pcm"] = {"client": root + "/pcm-player",
                           "alsa_config_sha256": "d" * 64, "library_sha256": "e" * 64}
    old = json.dumps(config).encode()
    hashes = ("ec43eb7ada625616dc04c5c6f58ab619ac1ce758bcec5d5f7a92780c17303806", TTS_CLIENT_SHA256,
              ASR_CLIENT_SHA256, sha(old), *(["c" * 64] * 3),
              sha(agent_provision.start_script("direct").encode()), sha(b"player"))
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
        assert len(args[3:]) == 21
        assert args[5:9] == ["a" * 64, package.sha256, "", "direct"]
        assert int(args[9]) >= 3 * ((len(package.package) + 1023) // 1024) + 4096
        assert int(args[10]) == len(package.package)
        assert tuple(args[11:20]) == package.old_hashes
        assert tuple(args[20:]) == package.hashes
        calls.append(command.command)
        return json.dumps({"outcome": "ready", "state_sha256": "b" * 64})

    controller.runner = runner
    monkeypatch.setattr(agent_upgrade, "_ssh", lambda s: replace(s, native_mcp={},
                                                               trust_sha256="ssh trust"))
    monkeypatch.setattr(native_transport, "_state", lambda *_: "f" * 64)
    package = agent_upgrade.bundle(station, b"new agent", b"player", old, hashes,
                                   "d" * 64, "e" * 64, layout="direct")
    return controller, station, package, calls, clock


def test_w0_baseline_migrates_to_all_without_changing_pcm_or_credentials(setup: Any) -> None:
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
    assert {k: v for k, v in config.items() if k != "allowed_actions"} == {
        k: v for k, v in old.items() if k != "allowed_actions"}
    assert config["allowed_actions"] == "all"
    assert config["preauthorize_all_actions"] is True
    assert package.old_config not in repr(package).encode()
    assert controller.plans == {}


def test_first_combined_baseline_accepts_reviewed_alsa_mapping(setup: Any) -> None:
    _, station, package, _, _ = setup
    config = json.loads(package.old_config)
    config["allowed_actions"] = "all"
    old = json.dumps(config).encode()
    hashes = ("32f709fd80a9769f02bf44473719e403a8ce665234924be44270999e15f46f5e",
              *package.old_hashes[1:3], sha(old), *package.old_hashes[4:])
    volume = {"backend": "alsa_ctl", "card": 0, "control": "Master", "numid": 178,
              "raw_min": 0, "raw_max": 32, "channels": 2, "max_percent": 30}
    updated = agent_upgrade.bundle(station, b"new agent", b"player", old, hashes,
                                   "d" * 64, "e" * 64, layout="direct",
                                   config_updates={"volume": volume})
    agent_upgrade._verify(station, updated)
    for change in ({"control": "Mic Capture"}, {"channels": 3}, {"raw_max": 0},
                   {"numid": True}, {"max_percent": 31}, {"amixer": "/bin/amixer"}):
        with pytest.raises(ValueError):
            agent_upgrade._updates(station, {"volume": {**volume, **change}})


def test_calibrated_baseline_preserves_verified_volume_and_rejects_unknown_fields(
        setup: Any) -> None:
    _, station, package, _, _ = setup
    config = json.loads(package.old_config)
    config.update(allowed_actions="all", volume={"backend": "alsa_ctl", "card": 0,
                  "control": "Master", "numid": 178, "raw_min": 0, "raw_max": 32,
                  "channels": 2, "max_percent": 30})
    for extra in ({}, {"arbitrary": True}, {"volume": {**config["volume"], "control": "Mic"}}):
        old = json.dumps({**config, **extra}).encode()
        hashes = ("19d233ad5dc29b3468d5be985d299e6c99428e13cf5eec9aac66365ab6d68ee3",
                  *package.old_hashes[1:3], sha(old), *package.old_hashes[4:])
        if extra:
            with pytest.raises(SafetyError):
                agent_upgrade.bundle(station, b"new agent", b"player", old, hashes,
                                     "d" * 64, "e" * 64, layout="direct")
        else:
            updated = agent_upgrade.bundle(station, b"new agent", b"player", old, hashes,
                                           "d" * 64, "e" * 64, layout="direct")
            agent_upgrade._verify(station, updated)


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

    def transfer(ssh: Any, command: str, payload: bytes, **kwargs: Any) -> dict[str, Any]:
        payloads.append(payload)
        kwargs["verify_tls"]()
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
    old_hashes = (*previous.old_hashes[:7], sha(b"older installed startup"),
                  previous.old_hashes[-1])
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
        value["allowed_actions"] = ["speech.say"]
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
                                    "legacy_all", "legacy_missing_actions",
                                    "station", "model", "path", "listen", "unknown_key",
                                    "old_agent", "original_agent", "pcm_pin", "tts", "asr",
                                    "hash_count", "hash_format"])
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
    elif change == "legacy_all":
        value["allowed_actions"] = "all"
    elif change == "legacy_missing_actions":
        value.pop("allowed_actions")
    elif change in {"station", "model", "path", "listen", "unknown_key"}:
        key = {"station": "station_id", "model": "model", "path": "token_file",
               "listen": "listen", "unknown_key": "extra"}[change]
        value[key] = "unapproved"
    elif change in {"old_agent", "tts", "asr"}:
        hashes[{"old_agent": 0, "tts": 1, "asr": 2}[change]] = "f" * 64
    elif change == "original_agent":
        hashes[0] = "6a4bb805d26dea1c144ee0ec310b0776b9deaf673c4dd7e852ae79093cc2db2f"
    elif change == "pcm_pin":
        hashes[-1] = "f" * 64
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

    def transfer(*args: Any, **kwargs: Any) -> dict[str, Any]:
        transfers.append(args)
        kwargs["verify_tls"]()
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
        assert result["production_native_route_verified"] is True
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
    monkeypatch.setattr(agent_provision, "_transfer", lambda *_, **kw: result)
    applied = agent_upgrade.apply(controller, plan["plan_id"], plan["expected_sha256"], package)
    assert applied["outcome"] == "unknown" and applied["do_not_retry"] is True


@pytest.mark.parametrize("outcome", ["blocked", "unknown"])
def test_remote_failure_never_exposes_reason_or_retries(
    setup: Any, monkeypatch: Any, outcome: str,
) -> None:
    controller, _, package, _, _ = setup
    plan = agent_upgrade.preview(controller, "desk", package)
    calls = []

    def transfer(*args: Any, **kwargs: Any) -> dict[str, Any]:
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


def test_migration_fields_are_bound_validated_and_opt_in(setup: Any) -> None:
    _, station, previous, _, _ = setup
    updates = {"media": {"tone": "c" * 64}, "led_map": {"channels": [
        [z, c] for z in range(6) for c in ("red", "green", "blue")]},
        "volume": {"amixer": "/PRIVATE/amixer", "amixer_sha256": "a" * 64,
                   "card": 0, "control": "Master", "max_percent": 30}}
    station = replace(station, controls={**station.controls,
        "local_media": {"tone": {"file": "/PRIVATE/tone.wav", "sha256": "c" * 64}}})
    for change in (None, "unknown", "media", "volume", "led_map"):
        fields = json.loads(json.dumps(updates))
        if change == "unknown":
            fields["shell"] = "PRIVATE_SENTINEL"
        elif change == "media":
            fields["media"]["tone"] = "d" * 64
        elif change == "volume":
            fields["volume"]["control"] = "Capture"
        elif change == "led_map":
            fields["led_map"]["channels"][0] = [7, "red"]
        if change:
            with pytest.raises(SafetyError):
                agent_upgrade.bundle(station, b"new agent", b"player", previous.old_config,
                                     previous.old_hashes, "d" * 64, "e" * 64, layout="direct",
                                     config_updates=fields)
        else:
            package = agent_upgrade.bundle(station, b"new agent", b"player", previous.old_config,
                                           previous.old_hashes, "d" * 64, "e" * 64,
                                           layout="direct", config_updates=fields)
            agent_upgrade._verify(station, package)
            assert package.sha256 != previous.sha256


@pytest.mark.parametrize("phase", ["before_migration", "after_migration", "revoke_during_tls"])
def test_tls_failure_never_retries_or_claims_success(setup: Any, monkeypatch: Any,
                                                   phase: str) -> None:
    controller, _, package, _, _ = setup
    plan = agent_upgrade.preview(controller, "desk", package)
    events = []

    def tls(*_: Any) -> str:
        events.append("tls")
        if phase == "before_migration" or events.count("tls") == 2:
            raise SafetyError("PRIVATE_SENTINEL")
        if phase == "revoke_during_tls":
            controller.writes = False
        return "f" * 64

    def transfer(*_: Any, **kwargs: Any) -> dict[str, Any]:
        events.append("new_binary")
        kwargs["verify_tls"]()
        events.append("migration")
        return {"outcome": "installed", "quasar_changed": False, "autostart_registered": True,
                "package_verified": True, "staging_cleaned": True, "do_not_retry": True}

    monkeypatch.setattr(native_transport, "_state", tls)
    monkeypatch.setattr(agent_provision, "_transfer", transfer)
    result = agent_upgrade.apply(controller, plan["plan_id"], plan["expected_sha256"], package)
    assert result["outcome"] == "unknown" and result["do_not_retry"] is True
    assert "PRIVATE_SENTINEL" not in str(result)
    assert events == (["new_binary", "tls"] if phase != "after_migration"
                      else ["new_binary", "tls", "migration", "tls"])
    assert agent_upgrade.apply(controller, plan["plan_id"], plan["expected_sha256"],
                               package)["outcome"] == "blocked"


@pytest.mark.parametrize("answer", ["UPGRADE", " upgrade ", "", "UPGRADE\n"])
def test_cli_confirmation_uses_shared_confirm_and_consumes_cancellation(
    setup: Any, monkeypatch: Any, answer: str,
) -> None:
    controller, _, package, calls, _ = setup
    from alice_mcp.core import confirm
    words = []

    def check(value: str, word: str) -> Any:
        words.append(word)
        return confirm(value, word)

    monkeypatch.setattr(agent_upgrade, "confirm", check, raising=False)
    monkeypatch.setattr(agent_upgrade, "apply", lambda *a: {"outcome": "installed"})
    result = agent_upgrade.operator_upgrade(controller, "desk", package, ask=lambda _: answer)
    assert words == ["UPGRADE"] and len(calls) == 1
    if answer.strip() != "UPGRADE":
        assert result["outcome"] == "cancelled" and not controller.plans
    else:
        assert result["outcome"] == "installed"


def test_non_tty_cli_can_preview_but_cannot_apply_without_the_python_api(
    setup: Any, tmp_path: Path, monkeypatch: Any, capsys: Any,
) -> None:
    controller, _, package, calls, _ = setup
    inputs = {"agent": b"new agent", "player": b"player", "old-config": package.old_config,
              "old-hashes": json.dumps(dict(zip(agent_upgrade.BASE_NAMES, package.old_hashes,
                                               strict=True))).encode()}
    argv = ["--station", "desk", "--layout", "direct"]
    for name, data in inputs.items():
        path = tmp_path / name
        path.write_bytes(data)
        path.chmod(0o600)
        argv.extend(["--" + name, str(path)])
    monkeypatch.setenv("ALICE_MCP_INVENTORY", "PRIVATE_SENTINEL")
    monkeypatch.setattr(agent_upgrade, "Controller", lambda *_, **kw: controller)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert agent_upgrade.main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "previewed" and len(calls) == 1
    assert "PRIVATE_SENTINEL" not in str(report)
    with pytest.raises(SystemExit):
        agent_upgrade.main([*argv, "--live"])
    assert len(calls) == 1
