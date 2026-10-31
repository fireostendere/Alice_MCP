"""Untrusted station transcripts and explicit source configuration; no device access."""

import json

import pytest

from alice_mcp import device_runtime as rt
from alice_mcp import station_asr as asr
from alice_mcp.core import SafetyError


def test_station_wake_stream_is_typed_pinned_and_bounded() -> None:
    from test_wake_pilot import END, READY, WAKE

    ready = json.loads(json.dumps(READY))
    ready["models"][0]["sha256"] = asr.WAKE_MODEL_SHA256
    events = asr.WakeEvents()
    assert events.accept(json.dumps(ready).encode()) is None
    event = events.accept(json.dumps(WAKE).encode())
    assert event == {"event": "wake", "audio_ms": WAKE["t_ms"], "trusted": False}
    assert events.accept(json.dumps(dict(END, audio_ms=500)).encode()) is None
    assert events.complete
    for bad in (dict(WAKE, trusted=True), dict(WAKE, model="other"),
                dict(WAKE, t_ms=3600001), dict(WAKE, private="sentinel")):
        stream = asr.WakeEvents()
        stream.accept(json.dumps(ready).encode())
        with pytest.raises(SafetyError):
            stream.accept(json.dumps(bad).encode())
    stream = asr.WakeEvents()
    ready["models"][0]["sha256"] = "0" * 64
    with pytest.raises(SafetyError):
        stream.accept(json.dumps(ready).encode())


def test_station_asr_source_requires_only_its_restricted_install_directory() -> None:
    voice = {
        "source": "station_asr",
        "asr_directory": "/bkupgrade/data/opt/alice-local-asr-123456abcdef",
    }
    assert rt.validate_policy({"firmware_sha256": "a" * 64, "voice": voice}, "station-mini")[
        "voice"
    ] == {**voice, "autostart": False}
    for bad in ("/tmp/x", voice["asr_directory"] + "/../other", "relative", "$(id)"):
        with pytest.raises(ValueError):
            rt.validate_policy(
                {"firmware_sha256": "a" * 64, "voice": {**voice, "asr_directory": bad}},
                "station-mini",
            )


def test_station_events_validate_text_trust_rate_and_bounds() -> None:
    event = {
        "event": "final",
        "result": {"text": "компьютер давай поговорим"},
        "audio_ms": 3200,
        "elapsed_ms": 3400,
        "trusted": False,
    }
    assert asr.parse(json.dumps(event).encode()) == event
    for bad in (
        {**event, "trusted": True},
        {**event, "audio_ms": -1},
        {**event, "audio_ms": True},
        {**event, "result": {"text": "x" * 8193}},
        {**event, "action": "execute"},
        {**event, "result": {"text": 1}},
        {"event": "ready", "rate": 48000, "load_ms": 1},
    ):
        with pytest.raises(SafetyError):
            asr.parse(json.dumps(bad).encode())
    with pytest.raises(SafetyError):
        asr.parse(b'{"event":"ready","event":"final"}')


def test_station_script_uses_bounded_pinned_stdin_instead_of_long_ssh_exec() -> None:
    from types import SimpleNamespace
    station = SimpleNamespace(controls={"firmware_sha256": "a" * 64, "voice": {
        "asr_directory": "/bkupgrade/data/opt/alice-local-asr-123456abcdef"}})
    request = asr.request(station, "apply", "b" * 64)
    assert len(request.command) < 1024
    assert "dd bs=1 count=" in request.command and "sha256sum" in request.command

    import subprocess
    corrupt = subprocess.run(["sh", "-c", request.command], input=request.data[:-1] + b"x",
                             capture_output=True, timeout=5)
    assert corrupt.returncode == 1 and not corrupt.stdout
    assert not corrupt.stderr  # The loader shares stderr with the keyword event protocol.


def test_failed_station_start_reports_resource_guard_and_decoder_exit() -> None:
    event = {"event": "cleanup", "outcome": "unknown", "reason": "memory_guard",
             "cleanup_verified": True, "quasar_unchanged": True,
             "decoder_exit": 143, "memory_low_kib": 11000}
    assert asr.parse(json.dumps(event).encode()) == event


def test_activity_is_typed_and_never_marked_as_recognized_text() -> None:
    event = {'event': 'activity', 'audio_ms': 1700, 'trusted': False}
    assert asr.parse(json.dumps(event).encode()) == event
    for bad in ({**event, 'trusted': True}, {**event, 'audio_ms': -1},
                {**event, 'text': 'not a transcript'}):
        with pytest.raises(SafetyError):
            asr.parse(json.dumps(bad).encode())
