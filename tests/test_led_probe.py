"""Offline LED discovery checks; no physical effect or device ABI is verified."""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import core, server

FLAGS = (
    "sn3236_device", "ledring_device", "framebuffer_device", "led_class_entries",
    "sn3236_i2c", "ledring_i2c", "light_library", "patterns_directory", "maind_running",
)


def sample() -> str:
    return "".join(f"{key}=1\n" for key in FLAGS) + (
        "sn3236_driver=sn3236\nledring_driver=ledring\n"
    )


def test_led_probe_is_typed_and_does_not_return_remote_text() -> None:
    result = core.parse_probe("led", sample() + "private=SENSITIVE_SENTINEL\n")
    assert result == {
        **dict.fromkeys(FLAGS, True), "sn3236_driver": "sn3236",
        "ledring_driver": "ledring", "physical_effects_verified": False,
        "control_protocol_verified": False,
    }
    result = core.parse_probe("led", sample().replace("=sn3236", "=SENSITIVE_SENTINEL"))
    assert result["sn3236_driver"] == "unknown"
    assert "SENSITIVE_SENTINEL" not in str(result)


@pytest.mark.parametrize("change", ["missing", "nonboolean", "duplicate"])
def test_led_probe_rejects_incomplete_or_ambiguous_flags(change: str) -> None:
    text = sample()
    if change == "missing":
        text = text.replace("light_library=1\n", "")
    elif change == "nonboolean":
        text = text.replace("light_library=1", "light_library=2")
    else:
        text += "light_library=0\n"
    with pytest.raises(core.SafetyError):
        core.parse_probe("led", text)


def test_led_probe_command_is_fixed_read_only_shell() -> None:
    command = core.PROBES["led"]
    subprocess.run(["sh", "-n", "-c", command], check=True)
    for forbidden in ("i2cget", "i2cset", "i2cdetect", "unbind", "kill", "ctypes", "eval"):
        assert forbidden not in command
    assert ">" not in command
    assert "/sys/bus/i2c/devices/0-003c/driver" in command
    assert "/sys/bus/i2c/devices/1-001f/driver" in command


def test_led_probe_shell_reports_synthetic_files_without_opening_devices(tmp_path: Path) -> None:
    library = tmp_path / "system/vendor/quasar/libs/libwmlighteffect.so"
    library.parent.mkdir(parents=True)
    library.write_text("not an executable library")
    patterns = tmp_path / "system/vendor/quasar/ledpatterns"
    patterns.mkdir()
    driver = tmp_path / "sys/bus/i2c/drivers/sn3236"
    driver.mkdir(parents=True)
    chip = tmp_path / "sys/bus/i2c/devices/0-003c"
    chip.mkdir(parents=True)
    (chip / "driver").symlink_to(driver, target_is_directory=True)
    command = core.PROBES["led"]
    for root in ("/sys/", "/dev/", "/system/"):
        command = command.replace(root, str(tmp_path) + root)
    command = command.replace("pidof maind", "printf 123")
    completed = subprocess.run(["sh", "-c", command], check=True, capture_output=True, text=True)
    result = core.parse_probe("led", completed.stdout)
    assert result["sn3236_driver"] == "sn3236"
    assert result["ledring_driver"] == "none"
    assert result["light_library"] is True
    assert result["framebuffer_device"] is False
    assert library.read_text() == "not an executable library"


def test_led_probe_uses_existing_read_only_transport(monkeypatch: Any) -> None:
    calls: list[str] = []

    def runner(station: object, command: str) -> str:
        calls.append(command)
        return sample()

    controller = core.Controller(None, writes=False, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: SimpleNamespace(native_mcp={}))
    monkeypatch.setattr(server, "controller", controller)
    result = asyncio.run(server.station_probe("desk", "led"))
    assert result["data"]["light_library"] is True
    assert calls == [core.PROBES["led"]]
    assert "led" in server.get_capabilities()["probes"]
    with pytest.raises(core.SafetyError):
        controller.probe("desk", "led;id")
    assert len(calls) == 1
