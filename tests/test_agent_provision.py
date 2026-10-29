"""Operator-only native-agent provisioning uses synthetic bundles/transports."""

import hashlib
import io
import ipaddress
import json
import os
import shlex
import shutil
import signal
import ssl
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from alice_mcp import agent_provision
from alice_mcp.core import Controller, SafetyError


@pytest.fixture
def provision(tmp_path: Path, monkeypatch: Any) -> Any:
    directory = tmp_path / "bundle"
    directory.mkdir()
    binaries = {"agent": b"synthetic reviewed native agent", "tts-once": b"synthetic reviewed TTS",
                "asr-once": b"synthetic reviewed ASR"}
    for name, content in binaries.items():
        (directory / name).write_bytes(content)
    monkeypatch.setattr(agent_provision, "AGENT_SHA256",
                        hashlib.sha256(binaries["agent"]).hexdigest())
    monkeypatch.setattr(agent_provision, "TTS_CLIENT_SHA256",
                        hashlib.sha256(binaries["tts-once"]).hexdigest())
    monkeypatch.setattr(agent_provision, "ASR_CLIENT_SHA256",
                        hashlib.sha256(binaries["asr-once"]).hexdigest(), raising=False)
    for name in ("certificate.pem", "private-key.pem"):
        (directory / name).write_bytes(b"synthetic")
        (directory / name).chmod(0o600)
    (directory / "credential").write_bytes(b"a" * 64)
    (directory / "credential").chmod(0o600)
    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", lambda *args: None)
    root = "/data/opt/alice-mcp-agent"
    (directory / "config.json").write_text(json.dumps({
        "station_id": "desk", "model": "station-mini",
        "listen": str(ipaddress.IPv4Address(0x7F000001)) + ":18443",
        "certificate_file": root + "/certificate.pem",
        "private_key_file": root + "/private-key.pem",
        "token_file": root + "/credential", "firmware_sha256": "a" * 64,
        "tts_client": root + "/tts-once", "asr_client": root + "/asr-once",
        "allowed_actions": ["speech.say", "speech.listen"],
        "preauthorize_all_actions": True,
    }))
    (directory / "config.json").chmod(0o600)
    key, hosts = tmp_path / "identity", tmp_path / "hosts"
    key.touch(mode=0o600)
    hosts.touch()
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"stations": [{
        "id": "desk", "model": "station-mini", "host": "station.invalid", "port": 2222,
        "user": "root", "identity_file": str(key), "known_hosts_file": str(hosts),
        "preauthorize_all_actions": True,
        "controls": {"firmware_sha256": "a" * 64, "allowed_actions": ["speech.say"]},
    }]}))
    inventory.chmod(0o600)
    calls = []

    def read_only(station: Any, command: Any) -> str:
        calls.append("inspect")
        assert " inspect " in command
        return '{"outcome":"ready","state_sha256":"' + "b" * 64 + '"}'

    controller = Controller(inventory, writes=True, runner=read_only)
    return controller, directory, calls


def test_bundle_only_contains_pinned_roles_and_fixed_paths(provision: Any) -> None:
    controller, directory, _ = provision
    bundle = agent_provision.bundle(controller.station("desk"), directory, layout="direct")
    with tarfile.open(fileobj=io.BytesIO(bundle.package)) as archive:
        members = archive.getmembers()
        assert {x.name for x in members} == {
            "agent", "tts-once", "asr-once", "config.json", "certificate.pem", "private-key.pem",
            "credential", "start",
        }
        assert all(x.isfile() and x.uid == 0 and x.gid == 0 for x in members)
        assert all(x.mode == (0o500 if x.name in {"agent", "tts-once", "asr-once", "start"}
                             else 0o600)
                   for x in members)
    assert bundle.sha256 == hashlib.sha256(bundle.package).hexdigest()


@pytest.mark.parametrize("change", ["agent", "tts-once", "asr-once", "symlink", "wrong_target",
                                    "config_path"])
def test_changed_or_untrusted_provisioning_payload_is_blocked(provision: Any, change: str) -> None:
    controller, directory, _ = provision
    if change in {"agent", "tts-once", "asr-once"}:
        (directory / change).write_bytes(b"changed")
    elif change == "symlink":
        target = directory / "credential"
        target.rename(directory / "actual")
        target.symlink_to(directory / "actual")
    else:
        path = directory / "config.json"
        value = json.loads(path.read_text())
        value["station_id" if change == "wrong_target" else "tts_client"] = "unapproved"
        path.write_text(json.dumps(value))
    with pytest.raises(SafetyError):
        agent_provision.bundle(controller.station("desk"), directory, layout="direct")


@pytest.mark.parametrize("change", ["none", "expire", "revoke", "bundle", "unknown"])
def test_provisioning_has_bound_fresh_single_use_preview(provision: Any, monkeypatch: Any,
                                                       change: str) -> None:
    controller, directory, calls = provision
    package = agent_provision.bundle(controller.station("desk"), directory, layout="direct")
    preview = agent_provision.preview(controller, "desk", package)
    assert calls == ["inspect"]
    transfers = []

    def transfer(*args: Any) -> dict[str, Any]:
        transfers.append(1)
        if change == "unknown":
            raise SafetyError("SENSITIVE_SENTINEL")
        return {"outcome": "installed", "quasar_changed": False, "autostart_registered": True}

    monkeypatch.setattr(agent_provision, "_transfer", transfer)
    if change == "expire":
        monkeypatch.setattr(controller, "clock", lambda: float("inf"))
    elif change == "revoke":
        value = json.loads(controller.inventory.read_text())
        value["stations"][0]["preauthorize_all_actions"] = False
        controller.inventory.write_text(json.dumps(value))
    elif change == "bundle":
        from dataclasses import replace
        package = replace(package, package=package.package + b"changed")
    result = agent_provision.apply(controller, preview["plan_id"], preview["expected_sha256"],
                                   package)
    assert "SENSITIVE_SENTINEL" not in str(result)
    assert result["outcome"] == ({"none": "installed", "unknown": "unknown"}.get(change, "blocked"))
    assert len(transfers) == (1 if change in {"none", "unknown"} else 0)
    assert agent_provision.apply(controller, preview["plan_id"], preview["expected_sha256"],
                                 package)["outcome"] == "blocked"
    assert len(transfers) <= 1


def test_provisioning_shell_syntax_and_no_quasar_stop() -> None:
    subprocess.run(["sh", "-n"], input=agent_provision.SCRIPT.encode(),
                   check=True, capture_output=True)
    source = agent_provision.SCRIPT + agent_provision.start_script("direct")
    assert "killall" not in source and "S99quasar" not in source
    assert "mount -o" not in source and "LD_PRELOAD" not in source


def _owned_daemon(pid: int, root: Path) -> bool:
    try:
        return Path(f"/proc/{pid}/exe").readlink() == root / "agent"
    except FileNotFoundError:
        return False


@pytest.fixture
def startup(tmp_path: Path) -> Any:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("Synthetic daemon requires an existing host C compiler")
    root = tmp_path / "runtime"
    root.mkdir()
    daemon = tmp_path / "daemon.c"
    daemon.write_text("#include <unistd.h>\n"
                      "int main(void) { alarm(20); for (;;) pause(); }\n")
    subprocess.run([compiler, "-O2", "-Wall", "-Wextra", "-Werror", str(daemon),
                    "-o", str(root / "agent")], capture_output=True, check=True, timeout=30)
    (root / "agent").chmod(0o500)
    script = root / "start"
    script.write_text(agent_provision.start_script("direct").replace(
        "/data/opt/alice-mcp-agent", str(root)))
    tools = tmp_path / "tools"
    tools.mkdir()
    # Delay exec to expose the PID publication race; the compiled daemon has a hard alarm.
    shim = tools / "setsid"
    shim.write_text("#!/bin/sh\nsleep 0.2\n"
                    f"printf '%s\\n' \"$$\" > {root}/launched.$$\n"
                    f"exec {shutil.which('setsid')} \"$1\" 20\n")
    shim.chmod(0o500)
    env = dict(os.environ, PATH=str(tools) + ":" + os.environ["PATH"])

    def run(action: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["sh", str(script), action], env=env,
                              capture_output=True, timeout=5)

    try:
        yield root, run
    finally:
        for record in root.glob("launched.*"):
            pid = int(record.read_text())
            if _owned_daemon(pid, root):
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass  # The bounded synthetic daemon may exit between check and signal.


def test_generated_start_serializes_concurrent_launches(startup: Any) -> None:
    root, run = startup
    barrier = Barrier(2)

    def start() -> subprocess.CompletedProcess[bytes]:
        barrier.wait(timeout=3)
        return run("start")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert any(result.returncode == 0 for result in results)
    records = list(root.glob("launched.*"))
    assert len(records) == 1
    pid = int(records[0].read_text())
    assert (root / "agent.pid").read_text().strip() == str(pid)
    assert _owned_daemon(pid, root)
    assert not (root / ".agent-lock").exists()
    assert run("start").returncode == 0
    assert len(list(root.glob("launched.*"))) == 1
    assert run("status").returncode == 0
    assert run("stop").returncode == 0
    for _ in range(100):
        if not _owned_daemon(pid, root):
            break
        time.sleep(0.01)
    assert not _owned_daemon(pid, root)
    assert run("status").returncode != 0
    assert not (root / "agent.pid").exists()
    assert not (root / ".agent-lock").exists()


@pytest.mark.parametrize("obstacle", ["symlink", "directory", "stale_lock", "lock_symlink",
                                     "unrelated_pid", "failed_daemon"])
def test_generated_start_fails_closed_and_preserves_unrelated_process(
    startup: Any, obstacle: str,
) -> None:
    root, run = startup
    with subprocess.Popen(["sleep", "20"]) as unrelated:
        try:
            target = root / "unrelated"
            target.write_text(str(unrelated.pid) + "\n")
            if obstacle == "symlink":
                (root / "agent.pid").symlink_to(target)
            elif obstacle == "directory":
                (root / "agent.pid").mkdir()
            elif obstacle == "stale_lock":
                (root / ".agent-lock").mkdir()
            elif obstacle == "lock_symlink":
                (root / ".agent-lock").symlink_to(root, target_is_directory=True)
            elif obstacle == "unrelated_pid":
                (root / "agent.pid").write_text(str(unrelated.pid) + "\n")
            else:
                (root / "agent").chmod(0o700)
                shutil.copyfile(shutil.which("false"), root / "agent")
            assert run("stop" if obstacle == "unrelated_pid" else "start").returncode != 0
            assert unrelated.poll() is None
            assert target.read_text() == str(unrelated.pid) + "\n"
            assert (root / ".agent-lock").exists() == (obstacle in {"stale_lock", "lock_symlink"})
            if obstacle == "failed_daemon":
                assert not (root / "agent.pid").exists()
            else:
                assert not list(root.glob("launched.*"))
        finally:
            unrelated.terminate()


def test_generated_stop_cannot_race_start_claim(startup: Any) -> None:
    root, run = startup
    with ThreadPoolExecutor(max_workers=1) as pool:
        starting = pool.submit(run, "start")
        for _ in range(100):
            if (root / ".agent-lock").is_dir():
                break
            time.sleep(0.005)
        assert (root / ".agent-lock").is_dir()
        assert run("stop").returncode != 0
        assert (root / ".agent-lock").is_dir()
        assert starting.result(timeout=5).returncode == 0
    assert run("status").returncode == 0
    assert run("stop").returncode == 0


def test_forged_bundle_cannot_self_authorize_an_unreviewed_agent(provision: Any) -> None:
    from dataclasses import replace
    controller, directory, calls = provision
    package = agent_provision.bundle(controller.station("desk"), directory, layout="direct")
    with tarfile.open(fileobj=io.BytesIO(package.package)) as archive:
        content = {m.name: archive.extractfile(m).read() for m in archive.getmembers()}
    content["agent"] = b"unreviewed"
    forged = agent_provision._archive(content)
    package = replace(package, package=forged, sha256=hashlib.sha256(forged).hexdigest(),
                      hashes=tuple(hashlib.sha256(content[name]).hexdigest()
                                   for name in agent_provision.NAMES))
    with pytest.raises(SafetyError):
        agent_provision.preview(controller, "desk", package)
    assert not calls


@pytest.mark.parametrize("corrupt", [False, True])
def test_bootstrap_file_transaction_and_cleanup_are_bounded(
    provision: Any, tmp_path: Path, corrupt: bool,
) -> None:
    controller, directory, _ = provision
    data, hooks = tmp_path / "data", tmp_path / "hooks"
    (data / "opt").mkdir(parents=True)
    hooks.mkdir()
    firmware = tmp_path / "firmware"
    firmware.write_bytes(b"synthetic firmware")
    digest = hashlib.sha256(firmware.read_bytes()).hexdigest()
    inventory = json.loads(controller.inventory.read_text())
    inventory["stations"][0]["controls"]["firmware_sha256"] = digest
    controller.inventory.write_text(json.dumps(inventory))
    config = json.loads((directory / "config.json").read_text())
    config["firmware_sha256"] = digest
    (directory / "config.json").write_text(json.dumps(config))
    station = controller.station("desk")
    package = agent_provision.bundle(station, directory, layout="direct")
    source = agent_provision.SCRIPT.replace('"$(id -u)" = 0',
                                            '"$(id -u)" = ' + str(os.getuid()))
    source = source.replace('"$3" = 0', '"$3" = ' + str(os.getuid()))
    source = source.replace("DATA=/data", "DATA=" + str(data))
    source = source.replace("direct:/data|", "direct:" + str(data) + "|")
    source = source.replace("/system/vendor/quasar/maind", str(firmware))
    source = source.replace("pidof maind", "true")
    source = source.replace("INIT=/etc/init.d/S99zz-alice-mcp", "INIT=" + str(hooks / "startup"))
    source = source.replace("trusted /etc && trusted /etc/init.d",
                            "trusted " + str(hooks) + " && trusted " + str(hooks))
    root_check = "awk '$2==\"/\" && $4 ~ /(^|,)rw(,|$)/ {found=1} END {exit !found}' /proc/mounts"
    assert root_check in source
    source = source.replace(root_check, "true")
    # This fixture tests extraction/rollback only: it never starts a process or opens a port.
    begin = source.index('setsid "$DEST/agent"')
    end = source.index("started=1", begin)
    source = source[:begin] + source[end:]
    args = shlex.split(agent_provision._command(station, package, "c" * 32))
    args[2] = source
    inspected = subprocess.run(args, input=b"", capture_output=True, check=True, timeout=5)
    state = json.loads(inspected.stdout)["state_sha256"]
    assert not list((data / "opt").iterdir()) and not list(hooks.iterdir())
    args = shlex.split(agent_provision._command(station, package, "c" * 32, state))
    args[2] = source
    payload = package.package + b"changed" if corrupt else package.package
    applied = subprocess.run(args, input=payload, capture_output=True, check=True, timeout=8)
    result = json.loads(applied.stdout)
    assert result["staging_cleaned"]
    if corrupt:
        assert result["outcome"] == "unknown" and result["do_not_retry"]
        assert not list((data / "opt").iterdir()) and not list(hooks.iterdir())
    else:
        assert result["outcome"] == "installed" and result["autostart_registered"]
        target = data / "opt/alice-mcp-agent"
        assert {p.name for p in target.iterdir()} == set(agent_provision.NAMES)
        assert (hooks / "startup").read_text().endswith('/start "$@"\n')
        again = subprocess.run(args, input=payload, capture_output=True, check=True, timeout=5)
        assert json.loads(again.stdout)["outcome"] == "blocked"
        assert {p.name for p in target.iterdir()} == set(agent_provision.NAMES)
