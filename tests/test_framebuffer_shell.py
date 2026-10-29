"""Synthetic Bergamot framebuffer; never accesses real devices or a station."""

import base64
import copy
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import controls, device_runtime
from alice_mcp.core import Controller, SafetyError, SSHInput


def request() -> dict[str, Any]:
    return {"schema": device_runtime.SCHEMA, "mode": "inspect", "model": "station-mini3",
            "action": "led.effect", "nonce": "a" * 32,
            "policy": {"firmware_sha256": hashlib.sha256(b"synthetic").hexdigest(),
                       "allowed_actions": ["led.effect"]},
            "params": {"red": 16, "green": 8, "blue": 4, "count": 1, "period_ms": 500}}


def test_mini3_effect_transport_requires_no_device_python() -> None:
    def runner(station: Any, command: SSHInput) -> str:
        assert isinstance(command, SSHInput)
        assert b"python3" not in command.data and len(command.command) < 1024
        subprocess.run(["sh", "-n"], input=command.data, check=True)
        return json.dumps({"outcome": "ready", "state_sha256": "b" * 64})

    assert controls.invoke(Controller(None, runner=runner), None, request())["outcome"] == "ready"


@pytest.fixture
def framebuffer(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    from alice_mcp import led_framebuffer

    for name, content in {
        "system/vendor/quasar/maind": b"synthetic",
        "proc/device-tree/compatible": b"yandex,bergamot-evt1\0yandex,bergamot\0",
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
    dd = shims / "dd"
    dd.write_text(f"#!{sys.executable}\n" + '''
import base64, os, pathlib, subprocess, sys
if any(arg.startswith('of=') for arg in sys.argv[1:]):
    data = sys.stdin.buffer.read()
    with open(os.environ['TEST_WRITES'], 'ab') as log:
        log.write(base64.b64encode(data) + b'\\n')
    if os.environ.get('TEST_SHORT_WRITE') == '1' and data == bytes([4, 8, 16]) * 6:
        target = next(arg[3:] for arg in sys.argv[1:] if arg.startswith('of='))
        with open(target, 'r+b') as out:
            out.write(data[:3])
        sys.exit(1)
    if os.environ.get('TEST_RESTORE_MISMATCH') == '1' and data == bytes(range(18)):
        data = b'x' * 18
    sys.exit(subprocess.run(['/bin/dd', *sys.argv[1:]], input=data).returncode)
os.execv('/bin/dd', ['dd', *sys.argv[1:]])
''')
    dd.chmod(0o700)
    source = led_framebuffer.SCRIPT.replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
    # Regular files emulate the character framebuffer only in this synthetic test.
    source = source.replace('[ -c "$FB" ]', '[ -f "$FB" ]')
    source = re.sub(r"/(?:data\b|system\b|sys/|proc/|dev/fb0)",
                    lambda match: str(tmp_path) + match[0], source)
    env = {**os.environ, "PATH": str(shims) + ":" + os.environ["PATH"],
           "TEST_WRITES": str(tmp_path / "writes")}
    return tmp_path, source, env


def run(framebuffer: Any, req: dict[str, Any]) -> dict[str, Any]:
    from alice_mcp import led_framebuffer

    _, source, env = framebuffer
    command = led_framebuffer.command(req, source=source)
    result = subprocess.run(command.command, input=command.data, shell=True, env=env,
                            capture_output=True, timeout=10, check=True)
    return controls.parse_response(result.stdout.decode())


def apply_request(framebuffer: Any) -> dict[str, Any]:
    preview = run(framebuffer, request())
    assert preview["outcome"] == "ready"
    return dict(request(), mode="apply", expected_sha256=preview["state_sha256"])


def test_preview_readonly_and_effect_restores_exact_snapshot(framebuffer: Any) -> None:
    root, _, _ = framebuffer
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    req = apply_request(framebuffer)
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = run(framebuffer, req)
    assert result["outcome"] == "applied" and result["restoration_verified"] is True
    assert result["effect_observed"] is False
    writes = [base64.b64decode(line) for line in (root / "writes").read_bytes().splitlines()]
    assert writes[0] == bytes([4, 8, 16]) * 6
    assert all(frame == bytes(range(18)) for frame in writes[1:])
    assert (root / "dev/fb0").read_bytes() == bytes(range(18))


@pytest.mark.parametrize("target,value", [
    ("dev/fb0", b"x" * 18),
    ("sys/class/graphics/fb0/stride", b"19\n"),
    ("proc/device-tree/compatible", b"yandex,pickle\0"),
    ("system/vendor/quasar/maind", b"different firmware"),
    ("proc/sys/kernel/random/boot_id", b"different boot\n"),
])
def test_changed_target_fails_closed_before_any_write(framebuffer: Any,
                                                      target: str, value: bytes) -> None:
    root, _, _ = framebuffer
    req = apply_request(framebuffer)
    (root / target).write_bytes(value)
    assert run(framebuffer, req)["outcome"] in {"blocked", "conflict"}
    assert not (root / "writes").exists()


def test_short_write_attempts_restoration_without_retrying_effect(framebuffer: Any) -> None:
    root, source, env = framebuffer
    device = root, source, {**env, "TEST_SHORT_WRITE": "1"}
    result = run(device, apply_request(device))
    assert result["outcome"] == "unknown" and result["restoration_verified"] is True
    writes = (root / "writes").read_bytes().splitlines()
    assert len(writes) == 2 and (root / "dev/fb0").read_bytes() == bytes(range(18))


def test_failed_restore_readback_is_unknown(framebuffer: Any) -> None:
    root, source, env = framebuffer
    device = root, source, {**env, "TEST_RESTORE_MISMATCH": "1"}
    result = run(device, apply_request(device))
    assert result["outcome"] == "unknown" and result["restoration_verified"] is False
    assert result["effect_observed"] is False


@pytest.mark.parametrize("target", ["system/vendor/quasar/maind",
                                   "data/opt/alice-mcp/control.lock"])
def test_symlink_targets_never_receive_writes(framebuffer: Any, target: str) -> None:
    root, _, _ = framebuffer
    req = apply_request(framebuffer)
    path = root / target
    if path.exists():
        path.unlink()
    else:
        path.parent.mkdir(mode=0o700, parents=True)
    path.symlink_to(root / "dev/fb0")
    assert run(framebuffer, req)["outcome"] == "blocked"
    assert not (root / "writes").exists()


def test_handled_termination_restores_snapshot(framebuffer: Any) -> None:
    from alice_mcp import led_framebuffer

    root, source, env = framebuffer
    command = led_framebuffer.command(apply_request(framebuffer), source=source)
    process = subprocess.Popen(command.command, shell=True, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, start_new_session=True)
    assert process.stdin is not None
    process.stdin.write(command.data)
    process.stdin.close()
    process.stdin = None
    try:
        deadline = time.monotonic() + 3
        while not (root / "writes").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (root / "writes").exists()
        os.killpg(process.pid, signal.SIGTERM)
        stdout, _ = process.communicate(timeout=5)
        result = controls.parse_response(stdout.decode())
        assert result["outcome"] == "unknown" and result["restoration_verified"] is True
        assert (root / "dev/fb0").read_bytes() == bytes(range(18))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


@pytest.mark.parametrize("change", [
    {"model": "station-mini3-pro"}, {"action": "led.replay"}, {"mode": "worker"},
    {"nonce": "$(id)"}, {"extra": 1}, {"expected_sha256": "b" * 64},
    {"params": {**request()["params"], "path": "/dev/fb1"}},
    {"params": {**request()["params"], "red": True}},
])
def test_unapproved_or_unbounded_requests_rejected(change: dict[str, Any]) -> None:
    from alice_mcp import led_framebuffer

    req = copy.deepcopy(request())
    req.update(change)
    with pytest.raises(SafetyError):
        led_framebuffer.command(req)
