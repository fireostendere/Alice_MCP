"""Host side of the stock command-spotter listener; generated output only, no station."""

import hashlib
import io
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import device_runtime as rt
from alice_mcp import quasar_client as qc
from alice_mcp.core import SafetyError


@pytest.mark.parametrize("line", [
    b'{"event":"spotter","phrase":"\xd1\x85\xd0\xb2\xd0\xb0\xd1\x82\xd0\xb8\xd1\x82","trusted":false}',
    b'{"event":"end","outcome":"complete","reason":"stopped","spotted":2}',
    b'{"event":"end","outcome":"unknown","error_code":"ipc_closed","spotted":0}',
])
def test_parse_accepts_only_fixed_event_shapes(line: bytes) -> None:
    assert qc.parse(line)["event"] in {"spotter", "end"}


@pytest.mark.parametrize("line", [
    b'{"event":"spotter","phrase":"PRIVATE","trusted":false}',
    b'{"event":"spotter","phrase":"\xd1\x81\xd1\x82\xd0\xbe\xd0\xbf","trusted":true}',
    b'{"event":"spotter","phrase":"\xd1\x81\xd1\x82\xd0\xbe\xd0\xbf","trusted":false,"x":1}',
    b'{"event":"end","outcome":"complete","reason":"other","spotted":1}',
    b'{"event":"final","result":{"text":"x"}}',
    b"not json",
])
def test_parse_rejects_anything_else(line: bytes) -> None:
    with pytest.raises(SafetyError):
        qc.parse(line)


def test_request_binds_script_client_and_firmware(tmp_path: Path) -> None:
    client = b"\x7fELF" + b"\0" * 64
    launch = qc.request("a" * 64, client, pin=hashlib.sha256(client).hexdigest())
    script = launch.data[:-len(client)]
    # The loader reads the script via $(dd ...), which drops trailing newlines.
    assert not script.endswith(b"\n") and script == qc.SCRIPT.rstrip("\n").encode()
    assert hashlib.sha256(script).hexdigest() in launch.command
    assert "a" * 64 in launch.command and str(len(client)) in launch.command
    assert len(launch.command) < 4096
    with pytest.raises(SafetyError):
        qc.request("a" * 64, client + b"x", pin=hashlib.sha256(client).hexdigest())


def test_policy_accepts_optional_quasar_client_path() -> None:
    policy = {"firmware_sha256": "a" * 64, "quasar_client": "/private/owner/quasar-armhf"}
    assert rt.validate_policy(policy, "station-mini")["quasar_client"] == policy["quasar_client"]
    for bad in ("relative/path", "/x/../y"):
        with pytest.raises(ValueError):
            rt.validate_policy({**policy, "quasar_client": bad}, "station-mini")


class FakeProcess:
    def __init__(self, lines: list[bytes]) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(b"".join(line + b"\n" for line in lines))
        self.returncode: int | None = None
        self.sent = b""

    def __enter__(self) -> "FakeProcess":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


def test_stream_forwards_spotted_phrases_and_returns_the_end_summary() -> None:
    process = FakeProcess([
        '{"event":"spotter","phrase":"хватит","trusted":false}'.encode(),
        '{"event":"spotter","phrase":"громче","trusted":false}'.encode(),
        b'{"event":"end","outcome":"complete","reason":"deadline","spotted":2}',
    ])
    seen: list[dict[str, Any]] = []
    summary = qc.stream(process, seen.append, threading.Event())
    assert [e["phrase"] for e in seen] == ["хватит", "громче"]
    assert summary == {"event": "end", "outcome": "complete", "reason": "deadline", "spotted": 2}


def test_stream_without_end_is_unknown() -> None:
    process = FakeProcess(['{"event":"spotter","phrase":"стоп","trusted":false}'.encode()])
    summary = qc.stream(process, lambda _: None, threading.Event())
    assert summary["outcome"] == "unknown"


def fake_station(tmp_path: Path, client_body: str) -> tuple[Any, bytes]:
    root = tmp_path / "dev"
    (root / "bin").mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "maind").write_bytes(b"synthetic firmware")
    (root / "tcp").write_text("  sl  local_address rem_address   st\n"
                              "   0: 0100007F:2693 00000000:0000 0A\n")
    (root / "vol").write_text("10000")
    (root / "log").write_text("old line\n")
    (root / "boot").write_text("boot-1\n")
    for name, body in {"id": "echo 0", "pidof": "exit 0"}.items():
        (root / "bin" / name).write_text("#!/bin/sh\n" + body + "\n")
        (root / "bin" / name).chmod(0o755)
    client = ("#!/bin/sh\n" + client_body.replace("ROOT", str(root)) + "\n").encode()
    script = qc.CONTROL_SCRIPT
    for old, new in (("/system/vendor/quasar/maind", "maind"), ("/proc/net/tcp", "tcp"),
                     ("/data/quasar/last_vendor_volume", "vol"),
                     ("/data/quasar/daemons_logs/maind.log", "log"),
                     ("/proc/sys/kernel/random/boot_id", "boot"),
                     ("/tmp/alice-mcp-quasar", "tmp/alice-mcp-quasar")):
        script = script.replace(old, str(root / new))
    return SimpleNamespace(root=root, script=script), client


def run_control(device: Any, client: bytes, command: str, arg: str, *,
                firmware: bytes = b"synthetic firmware", verified: bool = False,
                stamp: str = "c" * 64) -> dict[str, Any]:
    firmware_sha = hashlib.sha256(firmware).hexdigest()
    pin = hashlib.sha256(client).hexdigest()
    env = {"PATH": f"{device.root / 'bin'}:/usr/bin:/bin"}
    suffix = "_verified" if verified else ""

    def run(mode: str, expected: str = "", data: bytes = b"") -> dict[str, Any]:
        args = [mode + suffix, firmware_sha, str(len(data)), pin, stamp, command, arg,
                *([expected] if expected else [])]
        out = subprocess.run(["dash", "-c", device.script, "sh", *args], input=data,
                             capture_output=True, env=env, timeout=20)
        return qc.parse_control(out.stdout.decode())

    ready = run("inspect")
    if ready["outcome"] != "ready":
        return ready
    return run("apply", ready["state_sha256"], client)


def test_full_mode_checks_firmware_and_verified_mode_relies_on_the_resident_stamp(
        tmp_path: Path, dash: None) -> None:
    device, client = fake_station(tmp_path, 'echo 3000 > ROOT/vol')
    assert run_control(device, client, "--send-volume-level", "3", firmware=b"other") == {
        "outcome": "blocked", "error_code": "firmware_mismatch"}
    (device.root / "vol").write_text("10000")
    assert run_control(device, client, "--send-volume-level", "3", firmware=b"other",
                       verified=True)["outcome"] == "applied"


def test_verified_cache_only_follows_a_full_check_for_the_same_resident_stamp(
        monkeypatch: Any) -> None:
    calls: list[str] = []
    stamps = iter(["1" * 64] * 3 + ["2" * 64] * 2)
    monkeypatch.setattr(qc.native_transport, "_state", lambda *a: next(stamps))
    station = SimpleNamespace(id="mini1", native_mcp={"url": "x"},
                              controls={"firmware_sha256": "a" * 64})

    def runner(target: Any, command: Any) -> str:
        calls.append(shlex.split(command.command)[4])
        return '{"outcome":"ready","state_sha256":"' + "b" * 64 + '"}'

    controller = SimpleNamespace(runner=runner, station=lambda _: station)
    qc._verified.clear()
    for _ in range(4):
        qc.invoke(controller, station, {"mode": "inspect", "action": "quasar.volume",
                                        "params": {"level": 3}})
    assert calls == ["inspect", "inspect_verified", "inspect_verified", "inspect"]


@pytest.fixture
def dash() -> None:
    if not shutil.which("dash"):
        pytest.skip("Synthetic shell checks need dash")


def test_level_is_applied_only_when_quasar_saves_the_requested_level(
        tmp_path: Path, dash: None) -> None:
    device, client = fake_station(tmp_path, 'echo 3000 > ROOT/vol')
    assert run_control(device, client, "--send-volume-level", "3") == {
        "outcome": "applied", "previous_level": 10, "level": 3}
    assert not list((device.root / "tmp").iterdir())  # Client directory removed.


def test_step_at_the_limit_is_blocked_before_sending(tmp_path: Path, dash: None) -> None:
    device, client = fake_station(tmp_path, 'echo SENT > ROOT/sent')
    assert run_control(device, client, "--send-volume-step", "louder") == {
        "outcome": "blocked", "error_code": "at_limit"}
    assert not (device.root / "sent").exists()


def test_activation_is_confirmed_by_a_new_spotter_log_line(tmp_path: Path, dash: None) -> None:
    device, client = fake_station(tmp_path, 'echo "x onPhraseSpotterStop" >> ROOT/log')
    assert run_control(device, client, "--send-activation", "off") == {
        "outcome": "applied", "activation": "off"}


def test_unobserved_effect_is_unknown_never_retried(tmp_path: Path, dash: None) -> None:
    device, client = fake_station(tmp_path, 'echo once >> ROOT/sent')
    assert run_control(device, client, "--send-volume-level", "5") == {
        "outcome": "unknown", "error_code": "not_observed"}
    assert (device.root / "sent").read_text() == "once\n"


def test_invoke_maps_actions_to_fixed_client_commands() -> None:
    assert qc.control("quasar.volume", {"level": 7}) == ("--send-volume-level", "7")
    assert qc.control("quasar.volume", {"step": "quieter"}) == ("--send-volume-step", "quieter")
    assert qc.control("alice.activation", {"enabled": False}) == ("--send-activation", "off")
    assert qc.parse_control('{"outcome":"applied","level":11,"previous_level":1}') == {
        "outcome": "unknown", "error_code": "send_unverified"}


@pytest.mark.parametrize("params,ok", [({"level": 10}, True), ({"level": 0}, True),
                                        ({"step": "louder"}, True), ({"level": 11}, False),
                                        ({"level": 4}, False), ({"step": "up"}, False),
                                        ({"level": 3, "step": "louder"}, False)])
def test_quasar_volume_parameters_respect_the_private_ceiling(params: Any, ok: bool) -> None:
    policy = {"firmware_sha256": "a" * 64, "quasar_client": "/x/client",
              "max_volume": 100 if params != {"level": 4} else 30}
    if ok:
        assert rt.validate_parameters("quasar.volume", params, policy, "station-mini") == params
    else:
        with pytest.raises(ValueError):
            rt.validate_parameters("quasar.volume", params, policy, "station-mini")


def test_controls_run_quasar_actions_over_the_pinned_ssh_twin_of_a_resident_station(
        tmp_path: Path, monkeypatch: Any) -> None:
    from alice_mcp import controls
    from alice_mcp.core import Controller, Station

    key, hosts, client = tmp_path / "key", tmp_path / "hosts", tmp_path / "client"
    key.write_text("synthetic key\n")
    key.chmod(0o600)
    hosts.write_text("synthetic host key\n")
    client.write_bytes(b"\x7fELF synthetic")
    monkeypatch.setattr(qc, "CLIENT_SHA256", hashlib.sha256(client.read_bytes()).hexdigest())
    policy = rt.validate_policy({"firmware_sha256": "a" * 64, "quasar_client": str(client),
                                 "max_volume": 100}, "station-mini")
    station = Station("mini1", "station-mini", "h", 22, "root", key, hosts, True, "", "c" * 64,
                      "t" * 64, policy, preauthorize_all_actions=True,
                      native_mcp={"url": "https://h/mcp"})
    seen: list[tuple[bool, str, bytes]] = []

    def runner(target: Station, command: Any) -> str:
        seen.append((bool(target.native_mcp), command.command, command.data[-14:]))
        if shlex.split(command.command)[4].startswith("inspect"):
            return '{"outcome":"ready","state_sha256":"' + "b" * 64 + '"}\n'
        return '{"outcome":"applied","previous_level":10,"level":7}\n'

    controller = Controller(None, writes=True, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: station)
    monkeypatch.setattr(qc.native_transport, "_state", lambda *a: "d" * 64)
    qc._verified.clear()
    plan = controls.preview(controller, "mini1", "quasar.volume", {"level": 7})
    result = controller.execute_plan(controller.take_plan(plan["plan_id"],
                                                          plan["expected_sha256"]))
    assert {k: result[k] for k in ("outcome", "previous_level", "level", "do_not_retry")} == {
        "outcome": "applied", "previous_level": 10, "level": 7, "do_not_retry": True}
    assert [native for native, _, _ in seen] == [False, False, False]  # Never the resident.
    assert all("--send-volume-level 7" in command for _, command, _ in seen)
    assert seen[-1][2] == b"\x7fELF synthetic"  # Only apply carries the pinned client.


def test_voice_policy_accepts_a_bounded_private_conversation_prompt() -> None:
    base = {"firmware_sha256": "a" * 64, "voice": {
        "source": "station_asr",
        "asr_directory": "/bkupgrade/data/opt/alice-local-asr-0123456789ab"}}
    base["voice"]["conversation_prompt"] = "Будь кратким и начинай с приветствия."
    voice = rt.validate_policy(base, "station-mini")["voice"]
    assert voice["conversation_prompt"] == "Будь кратким и начинай с приветствия."
    for bad in ("", "x" * 1001, "строка\u0000", 5):
        base["voice"]["conversation_prompt"] = bad
        with pytest.raises(ValueError):
            rt.validate_policy(base, "station-mini")
