"""Synthetic LED-only tracing; never a station or a real tracefs mount."""

import copy
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import controls, server
from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller, SafetyError


@pytest.fixture
def capture(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    firmware = tmp_path / "system/vendor/quasar/maind"
    firmware.parent.mkdir(parents=True)
    firmware.write_bytes(b"synthetic firmware")
    (tmp_path / "data/opt").mkdir(parents=True)
    for address, driver in (("0-003c", "sn3236"), ("1-001f", "ledring")):
        node = tmp_path / "sys/bus/i2c/devices" / address
        node.mkdir(parents=True)
        (node / "driver").symlink_to("../../../drivers/" + driver)
    trace = tmp_path / "sys/kernel/tracing"
    event = trace / "events/i2c/i2c_write"
    event.mkdir(parents=True)
    (trace / "instances").mkdir()
    (trace / "tracing_on").write_text("1\n")
    (trace / "trace").write_text("SENSITIVE_SENTINEL\n")
    (trace / "available_events").write_text("i2c:i2c_write\n")
    (event / "format").write_text(
        "name: i2c_write\nfield:int adapter_nr;\nfield:__u16 addr;\n"
        "field:__u16 len;\nfield:__data_loc u8[] buf;\n")
    (event / "enable").write_text("1\n")
    (event / "filter").write_text("global_filter\n")
    return tmp_path, {
        "schema": rt.SCHEMA, "mode": "inspect", "model": "station-mini",
        "policy": {"firmware_sha256": hashlib.sha256(b"synthetic firmware").hexdigest(),
                   "allowed_actions": ["led.capture"]},
        "action": "led.capture", "params": {"duration_seconds": 10}, "nonce": "a" * 32,
    }


def emulate_instance(monkeypatch: Any, root: Path) -> None:
    create = rt._capture_create_instance

    def populate(path: Path) -> tuple[int, int]:
        stamp = create(path)
        for name, value in {
            "tracing_on": "0", "current_tracer": "nop", "buffer_size_kb": "16",
            "events/enable": "0", "events/i2c/i2c_write/enable": "0",
            "events/i2c/i2c_write/filter": "none", "trace": "",
        }.items():
            node = path / name
            node.parent.mkdir(parents=True, exist_ok=True)
            node.write_text(value)
        return stamp

    monkeypatch.setattr(rt, "_capture_create_instance", populate)

    def remove(path: Path) -> None:
        assert path.is_relative_to(root / "sys/kernel/tracing/instances")
        shutil.rmtree(path)

    monkeypatch.setattr(rt, "_capture_remove_instance", remove)
    monkeypatch.setattr(rt, "_capture_worker_identity", lambda _, pid: [pid, "100"])


def test_capture_preview_is_read_only_and_requires_known_format(capture: Any) -> None:
    root, req = capture
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = rt.dispatch(req, root)
    assert result["outcome"] == "ready"
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    (root / "sys/kernel/tracing/events/i2c/i2c_write/format").write_text("unknown\n")
    assert rt.dispatch(req, root)["outcome"] == "blocked"


@pytest.mark.parametrize("params", [
    {"duration_seconds": True}, {"duration_seconds": 0}, {"duration_seconds": 61},
    {"duration_seconds": 5, "address": 16}, {"duration_seconds": 5, "command": "bad"},
])
def test_capture_parameters_cannot_broaden_scope(capture: Any, params: Any) -> None:
    _, req = capture
    with pytest.raises(ValueError):
        rt.validate_parameters("led.capture", params, req["policy"], "station-mini")


def test_capture_does_not_guess_other_models(capture: Any) -> None:
    root, req = capture
    req["model"] = "station-lite"
    assert rt.dispatch(req, root)["outcome"] == "blocked"


def test_capture_requires_both_documented_led_driver_bindings(capture: Any) -> None:
    root, req = capture
    driver = root / "sys/bus/i2c/devices/1-001f/driver"
    driver.unlink()
    driver.symlink_to("../../../drivers/microphone")
    assert rt.dispatch(req, root)["outcome"] == "blocked"


def test_worker_readiness_autostop_and_isolation(capture: Any, monkeypatch: Any) -> None:
    root, req = capture
    emulate_instance(monkeypatch, root)
    trace = root / "sys/kernel/tracing"
    global_before = {str(p): p.read_bytes() for p in trace.rglob("*") if p.is_file()}
    rt._private_dir(root, rt.BASE)
    rt._private_dir(root, rt.CAPTURE_BASE)
    state, _ = rt._snapshot(root, req)
    ticks = [1_000_000_000]
    monkeypatch.setattr(rt.time, "monotonic_ns", lambda: ticks[0])
    record = rt._capture_record(root, req, state)
    monkeypatch.setattr(rt, "_capture_worker_identity", lambda *_: [os.getpid(), "100"])
    alarms = []
    monkeypatch.setattr(rt.signal, "setitimer", lambda *args: alarms.append(args))

    def wait(_: float) -> None:
        status_req = dict(req, mode="capture_status", params={"capture_id": req["nonce"]})
        ready = rt.dispatch(status_req, root)
        assert ready["outcome"] == "capturing" and ready["capture_ready"] is True
        assert ready["remaining_seconds"] > 0
        instance = trace / "instances" / ("alice-led-" + req["nonce"])
        (instance / "trace").write_text(
            "maind-10 [000] .... 1.0: i2c_write: i2c-0 #0 a=03c f=0000 l=2 [01-08]\n"
            "maind-10 [000] .... 1.1: i2c_write: i2c-1 #0 a=01f f=0000 l=1 [10]\n"
            "private-20 [000] .... 1.2: i2c_write: i2c-1 #0 a=010 f=0000 l=2 [aa-bb]\n"
            "SENSITIVE_SENTINEL\n")
        ticks[0] += 10_000_000_000

    monkeypatch.setattr(rt.time, "sleep", wait)
    rt._capture_worker(root, req, record)
    status = rt.dispatch(dict(req, mode="capture_status", params={"capture_id": req["nonce"]}),
                         root)
    assert status["outcome"] == "complete" and status["cleanup_verified"] is True
    assert status["capture_ready"] is False and status["remaining_seconds"] == 0
    assert status["records"] == [
        {"bus": 0, "address": 60, "data_hex": "0108"},
        {"bus": 1, "address": 31, "data_hex": "10"},
    ]
    assert "SENSITIVE_SENTINEL" not in json.dumps(status)
    assert not list((trace / "instances").iterdir())
    assert global_before == {str(p): p.read_bytes() for p in trace.rglob("*") if p.is_file()}
    assert alarms and alarms[0][1] <= 10


def test_capture_failure_never_claims_ready_and_cleans_own_instance(capture: Any,
                                                                 monkeypatch: Any) -> None:
    root, req = capture
    emulate_instance(monkeypatch, root)
    rt._private_dir(root, rt.BASE)
    rt._private_dir(root, rt.CAPTURE_BASE)
    state, _ = rt._snapshot(root, req)
    record = rt._capture_record(root, req, state)
    write = rt._capture_write

    def fail_filter(path: Path, value: bytes, synthetic: bool) -> None:
        if path.name == "filter":
            raise OSError("SENSITIVE_SENTINEL")
        write(path, value, synthetic)

    monkeypatch.setattr(rt, "_capture_write", fail_filter)
    rt._capture_worker(root, req, record)
    result = rt.dispatch(dict(req, mode="capture_status", params={"capture_id": req["nonce"]}),
                         root)
    assert result["outcome"] == "unknown" and result["capture_ready"] is False
    assert result["cleanup_verified"] is True
    assert "SENSITIVE_SENTINEL" not in json.dumps(result)
    assert not list((root / "sys/kernel/tracing/instances").iterdir())


def test_capture_payload_parser_is_bounded_and_led_only() -> None:
    lines = (b"x: i2c_write: i2c-0 #0 a=03c f=0000 l=1 [08]\n" * 100)
    records, truncated = rt._capture_parse(lines)
    assert len(records) == 64 and truncated is True
    assert rt._capture_parse(b"x: i2c_write: i2c-0 #0 a=03c f=0000 l=2 [08]\n")[0] == []
    assert rt._capture_parse(b"x: i2c_read: i2c-0 #0 a=03c f=0001 l=1 [08]\n")[0] == []


def test_host_capture_policy_and_fresh_state_gates(tmp_path: Path, monkeypatch: Any) -> None:
    identity = tmp_path / "identity"
    identity.touch(mode=0o600)
    hosts = tmp_path / "hosts"
    hosts.touch()
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"stations": [{
        "id": "desk", "model": "station-mini", "host": "station.invalid", "port": 2222,
        "user": "root", "identity_file": str(identity), "known_hosts_file": str(hosts),
        "controls": {"firmware_sha256": "a" * 64, "allowed_actions": ["led.capture"]},
    }]}))
    inventory.chmod(0o600)
    calls = []

    def invoke(*args: Any) -> dict[str, Any]:
        calls.append(copy.deepcopy(args[-1]))
        return {"outcome": "ready", "state_sha256": "b" * 64}

    monkeypatch.setattr(controls, "invoke", invoke)
    c = Controller(inventory)
    with pytest.raises(SafetyError):
        controls.preview(c, "desk", "led.capture", {"duration_seconds": 10})
    assert not calls
    c.writes = True
    plan = controls.preview(c, "desk", "led.capture", {"duration_seconds": 10})
    assert calls[-1]["mode"] == "inspect"
    inventory.write_text(inventory.read_text() + "\n")
    with pytest.raises(SafetyError):
        c.execute_plan(c.take_plan(plan["plan_id"], plan["expected_sha256"]))
    assert len(calls) == 1
    assert callable(server.get_capture_status)


@pytest.mark.parametrize("change", [
    {"capture_ready": "yes"}, {"remaining_seconds": True}, {"records": ["SENSITIVE_SENTINEL"]},
    {"records": [{"bus": 1, "address": 16, "data_hex": "ff"}]},
    {"records": [{"bus": 0, "address": 60, "data_hex": "not-hex"}]},
])
def test_host_rejects_untyped_capture_output(change: Any) -> None:
    data = {"outcome": "capturing", "capture_id": "a" * 32, "capture_ready": True,
            "remaining_seconds": 10, "cleanup_verified": False, "records": [], "truncated": False}
    assert controls.parse_response(json.dumps(data))["capture_ready"] is True
    data.update(change)
    with pytest.raises(SafetyError):
        controls.parse_response(json.dumps(data))


def test_detached_capture_returns_live_ready_and_stops_without_host_polling(capture: Any,
                                                                         monkeypatch: Any) -> None:
    root, req = capture
    req["params"]["duration_seconds"] = 5
    identity = rt._capture_worker_identity
    emulate_instance(monkeypatch, root)
    monkeypatch.setattr(rt, "_capture_worker_identity", lambda _, pid: identity(Path("/"), pid))
    preview = rt.dispatch(req, root)
    result = rt.dispatch(dict(req, mode="apply", expected_sha256=preview["state_sha256"]), root)
    assert result["outcome"] == "capturing" and result["capture_ready"] is True
    saved = json.loads(rt._capture_record_path(root, req["nonce"]).read_bytes())
    pid = saved["worker"][0]
    assert os.getsid(pid) == pid  # Not in the invoking SSH process's session.
    try:
        time.sleep(5.2)  # No status requests are needed for the on-device deadline.
        status = rt.dispatch(dict(req, mode="capture_status", params={"capture_id": req["nonce"]}),
                             root)
        assert status["outcome"] == "complete" and status["cleanup_verified"] is True
        assert status["capture_ready"] is False
    finally:
        os.waitpid(pid, 0)


@pytest.mark.parametrize("change", ["filter", "worker", "deadline"])
def test_stale_or_tampered_live_status_is_not_ready(capture: Any, monkeypatch: Any,
                                                  change: str) -> None:
    root, req = capture
    emulate_instance(monkeypatch, root)
    rt._private_dir(root, rt.BASE)
    rt._private_dir(root, rt.CAPTURE_BASE)
    state, _ = rt._snapshot(root, req)
    record = rt._capture_record(root, req, state)
    observed = []

    def wait(_: float) -> None:
        status_req = dict(req, mode="capture_status", params={"capture_id": req["nonce"]})
        assert rt.dispatch(status_req, root)["capture_ready"] is True
        instance = rt._capture_instance(root, record)
        if change == "filter":
            (instance / "events/i2c/i2c_write/filter").write_text("addr == 16")
        elif change == "worker":
            monkeypatch.setattr(rt, "_capture_worker_identity", lambda *_: [123, "changed"])
        else:
            monkeypatch.setattr(rt.time, "monotonic_ns", lambda: record["deadline_ns"] + 1)
        observed.append(rt.dispatch(status_req, root))

    monkeypatch.setattr(rt.time, "sleep", wait)
    rt._capture_worker(root, req, record)
    assert observed and observed[0]["capture_ready"] is False
    assert observed[0]["outcome"] == "unknown"


def test_trace_read_failure_still_removes_own_instance(capture: Any, monkeypatch: Any) -> None:
    root, req = capture
    emulate_instance(monkeypatch, root)
    rt._private_dir(root, rt.BASE)
    rt._private_dir(root, rt.CAPTURE_BASE)
    state, _ = rt._snapshot(root, req)
    record = rt._capture_record(root, req, state)

    def wait(_: float) -> None:
        (rt._capture_instance(root, record) / "trace").unlink()

    monkeypatch.setattr(rt.time, "sleep", wait)
    rt._capture_worker(root, req, record)
    assert not rt._capture_instance(root, record).exists()
    result = rt.dispatch(dict(req, mode="capture_status", params={"capture_id": req["nonce"]}),
                         root)
    assert result["outcome"] == "unknown" and result["capture_ready"] is False
    assert result["cleanup_verified"] is True


def test_capture_does_not_reuse_existing_instance(capture: Any, monkeypatch: Any) -> None:
    root, req = capture
    emulate_instance(monkeypatch, root)
    rt._private_dir(root, rt.BASE)
    rt._private_dir(root, rt.CAPTURE_BASE)
    state, _ = rt._snapshot(root, req)
    record = rt._capture_record(root, req, state)
    instance = rt._capture_instance(root, record)
    instance.mkdir()
    sentinel = instance / "owner"
    sentinel.write_bytes(b"unchanged")
    rt._capture_worker(root, req, record)
    assert sentinel.read_bytes() == b"unchanged"
    status = rt.dispatch(dict(req, mode="capture_status", params={"capture_id": req["nonce"]}),
                         root)
    assert status["capture_ready"] is False and status["cleanup_verified"] is False


def test_transport_delay_cannot_return_stale_ready(monkeypatch: Any) -> None:
    response = {"outcome": "capturing", "capture_id": "a" * 32, "capture_ready": True,
                "remaining_seconds": 5, "cleanup_verified": False, "records": [],
                "truncated": False}
    clock = iter((1.0, 7.0))
    monkeypatch.setattr(controls.time, "monotonic", lambda: next(clock))
    c = Controller(None, runner=lambda *_: json.dumps(response))
    result = controls.invoke(c, None, {})
    assert result["capture_ready"] is False and result["remaining_seconds"] == 0
    assert result["outcome"] == "unknown"


def test_status_remains_read_only_when_global_writes_are_disabled(tmp_path: Path,
                                                                monkeypatch: Any) -> None:
    key, hosts = tmp_path / "identity", tmp_path / "hosts"
    key.touch(mode=0o600)
    hosts.touch()
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"stations": [{
        "id": "desk", "model": "station-mini", "host": "station.invalid", "port": 2222,
        "user": "root", "identity_file": str(key), "known_hosts_file": str(hosts),
        "controls": {"firmware_sha256": "a" * 64, "allowed_actions": ["led.capture"]},
    }]}))
    path.chmod(0o600)
    calls = []

    def invoke(*args: Any) -> dict[str, Any]:
        calls.append(args[-1])
        return {"outcome": "complete", "capture_id": "a" * 32, "capture_ready": False,
                "remaining_seconds": 0, "cleanup_verified": True, "records": [], "truncated": False}

    monkeypatch.setattr(controls, "invoke", invoke)
    c = Controller(path, writes=False)
    result = controls.capture_status(c, "desk", "a" * 32)
    assert result["outcome"] == "complete" and calls[0]["mode"] == "capture_status"
    with pytest.raises(SafetyError):
        controls.preview(c, "desk", "led.capture", {"duration_seconds": 10})
    assert len(calls) == 1
