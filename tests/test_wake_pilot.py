"""Synthetic wake pilot: fake loopback IPC and a stand-in detector, never a station or audio."""

import hashlib
import json
import os
import shlex
import shutil
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_io_channel_pilot import SENTINEL, FakeIPC, audio, channel, device, field, frame, station

from alice_mcp import io_channel_pilot as iop
from alice_mcp import pcm_pilot
from alice_mcp import wake_pilot as pilot
from alice_mcp.core import Controller, SafetyError

SOURCE = Path(__file__).resolve().parents[1] / "src/alice_mcp/native/io_channel_levels.c"
CHUNKS = [[300, -300] * 128, [12, -40] * 128, [7] * 256]
PCM = b"".join(struct.pack("<256h", *c) for c in CHUNKS)
MODEL = "a" * 64


@pytest.fixture(scope="module")
def client(tmp_path_factory: Any) -> bytes:
    return build_client(tmp_path_factory)


def build_client(tmp_path_factory: Any) -> bytes:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not compiler:
        pytest.skip("Native pilot checks require a host C compiler; no automatic install")
    binary = tmp_path_factory.mktemp("wake") / "client"
    subprocess.run([compiler, "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-O2", "-Wall",
                    "-Wextra", "-Werror", "-Wconversion", "-Wshadow", "-Wstrict-prototypes",
                    "-s", str(SOURCE), "-o", str(binary)], check=True, capture_output=True)
    return binary.read_bytes()


def stand_in(pcm_sha256: str, audio_ms: int) -> bytes:
    """Detector contract stand-in: checks its fixed arguments and the exact PCM it was fed."""
    return f'''#!/bin/sh
[ "$*" = "--max-seconds 90 --gain-db 6" ] || exit 9
printf '{{"event":"ready","rate":16000,"threshold":0.9800,"window":5,"cooldown_ms":2500,'
printf '"gain_db":6,"models":[{{"name":"komp","sha256":"{MODEL}","stride":3,'
printf '"arena_bytes":24624}}]}}\\n'
sum=$(sha256sum | cut -d ' ' -f 1)
sleep 1
if [ "$sum" = {pcm_sha256} ]; then
    printf '{{"event":"wake","model":"komp","t_ms":480,"probability":0.9900,"peak":0.9961,'
    printf '"last":0.9883,"trusted":false}}\\n{{"event":"end","reason":"eof","audio_ms":{audio_ms},'
    printf '"detections":[1]}}\\n'
else
    printf '{{"event":"end","reason":"eof","audio_ms":0,"detections":[0]}}\\n'
fi
'''.encode()


DETECTOR = stand_in(hashlib.sha256(PCM).hexdigest(), len(PCM) // 320 * 10)


def local(env: dict[str, str], command: str, data: bytes,
          extra: tuple[tuple[str, str], ...] = ()) -> bytes:
    """Run the fixed wrapper under dash against a synthetic root; detector /proc stays real."""
    return subprocess.run(localize(env, command, extra), input=data, capture_output=True,
                          timeout=30, env={**os.environ, **env}, check=True).stdout


def localize(env: dict[str, str], command: str,
             extra: tuple[tuple[str, str], ...] = ()) -> list[str]:
    """The fixed `sh -c` call as a local dash argv against the synthetic root."""
    argv = shlex.split(command)
    assert argv[:2] == ["sh", "-c"] and argv[3] == "sh"
    script = argv[2].replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
    for prefix in ("/proc/sys/", "/proc/net/", "/proc/$p/", "/system/"):
        script = script.replace(prefix, env["root"] + prefix)
    script = script.replace("DATA=/data", "DATA=" + env["root"] + "/data")
    for old, new in extra:
        assert old in script, old
        script = script.replace(old, new)
    return ["dash", "-c", script, *argv[3:]]


def setup(tmp_path: Path, client: bytes, port: int, monkeypatch: Any, awk: str = "gawk",
          tamper: bool = False) -> tuple[Controller, pilot.Pilot, list[str]]:
    if not shutil.which("dash") or not shutil.which(awk):
        pytest.skip("Synthetic shell checks need dash and the requested awk")
    env = device(tmp_path, port, awk)
    calls: list[str] = []

    def runner(_: Any, command: str) -> str:
        calls.append(command)
        assert isinstance(command, str)
        return local(env, command, b"").decode()

    def transfer(_: Any, request: Any, *, limit: int) -> str:
        calls.append(request.command)
        assert request.data == client + DETECTOR and limit == pilot.OUTPUT_LIMIT
        data = request.data[:-1] + b"X" if tamper else request.data
        return local(env, request.command, data).decode()

    controller = Controller(None, writes=True, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(pilot, "_ssh", lambda item: item)
    monkeypatch.setattr(pilot, "_transfer", transfer)
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(client).hexdigest())
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(DETECTOR).hexdigest())
    return controller, pilot.Pilot(1, client, DETECTOR, gain_db=6, port=port), calls


def vqe_server() -> FakeIPC:
    return FakeIPC([audio(channel(6, b"vqe_0", c, duration=16), full_message=True)
                    for c in CHUNKS] + [frame(field(7, b"x-" + SENTINEL), 3)])


@pytest.mark.parametrize("awk", ["gawk", "mawk"])
def test_end_to_end_pipes_pcm_into_the_detector_and_returns_only_events(
    tmp_path: Path, client: bytes, monkeypatch: Any, awk: str,
) -> None:
    server = vqe_server()
    controller, item, calls = setup(tmp_path, client, server.port, monkeypatch, awk)
    preview = pilot.preview(controller, "desk", item)
    assert preview["expires_in_seconds"] == 300 and preview["audio_exported"] is False
    assert "dd bs=1" not in calls[0]  # The inspect script has no transfer stanza at all.
    result = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert server.close() == [field(159, field(3, b"")), field(159, field(6, b""))]
    assert result["outcome"] == "complete", result
    assert result["detections"] == [{"word": "komp", "probability": 0.99, "t_ms": 480,
                                     "trusted": False}]
    assert result["models"] == ["komp"]
    io = result["io"]
    assert io["frames"] == 4 and io["audio_frames"] == 3 and io["other_frames"] == 1
    assert io["input"]["bytes"] == len(PCM) and io["input"]["peak_s16"] == 300
    assert io["input"]["rms_dbfs"] < 0 and io["unsubscribe_sent"] is True
    detector = result["detector"]
    assert detector["audio_ms"] == len(PCM) // 320 * 10 and detector["exit"] == 0
    assert detector["end_reason"] == "eof" and detector["vmhwm_kib"] > 0
    assert detector["cpu_ms"] == detector["cpu_ticks"] * 10 >= 0
    assert result["cleanup_verified"] and result["quasar_running"] and result["quasar_unchanged"]
    assert result["do_not_retry"] is True and result["audio_exported"] is False
    assert not any((tmp_path / "dev/data/opt").iterdir())  # Helpers removed, nothing else.
    assert SENTINEL.decode() not in json.dumps(result)
    with pytest.raises(SafetyError):  # One use.
        controller.take_plan(preview["plan_id"], preview["expected_sha256"])


def test_tampered_transfer_never_runs_and_is_cleaned(
    tmp_path: Path, client: bytes, monkeypatch: Any,
) -> None:
    controller, item, _ = setup(tmp_path, client, 9850, monkeypatch, tamper=True)
    preview = pilot.preview(controller, "desk", item)
    result = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert result["outcome"] == "unknown" and result["error_code"] == "helper_output_missing"
    assert result["cleanup_verified"] and result["quasar_unchanged"] and result["do_not_retry"]
    assert not any((tmp_path / "dev/data/opt").iterdir())


def test_changed_quasar_identity_blocks_before_any_helper_runs(
    tmp_path: Path, client: bytes, monkeypatch: Any,
) -> None:
    controller, item, _ = setup(tmp_path, client, 9850, monkeypatch)
    preview = pilot.preview(controller, "desk", item)
    stat = tmp_path / "dev/proc/123/stat"
    stat.write_text(stat.read_text().replace(" 777 ", " 778 "))  # Quasar restarted.
    result = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert result == {"outcome": "blocked", "error_code": "preview_state_changed",
                      "do_not_retry": True, "audio_exported": False}
    assert not any((tmp_path / "dev/data/opt").iterdir())


def test_listener_must_be_owned_by_quasar(tmp_path: Path, client: bytes,
                                          monkeypatch: Any) -> None:
    controller, item, _ = setup(tmp_path, client, 9850, monkeypatch)
    os.unlink(tmp_path / "dev/proc/123/fd/7")
    with pytest.raises(SafetyError, match="ipc_unavailable"):
        pilot.preview(controller, "desk", item)


@pytest.mark.parametrize("change", [
    {"model": "station-mini3"}, {"preauthorize_all_actions": False},
    {"controls": {"firmware_sha256": "a" * 64, "allowed_actions": ["speech.say"]}},
    {"controls": {"allowed_actions": ["speech.listen"]}},
])
def test_policy_gates(tmp_path: Path, monkeypatch: Any, change: dict[str, Any]) -> None:
    controller = Controller(None, writes=True, runner=lambda *_: pytest.fail("no SSH"))
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path, **change))
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"c").hexdigest())
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(b"d").hexdigest())
    with pytest.raises(SafetyError):
        pilot.preview(controller, "desk", pilot.Pilot(45, b"c", b"d"))
    controller.writes = False
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    with pytest.raises(SafetyError):
        pilot.preview(controller, "desk", pilot.Pilot(45, b"c", b"d"))


@pytest.mark.parametrize("item", [
    pilot.Pilot(0, b"c", b"d"), pilot.Pilot(61, b"c", b"d"),
    pilot.Pilot(True, b"c", b"d"),  # type: ignore[arg-type]
    pilot.Pilot(45, b"x", b"d"), pilot.Pilot(45, b"c", b"x"),
    pilot.Pilot(45, b"c", b"d", gain_db=31), pilot.Pilot(45, b"c", b"d", gain_db=-1),
    pilot.Pilot(45, b"c", b"d", port=80), pilot.Pilot(45, b"c", b"d", nonce="x"),
])
def test_inputs_and_both_pins_are_validated(monkeypatch: Any, item: Any) -> None:
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"c").hexdigest())
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(b"d").hexdigest())
    with pytest.raises(SafetyError):
        item.validate()


def test_pinned_sizes_fit_the_transfer_bounds(monkeypatch: Any, tmp_path: Path) -> None:
    client, detector = b"\0" * 19664, b"\1" * 604488  # docs/IO_CHANNEL.md pins.
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(client).hexdigest())
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(detector).hexdigest())
    item = pilot.Pilot(60, client, detector)
    request = pilot._command(station(tmp_path), item, "apply", "e" * 64)
    assert len(request.command.encode()) < 20000 and len(request.data) <= pcm_pilot.MAX_TRANSFER
    assert request.data == client + detector and " 19664 " in request.command
    assert pilot._command(station(tmp_path), item, "inspect").data == b""


# Strict result parser.

READY = {"event": "ready", "rate": 16000, "threshold": 0.98, "window": 5, "cooldown_ms": 2500,
         "gain_db": 0, "models": [{"name": "komp", "sha256": MODEL, "stride": 3,
                                   "arena_bytes": 24624}]}
WAKE = {"event": "wake", "model": "komp", "t_ms": 480, "probability": 0.99, "peak": 0.9961,
        "last": 0.9883, "trusted": False}
END = {"event": "end", "reason": "eof", "audio_ms": 40, "detections": [1]}
TRAILER = {"outcome": "cleanup", "cleanup_verified": True, "quasar_running": True,
           "quasar_unchanged": True, "detector_exit": 0, "detector_vmhwm_kib": 812,
           "detector_cpu_ticks": 37}


def report(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "outcome": "complete", "error_code": "none", "connected": True, "subscribe_sent": True,
        "unsubscribe_sent": True, "frames": 3, "audio_frames": 3, "other_frames": 0,
        "bytes": 1300, "first_audio_ms": 3, "elapsed_ms": 1001, "audio_exported": False,
        "other": [],
        "channels": [{"type": 6, "name": "vqe_0", "sample_rate": 16000, "format": 1,
                      "frames": 3, "bytes": 1300, "duration_ms": 40,
                      "bytes_per_sample_estimate": 2, "peak_s16": 300,
                      "mean_square_s16": 90000}],
    }
    value.update(changes)
    return value


def raw(*values: Any) -> str:
    return "\n".join(v if isinstance(v, str) else json.dumps(v) for v in values) + "\n"


GOOD = (READY, WAKE, report(), END, TRAILER)


def test_personalized_arm_pin_is_accepted_without_removing_the_old_build(
    monkeypatch: Any, tmp_path: Path,
) -> None:
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"client").hexdigest())
    monkeypatch.setattr(pilot, "FT_DETECTOR_SHA256", hashlib.sha256(b"new").hexdigest(),
                        raising=False)
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(b"old").hexdigest())
    for detector in (b"new", b"old"):
        item = pilot.Pilot(45, b"client", detector)
        item.validate()
        command = pilot._command(station(tmp_path), item, "apply", "e" * 64)
        assert hashlib.sha256(detector).hexdigest() in command.command
    with pytest.raises(SafetyError):
        pilot.Pilot(45, b"client", b"unreviewed").validate()


@pytest.mark.parametrize("bad", [False, True])
def test_live_wake_callback_is_validated_and_does_not_wait_for_cleanup(
    monkeypatch: Any, tmp_path: Path, bad: bool,
) -> None:
    c = Controller(None, writes=True, runner=lambda *_: raw({
        "outcome": "ready", "state_sha256": "e" * 64}))
    monkeypatch.setattr(c, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(pilot, "_ssh", lambda value: value)
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"client").hexdigest())
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(b"detector").hexdigest())
    item = pilot.Pilot(45, b"client", b"detector")
    seen = []

    def transfer(_: Any, request: Any, *, limit: int, on_line: Any) -> str:
        assert limit == pilot.OUTPUT_LIMIT
        on_line(raw(READY).encode())
        assert seen == [READY]
        on_line(raw(dict(WAKE, trusted=True) if bad else WAKE).encode())
        assert seen == [READY, WAKE]
        for value in GOOD[2:]:
            on_line(raw(value).encode())
        return raw(*GOOD)

    monkeypatch.setattr(pilot, "_transfer", transfer)
    plan = pilot.preview(c, "desk", item)
    result = pilot.apply(c, plan["plan_id"], plan["expected_sha256"], item,
                         on_event=seen.append)
    assert result["outcome"] == ("unknown" if bad else "complete")
    assert seen == ([READY] if bad else [READY, WAKE, END])
    assert result["do_not_retry"] is True


@pytest.mark.parametrize(("lines", "outcome", "code"), [
    (GOOD, "complete", "none"),
    ((READY, report(), WAKE, END, TRAILER), "complete", "none"),  # Interleaving is fine.
    ((READY, report(), dict(END, detections=[0]), TRAILER), "complete", "none"),
    ((READY, WAKE, report(), END, dict(TRAILER, quasar_unchanged=False)), "unknown",
     "quasar_changed_or_cleanup_unverified"),
    ((READY, WAKE, report(), END, dict(TRAILER, detector_exit=1)), "unknown",
     "detector_incomplete"),
    ((READY, WAKE, report(), dict(END, reason="limit"), TRAILER), "unknown",
     "detector_incomplete"),
    ((READY, WAKE, report(), TRAILER), "unknown", "detector_incomplete"),
    ((READY, WAKE, report(), dict(END, audio_ms=30), TRAILER), "unknown", "pcm_count_mismatch"),
    ((READY, report(outcome="unknown", error_code="output_error"), dict(END, detections=[0]),
      TRAILER), "unknown",
     "output_error"),
    ((report(), {"event": "error", "error": "arena"}, TRAILER), "unknown", "detector_error"),
    ((TRAILER,), "unknown", "helper_output_missing"),
    ((READY, WAKE, END, TRAILER), "unknown", "helper_output_invalid"),
    ((READY, READY, WAKE, report(), END, TRAILER), "unknown", "helper_output_invalid"),
    ((READY, dict(WAKE, model="other"), report(), END, TRAILER), "unknown",
     "helper_output_invalid"),
    ((READY, dict(WAKE, trusted=True), report(), END, TRAILER), "unknown",
     "helper_output_invalid"),
    ((READY, dict(WAKE, probability=1.5), report(), END, TRAILER), "unknown",
     "helper_output_invalid"),
    ((READY, dict(WAKE, extra=SENTINEL.decode()), report(), END, TRAILER), "unknown",
     "helper_output_invalid"),
    ((READY, {"event": "p", "model": "komp", "t_ms": 30, "q": 3}, report(), END, TRAILER),
     "unknown", "helper_output_invalid"),
    ((READY, WAKE, report(), dict(END, detections=[2]), TRAILER), "unknown",
     "helper_output_invalid"),
    ((READY, WAKE, report(channels=[dict(report()["channels"][0], name="raw_mic_0")]), END,
      TRAILER), "unknown", "helper_output_invalid"),
    ((READY, WAKE, report(audio_exported=True), END, TRAILER), "unknown",
     "helper_output_invalid"),
    ((READY, WAKE, report(), END, dict(TRAILER, extra=1)), "unknown", "helper_output_invalid"),
    ((READY, WAKE, report(), END, SENTINEL.decode()), "unknown", "helper_output_invalid"),
    ((READY, WAKE, report(), END, TRAILER, TRAILER), "unknown", "helper_output_invalid"),
    (({"outcome": "blocked", "error_code": "temporary_path_busy"},), "blocked",
     "temporary_path_busy"),
    (({"outcome": "blocked", "error_code": SENTINEL.decode()},), "unknown",
     "helper_output_invalid"),
])
def test_result_parser_is_strict_and_never_echoes_raw_output(
    lines: tuple[Any, ...], outcome: str, code: str,
) -> None:
    result = pilot.parse_result(raw(*lines), gain_db=0)
    assert result["outcome"] == outcome and result["error_code"] == code, result
    assert result["do_not_retry"] is True and result["audio_exported"] is False
    assert SENTINEL.decode() not in json.dumps(result)
    if outcome == "complete":
        assert result["detector"]["cpu_ms"] == 370 and result["detector"]["vmhwm_kib"] == 812


def test_parser_rejects_a_gain_the_pilot_did_not_send() -> None:
    assert pilot.parse_result(raw(*GOOD), gain_db=6)["error_code"] == "helper_output_invalid"


def test_operator_flow_requires_typed_word_after_state_bound_preview(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(pilot, "preview", lambda *a: calls.append("preview") or {
        "plan_id": "p", "expected_sha256": "e" * 64, "impact": "x", "audio_exported": False,
        "expires_in_seconds": 300})
    monkeypatch.setattr(pilot, "apply", lambda *a: calls.append("apply") or {"outcome": "x"})
    controller: Any = SimpleNamespace(take_plan=lambda *a: calls.append("consumed"))
    item = pilot.Pilot(45, b"c", b"d")
    prompts: list[str] = []
    cancelled = pilot.operator_run(controller, "desk", item,
                                   ask=lambda p: prompts.append(p) or "wake")
    assert cancelled["outcome"] == "cancelled" and cancelled["do_not_retry"] is True
    assert calls == ["preview", "consumed"] and "no audio" in prompts[0].lower()
    assert pilot.operator_run(controller, "desk", item, ask=lambda p: " WAKE\r") == \
        {"outcome": "x"}
    assert calls == ["preview", "consumed", "preview", "apply"]


def test_apply_uncertainty_is_never_retried(tmp_path: Path, monkeypatch: Any) -> None:
    attempts: list[int] = []

    def transfer(*_: Any, **__: Any) -> str:
        attempts.append(1)
        raise SafetyError("Local PCM transfer uncertain; do not retry")

    controller = Controller(None, writes=True, runner=lambda *_: (
        '{"outcome":"ready","state_sha256":"' + "c" * 64 + '"}\n'))
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(pilot, "_ssh", lambda item: item)
    monkeypatch.setattr(pilot, "_transfer", transfer)
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"c").hexdigest())
    monkeypatch.setattr(pilot, "DETECTOR_SHA256", hashlib.sha256(b"d").hexdigest())
    item = pilot.Pilot(45, b"c", b"d")
    preview = pilot.preview(controller, "desk", item)
    result = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert result["outcome"] == "unknown" and result["error_code"] == "transport_uncertain"
    assert attempts == [1] and result["do_not_retry"] is True
    again = pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert again["outcome"] == "blocked" and attempts == [1]  # Consumed: no second run.


def test_parser_accepts_a_long_live_stream() -> None:
    # Real Mini 1 shape: the window ends a millisecond late and the level pilot's 10 s cap
    # rejected every wake run; 45 s of vqe_0 must parse.
    channel = dict(report()["channels"][0], frames=2813, bytes=1440000, duration_ms=0,
                   bytes_per_sample_estimate=-1)
    long = report(elapsed_ms=45001, frames=2813, audio_frames=2813, bytes=1440000,
                  channels=[channel])
    result = pilot.parse_result(raw(READY, long, dict(END, audio_ms=45000, detections=[0]),
                                    TRAILER), gain_db=0)
    assert result["outcome"] == "complete", result
