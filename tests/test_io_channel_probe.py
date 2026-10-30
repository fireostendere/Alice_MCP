"""Synthetic IO-channel service facts; no station, no socket connections, no audio."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import io_channel_probe as probe
from alice_mcp.core import Controller, SSHInput

SENTINEL = "SENSITIVE_SENTINEL"
HEADER = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
          "   uid  timeout inode\n")


def _write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def _tcp(local: str, remote: str, state: str, inode: int) -> str:
    return (f"   0: {local} {remote} {state} 00000000:00000000 00:00000000 00000000"
            f"     0        0 {inode} 1 0 100 0 0 10 0\n")


def _tree(root: Path) -> None:
    links = {"123": ("--platformd", {"5": "socket:[880]", "6": "socket:[881]",
                                     "7": "socket:[999]", "8": "socket:[700]"}),
             "456": ("--aliced", {"3": "socket:[875]", "4": "socket:[882]"}),
             "789": ("--mediad", {"3": "socket:[883]"})}
    for pid, (role, fds) in links.items():
        _write(root / f"proc/{pid}/cmdline", f"maind\0{role}\0")
        (root / f"proc/{pid}/fd").mkdir(parents=True)
        for fd, target in fds.items():
            os.symlink(target, root / f"proc/{pid}/fd/{fd}")
    lo, any_ = "0100007F", "00000000"
    _write(root / "proc/net/tcp", HEADER
           + _tcp(f"{lo}:2693", f"{any_}:0000", "0A", 875)       # aliced 9875
           + _tcp(f"{lo}:2698", f"{any_}:0000", "0A", 880)       # audiod 9880
           + _tcp(f"{lo}:2698", f"{lo}:C350", "01", 881)         # audiod side of aliced client
           + _tcp(f"{lo}:C350", f"{lo}:2698", "01", 882)         # aliced -> audiod
           + _tcp(f"{lo}:C351", f"{lo}:2698", "01", 883)         # mediad -> audiod
           + _tcp(f"{lo}:C352", f"{lo}:2698", "01", 555)         # non-Quasar client
           + _tcp(f"{any_}:270F", f"{any_}:0000", "0A", 999)     # unmapped 9999
           + _tcp(f"{lo}:2699", "0A0B0C0D:D431", "01", 556))    # remote peer never printed
    _write(root / "proc/net/tcp6", HEADER)
    _write(root / "proc/net/unix",
           "Num       RefCount Protocol Flags    Type St Inode Path\n"
           "00000000: 00000002 00000000 00010000 0001 01 700 /run/q/audio_" + SENTINEL + "\n"
           "00000000: 00000002 00000000 00010000 0001 01 701 /run/other\n")
    _write(root / "sys/fs/selinux/enforce", "0\n")
    _write(root / "system/vendor/quasar/quasar.cfg", json.dumps({
        "aliced": {"port": 9875, "token": SENTINEL},
        "audiod": {"port": 9880, "inRate": 48000, "nested": {"port": 1}},
        "audiosender": {"port": 9881, "url": "wss://" + SENTINEL + ":443/x"},
        "glagold": {"port": 1961}, "bad;" + SENTINEL: {"port": 2},
        "list": [{"port": 3}]}) + "\n")
    _write(root / "system/vendor/quasar/maind",
           b"\x7fELF\0yandex_io/services/audiod/audio_device.cpp\0"
           b"yandex_io/libs/audio_source/audio_source_client.cpp\0"
           b"yandex_io/services/aliced/spotter.cpp\0" + SENTINEL.encode() + b"\0")


def _run(root: Path, awk: str = "gawk") -> str:
    if not shutil.which(awk) or not shutil.which("dash"):
        pytest.skip("Synthetic probe checks need dash and the requested awk")
    shims = root / "shims"
    shims.mkdir(exist_ok=True)
    (shims / "pidof").write_text('#!/bin/sh\n[ "$1" = maind ] && echo "123 456 789"\n')
    (shims / "pidof").chmod(0o700)
    os.symlink(shutil.which(awk) or "", shims / "awk")
    command = probe.COMMAND
    for prefix in ("/proc/", "/sys/", "/system/"):
        command = command.replace(prefix, str(root) + prefix)
    env = {**os.environ, "PATH": f"{shims}:{os.environ['PATH']}"}
    return subprocess.run(["dash"], input=command, text=True, capture_output=True, env=env,
                          timeout=20, check=True).stdout


def test_command_is_fixed_read_only_and_never_connects() -> None:
    subprocess.run(["sh", "-n", "-c", probe.COMMAND], check=True)
    for forbidden in ("nc ", "socat", "telnet", "/dev/tcp", "curl", "wget", "kill", "rm ",
                      "mkdir", "dd ", "tee ", "LD_PRELOAD", "q stop", "q restart",
                      "arecord", "connect"):
        assert forbidden not in probe.COMMAND


@pytest.mark.parametrize("awk", ["gawk", "mawk"])
def test_probe_maps_services_to_listeners_and_quasar_clients(tmp_path: Path, awk: str) -> None:
    _tree(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    output = _run(tmp_path, awk)
    assert SENTINEL not in output and "0A0B0C0D" not in output
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*")
                      if p.is_file() and "shims" not in p.parts}
    facts = probe.parse(output + "private=" + SENTINEL + "\n")
    assert SENTINEL not in str(facts)
    assert facts["maind_processes"] == 3 and facts["selinux"] == "permissive"
    assert facts["services"] == [
        {"name": "aliced", "port": 9875, "listen": "loopback", "owner": "aliced",
         "accepted_connections": 0, "client_roles": [], "foreign_clients": 0},
        {"name": "audiod", "port": 9880, "listen": "loopback", "owner": "platformd",
         "accepted_connections": 1, "client_roles": ["aliced", "mediad"], "foreign_clients": 1},
        {"name": "audiosender", "port": 9881, "listen": "none", "owner": "-",
         "accepted_connections": 0, "client_roles": [], "foreign_clients": 0},
        {"name": "glagold", "port": 1961, "listen": "none", "owner": "-",
         "accepted_connections": 0, "client_roles": [], "foreign_clients": 0},
    ]
    assert facts["unmapped_quasar_listeners"] == 1
    assert facts["unix_quasar_listeners"] == 1 and facts["unix_quasar_audio_named"] == 1
    assert facts["maind_cpp_tokens"] == {"audiod": 1, "audio_source": 1, "iohub": 0,
                                         "channel": 0}
    assert facts["io_channel_candidates"] == [
        {"service": "audiod", "port": 9880, "client_roles": ["aliced", "mediad"]},
        {"service": "aliced", "port": 9875, "client_roles": []},
    ]
    assert facts["connections_opened"] is False and facts["audio_captured"] is False


def test_probe_tolerates_missing_files(tmp_path: Path) -> None:
    (tmp_path / "proc").mkdir()
    facts = probe.parse(_run(tmp_path))
    assert facts["services"] == [] and facts["quasar_cfg_present"] is False
    assert facts["maind_cpp_tokens"] is None and facts["selinux"] == "absent"
    assert facts["io_channel_candidates"] == []


@pytest.mark.parametrize("change", [
    ("maind_processes=3", "maind_processes=x"),
    ("service_1_port=9880", "service_1_port=70000"),
    ("service_1_name=audiod", "service_1_name=bad;" + SENTINEL),
    ("service_1_listen=loopback", "service_1_listen=elsewhere"),
    ("service_1_client_roles=aliced+mediad", "service_1_client_roles=a+" + SENTINEL),
    ("service_count=4", "service_count=5"),
    ("maind_processes=3", "maind_processes=3\nmaind_processes=4"),
])
def test_parse_rejects_incompatible_output(tmp_path: Path, change: tuple[str, str]) -> None:
    _tree(tmp_path)
    output = _run(tmp_path)
    assert change[0] in output
    with pytest.raises(ValueError):
        probe.parse(output.replace(change[0], change[1]))


def test_probe_uses_bounded_stdin_over_explicit_ssh(tmp_path: Path, monkeypatch: Any) -> None:
    _tree(tmp_path)
    output = _run(tmp_path)
    calls: list[SSHInput] = []

    def runner(station: Any, command: SSHInput) -> str:
        calls.append(command)
        assert command.command == "sh -s" and len(command.data) < 120 * 1024
        return output

    controller = Controller(None, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: SimpleNamespace(native_mcp={}))
    monkeypatch.setattr(probe, "_ssh", lambda station: station)
    result = probe.run(controller, "desk")
    assert result["outcome"] == "complete" and result["data"]["maind_processes"] == 3
    assert len(calls) == 1 and controller.writes is False
