"""Synthetic resident peers only; no Station, TLS endpoint, mixer or household audio is used."""

import base64
import hashlib
import io
import json
import wave
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import controls, native_acceptance, native_transport
from alice_mcp.core import Controller, SafetyError, load_inventory


def clip(seconds: float = 0.5, rate: int = 16000, sample: int = 1000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * int(rate * seconds))
    return buffer.getvalue()


@pytest.fixture
def station(tmp_path: Path, monkeypatch: Any) -> Any:
    ca, credential, chime = tmp_path / "ca", tmp_path / "credential", tmp_path / "chime.wav"
    ca.write_bytes(b"synthetic")
    credential.write_bytes(b"a" * 64)
    credential.chmod(0o600)
    chime.write_bytes(clip())
    data = {"stations": [{
        "id": "desk", "model": "station-mini", "preauthorize_all_actions": True,
        "native_mcp": {"url": "https://station.invalid:18443/mcp", "ca_file": str(ca),
                       "token_file": str(credential)},
        "controls": {"firmware_sha256": "a" * 64, "max_volume": 30,
                     "allowed_actions": ["audio.volume", "audio.play"],
                     "local_media": {"chime": {"file": str(chime),
                                               "sha256": hashlib.sha256(clip()).hexdigest()}}},
    }]}
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(data))
    inventory.chmod(0o600)
    calls: list[tuple[str, dict[str, Any]]] = []
    replies: dict[str, Any] = {"audio.volume": {}, "audio.play": {}, "probe": {}}

    def peer(target: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((tool, arguments))
        if tool == "get_station_controls":
            actions = target.controls["allowed_actions"]
            return {"station_id": "desk", "model": "station-mini", "writes_enabled": True,
                    "allowed_actions": actions, "preauthorize_all_actions": True,
                    "preauthorized_actions": actions, "transport": "streamable_http_tls",
                    "state_contract": "alice.native.state.v1", "state_sha256": "c" * 64,
                    "tts_backend": "local_pcm"}
        if tool == "station_probe":
            data = replies.get("probes", {}).get(arguments["probe"], replies["probe"])
            return {"evidence_class": "analytical", "data": data}
        assert tool == "run_station_action"
        reply = replies[arguments["action"]]
        return dict(reply.pop(0) if isinstance(reply, list) else reply)

    monkeypatch.setattr(native_transport, "call", peer)

    def no_ssh(*args: Any) -> str:
        raise AssertionError("Native transport must never fall back to SSH")

    controller = Controller(inventory, writes=True, runner=no_ssh)
    return controller, calls, replies, data, chime


def run(controller: Controller, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
    try:
        return native_transport.run_action(controller, controller.station("desk"), action,
                                           parameters)
    except SafetyError as error:
        return {"outcome": "blocked", "error": str(error)}


def volume(previous: int, percent: int | None, outcome: str = "applied") -> dict[str, Any]:
    return {"outcome": outcome, "previous_percent": previous, "percent": percent,
            "quasar_running": True, "do_not_retry": True, "evidence_class": "analytical",
            "physical_effects_verified": False}


def dispatched(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [arguments for tool, arguments in calls if tool == "run_station_action"]


@pytest.mark.parametrize("name,data", [
    ("storage", {"total_kib": 1000, "used_kib": 400, "free_kib": 500}),
    ("services", {"maind": True, "quasar_launcher2": True, "dropbear": False,
                  "shairport-sync": False, "updatesd": True, "agent": True}),
    ("audio", {"sound_devices": True, "volume_control": "ready", "volume_percent": 25}),
    ("audio", {"sound_devices": True, "volume_control": "unconfigured", "volume_percent": None}),
])
def test_native_typed_probes_return_only_allowlisted_values(station: Any, name: str,
                                                            data: dict[str, Any]) -> None:
    controller, calls, replies, _, _ = station
    replies["probe"] = data
    assert controller.probe("desk", name) == {"evidence_class": "analytical", "data": data}
    for change in ({"private": "SENSITIVE_SENTINEL"}, {next(iter(data)): "SENSITIVE_SENTINEL"}):
        replies["probe"] = {**data, **change}
        with pytest.raises(SafetyError) as error:
            controller.probe("desk", name)
        assert "SENSITIVE_SENTINEL" not in str(error.value)
    assert all(tool == "station_probe" for tool, _ in calls)


@pytest.mark.parametrize("data", [
    {"total_kib": 1000, "used_kib": 1001, "free_kib": 0},
    {"maind": True, "quasar_launcher2": True, "dropbear": False, "shairport-sync": False,
     "updatesd": True, "agent": False},
    {"sound_devices": True, "volume_control": "ready", "volume_percent": None},
    {"sound_devices": True, "volume_control": "unavailable", "volume_percent": 25},
    {"sound_devices": True, "volume_control": "ready", "volume_percent": 101},
])
def test_native_typed_probes_reject_inconsistent_values(station: Any, data: dict[str, Any]) -> None:
    controller, _, replies, _, _ = station
    replies["probe"] = data
    name = ("storage" if "total_kib" in data else "services" if "agent" in data else "audio")
    with pytest.raises(SafetyError):
        controller.probe("desk", name)


def test_native_volume_set_and_step_are_bound_to_the_host_ceiling(station: Any) -> None:
    controller, calls, replies, _, _ = station
    replies["audio.volume"] = volume(25, 20)
    result = run(controller, "audio.volume", {"percent": 20})
    assert result["outcome"] == "applied" and result["previous_percent"] == 25
    assert result["do_not_retry"] and not result["physical_effects_verified"]
    replies["audio.volume"] = volume(25, 15)
    assert run(controller, "audio.volume", {"step": "quieter"})["percent"] == 15
    assert [a["parameters"] for a in dispatched(calls)] == [
        {"percent": 20}, {"step": "quieter", "max_percent": 30}]
    assert all(a["expected_state_sha256"] == "c" * 64 for a in dispatched(calls))
    calls.clear()
    for parameters in ({"percent": 31}, {"percent": True}, {"step": "max"},
                       {"percent": 5, "step": "louder"}, {"percent": 5, "control": "Capture"}):
        assert run(controller, "audio.volume", parameters)["outcome"] == "blocked"
    assert not calls


@pytest.mark.parametrize("parameters,reply", [
    ({"percent": 20}, volume(25, 21)),
    ({"step": "louder"}, volume(25, 31)),
    ({"step": "quieter"}, volume(25, 26)),
    ({"percent": 20}, {**volume(25, 20), "private": "SENSITIVE_SENTINEL"}),
    ({"percent": 20}, {**volume(25, 20), "do_not_retry": False}),
])
def test_native_volume_result_is_postchecked_and_never_retried(
    station: Any, parameters: dict[str, Any], reply: dict[str, Any],
) -> None:
    controller, calls, replies, _, _ = station
    replies["audio.volume"] = reply
    result = run(controller, "audio.volume", parameters)
    assert result["outcome"] == "unknown" and result["do_not_retry"]
    assert "SENSITIVE_SENTINEL" not in str(result) and len(dispatched(calls)) == 1


def test_native_volume_unknown_keeps_prior_value_for_owner_inspection(station: Any) -> None:
    controller, _, replies, _, _ = station
    replies["audio.volume"] = volume(25, None, "unknown")
    result = run(controller, "audio.volume", {"percent": 20})
    assert result["outcome"] == "unknown" and result["previous_percent"] == 25
    replies["audio.volume"] = {"outcome": "blocked", "error": "SENSITIVE_SENTINEL"}
    result = run(controller, "audio.volume", {"percent": 20})
    assert result["outcome"] == "blocked" and "SENSITIVE_SENTINEL" not in str(result)


def pcm(frames: int, rate: int = 16000) -> dict[str, Any]:
    return {"outcome": "applied", "playback_started": True, "playback_completed": True,
            "cleanup_verified": True, "frames_written": frames, "input_frames": frames,
            "sample_rate": rate, "stage": "complete", "alsa_return_code": 0,
            "quasar_running": True, "do_not_retry": True, "evidence_class": "analytical",
            "physical_effects_verified": False, "tts_backend": "local_pcm",
            "hardware_parameters_verified": True, "playback_elapsed_ms": frames * 1000 // rate}


def test_native_media_sends_only_the_registered_hash_pinned_wav(station: Any) -> None:
    controller, calls, replies, _, _ = station
    replies["audio.play"] = pcm(8000)
    result = run(controller, "audio.play", {"asset": "chime", "duration_seconds": 1})
    assert result["outcome"] == "applied" and not result["physical_effects_verified"]
    (sent,) = dispatched(calls)
    parameters = sent["parameters"]
    assert set(parameters) == {"asset", "duration_seconds", "wav_base64", "sha256"}
    assert base64.b64decode(parameters["wav_base64"]) == clip()
    assert parameters["sha256"] == hashlib.sha256(clip()).hexdigest()
    replies["audio.play"] = pcm(7999)
    assert run(controller, "audio.play", {"asset": "chime", "duration_seconds": 1})[
        "outcome"] == "unknown"


@pytest.mark.parametrize("change", ["alias", "duration", "too_long", "loud", "changed", "model"])
def test_native_media_outside_registration_never_dispatches(station: Any, change: str) -> None:
    controller, calls, _, data, chime = station
    parameters = {"asset": "chime", "duration_seconds": 1}
    if change == "alias":
        parameters["asset"] = "other"
    elif change == "duration":
        parameters["duration_seconds"] = 11
    elif change in {"too_long", "loud"}:
        audio = clip(2.0) if change == "too_long" else clip(sample=9000)
        chime.write_bytes(audio)
        item = data["stations"][0]["controls"]["local_media"]["chime"]
        item["sha256"] = hashlib.sha256(audio).hexdigest()
    elif change == "changed":
        chime.write_bytes(clip(sample=999))
    else:
        data["stations"][0]["model"] = "station-mini3"
    controller.inventory.write_text(json.dumps(data))
    assert run(controller, "audio.play", parameters)["outcome"] == "blocked"
    assert not dispatched(calls)


def test_native_media_changed_after_preview_never_dispatches(station: Any) -> None:
    controller, calls, _, _, chime = station
    preview = native_transport.preview_action(controller, controller.station("desk"),
                                              "audio.play",
                                              {"asset": "chime", "duration_seconds": 1})
    chime.write_bytes(clip(sample=999))
    result = controller.execute_plan(controller.take_plan(preview["plan_id"],
                                                          preview["expected_sha256"]))
    assert result["outcome"] == "blocked" and not dispatched(calls)


def test_native_controls_advertise_typed_audio_and_registered_aliases(station: Any) -> None:
    controller, _, _, _, _ = station
    result = controls.capabilities(controller, "desk")
    assert result["allowed_actions"] == ["audio.play", "audio.volume"]
    assert result["assets"] == ["chime"]


@pytest.mark.parametrize("change", ["ssh", "sha", "path", "alias"])
def test_local_media_policy_requires_native_and_exact_entries(station: Any, change: str) -> None:
    controller, _, _, data, _ = station
    entry = data["stations"][0]
    item = entry["controls"]["local_media"]["chime"]
    if change == "ssh":
        native = entry.pop("native_mcp")
        entry.update(host="station.invalid", port=22, user="root",
                     identity_file=native["token_file"], known_hosts_file=native["ca_file"])
        media = entry["controls"].pop("local_media")
        entry["controls"]["allowed_actions"] = ["audio.volume"]
        controller.inventory.write_text(json.dumps(data))
        assert load_inventory(controller.inventory)["desk"].host == "station.invalid"
        entry["controls"]["local_media"] = media
    elif change == "sha":
        item["sha256"] = "SENSITIVE_SENTINEL"
    elif change == "path":
        item["file"] = "relative.wav"
    else:
        entry["controls"]["local_media"] = {"Bad Alias": item}
    controller.inventory.write_text(json.dumps(data))
    with pytest.raises(SafetyError):
        load_inventory(controller.inventory)


def answers(*values: str) -> Any:
    queue = list(values)

    def ask(prompt: str) -> str:
        value = queue.pop(0)
        if value == "INTERRUPT":
            raise KeyboardInterrupt
        return value
    return ask


def audio_probe(percent: int | None) -> dict[str, Any]:
    return {"sound_devices": True, "volume_control": "ready" if percent is not None
            else "unavailable", "volume_percent": percent}


def test_acceptance_health_and_previews_are_read_only(station: Any) -> None:
    controller, calls, replies, _, _ = station
    replies["probes"] = {
        "health": {"uptime_seconds": 12.0, "memory_total_kib": 2000,
                   "memory_available_kib": 1000, "kernel_arch": "armv7l"},
        "storage": {"total_kib": 1000, "used_kib": 400, "free_kib": 500},
        "services": {"maind": True, "quasar_launcher2": True, "dropbear": False,
                     "shairport-sync": False, "updatesd": True, "agent": True},
        "audio": audio_probe(25)}
    assert native_acceptance.health(controller, "desk")["outcome"] == "ready"
    never = answers()
    assert native_acceptance.volume(controller, "desk", live=False, ask=never)[
        "before_percent"] == 25
    assert native_acceptance.media(controller, "desk", "chime", 1, live=False, ask=never)[
        "outcome"] == "preview"
    assert not dispatched(calls)


def test_acceptance_volume_steps_once_then_restores_prior_value(station: Any) -> None:
    controller, calls, replies, _, _ = station
    replies["probes"] = {"audio": audio_probe(25)}
    replies["audio.volume"] = [volume(25, 15), volume(15, 25)]
    report = native_acceptance.volume(controller, "desk", live=True,
                                      ask=answers("VOLUME", "QUIETER"))
    assert report["outcome"] == "accepted" and report["restored"]
    assert [a["parameters"] for a in dispatched(calls)] == [
        {"step": "quieter", "max_percent": 30}, {"percent": 25}]


def test_acceptance_volume_restores_after_interrupt_but_not_after_unknown(station: Any) -> None:
    controller, calls, replies, _, _ = station
    replies["probes"] = {"audio": audio_probe(25)}
    replies["audio.volume"] = [volume(25, 15), volume(15, 25)]
    report = native_acceptance.volume(controller, "desk", live=True,
                                      ask=answers("VOLUME", "INTERRUPT"))
    assert report["restored"] and report["outcome"] == "not_accepted"
    assert len(dispatched(calls)) == 2
    calls.clear()
    replies["audio.volume"] = [volume(25, None, "unknown")]
    report = native_acceptance.volume(controller, "desk", live=True, ask=answers("VOLUME"))
    assert report["outcome"] == "unknown" and report["do_not_retry"]
    assert len(dispatched(calls)) == 1


@pytest.mark.parametrize("percent,word", [(31, "VOLUME"), (None, "VOLUME"), (0, "VOLUME"),
                                          (25, "volume")])
def test_acceptance_volume_refuses_unrestorable_or_unconfirmed_writes(
    station: Any, percent: int | None, word: str,
) -> None:
    controller, calls, replies, _, _ = station
    replies["probes"] = {"audio": audio_probe(percent)}
    report = native_acceptance.volume(controller, "desk", live=True, ask=answers(word))
    assert report["outcome"] in {"blocked", "cancelled"} and not dispatched(calls)


def test_acceptance_media_plays_once_after_exact_word(station: Any) -> None:
    controller, calls, replies, _, _ = station
    replies["audio.play"] = pcm(8000)
    replies["probes"] = {"services": {"maind": True, "quasar_launcher2": True,
                                      "dropbear": False, "shairport-sync": False,
                                      "updatesd": True, "agent": True}}
    report = native_acceptance.media(controller, "desk", "chime", 1, live=True,
                                     ask=answers("PLAY", "HEARD"))
    assert report["outcome"] == "accepted" and len(dispatched(calls)) == 1
    calls.clear()
    report = native_acceptance.media(controller, "desk", "chime", 1, live=True,
                                     ask=answers("play"))
    assert report["outcome"] == "cancelled" and not dispatched(calls)
    assert native_acceptance.media(controller, "desk", "other", 1, live=True,
                                   ask=answers())["outcome"] == "blocked"
