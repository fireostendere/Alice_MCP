"""Synthetic Pickle Zigbee panel; never accesses a real station or radio."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import controls, device_runtime, models, zigbee_http
from alice_mcp.core import Controller, SSHInput
from scripts.check_public import scan_text

FIRMWARE = hashlib.sha256(b"synthetic").hexdigest()
STATUS = '{"eui64":"0011223344556677","network":true,"panId":"1a2b"}'


def test_adapter_source_contains_no_private_endpoint_literals() -> None:
    source = Path(zigbee_http.__file__).read_text()
    assert scan_text("src/alice_mcp/zigbee_http.py", source) == []


def request(action: str = "zigbee.permit_join", params: Any = None,
            mode: str = "inspect", **extra: Any) -> dict[str, Any]:
    return {"schema": device_runtime.SCHEMA, "mode": mode, "model": "station-mini3-pro",
            "action": action, "nonce": "a" * 32,
            "policy": {"firmware_sha256": FIRMWARE, "allowed_actions": "all"},
            "params": {"duration_seconds": 60} if params is None else params, **extra}


@pytest.mark.parametrize(("action", "params", "ok"), [
    ("zigbee.permit_join", {"duration_seconds": 0}, True),
    ("zigbee.permit_join", {"duration_seconds": 254}, True),
    ("zigbee.permit_join", {"duration_seconds": 255}, False),  # 255 never closes the network.
    ("zigbee.permit_join", {"duration_seconds": "60"}, False),
    ("zigbee.on_off", {"command": "toggle", "address": "1a2b", "endpoint": 1}, True),
    ("zigbee.on_off", {"command": "on", "address": "fffd", "endpoint": 1}, False),  # Broadcast.
    ("zigbee.on_off", {"command": "on", "address": "1A2B", "endpoint": 1}, False),
    ("zigbee.on_off", {"command": "on", "address": "1a2b&x=1", "endpoint": 1}, False),
    ("zigbee.on_off", {"command": "blink", "address": "1a2b", "endpoint": 1}, False),
    ("zigbee.on_off", {"command": "off", "address": "1a2b", "endpoint": 241}, False),
])
def test_parameters(action: str, params: dict[str, Any], ok: bool) -> None:
    policy = {"firmware_sha256": FIRMWARE}
    if ok:
        assert device_runtime.validate_parameters(
            action, params, policy, "station-mini3-pro") == params
    else:
        with pytest.raises(ValueError):
            device_runtime.validate_parameters(action, params, policy, "station-mini3-pro")


def test_only_the_pro_has_the_adapter() -> None:
    for action in zigbee_http.ACTIONS:
        assert models.supports("station-mini3-pro", action)
        assert not models.supports("station-mini3", action)
        with pytest.raises(ValueError):
            device_runtime.validate_parameters(
                action, {"duration_seconds": 1}, {"firmware_sha256": FIRMWARE}, "station-mini3")
        assert controls.describe(request(action, {
            "duration_seconds": 60, "command": "on", "address": "1a2b", "endpoint": 1}))


def test_controls_route_through_stock_shell() -> None:
    def runner(station: Any, command: SSHInput) -> str:
        assert isinstance(command, SSHInput) and command.command == "sh -s"
        assert b"python" not in command.data
        subprocess.run(["sh", "-n"], input=command.data, check=True)
        return json.dumps({"outcome": "ready", "state_sha256": "b" * 64})

    assert controls.invoke(Controller(None, runner=runner), None, request())["outcome"] == "ready"


@pytest.fixture
def panel(tmp_path: Path) -> Any:
    """A fake station root plus a wget shim that logs every POST."""
    for name, content in {"system/vendor/quasar/maind": b"synthetic",
                          "proc/device-tree/compatible": b"yandex,pickle-evt\0yandex,pickle\0"
                          }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "wget").write_text(f"#!{sys.executable}\n" + '''
import os, sys
args = sys.argv[1:]
if "--post-data" in args:
    with open(os.environ["TEST_POSTS"], "a") as log:
        log.write(args[args.index("--post-data") + 1] + " " + args[-1] + "\\n")
    sys.exit(int(os.environ.get("TEST_POST_RC", "0")))
status = os.environ.get("TEST_STATUS", "")
if not status:
    sys.exit(1)
print(status)
''')
    (shims / "wget").chmod(0o755)
    posts = tmp_path / "posts"

    def run(req: dict[str, Any], **env: str) -> dict[str, Any]:
        source = (zigbee_http.SCRIPT.replace("/system/", f"{tmp_path}/system/")
                  .replace("/proc/", f"{tmp_path}/proc/")
                  .replace("PATH=", f"PATH={shims}:"))
        data = zigbee_http.command(req, source=source).data
        out = subprocess.run(["sh", "-s"], input=data, capture_output=True, check=True, env={
            "PATH": os.environ["PATH"], "TEST_POSTS": str(posts), "TEST_STATUS": STATUS, **env})
        return controls.parse_response(out.stdout.decode())

    run.posts = lambda: posts.read_text().splitlines() if posts.exists() else []  # type: ignore[attr-defined]
    return run


def test_preview_reads_only_and_apply_posts_once(panel: Any) -> None:
    ready = panel(request())
    assert ready["outcome"] == "ready" and panel.posts() == []
    done = panel(request(mode="apply", expected_sha256=ready["state_sha256"]))
    assert done["outcome"] == "applied" and done["command_succeeded"] is True
    assert panel.posts() == ["duration=60 http://localhost:9233/permit_join"]
    on = request("zigbee.on_off", {"command": "off", "address": "1a2b", "endpoint": 3})
    state = panel(on)["state_sha256"]
    assert state != ready["state_sha256"]  # A plan is bound to its own action.
    assert panel(dict(on, mode="apply", expected_sha256=state))["outcome"] == "applied"
    assert panel.posts()[1] == "address=1a2b&endpoint=3 http://localhost:9233/on_off/off"


def test_blocked_conflict_and_unknown_never_claim_success(panel: Any) -> None:
    assert panel(request(), TEST_STATUS="")["outcome"] == "blocked"  # Panel is not enabled.
    unformed = STATUS.replace("true", "false")
    assert panel(request(), TEST_STATUS=unformed)["outcome"] == "blocked"
    other = dict(request(), policy={"firmware_sha256": "0" * 64, "allowed_actions": "all"})
    assert panel(other)["outcome"] == "blocked"  # Another firmware build.
    state = panel(request())["state_sha256"]
    apply = request(mode="apply", expected_sha256=state)
    changed = STATUS.replace("1a2b", "ffff")
    assert panel(apply, TEST_STATUS=changed)["outcome"] == "conflict"
    assert panel(apply, TEST_POST_RC="1")["outcome"] == "unknown"
    assert len(panel.posts()) == 1  # Only the failed attempt reached the panel, once.
