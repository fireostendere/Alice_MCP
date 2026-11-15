"""Mini 3 segment clock on synthetic sysfs trees only; these establish no station compatibility."""

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import clock_sysfs, controls, models
from alice_mcp import device_runtime as rt
from alice_mcp.core import SafetyError, SSHInput

FIRMWARE = hashlib.sha256(b"synthetic").hexdigest()
SHOWN = {"text": "12", "duration_seconds": 2, "colon": True}


def request(params: dict[str, Any], **change: Any) -> dict[str, Any]:
    req = {"schema": rt.SCHEMA, "mode": "inspect", "model": "station-mini3",
           "action": "display.show", "nonce": "a" * 32,
           "policy": {"firmware_sha256": FIRMWARE}, "params": params}
    req.update(change)
    return req


@pytest.mark.parametrize("model,change", [
    ("station-mini3", {"text": "12345"}), ("station-mini3", {"text": ""}),
    ("station-mini3", {"text": "1x"}), ("station-mini3", {"text": 12}),
    ("station-mini3", {"level": 91}), ("station-mini3", {"level": 0}),
    ("station-mini3", {"duration_seconds": 16}), ("station-mini3", {"duration_seconds": 0}),
    ("station-mini3", {"colon": 1}), ("station-mini3", {"brightness": 1}),
    # Unverified clocks: no adapter, whatever the wiki lists.
    ("station-mini2", {}), ("station-mini3-pro", {}), ("station-midi", {}),
    ("station-lite", {}),
])
def test_display_parameters_reject_unbounded_or_unknown_requests(
        model: str, change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        rt.validate_parameters("display.show", {**SHOWN, **change}, {}, model)


def test_clock_frame_is_right_aligned_seven_segment() -> None:
    params = rt.validate_parameters("display.show", SHOWN, {}, "station-mini3")
    assert params["level"] == 32
    nodes = clock_sysfs.NODES
    lit = {n.split(":")[2] for n, v in zip(nodes, clock_sysfs.frame(params), strict=True) if v}
    # "  12": digit 2 is "1" (B, C), digit 3 is "2" (A, B, D, E, G); degree and dot stay dark.
    assert lit == {"clockB2", "clockC2", "clockA3", "clockB3", "clockD3", "clockE3", "clockG3",
                   "clockDH", "clockDL"}
    assert nodes[0] == "bergamot:white:clockA0" and len(nodes) == 32
    assert set(rt.SEGMENTS["8"]) == set("ABCDEFG") and rt.SEGMENTS[" "] == ""


def device(tmp_path: Path, nodes: list[str], trigger: str = "[none] timer",
           level: int = 3) -> tuple[Path, str, dict[str, str]]:
    """Synthetic sysfs tree plus the script with its paths moved under `tmp_path`."""
    for name, content in {"system/vendor/quasar/maind": b"synthetic",
                          "proc/device-tree/compatible": b"yandex,bergamot\0amlogic, axg\0",
                          "proc/sys/kernel/random/boot_id": b"synthetic-boot\n"}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (tmp_path / "data").mkdir()
    for node in nodes:
        base = tmp_path / "sys/class/leds" / node
        base.mkdir(parents=True)
        (base / "max_brightness").write_text("255\n")
        (base / "trigger").write_text(trigger + "\n")
        (base / "brightness").write_text(f"{level}\n")
    shims = tmp_path / "shims"
    shims.mkdir()
    # `sleep` is where the text is on show: log what the nodes hold, optionally act as firmware.
    (shims / "sleep").write_text(f"#!{sys.executable}\n" + '''
import os, sys
if sys.argv[1] != "0.001":
    leds = os.environ["TEST_LEDS"]
    names = sorted(os.listdir(leds))
    with open(os.environ["TEST_LOG"], "a") as log:
        log.write(" ".join(open(f"{leds}/{n}/brightness").read().strip() for n in names) + "\\n")
    if os.environ.get("TEST_FOREIGN") == "1":
        open(f"{leds}/{names[0]}/brightness", "w").write("77\\n")  # Firmware redraws.
''')
    (shims / "sleep").chmod(0o700)
    source = clock_sysfs.SCRIPT.replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
    source = re.sub(r"/(?:data\b|system\b|sys/|proc/(?!uptime))",
                    lambda match: str(tmp_path) + match[0], source)
    env = {**os.environ, "PATH": str(shims) + ":" + os.environ["PATH"],
           "TEST_LEDS": str(tmp_path / "sys/class/leds"), "TEST_LOG": str(tmp_path / "log")}
    return tmp_path, source, env


def run(dev: Any, req: dict[str, Any], **env: str) -> dict[str, Any]:
    _, source, base = dev
    command = clock_sysfs.command(req, source=source)
    done = subprocess.run(command.command, input=command.data, shell=True,
                          env={**base, **env}, capture_output=True, timeout=20, check=True)
    return controls.parse_response(done.stdout.decode())


def apply(dev: Any, req: dict[str, Any], **env: str) -> dict[str, Any]:
    preview = run(dev, req)
    assert preview["outcome"] == "ready" and not (dev[0] / "log").exists()
    return run(dev, dict(req, mode="apply", expected_sha256=preview["state_sha256"]), **env)


def held(dev: Any) -> list[list[int]]:
    return [[int(v) for v in line.split()] for line in (dev[0] / "log").read_text().splitlines()]


def test_clock_shows_text_then_restores_the_time(tmp_path: Path) -> None:
    nodes = clock_sysfs.NODES
    dev = device(tmp_path, nodes)
    result = apply(dev, request(SHOWN))
    assert result["outcome"] == "applied" and result["restoration_verified"] is True
    assert result["yielded"] is False and result["effect_observed"] is False
    frame, = held(dev)
    params = rt.validate_parameters("display.show", SHOWN, {}, "station-mini3")
    expected = dict(zip(nodes, clock_sysfs.frame(params), strict=True))
    assert frame == [expected[n] for n in sorted(nodes)]
    for node in nodes:  # The captured segments are back.
        assert (tmp_path / "sys/class/leds" / node / "brightness").read_text() == "3\n"


def test_clock_yields_when_the_firmware_redraws_the_time(tmp_path: Path) -> None:
    nodes = clock_sysfs.NODES
    dev = device(tmp_path, nodes)
    result = apply(dev, request(SHOWN), TEST_FOREIGN="1")
    assert result["outcome"] == "applied" and result["yielded"] is True
    assert result["restoration_verified"] is False
    first = sorted(nodes)[0]  # Left as the firmware wrote it, not restored over.
    assert (tmp_path / "sys/class/leds" / first / "brightness").read_text() == "77\n"


@pytest.mark.parametrize("damage", ["missing", "trigger", "board"])
def test_unexpected_clock_is_refused_before_any_write(tmp_path: Path, damage: str) -> None:
    nodes = clock_sysfs.NODES
    dev = device(tmp_path, nodes[:-1] if damage == "missing" else nodes,
                 trigger="none [timer]" if damage == "trigger" else "[none] timer")
    if damage == "board":
        (tmp_path / "proc/device-tree/compatible").write_bytes(b"yandex,pickle\0")
    assert run(dev, request(SHOWN))["outcome"] == "blocked"
    assert not (tmp_path / "log").exists() and not (tmp_path / "data/opt").exists()


def test_changed_state_between_preview_and_apply_never_writes(tmp_path: Path) -> None:
    dev = device(tmp_path, clock_sysfs.NODES)
    preview = run(dev, request(SHOWN))
    assert preview["outcome"] == "ready"
    # The minute changed between preview and apply.
    (tmp_path / "sys/class/leds" / clock_sysfs.NODES[0] / "brightness").write_text("9\n")
    result = run(dev, request(SHOWN, mode="apply", expected_sha256=preview["state_sha256"]))
    assert result["outcome"] == "conflict" and not (tmp_path / "log").exists()


@pytest.mark.parametrize("damage", [
    "NODES='../x'", "NODES='a b;c'", "NODES='bergamot:white:clockA0'",
    "FRAME='1,2'", "FRAME='" + ",".join(["91"] * 32) + "'", "PERIOD=100", "PERIOD=16000",
])
def test_shell_rechecks_what_the_host_prepended(tmp_path: Path, damage: str) -> None:
    root, source, env = device(tmp_path, clock_sysfs.NODES)
    command = clock_sysfs.command(request(SHOWN), source=source)
    head, body = command.data.decode().split("\nOWNER_UID=", 1)
    name = damage.split("=")[0]
    head = "\n".join(damage if line.startswith(name + "=") else line
                     for line in head.splitlines())
    done = subprocess.run(command.command, input=(head + "\nOWNER_UID=" + body).encode(),
                          shell=True, env=env, capture_output=True, timeout=20, check=True)
    assert controls.parse_response(done.stdout.decode())["outcome"] == "blocked"
    assert not (root / "log").exists()


def test_host_rejects_other_models_actions_and_policies() -> None:
    clock_sysfs.command(request(SHOWN))
    for change in ({"model": "station-mini3-pro"}, {"model": "station-mini2"},
                   {"action": "led.show",
                    "params": {"effect": "off", "duration_seconds": 1}},
                   {"nonce": "x"}, {"expected_sha256": "0" * 64}):
        with pytest.raises(SafetyError):
            clock_sysfs.command(dict(request(SHOWN), **change))
    with pytest.raises(SafetyError):  # The private policy does not allow it.
        clock_sysfs.command(request(SHOWN, policy={"firmware_sha256": FIRMWARE,
                                                   "allowed_actions": ["led.effect"]}))


def test_invoke_routes_display_to_the_stock_shell_adapter() -> None:
    seen: list[Any] = []

    class Runner:
        def runner(self, station: Any, command: Any) -> str:
            seen.append(command)
            return '{"outcome":"ready","state_sha256":"' + "0" * 64 + '"}'

    controls.invoke(Runner(), object(), request(SHOWN))  # type: ignore[arg-type]
    command, = seen
    assert isinstance(command, SSHInput) and b"python3" not in command.data
    assert b"LEDS=/sys/class/leds" in command.data and len(command.command) < 400
    subprocess.run(["sh", "-n"], input=command.data, check=True)


def test_preview_text_names_the_stale_time() -> None:
    clock = controls.describe(request(dict(SHOWN, level=32)))
    assert "'12'" in clock and "colon" in clock and "stale" in clock and "90" in clock


def test_registry_reports_the_clock_honestly() -> None:
    mini3 = models.describe("station-mini3")
    assert mini3["display"]["supported"] is True and mini3["display"]["colon"] is True
    assert mini3["actions"]["display.show"] == "adapter"
    assert any("not visually confirmed" in line for line in mini3["profile"]["todo"])
    for model in models.PROFILES:
        if model != "station-mini3":
            assert models.describe(model)["display"] == {"supported": False}, model
            assert models.actions(model)["display.show"] == "none", model
