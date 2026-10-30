"""Owned host jobs, siren composite and guarded reboot; every station response is synthetic."""

import asyncio
import base64
import copy
import hashlib
import json
import struct
import subprocess
import threading
import wave
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from alice_mcp import controls, device_runtime, local_tts, native_transport, owned_jobs, server
from alice_mcp.controls import invoke as real_invoke
from alice_mcp.core import MAX_OUTPUT, Controller, SafetyError, SSHInput

LED = {"red": 32, "green": 0, "blue": 0, "count": 1, "period_ms": 500}
BOOT = "0123abcd-0000-4000-8000-0123456789ab"


def station_entry(tmp_path: Path) -> dict[str, Any]:
    key, hosts = tmp_path / "identity", tmp_path / "hosts"
    key.touch(mode=0o600)
    hosts.touch()
    return copy.deepcopy({
        "id": "desk", "model": "station-mini3", "host": "station.invalid", "port": 2222,
        "user": "root", "identity_file": str(key), "known_hosts_file": str(hosts),
        "preauthorize_all_actions": True,
        "controls": {
            "firmware_sha256": "a" * 64,
            "allowed_actions": ["led.effect", "audio.volume", "audio.play", "restore",
                                "speech.say"],
            "tts_client": {"path": "/data/opt/tts", "sha256": "b" * 64},
            "media": {"siren": {"path": "/data/sounds/alice-siren.wav",
                                "sha256": owned_jobs.SIREN_WAV_SHA256},
                      "chime": {"path": "/data/sounds/chime.wav", "sha256": "c" * 64}},
        },
        "jobs": {
            "presets": {"done": [{"action": "led.effect", "parameters": LED},
                                 {"action": "audio.play",
                                  "parameters": {"asset": "chime", "duration_seconds": 2}}]},
            "siren": {"asset": "siren", "volume": 10,
                      "led": {"action": "led.effect", "parameters": LED}},
        },
    })


def write(path: Path, entry: dict[str, Any]) -> None:
    path.write_text(json.dumps({"stations": [entry]}))
    path.chmod(0o600)


@pytest.fixture
def jobs(tmp_path: Path, monkeypatch: Any) -> Any:
    path = tmp_path / "inventory.json"
    entry = station_entry(tmp_path)
    write(path, entry)
    now = [0.0]
    calls: list[tuple[str, str, dict[str, Any]]] = []
    fail: dict[str, str] = {}

    def invoke(controller: Controller, station: Any, request: dict[str, Any]) -> dict[str, Any]:
        calls.append((request["mode"], request["action"], copy.deepcopy(request["params"])))
        if request["mode"] == "inspect":
            return {"outcome": "ready", "state_sha256": "d" * 64}
        if fail.get(request["action"]) == "raise":
            raise SafetyError("SENSITIVE_SENTINEL")
        if request["action"] in fail:
            return {"outcome": fail[request["action"]]}
        result: dict[str, Any] = {"outcome": "applied"}
        if request["action"] == "audio.volume":
            result["backup_id"] = request["nonce"]
        return result

    monkeypatch.setattr(controls, "invoke", invoke)
    controller = Controller(path, writes=True, clock=lambda: now[0])
    return controller, calls, now, entry, fail


def applied(calls: list[tuple[str, str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    return [(action, params) for mode, action, params in calls if mode == "apply"]


def finish(controller: Controller, job_id: str) -> dict[str, Any]:
    thread = controller.jobs[job_id].thread
    assert thread is not None
    thread.join(5)
    assert not thread.is_alive()
    reports = {r["job_id"]: r for r in owned_jobs.list_jobs(controller)["jobs"]}
    return reports[job_id]


def test_siren_tone_is_generated_bounded_and_pinned() -> None:
    data = owned_jobs.siren_wav()
    assert hashlib.sha256(data).hexdigest() == owned_jobs.SIREN_WAV_SHA256
    assert data == owned_jobs.siren_wav()  # Integer synthesis: identical on every host.
    assert device_runtime._media(data, "/data/sounds/alice-siren.wav")
    assert len(data) <= device_runtime.MAX_MEDIA
    with wave.open(BytesIO(data)) as source:
        assert (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (1, 2, 8000)
        frames = source.readframes(source.getnframes())
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    assert 8000 < max(abs(s) for s in samples) <= 8191 and samples[0] == samples[-1] == 0
    assert len(samples) / 8000 <= owned_jobs.SIREN_PLAY_SECONDS


def test_tone_upload_fits_the_bounded_helper_with_margin(jobs: Any, monkeypatch: Any) -> None:
    controller = jobs[0]
    sizes: list[int] = []
    monkeypatch.setattr(controller, "runner", lambda station, command: (
        sizes.append(len(command.command) + len(command.data)) or
        '{"outcome":"ready","state_sha256":"' + "d" * 64 + '"}'))
    station = controller.station("desk")
    request = {"schema": device_runtime.SCHEMA, "mode": "inspect", "model": station.model,
               "policy": station.controls, "action": "media.upload", "nonce": "e" * 32,
               "params": {"asset": "siren", "content_base64":
                          base64.b64encode(owned_jobs.siren_wav()).decode()}}
    assert real_invoke(controller, station, request)["outcome"] == "ready"
    # Headroom for helper growth; the upload must never be split into retried writes.
    assert sizes and controls.MAX_REQUEST_COMMAND - sizes[0] >= 16 * 1024


def test_timer_fires_declared_preset_once_through_fresh_previews(jobs: Any) -> None:
    controller, calls, now, _, _ = jobs
    started = owned_jobs.start(controller, "desk", "timer", {"seconds": 5, "preset": "done"})
    assert started["state"] == "scheduled" and started["remaining_seconds"] == 5
    assert started["survives_host_restart"] is False and calls == []
    now[0] = 6.0
    report = finish(controller, started["job_id"])
    assert report["state"] == "complete" and report["step_outcomes"] == ["applied", "applied"]
    assert [mode for mode, _, _ in calls] == ["inspect", "inspect", "apply"] * 2
    assert applied(calls) == [("led.effect", LED),
                              ("audio.play", {"asset": "chime", "duration_seconds": 2})]
    assert controller.plans == {}  # Every step preview was consumed.


def test_cancel_is_by_owned_id_and_never_claims_all_sound_stopped(jobs: Any) -> None:
    controller, calls, now, _, _ = jobs
    first = owned_jobs.start(controller, "desk", "timer", {"seconds": 60, "preset": "done"})
    second = owned_jobs.start(controller, "desk", "timer", {"seconds": 60, "preset": "done"})
    result = owned_jobs.cancel(controller, first["job_id"])
    assert result["cancel_requested"] == [first["job_id"]]
    assert result["all_sound_stopped"] is False
    with pytest.raises(SafetyError):
        owned_jobs.cancel(controller, "f" * 32)
    with pytest.raises(SafetyError):
        owned_jobs.cancel(controller, "maind")
    assert finish(controller, first["job_id"])["state"] == "cancelled"
    assert owned_jobs.cancel(controller, "all")["cancel_requested"] == [second["job_id"]]
    now[0] = 120.0
    assert finish(controller, second["job_id"])["state"] == "cancelled"
    assert calls == []


@pytest.mark.parametrize("delegation", ["none", "selective_partial", "selective_full"])
def test_background_steps_require_standing_delegation(jobs: Any, tmp_path: Path,
                                                      delegation: str) -> None:
    controller, calls, now, entry, _ = jobs
    entry["preauthorize_all_actions"] = False
    if delegation != "none":
        entry["controls"]["preauthorized_actions"] = (
            ["led.effect"] if delegation == "selective_partial" else ["led.effect", "audio.play"])
    write(controller.inventory, entry)
    if delegation != "selective_full":
        with pytest.raises(SafetyError):
            owned_jobs.start(controller, "desk", "preset", {"preset": "done"})
        assert calls == []
        return
    report = finish(controller, owned_jobs.start(controller, "desk", "preset",
                                                 {"preset": "done"})["job_id"])
    assert report["state"] == "complete"


@pytest.mark.parametrize("change", ["revoke", "writes", "config"])
def test_changes_before_fire_block_the_job_without_writes(jobs: Any, change: str) -> None:
    controller, calls, now, entry, _ = jobs
    job = owned_jobs.start(controller, "desk", "timer", {"seconds": 30, "preset": "done"})
    if change == "revoke":
        entry["preauthorize_all_actions"] = False
        write(controller.inventory, entry)
    elif change == "writes":
        controller.writes = False
    else:
        entry["controls"]["max_volume"] = 20
        write(controller.inventory, entry)
    now[0] = 31.0
    report = finish(controller, job["job_id"])
    assert report["state"] == "blocked" and applied(calls) == []


@pytest.mark.parametrize("failure", ["raise", "unknown"])
def test_uncertain_step_stops_the_job_and_is_never_retried(jobs: Any, failure: str) -> None:
    controller, calls, now, _, fail = jobs
    fail["led.effect"] = failure
    report = finish(controller, owned_jobs.start(controller, "desk", "preset",
                                                 {"preset": "done"})["job_id"])
    assert report["state"] == "unknown" and report["do_not_retry"] is True
    assert report["step_outcomes"] == ["unknown"]
    assert applied(calls) == [("led.effect", LED)]
    assert "SENSITIVE_SENTINEL" not in json.dumps(owned_jobs.list_jobs(controller))


def test_siren_full_is_low_volume_bounded_and_restores_volume(jobs: Any) -> None:
    controller, calls, _, _, _ = jobs
    report = finish(controller, owned_jobs.start(controller, "desk", "siren",
                                                 {"mode": "full", "repeats": 2})["job_id"])
    play = {"asset": "siren", "duration_seconds": owned_jobs.SIREN_PLAY_SECONDS}
    backup = report["volume_backup_id"]
    assert applied(calls) == [
        ("audio.volume", {"percent": 10}), ("speech.say", {"text": owned_jobs.SIREN_TEXT}),
        ("audio.play", play), ("led.effect", LED), ("audio.play", play), ("led.effect", LED),
        ("restore", {"backup_id": backup}),
    ]
    assert report["state"] == "complete" and report["volume_restored"] is True


@pytest.mark.parametrize("mode,expected", [
    ("sound_only", ["audio.volume", "audio.play", "audio.play", "audio.play", "restore"]),
    ("test", ["audio.volume", "audio.play", "led.effect", "restore"]),
])
def test_siren_modes(jobs: Any, mode: str, expected: list[str]) -> None:
    controller, calls, _, _, _ = jobs
    finish(controller, owned_jobs.start(controller, "desk", "siren", {"mode": mode})["job_id"])
    assert [action for action, _ in applied(calls)] == expected


@pytest.mark.parametrize("failure", ["cancel", "unknown_play", "blocked_play"])
def test_siren_restores_volume_after_cancel_or_failed_step(jobs: Any, monkeypatch: Any,
                                                           failure: str) -> None:
    controller, calls, _, _, fail = jobs
    if failure == "cancel":
        original = controls.invoke

        def invoke(*args: Any) -> dict[str, Any]:
            result = original(*args)
            if args[2]["mode"] == "apply" and args[2]["action"] == "audio.volume":
                owned_jobs.start(controller, "desk", "siren", {"mode": "off"})
            return result

        monkeypatch.setattr(controls, "invoke", invoke)
    else:
        fail["audio.play"] = "unknown" if failure == "unknown_play" else "conflict"
    job = owned_jobs.start(controller, "desk", "siren", {"mode": "full"})
    report = finish(controller, job["job_id"])
    actions = [action for action, _ in applied(calls)]
    assert actions[-1] == "restore" and actions.count("restore") == 1
    assert report["volume_restored"] is True
    assert report["state"] == {"cancel": "cancelled", "unknown_play": "unknown",
                               "blocked_play": "blocked"}[failure]
    if failure == "cancel":
        assert actions == ["audio.volume", "restore"]


def test_siren_without_verified_backup_plays_nothing(jobs: Any) -> None:
    controller, calls, _, _, fail = jobs
    fail["audio.volume"] = "unknown"
    report = finish(controller, owned_jobs.start(controller, "desk", "siren",
                                                 {"mode": "test"})["job_id"])
    assert [action for action, _ in applied(calls)] == ["audio.volume"]
    assert report["state"] == "unknown" and report["volume_restored"] is False


def test_one_siren_per_station_and_off_cancels_only_sirens(jobs: Any, monkeypatch: Any) -> None:
    controller, _, _, _, _ = jobs
    release, original = threading.Event(), controls.invoke

    def invoke(*args: Any) -> dict[str, Any]:
        if args[2]["mode"] == "apply":
            assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(controls, "invoke", invoke)
    timer = owned_jobs.start(controller, "desk", "timer", {"seconds": 60, "preset": "done"})
    siren = owned_jobs.start(controller, "desk", "siren", {"mode": "full"})
    with pytest.raises(SafetyError):
        owned_jobs.start(controller, "desk", "siren", {"mode": "test"})
    off = owned_jobs.start(controller, "desk", "siren", {"mode": "off"})
    release.set()
    assert off["cancel_requested"] == [siren["job_id"]]
    assert finish(controller, siren["job_id"])["state"] == "cancelled"
    assert owned_jobs.cancel(controller, "all")["cancel_requested"] == [timer["job_id"]]


@pytest.mark.parametrize("kind,params", [
    ("timer", {"seconds": 4, "preset": "done"}),
    ("timer", {"seconds": 86401, "preset": "done"}),
    ("timer", {"seconds": True, "preset": "done"}),
    ("timer", {"seconds": 60, "preset": "missing"}),
    ("timer", {"seconds": 60}),
    ("preset", {"preset": "done", "extra": 1}),
    ("preset", {"preset": "../done"}),
    ("siren", {"mode": "full", "repeats": 6}),
    ("siren", {"mode": "test", "repeats": 1}),
    ("siren", {"mode": "loud"}),
    ("reboot", {}),
    ("shell", {"command": "true"}),
])
def test_job_parameters_are_bounded(jobs: Any, kind: str, params: dict[str, Any]) -> None:
    controller, calls, _, _, _ = jobs
    with pytest.raises(SafetyError):
        owned_jobs.start(controller, "desk", kind, params)
    assert calls == [] and controller.jobs == {}


def test_active_jobs_are_bounded(jobs: Any) -> None:
    controller = jobs[0]
    for _ in range(owned_jobs.MAX_ACTIVE):
        owned_jobs.start(controller, "desk", "timer", {"seconds": 60, "preset": "done"})
    with pytest.raises(SafetyError):
        owned_jobs.start(controller, "desk", "timer", {"seconds": 60, "preset": "done"})
    owned_jobs.cancel(controller, "all")


@pytest.mark.parametrize("change", [
    lambda e: e["jobs"]["presets"]["done"].append({"action": "restore",
                                                   "parameters": {"backup_id": "c" * 32}}),
    lambda e: e["jobs"]["presets"]["done"].append({"action": "media.upload", "parameters": {}}),
    lambda e: e["jobs"]["presets"]["done"].append({"action": "speech.listen",
                                                   "parameters": {"duration_seconds": 5}}),
    lambda e: e["jobs"]["presets"]["done"].append({"action": "reboot", "parameters": {}}),
    lambda e: e["jobs"]["presets"]["done"][0]["parameters"].update(red=255),
    lambda e: e["jobs"]["presets"]["done"][0].update(command="reboot"),
    lambda e: e["jobs"]["presets"]["done"].extend([{"action": "led.effect",
                                                    "parameters": LED}] * 7),
    lambda e: e["jobs"]["presets"].update({"Bad Name": []}),
    lambda e: e["jobs"]["presets"].update(empty=[]),
    lambda e: e["jobs"]["siren"].update(volume=31),
    lambda e: e["jobs"]["siren"].update(asset="chime"),
    lambda e: e["jobs"].update(shell="true"),
    lambda e: e.update(allow_reboot="yes"),
])
def test_private_job_policy_is_strict(jobs: Any, change: Any) -> None:
    controller, _, _, entry, _ = jobs
    change(entry)
    write(controller.inventory, entry)
    with pytest.raises(SafetyError):
        controller.station("desk")


def test_mcp_tools_expose_jobs_without_private_values(jobs: Any, monkeypatch: Any) -> None:
    controller, _, _, _, _ = jobs
    monkeypatch.setattr(server, "controller", controller)
    started = asyncio.run(server.start_job("desk", "timer", {"seconds": 60, "preset": "done"}))
    assert server.get_station_controls("desk")["presets"] == ["done"]
    listed = server.list_jobs()
    assert [j["job_id"] for j in listed["jobs"]] == [started["job_id"]]
    assert "chime.wav" not in json.dumps(listed) and "station.invalid" not in json.dumps(listed)
    cancelled = asyncio.run(server.cancel_job(started["job_id"]))
    assert cancelled["cancel_requested"] == [started["job_id"]]
    assert asyncio.run(server.start_job("desk", "preset", {"preset": "nope"}))["outcome"] == (
        "blocked")


# Reboot: synthetic runner only. Never execute generated reboot shell on the host.


@pytest.fixture
def reboot(jobs: Any, monkeypatch: Any) -> Any:
    controller, _, _, entry, _ = jobs
    entry["allow_reboot"] = True
    write(controller.inventory, entry)
    commands: list[str] = []
    state = {"boot": BOOT, "firmware": "a" * 64, "apply": "outcome=requested\n"}

    def runner(station: Any, command: str | SSHInput) -> str:
        assert isinstance(command, str)
        commands.append(command)
        if "outcome=requested" in command:
            if state["apply"] == "raise":
                raise SafetyError("SSH timed out; do not automatically retry writes")
            return state["apply"]
        return (f"boot_id={state['boot']}\nfirmware={state['firmware']}\nreboot_tool=1\n")

    monkeypatch.setattr(controller, "runner", runner)
    return controller, commands, state, entry


def test_reboot_can_be_disabled_and_is_separate_from_service_policy(jobs: Any) -> None:
    controller, _, _, entry, _ = jobs
    assert controller.station("desk").allow_reboot is True  # Owner default (2026-10-29).
    entry.update(allow_service_actions=True, quasar_script_sha256="e" * 64, allow_reboot=False)
    write(controller.inventory, entry)
    with pytest.raises(SafetyError):
        controller.preview_service("desk", "reboot")
    with pytest.raises(SafetyError):
        controller.preview_service("desk", "stop")


def test_reboot_preview_binds_boot_and_firmware_then_dispatches_once(reboot: Any) -> None:
    controller, commands, _, _ = reboot
    plan = controller.preview_service("desk", "reboot")
    assert plan["approval_required"] is False and len(commands) == 1
    assert BOOT not in json.dumps(plan)
    result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert result == {"outcome": "requested", "reboot_verified": False, "do_not_retry": True,
                      "next_step": owned_jobs.REBOOT_NEXT_STEP, "evidence_class": "analytical"}
    apply = commands[-1]
    assert len(commands) == 3 and BOOT in apply and "a" * 64 in apply
    assert apply.index(BOOT) < apply.index("reboot ") and "outcome=conflict" in apply
    subprocess.run(["sh", "-n", "-c", apply], check=True)  # Parse only; never executed.
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])


@pytest.mark.parametrize("change", ["boot", "firmware", "revoked", "job"])
def test_reboot_state_or_policy_change_dispatches_nothing(reboot: Any, change: str) -> None:
    controller, commands, state, entry = reboot
    plan = controller.preview_service("desk", "reboot")
    if change in {"boot", "firmware"}:
        state[change] = "1123abcd-0000-4000-8000-0123456789ab" if change == "boot" else "f" * 64
    elif change == "revoked":
        entry["allow_reboot"] = False
        write(controller.inventory, entry)
    else:
        owned_jobs.start(controller, "desk", "timer", {"seconds": 60, "preset": "done"})
    with pytest.raises(SafetyError):
        controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    if change == "job":
        owned_jobs.cancel(controller, "all")
    assert not any("outcome=requested" in c for c in commands)


@pytest.mark.parametrize("reply", ["raise", "outcome=other\n", "x" * (MAX_OUTPUT + 1)],
                         ids=["transport", "unexpected", "oversized"])
def test_uncertain_reboot_is_unknown_and_not_retried(reboot: Any, reply: str) -> None:
    controller, commands, state, _ = reboot
    state["apply"] = reply
    plan = controller.preview_service("desk", "reboot")
    result = controller.apply_service(plan["plan_id"], plan["expected_sha256"])
    assert result["outcome"] == "unknown" and result["do_not_retry"] is True
    assert sum("outcome=requested" in c for c in commands) == 1


def test_reboot_on_native_station_never_falls_back_to_ssh(reboot: Any, tmp_path: Path) -> None:
    controller, commands, _, entry = reboot
    ca, token = tmp_path / "ca.pem", tmp_path / "credential"
    ca.touch()
    token.touch(mode=0o600)
    entry["native_mcp"] = {"url": "https://station.invalid:18443/mcp", "ca_file": str(ca),
                           "token_file": str(token)}
    write(controller.inventory, entry)
    with pytest.raises(SafetyError):
        controller.preview_service("desk", "reboot")
    assert commands == []


def test_reboot_approval_form_names_the_disruption(reboot: Any, monkeypatch: Any) -> None:
    controller, _, _, entry = reboot
    entry["preauthorize_all_actions"] = False
    write(controller.inventory, entry)
    monkeypatch.setattr(server, "controller", controller)
    plan = controller.preview_service("desk", "reboot")
    assert plan["approval_required"] is True
    context = AsyncMock()
    context.elicit.return_value = type("Answer", (), {"action": "decline", "data": None})()
    result = asyncio.run(server.apply_service_action(plan["plan_id"], plan["expected_sha256"],
                                                     context))
    assert result["outcome"] == "cancelled"
    assert "reboot" in context.elicit.call_args.args[0].lower()


def test_operator_cli_previews_by_default_and_requires_the_exact_word(
    jobs: Any, monkeypatch: Any, capsys: Any,
) -> None:
    controller, calls, _, _, _ = jobs
    monkeypatch.setattr(owned_jobs, "Controller", lambda *args, **kwargs: controller)
    monkeypatch.setenv("ALICE_MCP_INVENTORY", str(controller.inventory))
    owned_jobs.main(["siren", "--station", "desk", "--mode", "test"])
    preview = json.loads(capsys.readouterr().out)
    assert preview["outcome"] == "preview"
    assert [p["outcome"] for p in preview["step_previews"]] == ["ready"] * 3
    assert {mode for mode, _, _ in calls} == {"inspect"} and controller.plans == {}
    monkeypatch.setattr(owned_jobs.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *args: "siren")
    owned_jobs.main(["siren", "--station", "desk", "--mode", "test", "--live"])
    assert json.loads(capsys.readouterr().out)["outcome"] == "not_confirmed"
    assert applied(calls) == []


@pytest.fixture
def native_jobs(tmp_path: Path, monkeypatch: Any) -> Any:
    ca, credential, tone = tmp_path / "ca", tmp_path / "credential", tmp_path / "siren.wav"
    ca.write_bytes(b"synthetic")
    credential.write_bytes(b"a" * 64)
    credential.chmod(0o600)
    tone.write_bytes(owned_jobs.siren_wav(16000))
    entry = {
        "id": "desk", "model": "station-mini", "preauthorize_all_actions": True,
        "native_mcp": {"url": "https://station.invalid:18443/mcp", "ca_file": str(ca),
                       "token_file": str(credential)},
        "controls": {"firmware_sha256": "a" * 64, "max_volume": 30,
                     "allowed_actions": ["audio.volume", "audio.play", "speech.say"],
                     "local_media": {"siren": {"file": str(tone),
                                               "sha256": owned_jobs.NATIVE_SIREN_WAV_SHA256}}},
        "jobs": {"siren": {"asset": "siren", "volume": 10},
                 "presets": {"hush": [
                     {"action": "audio.volume", "parameters": {"step": "quieter"}},
                     {"action": "audio.play",
                      "parameters": {"asset": "siren", "duration_seconds": 2}}]}},
    }
    path = tmp_path / "inventory.json"
    write(path, entry)
    calls: list[tuple[str, dict[str, Any]]] = []
    previous = [25]

    def run_action(controller: Any, station: Any, action: str,
                   params: dict[str, Any]) -> dict[str, Any]:
        calls.append((action, copy.deepcopy(params)))
        if action == "audio.volume" and params.get("percent", 0) > 30:
            raise SafetyError("Invalid native action parameters")  # Host ceiling, as in W1.
        if action == "audio.volume":
            return {"outcome": "applied", "previous_percent": previous[0],
                    "percent": params.get("percent", 15)}
        return {"outcome": "applied"}

    monkeypatch.setattr(native_transport, "run_action", run_action)
    controller = Controller(path, writes=True, clock=lambda: 0.0)
    return controller, calls, previous


def test_native_siren_tone_is_16khz_pinned_and_playable() -> None:
    data = owned_jobs.siren_wav(16000)
    assert hashlib.sha256(data).hexdigest() == owned_jobs.NATIVE_SIREN_WAV_SHA256
    assert local_tts.wav_parameters(data)["sha256"] == owned_jobs.NATIVE_SIREN_WAV_SHA256


def test_native_jobs_use_typed_adapters_and_restore_previous_volume(native_jobs: Any) -> None:
    controller, calls, _ = native_jobs
    play = {"asset": "siren", "duration_seconds": owned_jobs.SIREN_PLAY_SECONDS}
    report = finish(controller, owned_jobs.start(controller, "desk", "siren",
                                                 {"mode": "test"})["job_id"])
    assert calls == [("audio.volume", {"percent": 10}), ("audio.play", play),
                     ("audio.volume", {"percent": 25})]
    assert report["state"] == "complete" and report["volume_restored"] is True
    calls.clear()
    finish(controller, owned_jobs.start(controller, "desk", "preset", {"preset": "hush"})["job_id"])
    assert calls == [("audio.volume", {"step": "quieter"}), ("audio.play", play)]


def test_native_siren_never_claims_restore_above_the_ceiling(native_jobs: Any) -> None:
    controller, calls, previous = native_jobs
    previous[0] = 60
    report = finish(controller, owned_jobs.start(controller, "desk", "siren",
                                                 {"mode": "test"})["job_id"])
    assert calls[-1] == ("audio.volume", {"percent": 60})
    assert report["volume_restored"] is False


def test_native_tone_is_written_create_only_and_never_uploaded(
        native_jobs: Any, monkeypatch: Any, capsys: Any, tmp_path: Path) -> None:
    controller, calls, _ = native_jobs
    out = tmp_path / "private" / "native-siren.wav"
    out.parent.mkdir(mode=0o700)
    monkeypatch.setattr(owned_jobs, "Controller", lambda *args, **kwargs: controller)
    monkeypatch.setenv("ALICE_MCP_INVENTORY", str(controller.inventory))
    owned_jobs.main(["tone", "--station", "desk", "--out", str(out)])
    assert json.loads(capsys.readouterr().out)["outcome"] == "preview" and not out.exists()
    monkeypatch.setattr(owned_jobs.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *args: "UPLOAD")
    owned_jobs.main(["tone", "--station", "desk", "--out", str(out), "--live"])
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "written" and report["sha256"] == owned_jobs.NATIVE_SIREN_WAV_SHA256
    assert out.read_bytes() == owned_jobs.siren_wav(16000) and out.stat().st_mode & 0o777 == 0o600
    owned_jobs.main(["tone", "--station", "desk", "--out", str(out), "--live"])
    assert json.loads(capsys.readouterr().out)["outcome"] == "blocked"  # Never overwritten.
    assert calls == []
