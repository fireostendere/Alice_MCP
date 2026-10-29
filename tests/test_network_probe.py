"""Read-only synthetic Wi-Fi diagnosis; no actual station or network traffic."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp.core import PROBES, Controller, SafetyError, SSHInput, parse_probe


def test_network_probe_uses_bounded_stdin_without_write_optin(monkeypatch: Any) -> None:
    calls = []

    def runner(station: Any, command: SSHInput) -> str:
        calls.append(command)
        assert command.command == "sh -s" and len(command.data) < 120 * 1024
        return sample_output()

    controller = Controller(None, writes=False, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: SimpleNamespace(native_mcp={}))
    result = controller.probe("desk", "network")
    assert result["data"]["wifi_carrier_up"] == 1 and len(calls) == 1


def sample_output() -> str:
    return ("wifi_interfaces=1\nwifi_carrier_known=1\nwifi_carrier_up=1\n"
            "routes_readable=1\nwifi_default_routes=1\nsupplicant_running=1\n"
            "dhcp_client_running=0\nkernel_log_available=1\nkernel_window_lines=3\n"
            "wifi_disconnect_markers=1\nwifi_connect_markers=1\nwifi_driver_fault_markers=0\n"
            "suspend_markers=0\nresume_markers=0\noom_markers=0\n"
            "last_wifi_disconnect_uptime_seconds=123\nlast_wifi_connect_uptime_seconds=130\n")


def test_network_probe_never_transports_identifiers_or_raw_logs(tmp_path: Path) -> None:
    assert "network" in PROBES
    net = tmp_path / "sys/class/net/wlan0"
    (net / "wireless").mkdir(parents=True)
    (net / "carrier").write_text("1\n")
    route = tmp_path / "route"
    route.write_text("Iface Destination Gateway Flags RefCnt Use Metric Mask\n"
                     "wlan0 00000000 ABCDEF01 0003 0 0 0 00000000\n")
    shims = tmp_path / "shims"
    shims.mkdir()
    kernel = tmp_path / "kernel"
    kernel.write_text(
        "[ 123.45] wlan0: deauthenticated from SENSITIVE_SENTINEL\n"
        "[ 130.01] wlan0: associated with SENSITIVE_SENTINEL\n"
        "[ 131.00] unrelated: socket disconnected SENSITIVE_SENTINEL\n")
    dmesg = shims / "dmesg"
    dmesg.write_text('#!/bin/sh\ncat "$TEST_KERNEL"\n')
    dmesg.chmod(0o700)
    pidof = shims / "pidof"
    pidof.write_text('#!/bin/sh\n[ "$1" = wpa_supplicant ]\n')
    pidof.chmod(0o700)
    source = PROBES["network"].replace("/sys/class/net", str(tmp_path / "sys/class/net"))
    source = source.replace("/proc/net/route", str(route))
    env = {**os.environ, "PATH": str(shims) + ":" + os.environ["PATH"],
           "TEST_KERNEL": str(kernel)}
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(["sh"], input=source, text=True, capture_output=True,
                            env=env, timeout=5, check=True)
    assert "SENSITIVE_SENTINEL" not in result.stdout and "ABCDEF01" not in result.stdout
    parsed = parse_probe("network", result.stdout)
    assert parsed == parse_probe("network", sample_output())
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("field,value", [("wifi_carrier_up", "2"),
                                        ("kernel_log_available", "2"),
                                        ("kernel_window_lines", "401"),
                                        ("wifi_disconnect_markers", "4"),
                                        ("last_wifi_connect_uptime_seconds", "private"),
                                        ("wifi_interfaces", "-1")])
def test_network_probe_invalid_metrics_fail_closed(field: str, value: str) -> None:
    lines = sample_output().splitlines()
    text = "\n".join(field + "=" + value if line.startswith(field + "=") else line
                     for line in lines)
    with pytest.raises(SafetyError):
        parse_probe("network", text)
