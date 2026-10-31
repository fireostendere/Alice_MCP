"""Synthetic resource metadata; never a claim that recognition fits on a device."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import core


def test_asr_resources_are_typed_and_reject_inconsistent_memory() -> None:
    raw = ("mem_total=240000\nmem_available=130000\nswap_total=0\nswap_free=0\n"
           "zram_present=1\nzram_bytes=0\novercommit=0\ncpu_count=4\n"
           "data_free_kib=50000\nsystem_free_kib=100000\nsquashfs=1\n")
    facts = core.parse_probe("asr_resources", raw)
    assert facts["zram_present"] is True
    assert facts["mem_available"] == 130000
    assert facts["swap_total"] == 0
    assert facts["data_free_kib"] == 50000
    with pytest.raises(core.SafetyError):
        core.parse_probe("asr_resources", raw.replace("swap_free=0", "swap_free=1"))
    with pytest.raises(core.SafetyError):
        core.parse_probe("asr_resources", raw.replace("zram_present=1", "zram_present=2"))


def test_asr_resource_probe_is_explicit_ssh_metadata_even_with_native_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    key, hosts = tmp_path / "key", tmp_path / "hosts"
    key.touch(mode=0o600)
    hosts.touch(mode=0o600)
    station = core.Station("desk", "station-mini", "station.invalid", 2222, "root",
                           key, hosts, False, "", "a" * 64, "b" * 64)
    station = replace(station, native_mcp={"url": "https://station.invalid:18443"})
    seen: list[Any] = []
    controller = core.Controller(None, runner=lambda s, c: seen.append((s, c)) or "synthetic")
    monkeypatch.setattr(controller, "station", lambda _: station)
    monkeypatch.setattr(core, "parse_probe", lambda name, text: {"name": name})
    assert controller.probe("desk", "asr_resources")["data"] == {"name": "asr_resources"}
    assert seen[0][0].native_mcp == {}
    assert isinstance(seen[0][1], core.SSHInput)
    command = seen[0][1].data.decode()
    assert "/proc/meminfo" in command and "/sys/block/zram0/disksize" in command
    assert "swapon" not in command and "mkswap" not in command
