"""Offline BusyBox-compatible capture; no station and no real tracefs writes."""

import hashlib
import json
import os
import shlex
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
    return {"schema": device_runtime.SCHEMA, "mode": "inspect", "model": "station-mini",
            "policy": {"firmware_sha256": hashlib.sha256(b"synthetic").hexdigest(),
                       "allowed_actions": ["led.capture"]},
            "action": "led.capture", "params": {"duration_seconds": 5}, "nonce": "b" * 32}


def test_capture_transport_no_longer_requires_device_python() -> None:
    def runner(station: Any, command: SSHInput) -> str:
        assert "python3" not in command.command and b"python3" not in command.data
        assert len(command.command.encode()) < 1024
        subprocess.run(["sh", "-n"], input=command.data, check=True)
        return "outcome=ready\nstate_sha256=" + "c" * 64 + "\n"

    result = controls.invoke(Controller(None, runner=runner), None, request())
    assert result["outcome"] == "ready"


@pytest.fixture
def shell_device(tmp_path: Path) -> tuple[Path, dict[str, str], str]:
    from alice_mcp import led_capture

    firmware = tmp_path / "system/vendor/quasar/maind"
    firmware.parent.mkdir(parents=True)
    firmware.write_bytes(b"synthetic")
    (tmp_path / "data/opt").mkdir(parents=True)
    name = tmp_path / "sys/bus/i2c/devices/0-003c/name"
    name.parent.mkdir(parents=True)
    name.write_text("sn3236\n")  # No kernel driver binding and no Python on the station.
    trace = tmp_path / "sys/kernel/debug/tracing"
    event = trace / "events/i2c/i2c_write"
    event.mkdir(parents=True)
    (trace / "instances").mkdir()
    for path, value in {trace / "available_events": "i2c:i2c_write\n",
                        trace / "trace": "SENSITIVE_SENTINEL\n",
                        trace / "tracing_on": "1\n", event / "enable": "1\n",
                        event / "filter": "global_filter\n",
                        event / "format": "field:int adapter_nr;\nfield:__u16 addr;\n"
                        "field:__u16 len;\nfield:__data_loc u8[] buf;\n"}.items():
        path.write_text(value)
    mounts = tmp_path / "mounts"
    mounts.write_text(f"debugfs {tmp_path}/sys/kernel/debug debugfs rw 0 0\n")
    cpus = tmp_path / "cpuinfo"
    cpus.write_text("processor : 0\nprocessor : 1\n")
    shims = tmp_path / "shims"
    shims.mkdir()
    # A synthetic tracefs materializes virtual files at mkdir, and removes them at rmdir.
    shim = f"#!{sys.executable}\n" + '''
import os, pathlib, subprocess, sys
command = pathlib.Path(sys.argv[0]).name
path = pathlib.Path(sys.argv[-1])
if command == 'mkdir':
    result = subprocess.run(['/bin/mkdir', *sys.argv[1:]])
    if result.returncode:
        sys.exit(result.returncode)
    if path.name.startswith('alice-led-'):
        for name, data in {'tracing_on':'0', 'current_tracer':'nop', 'buffer_size_kb':'16',
            'events/enable':'0', 'events/i2c/i2c_write/enable':'0',
            'events/i2c/i2c_write/filter':'none', 'trace':''}.items():
            p = path / name
            p.parent.mkdir(parents=True, exist_ok=True)
            if name == 'current_tracer':
                data = os.environ.get('TEST_CURRENT_TRACER', data)
            p.write_text(data)
elif command == 'rmdir' and path.name.startswith('alice-led-'):
    for p in sorted(path.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if p.is_dir(): p.rmdir()
        else: p.unlink()
    path.rmdir()
else:
    os.execv('/bin/' + command, [command, *sys.argv[1:]])
'''
    for command in ("mkdir", "rmdir"):
        path = shims / command
        path.write_text(shim)
        path.chmod(0o700)
    env = {**os.environ, "PATH": str(shims) + ":" + os.environ["PATH"]}
    source = led_capture.SCRIPT.replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
    source = source.replace("DATA=/data", f"DATA={tmp_path}/data")
    for directory in ("/data/", "/sys/", "/system/"):
        source = source.replace(directory, str(tmp_path) + directory)
    source = source.replace("/proc/mounts", str(mounts))
    source = source.replace("/proc/cpuinfo", str(cpus))
    return tmp_path, env, source


def run_device(device: Any, req: dict[str, Any]) -> dict[str, Any]:
    from alice_mcp import led_capture

    root, env, source = device
    command = led_capture.command(req, source=source)
    raw = subprocess.run(command.command, input=command.data.decode(), shell=True, env=env,
                         capture_output=True, text=True, timeout=15, check=True)
    return controls.parse_response(json.dumps(led_capture.decode(raw.stdout)))


def test_busybox_preview_is_read_only_without_stat_or_timeout(shell_device: Any) -> None:
    root, _, source = shell_device
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = run_device(shell_device, request())
    assert result["outcome"] == "ready"
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    commands = shlex.split(source)
    assert "stat" not in commands and "timeout" not in commands and "python3" not in commands


def test_capture_minute_window_keeps_bounded_readiness_and_buffers(shell_device: Any) -> None:
    from alice_mcp import led_capture

    req = request()
    req["params"]["duration_seconds"] = 60
    assert run_device(shell_device, req)["outcome"] == "ready"
    raw = ("outcome=capturing\ncapture_id=" + "a" * 32 + "\ncapture_ready=1\n"
           "remaining_seconds=60\ncleanup_verified=0\ntruncated=0\n")
    assert controls.parse_response(json.dumps(led_capture.decode(raw)))["remaining_seconds"] == 60
    with pytest.raises(SafetyError):
        excessive = led_capture.decode(raw.replace("seconds=60", "seconds=61"))
        controls.parse_response(json.dumps(excessive))
    req["params"]["duration_seconds"] = 61
    with pytest.raises(SafetyError):
        led_capture.command(req)


def test_busybox_detached_capture_ready_autostop_and_scope(shell_device: Any) -> None:
    root, _, _ = shell_device
    (root / "data/opt").rmdir()  # Actual stock Mini 1 has no custom /data/opt yet.
    req = request()
    trace = root / "sys/kernel/debug/tracing"
    before = {str(p): p.read_bytes() for p in trace.rglob("*") if p.is_file()}
    preview = run_device(shell_device, req)
    assert preview["outcome"] == "ready" and not (root / "data/opt").exists()
    started = run_device(shell_device, dict(req, mode="apply",
                                           expected_sha256=preview["state_sha256"]))
    assert started["capture_ready"] is True and started["outcome"] == "capturing"
    assert (root / "data/opt").stat().st_mode & 0o777 == 0o700
    instance = trace / "instances" / ("alice-led-" + req["nonce"])
    (instance / "trace").write_text(
        "maind-10 [000] .... 1.0: i2c_write: i2c-0 #0 a=03c f=0000 l=2 [01-08]\n"
        "private-20 [000] .... 1.1: i2c_write: i2c-1 #0 a=010 f=0000 l=2 [aa-bb]\n"
        "SENSITIVE_SENTINEL\n")
    time.sleep(5.2)
    status = run_device(shell_device, dict(req, mode="capture_status",
                                          params={"capture_id": req["nonce"]}))
    assert status["outcome"] == "complete" and status["cleanup_verified"] is True
    assert status["capture_ready"] is False
    assert status["records"] == [{"bus": 0, "address": 60, "data_hex": "0108"}]
    assert not instance.exists()
    assert "SENSITIVE_SENTINEL" not in json.dumps(status)
    assert before == {str(p): p.read_bytes() for p in trace.rglob("*") if p.is_file()}


def test_busybox_bad_firmware_and_unknown_led_name_fail_closed(shell_device: Any) -> None:
    req = request()
    req["policy"]["firmware_sha256"] = "0" * 64
    assert run_device(shell_device, req)["outcome"] == "blocked"
    root, _, _ = shell_device
    (root / "sys/bus/i2c/devices/0-003c/name").write_text("microphone\n")
    assert run_device(shell_device, request())["outcome"] == "blocked"


def test_busybox_rejects_symlink_firmware_even_if_digest_matches(shell_device: Any) -> None:
    root, _, _ = shell_device
    firmware = root / "system/vendor/quasar/maind"
    actual = firmware.with_name("actual")
    firmware.rename(actual)
    firmware.symlink_to(actual.name)
    assert run_device(shell_device, request())["outcome"] == "blocked"


def test_busybox_setup_failure_cleans_only_own_instance(shell_device: Any) -> None:
    root, env, source = shell_device
    env = {**env, 'TEST_CURRENT_TRACER': 'function'}
    device = root, env, source
    req = request()
    preview = run_device(device, req)
    outcome = run_device(device, dict(req, mode="apply",
                                     expected_sha256=preview["state_sha256"]))
    assert outcome["capture_ready"] is False and outcome["cleanup_verified"] is True
    assert outcome["outcome"] == "unknown"
    assert not list((root / "sys/kernel/debug/tracing/instances").iterdir())
    assert (root / "sys/kernel/debug/tracing/tracing_on").read_text() == "1\n"


@pytest.mark.parametrize("change", [
    {"model": "station-mini3"}, {"mode": "worker"}, {"nonce": "../bad"},
    {"params": {"duration_seconds": 20, "address": 16}},
])
def test_busybox_command_builder_has_no_arbitrary_scope(change: Any) -> None:
    from alice_mcp import led_capture

    req = request()
    req.update(change)
    with pytest.raises(SafetyError):
        led_capture.command(req)


@pytest.mark.parametrize("raw", [
    "outcome=ready\nstate_sha256=" + "a" * 64 + "\nprivate=SENSITIVE_SENTINEL\n",
    "outcome=ready\noutcome=complete\n", "record=zz\n", "capture_ready=yes\n",
])
def test_busybox_decoder_never_passes_untyped_text(raw: str) -> None:
    from alice_mcp import led_capture

    with pytest.raises(SafetyError):
        controls.parse_response(json.dumps(led_capture.decode(raw)))


def test_busybox_watchdog_cleans_and_keeps_results_if_worker_dies(shell_device: Any) -> None:
    root, _, _ = shell_device
    req = request()
    preview = run_device(shell_device, req)
    started = run_device(shell_device, dict(req, mode="apply",
                                           expected_sha256=preview["state_sha256"]))
    assert started["capture_ready"] is True
    result_dir = root / "data/opt/alice-mcp/led-captures" / req["nonce"]
    pid = int((result_dir / "worker").read_text())
    # Only the explicitly created synthetic worker is signalled, never a station process.
    assert os.getsid(pid) == pid
    os.kill(pid, signal.SIGKILL)
    time.sleep(5.2)
    status = run_device(shell_device, dict(req, mode="capture_status",
                                          params={"capture_id": req["nonce"]}))
    assert status["cleanup_verified"] is True and status["capture_ready"] is False
    assert status["outcome"] == "complete"


def test_busybox_record_ceiling_reports_truncation(shell_device: Any) -> None:
    root, env, source = shell_device
    result_dir = root / "data/opt/alice-mcp/led-captures" / ("b" * 32)
    result_dir.mkdir(parents=True)
    (result_dir / "truncated").write_text("0\n")
    instance = root / "sys/kernel/debug/tracing/instances/for-collect-test"
    instance.mkdir()
    (instance / "trace").write_text(
        "".join(f"maind-10 [000] .... 1.0: i2c_write: i2c-0 #0 a=03c f=0000 l=1 [{i:02x}]\n"
                for i in range(100)))
    prefix = source.split("\nprofile || blocked\n", 1)[0]
    source = prefix + "\nINSTANCE=" + shlex.quote(str(instance)) + '; collect; cat "$OUT/truncated"'
    raw = subprocess.run(["sh", "-c", source, "sh", "test", "b" * 32, "5", "a" * 64,
                          "a" * 64, ""], env=env, capture_output=True, text=True, check=True)
    assert raw.stdout.strip() == "1"
    assert len((result_dir / "records").read_text().splitlines()) == 64
    assert (result_dir / "records").read_text().splitlines()[0] == "record=24"
    assert (result_dir / "records").read_text().splitlines()[-1] == "record=63"
    assert not (result_dir / "raw").exists()


@pytest.fixture
def replay_device(shell_device: Any) -> tuple[Any, dict[str, Any], Path]:
    root, env, source = shell_device
    capture_id = 'a' * 32
    for path in (root / 'data/opt/alice-mcp', root / 'data/opt/alice-mcp/led-captures',
                 root / 'data/opt/alice-mcp/led-captures' / capture_id):
        path.mkdir(mode=0o700)
    recorded = root / 'data/opt/alice-mcp/led-captures' / capture_id
    lock = root / 'data/opt/alice-mcp/control.lock'
    lock.touch(mode=0o600)
    for key, value in {'firmware': request()['policy']['firmware_sha256'],
                       'phase': 'complete', 'cleanup': '1',
                       'records': 'record=25aa\n'}.items():
        path = recorded / key
        path.write_text(value + ('\n' if not value.endswith('\n') else ''))
        path.chmod(0o600)
    log = root / 'synthetic-i2c-calls'
    utility = root / 'shims/i2cset'
    utility.write_text('#!/bin/sh\n'
                       '[ "$*" = "-y 0 0x3c 0x25 0xaa b" ] || exit 99\n'
                       'printf "%s\\n" "$*" >> "$TEST_I2C_LOG"\n'
                       'exit "${TEST_I2C_EXIT:-0}"\n')
    utility.chmod(0o700)
    env = {**env, 'TEST_I2C_LOG': str(log)}
    req = request()
    req.update(action='led.replay', params={'capture_id': capture_id})
    req['policy']['allowed_actions'] = ['led.replay']
    return (root, env, source), req, log


def test_replay_preview_is_read_only_and_apply_sends_one_fixed_update(replay_device: Any) -> None:
    device, req, log = replay_device
    trace = device[0] / 'sys/kernel/debug/tracing'
    before = {str(p): p.read_bytes() for p in trace.rglob('*') if p.is_file()}
    preview = run_device(device, req)
    assert preview['outcome'] == 'ready' and not log.exists()
    result = run_device(device, dict(req, mode='apply', expected_sha256=preview['state_sha256']))
    assert result['outcome'] == 'applied' and result['command_succeeded'] is True
    assert log.read_text().splitlines() == ['-y 0 0x3c 0x25 0xaa b']
    assert result['effect_observed'] is False
    assert before == {str(p): p.read_bytes() for p in trace.rglob('*') if p.is_file()}


def test_replay_refuses_unrecorded_packet_and_changed_capture(replay_device: Any) -> None:
    device, req, log = replay_device
    preview = run_device(device, req)
    root = device[0]
    records = root / 'data/opt/alice-mcp/led-captures' / req['params']['capture_id'] / 'records'
    records.write_text('record=25aa\nrecord=0001\n')
    conflict = run_device(device, dict(req, mode='apply', expected_sha256=preview['state_sha256']))
    assert conflict['outcome'] == 'conflict' and not log.exists()
    records.write_text('record=0001\n')
    assert run_device(device, req)['outcome'] == 'blocked'
    assert not log.exists()


def test_failed_replay_is_unknown_and_never_retried(replay_device: Any) -> None:
    (root, env, source), req, log = replay_device
    device = (root, {**env, 'TEST_I2C_EXIT': '1'}, source)
    preview = run_device(device, req)
    result = run_device(device, dict(req, mode='apply', expected_sha256=preview['state_sha256']))
    assert result['outcome'] == 'unknown' and result['command_succeeded'] is False
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize('field,value', [('bus', 1), ('data', '0001'), ('register', 0),
                                        ('count', 2), ('force', True)])
def test_replay_does_not_expose_arbitrary_i2c_parameters(replay_device: Any,
                                                       field: str, value: Any) -> None:
    from alice_mcp import led_capture

    _, req, _ = replay_device
    req['params'][field] = value
    with pytest.raises(SafetyError):
        led_capture.command(req)


@pytest.fixture
def pulse_device(replay_device: Any) -> tuple[Any, dict[str, Any], Path]:
    (root, env, source), req, log = replay_device
    req.update(action='led.pulse', params={'capture_id': 'a' * 32, 'channel': 1})
    req['policy']['allowed_actions'] = ['led.pulse']
    recorded = root / 'data/opt/alice-mcp/led-captures' / ('a' * 32)
    (recorded / 'records').write_text('record=01' + '0c0804' * 6 + '\nrecord=25aa\n'
                                      'record=01' + '00' * 18 + '\nrecord=25aa\n')
    boot = root / 'synthetic-boot-id'
    boot.write_text('synthetic-boot\n')
    (recorded / 'boot').write_text(hashlib.sha256(boot.read_bytes()).hexdigest() + '\n')
    (recorded / 'ended').write_text(str(int(float(Path('/proc/uptime').read_text().split()[0]))))
    # Replace both the actual proc path and the legacy fixture-rewritten form.
    source = source.replace('/proc' + str(root) + '/sys/kernel/random/boot_id', str(boot))
    source = source.replace('/proc/sys/kernel/random/boot_id', str(boot))
    utility = root / 'shims/i2cset'
    utility.write_text('#!/bin/sh\n'
                       'if [ "$*" = "--help" ]; then echo " i I2C block data"; exit 0; fi\n'
                       'printf "%s\\n" "$*" >> "$TEST_I2C_LOG"\n'
                       'count=$(wc -l < "$TEST_I2C_LOG")\n'
                       '[ "$count" != "${TEST_PULSE_FAIL_ON:-none}" ]\n')
    return (root, env, source), req, log


def test_one_dim_pulse_restores_captured_idle_without_claiming_readback(pulse_device: Any) -> None:
    device, req, log = pulse_device
    preview = run_device(device, req)
    assert preview['outcome'] == 'ready' and not log.exists()
    result = run_device(device, dict(req, mode='apply', expected_sha256=preview['state_sha256']))
    assert result['outcome'] == 'applied' and result['command_succeeded'] is True
    assert result['restoration_command_succeeded'] is True
    assert result['restoration_verified'] is False and result['effect_observed'] is False
    calls = log.read_text().splitlines()
    assert calls == ['-y 0 0x3c 0x01 ' + ' '.join(['0x00', '0x10', '0x00'] * 6) + ' i',
                     '-y 0 0x3c 0x25 0xaa b',
                     '-y 0 0x3c 0x01 ' + ' '.join(['0x00'] * 18) + ' i',
                     '-y 0 0x3c 0x25 0xaa b']


def test_changed_pulse_evidence_conflicts_before_writing(pulse_device: Any) -> None:
    device, req, log = pulse_device
    preview = run_device(device, req)
    recorded = device[0] / 'data/opt/alice-mcp/led-captures' / ('a' * 32) / 'records'
    recorded.write_text(recorded.read_text().replace('0c0804', '0b0703'))
    result = run_device(device, dict(req, mode='apply', expected_sha256=preview['state_sha256']))
    assert result['outcome'] == 'conflict' and not log.exists()


@pytest.mark.parametrize('failure', ['1', '3'])
def test_pulse_failure_is_unknown_and_effect_is_not_retried(pulse_device: Any,
                                                           failure: str) -> None:
    (root, env, source), req, log = pulse_device
    device = root, {**env, 'TEST_PULSE_FAIL_ON': failure}, source
    preview = run_device(device, req)
    result = run_device(device, dict(req, mode='apply', expected_sha256=preview['state_sha256']))
    assert result['outcome'] == 'unknown' and result['restoration_verified'] is False
    assert sum('0x10' in line for line in log.read_text().splitlines()) == 1


@pytest.mark.parametrize('change', ['non_idle', 'wrong_boot', 'unrecorded_format',
                                    'stale', 'future', 'active_capture'])
@pytest.mark.parametrize('action', ['led.pulse', 'led.animate'])
def test_led_sequences_require_observed_idle_and_same_boot(pulse_device: Any, change: str,
                                                          action: str) -> None:
    device, req, log = pulse_device
    if action == 'led.animate':
        req.update(action=action, params={'capture_id': 'a' * 32, 'mode': 'rainbow'})
        req['policy']['allowed_actions'] = [action]
    root = device[0]
    recorded = root / 'data/opt/alice-mcp/led-captures' / ('a' * 32)
    if change == 'wrong_boot':
        (recorded / 'boot').write_text('0' * 64 + '\n')
    elif change in {'stale', 'future'}:
        now = int(float(Path('/proc/uptime').read_text().split()[0]))
        (recorded / 'ended').write_text(str(now - 1801 if change == 'stale' else now + 60))
    elif change == 'active_capture':
        (root / 'data/opt/alice-mcp/led-capture-active').mkdir(mode=0o700)
    elif change == 'non_idle':
        data = (recorded / 'records').read_text()
        (recorded / 'records').write_text(data.replace('record=01' + '00' * 18,
                                                       'record=01' + '01' * 18))
    else:
        (recorded / 'records').write_text('record=0001\nrecord=25aa\n')
    assert run_device(device, req)['outcome'] == 'blocked' and not log.exists()


@pytest.mark.parametrize('field,value', [('channel', True), ('channel', 3), ('level', 255),
                                        ('duration_seconds', 10), ('force', True)])
def test_pulse_has_no_arbitrary_level_duration_or_register(pulse_device: Any,
                                                         field: str, value: Any) -> None:
    from alice_mcp import led_capture

    _, req, _ = pulse_device
    req['params'][field] = value
    with pytest.raises(SafetyError):
        led_capture.command(req)


@pytest.mark.parametrize('name,count,interval_ms', [('blink', 12, 500), ('rainbow', 1, 3000),
                                                  ('rainbow_swirl', 12, 500)])
def test_animation_presets_are_small_dim_and_bounded(name: str, count: int,
                                                    interval_ms: int) -> None:
    from alice_mcp import led_capture

    interval, frames = led_capture.animation_frames(name)
    assert interval == interval_ms and len(frames) == count
    assert len(frames) * interval <= 6000
    assert all(len(frame) == 18 and set(frame) <= {0, 16} for frame in frames)
    if name == 'blink':
        assert all(frame == bytes(18) for frame in frames[1::2])
        assert len(set(frames[::2])) == 6
    else:
        assert all(len({frame[i:i+3] for i in range(0, 18, 3)}) == 6 for frame in frames)
    if name == 'rainbow_swirl':
        assert frames[1] == frames[0][-3:] + frames[0][:-3]
        assert frames[6:] == frames[:6]


def test_animation_dispatch_uses_shell_and_cannot_claim_hardware_restoration() -> None:
    def runner(station: Any, command: SSHInput) -> str:
        assert b'python3' not in command.data and len(command.command) < 4096
        return 'outcome=applied\nrestoration_verified=1\nrestoration_command_succeeded=1\n'

    req = request()
    req.update(action='led.animate', params={'capture_id': 'a' * 32, 'mode': 'rainbow'})
    req['policy']['allowed_actions'] = ['led.animate']
    result = controls.invoke(Controller(None, runner=runner), None, req)
    assert result['restoration_verified'] is False and result['effect_observed'] is False


@pytest.mark.parametrize('name', ['blink', 'rainbow', 'rainbow_swirl'])
def test_animation_preview_readonly_and_preset_restores_idle(pulse_device: Any, name: str) -> None:
    from alice_mcp import led_capture

    device, req, log = pulse_device
    req.update(action='led.animate', params={'capture_id': 'a' * 32, 'mode': name})
    req['policy']['allowed_actions'] = ['led.animate']
    _, frames = led_capture.animation_frames(name)
    preview = run_device(device, req)
    assert preview['outcome'] == 'ready' and not log.exists()
    result = run_device(device, dict(req, mode='apply', expected_sha256=preview['state_sha256']))
    assert result['outcome'] == 'applied' and result['restoration_command_succeeded'] is True
    assert result['effect_observed'] is False and result['restoration_verified'] is False
    calls = log.read_text().splitlines()
    expected = []
    for frame in (*frames, bytes(18)):
        expected.extend(['-y 0 0x3c 0x01 ' + ' '.join(f'0x{v:02x}' for v in frame) + ' i',
                         '-y 0 0x3c 0x25 0xaa b'])
    assert calls == expected


def test_failed_animation_stops_without_replaying_frames(pulse_device: Any) -> None:
    (root, env, source), req, log = pulse_device
    device = root, {**env, 'TEST_PULSE_FAIL_ON': '3'}, source
    req.update(action='led.animate', params={'capture_id': 'a' * 32, 'mode': 'rainbow_swirl'})
    req['policy']['allowed_actions'] = ['led.animate']
    preview = run_device(device, req)
    result = run_device(device, dict(req, mode='apply', expected_sha256=preview['state_sha256']))
    assert result['outcome'] == 'unknown' and result['restoration_command_succeeded'] is True
    assert result['restoration_verified'] is False
    # First frame+update, failed second frame, one idle frame+update. No frame retry.
    assert len(log.read_text().splitlines()) == 5


def test_changing_preset_after_preview_conflicts_before_any_write(pulse_device: Any) -> None:
    device, req, log = pulse_device
    req.update(action='led.animate', params={'capture_id': 'a' * 32, 'mode': 'rainbow'})
    req['policy']['allowed_actions'] = ['led.animate']
    preview = run_device(device, req)
    req['params']['mode'] = 'rainbow_swirl'
    result = run_device(device, dict(req, mode='apply', expected_sha256=preview['state_sha256']))
    assert result['outcome'] == 'conflict' and not log.exists()


@pytest.mark.parametrize('permission', ['led.capture', 'led.pulse'])
def test_capture_or_pulse_permission_does_not_grant_animation(pulse_device: Any,
                                                           permission: str) -> None:
    from alice_mcp import led_capture

    _, req, _ = pulse_device
    req.update(action='led.animate', params={'capture_id': 'a' * 32, 'mode': 'rainbow'})
    req['policy']['allowed_actions'] = [permission]
    with pytest.raises(SafetyError):
        led_capture.command(req)


@pytest.mark.parametrize('damage', ['high_pwm', 'too_many_frames', 'shell_text'])
def test_remote_animation_payload_is_checked_before_writing(pulse_device: Any,
                                                           damage: str) -> None:
    from alice_mcp import led_capture

    (root, env, source), req, log = pulse_device
    req.update(action='led.animate', params={'capture_id': 'a' * 32, 'mode': 'rainbow_swirl'})
    req['policy']['allowed_actions'] = ['led.animate']
    command = led_capture.command(req, source=source)
    args = shlex.split(command.command)
    if damage == 'high_pwm':
        args[-1] = args[-1].replace('10', 'ff', 1)
    elif damage == 'too_many_frames':
        args[-1] = args[-1] + ' ' + args[-1]
    else:
        args[-1] = '$(echo SENSITIVE_SENTINEL)'
    result = subprocess.run(args, input=command.data, env=env, capture_output=True,
                            check=True, timeout=5)
    assert led_capture.decode(result.stdout.decode())['outcome'] == 'blocked'
    assert not log.exists() and b'SENSITIVE_SENTINEL' not in result.stdout


def test_interrupted_animation_attempts_idle_once(pulse_device: Any) -> None:
    from alice_mcp import led_capture

    (root, env, source), req, log = pulse_device
    req.update(action='led.animate', params={'capture_id': 'a' * 32, 'mode': 'rainbow'})
    req['policy']['allowed_actions'] = ['led.animate']
    preview = run_device((root, env, source), req)
    command = led_capture.command(dict(req, mode='apply', expected_sha256=preview['state_sha256']),
                                  source=source)
    process = subprocess.Popen(shlex.split(command.command), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, start_new_session=True)
    assert process.stdin is not None
    process.stdin.write(command.data)
    process.stdin.close()
    process.stdin = None
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if log.exists() and len(log.read_text().splitlines()) >= 2:
                break
            time.sleep(0.01)
        assert log.exists() and len(log.read_text().splitlines()) == 2
        os.killpg(process.pid, signal.SIGTERM)
        stdout, _ = process.communicate(timeout=5)
        result = led_capture.decode(stdout.decode())
        assert result['outcome'] == 'unknown' and result['restoration_command_succeeded'] is True
        calls = log.read_text().splitlines()
        assert len(calls) == 4
        assert calls[-2] == '-y 0 0x3c 0x01 ' + ' '.join(['0x00'] * 18) + ' i'
        assert calls[-1] == '-y 0 0x3c 0x25 0xaa b'
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


@pytest.mark.parametrize('change', [
    {'mode': 'forever'}, {'mode': '../bad'}, {'level': 255}, {'frames': ['ff' * 18]},
    {'duration_seconds': 60}, {'cycles': 100}, {'force': True}, {'mode': True},
])
def test_animation_request_does_not_expose_raw_frames_or_unbounded_modes(pulse_device: Any,
                                                                       change: Any) -> None:
    from alice_mcp import led_capture

    _, req, _ = pulse_device
    req.update(action='led.animate', params={'capture_id': 'a' * 32, 'mode': 'rainbow'})
    req['policy']['allowed_actions'] = ['led.animate']
    req['params'].update(change)
    with pytest.raises(SafetyError):
        led_capture.command(req)
