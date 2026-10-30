"""Bounded LED parity shows on synthetic devices only; never contacts a station."""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import controls
from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller, SafetyError, SSHInput

EFFECTS = ["rainbow", "breathe", "spin", "spin_color", "static", "off", "notify", "police"]
COLOR = {"red": 12, "green": 4, "blue": 0}


def params(effect: str, model: str = "station-lite", **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"effect": effect, "duration_seconds": 4, **extra}
    if effect in rt.SHOW_COLOR_EFFECTS:
        value.update(COLOR)
    if model == "station-mini":
        value["capture_id"] = "a" * 32
    return value


@pytest.mark.parametrize("model", ["station-mini", "station-lite", "station-mini3"])
@pytest.mark.parametrize("effect", EFFECTS)
@pytest.mark.parametrize("speed", [1, 5])
def test_every_effect_is_dim_finite_and_below_three_flashes(model: str, effect: str,
                                                           speed: int) -> None:
    checked = rt.validate_parameters(
        "led.show", params(effect, model, speed=speed, duration_seconds=15), {}, model)
    interval, frames = rt.show_frames(checked, model)
    assert 1 <= len(frames) <= 75 and len(frames) * interval <= 15000
    assert interval >= 200 and all(len(f) == len(rt.led_layout(model)) for f in frames)
    assert all(0 <= v <= 16 for frame in frames for v in frame)
    assert rt.show_frames(checked, model) == (interval, frames)  # Pure and deterministic.


def logical(effect: str, **extra: Any) -> list[list[tuple[int, ...]]]:
    """Mini 1 paper layout: channel j drives zone j // 3, colour j % 3."""
    _, frames = rt.show_frames(rt.validate_parameters(
        "led.show", params(effect, "station-mini", **extra), {}, "station-mini"), "station-mini")
    return [[tuple(frame[z * 3:z * 3 + 3]) for z in range(6)] for frame in frames]


def test_effect_shapes_match_reference_behaviour_not_its_brightness() -> None:
    rainbow = logical("rainbow")
    assert all(len(set(frame)) == 1 for frame in rainbow)  # Temporal whole ring.
    assert len({frame[0] for frame in rainbow}) >= 6
    breathe = [frame[0] for frame in logical("breathe")]
    assert max(breathe) == (12, 4, 0) and min(breathe)[0] < 12
    spin = logical("spin_color")
    assert [frame.index((12, 4, 0)) for frame in spin[:7]] == [0, 1, 2, 3, 4, 5, 0]
    assert spin[1][0] == (4, 1, 0) and spin[1][3] == (0, 0, 0)  # Dim tail, dark rest.
    notify = logical("notify")
    assert notify[0] == [(12, 4, 0)] * 6 and notify[1] == [(0, 0, 0)] * 6
    police = logical("police")
    assert police[0] == [(16, 0, 0)] * 3 + [(0, 0, 0)] * 3
    assert police[1] == [(0, 0, 0)] * 3 + [(0, 0, 16)] * 3
    assert logical("static") == [[(12, 4, 0)] * 6] and logical("off") == [[(0, 0, 0)] * 6]


def test_lite_police_shares_the_middle_ring_like_the_reference() -> None:
    _, frames = rt.show_frames(rt.validate_parameters("led.show", params("police"), {},
                                                      "station-lite"), "station-lite")
    # Lite node order: red ring1..3, green ring1..3, blue ring1..3.
    assert frames[0] == [16, 16, 0, 0, 0, 0, 0, 0, 0]
    assert frames[1] == [0, 0, 0, 0, 0, 0, 0, 16, 16]


@pytest.mark.parametrize("model,change", [
    ("station-lite", {"red": 17}), ("station-lite", {"speed": 6}),
    ("station-lite", {"duration_seconds": 16}), ("station-lite", {"duration_seconds": 0}),
    ("station-lite", {"effect": "strobe"}), ("station-lite", {"effect": ["rainbow"]}),
    ("station-lite", {"red": True}), ("station-lite", {"frames": ["ff" * 9]}),
    ("station-lite", {"capture_id": "a" * 32}), ("station-mini", {"capture_id": "A" * 32}),
    ("station-mini2", {}), ("station-2", {}),
])
def test_parameters_reject_unbounded_or_unknown_requests(model: str,
                                                         change: dict[str, Any]) -> None:
    value = params("breathe", model)
    value.update(change)
    with pytest.raises(ValueError):
        rt.validate_parameters("led.show", value, {}, model)


def test_colour_only_where_used_and_capture_only_for_mini1() -> None:
    with pytest.raises(ValueError):
        rt.validate_parameters("led.show", {**params("rainbow"), **COLOR}, {}, "station-lite")
    with pytest.raises(ValueError):
        rt.validate_parameters("led.show", {"effect": "static", "duration_seconds": 2},
                               {}, "station-lite")
    mini = params("rainbow", "station-mini")
    del mini["capture_id"]
    with pytest.raises(ValueError):
        rt.validate_parameters("led.show", mini, {}, "station-mini")
    assert rt.validate_parameters("led.show", params("off"), {}, "station-lite")["speed"] == 3


def test_calibrate_lights_exactly_one_physical_channel_and_ignores_the_map() -> None:
    policy = {"led_map": {"channels": [[5 - j // 3, "blue"] for j in range(18)]}}
    checked = rt.validate_parameters("led.show", {"effect": "calibrate", "channel": 7,
                                                  "duration_seconds": 2, "capture_id": "a" * 32},
                                     {}, "station-mini")
    interval, frames = rt.show_frames(checked, "station-mini", policy["led_map"])
    assert interval == 2000 and frames == [[16 if j == 7 else 0 for j in range(18)]]
    with pytest.raises(ValueError):
        rt.validate_parameters("led.show", {"effect": "calibrate", "channel": 9,
                                            "duration_seconds": 2}, {}, "station-lite")


def base_policy(model: str = "station-mini") -> dict[str, Any]:
    return {"firmware_sha256": hashlib.sha256(b"synthetic").hexdigest(),
            "allowed_actions": ["led.show"]}


def test_private_led_map_is_the_calibration_knob() -> None:
    swapped = [[j // 3, ("green", "red", "blue")[j % 3]] for j in range(18)]
    swapped[17] = None
    policy = rt.validate_policy({**base_policy(), "led_map": {
        "channels": swapped, "gain_percent": {"green": 50}}}, "station-mini")
    assert policy["led_map"]["gain_percent"] == {"red": 100, "green": 50, "blue": 100}
    checked = rt.validate_parameters("led.show", params("static", "station-mini"), {},
                                     "station-mini")
    _, frames = rt.show_frames(checked, "station-mini", policy["led_map"])
    # Byte 0 drives logical green at half gain; byte 1 logical red; the unused byte stays dark.
    assert frames[0][:3] == [2, 12, 0] and frames[0][17] == 0


@pytest.mark.parametrize("model,led_map", [
    ("station-mini", {}), ("station-mini", {"channels": [[0, "red"]] * 17}),
    ("station-mini", {"channels": [[6, "red"]] * 18}),
    ("station-mini", {"channels": [[0, "white"]] * 18}),
    ("station-mini", {"channels": [[True, "red"]] * 18}),
    ("station-mini", {"gain_percent": {"red": 101}}),
    ("station-mini", {"gain_percent": {"uv": 50}}), ("station-mini", {"extra": 1}),
    ("station-lite", {"channels": [[3, "red"]] * 9}), ("station-mini2", {"gain_percent": {}}),
])
def test_led_map_policy_is_bounded_per_model(model: str, led_map: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        rt.validate_policy({**base_policy(), "led_map": led_map}, model)


def test_default_layouts_follow_source_backed_profiles() -> None:
    assert rt.led_layout("station-mini3")[:3] == [[0, "blue"], [0, "green"], [0, "red"]]
    assert rt.led_layout("station-lite")[:4] == [[0, "red"], [1, "red"], [2, "red"],
                                                 [0, "green"]]
    assert rt.led_layout("station-mini")[3] == [1, "red"]


# Lite: device-side Python runtime on a synthetic sysfs tree.

def lite(tmp_path: Path) -> tuple[dict[str, Any], list[Path]]:
    (tmp_path / "data/opt").mkdir(parents=True)
    (tmp_path / "system/vendor/quasar").mkdir(parents=True)
    (tmp_path / "system/vendor/quasar/maind").write_bytes(b"synthetic")
    nodes = []
    for color in ("red", "green", "blue"):
        for ring in range(1, 4):
            base = tmp_path / f"sys/class/leds/micro:{color}:ring{ring}"
            base.mkdir(parents=True)
            (base / "max_brightness").write_bytes(b"255\n")
            (base / "trigger").write_bytes(b"[none] timer\n")
            (base / "brightness").write_bytes(b"5\n")
            nodes.append(base / "brightness")
    req = {"schema": rt.SCHEMA, "mode": "inspect", "model": "station-lite",
           "policy": base_policy(), "action": "led.show",
           "params": params("notify", speed=5, duration_seconds=1), "nonce": "b" * 32}
    return req, nodes


def test_lite_show_plays_bounded_frames_and_restores_snapshot(tmp_path: Path,
                                                              monkeypatch: Any) -> None:
    req, nodes = lite(tmp_path)
    preview = rt.dispatch(req, tmp_path)
    assert preview["outcome"] == "ready" and all(n.read_bytes() == b"5\n" for n in nodes)
    seen: list[list[int]] = []
    monkeypatch.setattr(rt.time, "sleep", lambda s: seen.append([int(n.read_bytes())
                                                                  for n in nodes]))
    result = rt.dispatch(dict(req, mode="apply", expected_sha256=preview["state_sha256"]),
                         tmp_path)
    assert result["outcome"] == "applied" and result["restoration_verified"] is True
    assert result["yielded"] is False
    assert seen == [[12] * 3 + [4] * 3 + [0] * 3, [0] * 9, [12] * 3 + [4] * 3 + [0] * 3,
                    [0] * 9, [12] * 3 + [4] * 3 + [0] * 3]
    assert all(n.read_bytes() == b"5\n" for n in nodes)


def test_lite_show_yields_to_a_firmware_indicator_instead_of_clobbering_it(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    req, nodes = lite(tmp_path)
    preview = rt.dispatch(req, tmp_path)

    def firmware_writes_mute(_: float) -> None:
        nodes[0].write_bytes(b"200\n")  # e.g. the firmware renders a mute indicator.

    monkeypatch.setattr(rt.time, "sleep", firmware_writes_mute)
    result = rt.dispatch(dict(req, mode="apply", expected_sha256=preview["state_sha256"]),
                         tmp_path)
    assert result["outcome"] == "applied" and result["yielded"] is True
    assert result["restoration_verified"] is False and nodes[0].read_bytes() == b"200\n"


def test_lite_show_failure_restores_and_is_unknown(tmp_path: Path, monkeypatch: Any) -> None:
    req, nodes = lite(tmp_path)
    preview = rt.dispatch(req, tmp_path)

    def fail(_: float) -> None:
        raise OSError("synthetic")

    monkeypatch.setattr(rt.time, "sleep", fail)
    result = rt.dispatch(dict(req, mode="apply", expected_sha256=preview["state_sha256"]),
                         tmp_path)
    assert result["outcome"] == "unknown" and result["restoration_verified"] is True
    assert all(n.read_bytes() == b"5\n" for n in nodes)


def test_host_accepts_yield_flag_but_not_claimed_observation() -> None:
    result = controls.parse_response(json.dumps({"outcome": "applied", "yielded": True,
                                                 "restoration_verified": False}))
    assert result["yielded"] is True and result["effect_observed"] is False
    with pytest.raises(SafetyError):
        controls.parse_response(json.dumps({"outcome": "applied", "yielded": "yes"}))


# Mini 3: stock-shell framebuffer path.

def mini3_request(**change: Any) -> dict[str, Any]:
    req = {"schema": rt.SCHEMA, "mode": "inspect", "model": "station-mini3",
           "action": "led.show", "nonce": "a" * 32, "policy": base_policy("station-mini3"),
           "params": params("police", "station-mini3", speed=5, duration_seconds=1)}
    req.update(change)
    return req


@pytest.fixture
def framebuffer(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    from alice_mcp import led_framebuffer

    for name, content in {
        "system/vendor/quasar/maind": b"synthetic",
        "proc/device-tree/compatible": b"yandex,bergamot\0",
        "proc/sys/kernel/random/boot_id": b"synthetic-boot\n",
        "sys/class/graphics/fb0/virtual_size": b"6,1\n",
        "sys/class/graphics/fb0/bits_per_pixel": b"24\n",
        "sys/class/graphics/fb0/stride": b"18\n",
        "dev/fb0": bytes(range(18)),
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (tmp_path / "data").mkdir()
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "dd").write_text(f"#!{sys.executable}\n" + '''
import base64, os, subprocess, sys
if any(arg.startswith('of=') for arg in sys.argv[1:]):
    data = sys.stdin.buffer.read()
    with open(os.environ['TEST_WRITES'], 'ab') as log:
        log.write(base64.b64encode(data) + b'\\n')
    code = subprocess.run(['/bin/dd', *sys.argv[1:]], input=data).returncode
    if os.environ.get('TEST_FOREIGN') == '1':
        target = next(arg[3:] for arg in sys.argv[1:] if arg.startswith('of='))
        with open(target, 'r+b') as out:
            out.write(b'M' * 18)  # Firmware renders an indicator right after our frame.
    sys.exit(code)
if os.environ.get('TEST_READ_FAIL') == '1' and os.path.exists(os.environ['TEST_WRITES']):
    sys.exit(1)  # Framebuffer reads fail once the show has started.
os.execv('/bin/dd', ['dd', *sys.argv[1:]])
''')
    (shims / "dd").chmod(0o700)
    source = led_framebuffer.SCRIPT.replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
    source = source.replace('[ -c "$FB" ]', '[ -f "$FB" ]')
    source = re.sub(r"/(?:data\b|system\b|sys/|proc/(?!uptime)|dev/fb0)",
                    lambda match: str(tmp_path) + match[0], source)
    env = {**os.environ, "PATH": str(shims) + ":" + os.environ["PATH"],
           "TEST_WRITES": str(tmp_path / "writes")}
    return tmp_path, source, env


def run_fb(framebuffer: Any, req: dict[str, Any], **env: str) -> dict[str, Any]:
    from alice_mcp import led_framebuffer

    _, source, base = framebuffer
    command = led_framebuffer.command(req, source=source)
    result = subprocess.run(command.command, input=command.data, shell=True,
                            env={**base, **env}, capture_output=True, timeout=20, check=True)
    return controls.parse_response(result.stdout.decode())


def test_mini3_show_writes_bgr_frames_then_restores(framebuffer: Any) -> None:
    root = framebuffer[0]
    preview = run_fb(framebuffer, mini3_request())
    assert preview["outcome"] == "ready" and not (root / "writes").exists()
    result = run_fb(framebuffer, mini3_request(mode="apply",
                                               expected_sha256=preview["state_sha256"]))
    assert result["outcome"] == "applied" and result["restoration_verified"] is True
    writes = [base64.b64decode(x) for x in (root / "writes").read_bytes().splitlines()]
    red, blue = bytes((0, 0, 16)), bytes((16, 0, 0))  # BGR byte order.
    assert writes[0] == red * 3 + bytes(9) and writes[1] == bytes(9) + blue * 3
    assert writes[-1] == bytes(range(18)) and len(writes) == 6
    assert (root / "dev/fb0").read_bytes() == bytes(range(18))


def test_mini3_unreadable_framebuffer_is_unknown_not_a_firmware_yield(framebuffer: Any) -> None:
    preview = run_fb(framebuffer, mini3_request())
    result = run_fb(framebuffer, mini3_request(mode="apply",
                                               expected_sha256=preview["state_sha256"]),
                    TEST_READ_FAIL="1")
    assert result["outcome"] == "unknown" and result["yielded"] is False
    writes = framebuffer[0] / "writes"
    assert base64.b64decode(writes.read_bytes().splitlines()[-1]) == bytes(range(18))


def test_mini3_show_yields_when_firmware_overwrites_the_frame(framebuffer: Any) -> None:
    root = framebuffer[0]
    preview = run_fb(framebuffer, mini3_request())
    result = run_fb(framebuffer, mini3_request(mode="apply",
                                               expected_sha256=preview["state_sha256"]),
                    TEST_FOREIGN="1")
    assert result["outcome"] == "applied" and result["yielded"] is True
    assert result["restoration_verified"] is False
    assert len((root / "writes").read_bytes().splitlines()) == 1
    assert (root / "dev/fb0").read_bytes() == b"M" * 18


@pytest.mark.parametrize("damage", ["shell", "too_many_frames", "fast_interval"])
def test_mini3_shell_rejects_damaged_frame_lists(framebuffer: Any, damage: str) -> None:
    import shlex

    from alice_mcp import led_framebuffer

    root, source, env = framebuffer
    command = led_framebuffer.command(mini3_request(), source=source)
    args = shlex.split(command.command)  # sh -s -- mode firmware digest expected frames ...
    if damage == "shell":
        args[7] = "$(id)"
    elif damage == "too_many_frames":
        args[7] = " ".join([args[7]] * 20)
    else:
        args[9] = "100"
    result = subprocess.run(args, input=command.data, env=env, capture_output=True,
                            timeout=10, check=True)
    assert controls.parse_response(result.stdout.decode())["outcome"] == "blocked"
    assert not (root / "writes").exists()


# Mini 1: shell helper with captured idle evidence and fixed i2cset writes.

@pytest.fixture
def mini1(tmp_path: Path) -> tuple[tuple[Path, dict[str, str], str], Path]:
    from alice_mcp import led_capture

    firmware = tmp_path / "system/vendor/quasar/maind"
    firmware.parent.mkdir(parents=True)
    firmware.write_bytes(b"synthetic")
    (tmp_path / "sys/bus/i2c/devices/0-003c").mkdir(parents=True)
    (tmp_path / "sys/bus/i2c/devices/0-003c/name").write_text("sn3236\n")
    trace = tmp_path / "sys/kernel/debug/tracing"
    (trace / "events/i2c/i2c_write").mkdir(parents=True)
    (trace / "instances").mkdir()
    (trace / "available_events").write_text("i2c:i2c_write\n")
    (trace / "events/i2c/i2c_write/format").write_text(
        "field:int adapter_nr;\nfield:__u16 addr;\nfield:__u16 len;\n"
        "field:__data_loc u8[] buf;\n")
    (tmp_path / "mounts").write_text(f"debugfs {tmp_path}/sys/kernel/debug debugfs rw 0 0\n")
    (tmp_path / "cpuinfo").write_text("processor : 0\n")
    captured = tmp_path / "data/opt/alice-mcp/led-captures" / ("a" * 32)
    captured.mkdir(parents=True)
    for path in (captured, captured.parent, captured.parent.parent):
        path.chmod(0o700)
    (tmp_path / "data/opt/alice-mcp/control.lock").touch(mode=0o600)
    boot = tmp_path / "boot-id"
    boot.write_text("synthetic-boot\n")
    now = int(float(Path("/proc/uptime").read_text().split()[0]))
    for key, value in {"firmware": hashlib.sha256(b"synthetic").hexdigest(),
                       "phase": "complete", "cleanup": "1",
                       "boot": hashlib.sha256(boot.read_bytes()).hexdigest(),
                       "ended": str(now),
                       "records": "record=01" + "0c0804" * 6 + "\nrecord=25aa\nrecord=01"
                                  + "00" * 18 + "\nrecord=25aa"}.items():
        (captured / key).write_text(value + "\n")
    shims = tmp_path / "shims"
    shims.mkdir()
    log = tmp_path / "i2c-calls"
    (shims / "i2cset").write_text(
        '#!/bin/sh\nif [ "$*" = "--help" ]; then echo " i I2C block data"; exit 0; fi\n'
        'printf "%s\\n" "$*" >> "$TEST_I2C_LOG"\n')
    (shims / "i2cset").chmod(0o700)
    env = {**os.environ, "PATH": str(shims) + ":" + os.environ["PATH"], "TEST_I2C_LOG": str(log)}
    source = led_capture.SCRIPT.replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
    source = source.replace("DATA=/data", f"DATA={tmp_path}/data")
    source = source.replace("/proc/sys/kernel/random/boot_id", str(boot))
    for directory in ("/data/", "/sys/", "/system/"):
        source = source.replace(directory, str(tmp_path) + directory)
    source = source.replace("/proc/mounts", str(tmp_path / "mounts"))
    source = source.replace("/proc/cpuinfo", str(tmp_path / "cpuinfo"))
    return (tmp_path, env, source), log


def mini1_request(effect: str = "spin", **change: Any) -> dict[str, Any]:
    req = {"schema": rt.SCHEMA, "mode": "inspect", "model": "station-mini",
           "policy": base_policy(), "action": "led.show", "nonce": "b" * 32,
           "params": params(effect, "station-mini", speed=5, duration_seconds=1)}
    req.update(change)
    return req


def run_mini1(device: Any, req: dict[str, Any]) -> dict[str, Any]:
    from alice_mcp import led_capture

    root, env, source = device
    command = led_capture.command(req, source=source)
    raw = subprocess.run(command.command, input=command.data.decode(), shell=True, env=env,
                         capture_output=True, text=True, timeout=20, check=True)
    return controls.parse_response(json.dumps(led_capture.decode(raw.stdout)))


def test_mini1_show_writes_mapped_frames_then_idle_without_readback_claim(mini1: Any) -> None:
    device, log = mini1
    preview = run_mini1(device, mini1_request())
    assert preview["outcome"] == "ready" and not log.exists()
    result = run_mini1(device, mini1_request(mode="apply", expected_sha256=preview["state_sha256"]))
    assert result["outcome"] == "applied" and result["restoration_command_succeeded"] is True
    assert result["restoration_verified"] is False and result["effect_observed"] is False
    _, frames = rt.show_frames(mini1_request()["params"], "station-mini")
    expected = []
    for frame in (*frames, [0] * 18):
        expected += [f"-y 0 0x3c 0x{r:02x} 0x{v:02x}" for r, v in enumerate(frame, 1)]
        expected += ["-y 0 0x3c 0x25 0xaa b"]
    assert log.read_text().splitlines() == expected


def test_mini1_show_requires_the_existing_idle_evidence_and_its_own_permission(mini1: Any) -> None:
    from alice_mcp import led_capture

    device, log = mini1
    records = device[0] / "data/opt/alice-mcp/led-captures" / ("a" * 32) / "records"
    records.write_text(records.read_text().replace("01" + "00" * 18, "01" + "01" * 18))
    assert run_mini1(device, mini1_request())["outcome"] == "blocked" and not log.exists()
    with pytest.raises(SafetyError):
        led_capture.command(mini1_request(policy={**base_policy(),
                                                  "allowed_actions": ["led.animate"]}))


@pytest.mark.parametrize("damage", ["high_pwm", "too_many_frames", "slow_interval", "shell"])
def test_mini1_remote_show_payload_is_checked_before_writing(mini1: Any, damage: str) -> None:
    import shlex

    from alice_mcp import led_capture

    (root, env, source), log = mini1
    command = led_capture.command(mini1_request("rainbow"), source=source)
    args = shlex.split(command.command)
    if damage == "high_pwm":
        args[-1] = args[-1].replace("10", "11", 1)
    elif damage == "too_many_frames":
        args[-1] = " ".join([args[-1]] * 20)
    elif damage == "slow_interval":
        args[5] = "100"
    else:
        args[-1] = "$(echo SENSITIVE_SENTINEL)"
    result = subprocess.run(args, input=command.data, env=env, capture_output=True,
                            check=True, timeout=10)
    assert led_capture.decode(result.stdout.decode())["outcome"] == "blocked"
    assert not log.exists() and b"SENSITIVE_SENTINEL" not in result.stdout


def test_maximum_shows_fit_the_short_ssh_command_bound() -> None:
    from alice_mcp import led_capture, led_framebuffer

    req = mini1_request("rainbow")
    req["params"].update(duration_seconds=15)
    assert len(led_capture.command(req).command.encode()) < 4096
    req = mini3_request()
    req["params"].update(duration_seconds=15)
    assert len(led_framebuffer.command(req).command.encode()) < 4096


def test_dispatch_routes_each_profile_to_its_existing_transport() -> None:
    seen: list[str] = []

    def runner(station: Any, command: SSHInput) -> str:
        seen.append("python3" if b"python3" in command.data else "shell")
        if b"i2cset" in command.data:
            return "outcome=ready\nstate_sha256=" + "c" * 64 + "\n"
        return json.dumps({"outcome": "ready", "state_sha256": "c" * 64})

    controller = Controller(None, runner=runner)
    for model in ("station-mini", "station-mini3", "station-lite"):
        req = {"schema": rt.SCHEMA, "mode": "inspect", "model": model, "action": "led.show",
               "policy": base_policy(), "nonce": "b" * 32,
               "params": params("rainbow", model)}
        assert controls.invoke(controller, None, req)["outcome"] == "ready"
    assert seen == ["shell", "shell", "python3"]


def test_impact_states_deadline_brightness_and_indicator_risk() -> None:
    text = controls.describe({"action": "led.show", "model": "station-mini",
                              "policy": base_policy(), "params": rt.validate_parameters(
                                  "led.show", params("police", "station-mini"), {},
                                  "station-mini")})
    assert "4 s" in text and "16/255" in text and "mute" in text
