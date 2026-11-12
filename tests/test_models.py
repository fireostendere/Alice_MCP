"""Synthetic offline checks of the model registry; these establish no station compatibility."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import controls, core, models, server
from alice_mcp import device_runtime as rt
from alice_mcp.core import Controller, SafetyError

# One accepted parameter set per action, with every optional private artifact declared,
# so only the model gate decides.
FULL_POLICY: dict[str, Any] = {
    "max_volume": 30, "quasar_client": "/x", "speech_client": {}, "tts_client": {},
    "local_tts": {"command": ["/x"]},
    "voice": {"live_client": "/x", "live_player": "/x"},
    "media": {"a": {"path": "/data/sounds/a.opus", "sha256": "0" * 64}},
    "configs": {"b": {"choices": {"c": "x"}}},
}
SAMPLES: dict[str, dict[str, Any]] = {
    "led.effect": {"red": 1, "green": 1, "blue": 1, "count": 1, "period_ms": 500},
    "led.capture": {"duration_seconds": 5},
    "led.replay": {"capture_id": "0" * 32},
    "led.pulse": {"capture_id": "0" * 32, "channel": 0},
    "led.animate": {"capture_id": "0" * 32, "mode": "blink"},
    "led.show": {"effect": "off", "duration_seconds": 1, "capture_id": "0" * 32},
    "speech.listen": {"duration_seconds": 5},
    "speech.say": {"text": "x"}, "speech.openai": {"text": "x"},
    "audio.volume": {"percent": 1}, "audio.play": {"asset": "a", "duration_seconds": 1},
    "sound.replace": {"asset": "a", "slot": "volume_sound"},
    "config.select": {"binding": "b", "option": "c"}, "restore": {"backup_id": "0" * 32},
    "quasar.volume": {"level": 1}, "alice.activation": {"enabled": True},
}


def test_registry_is_the_single_model_list() -> None:
    assert set(models.PROFILES) == core.MODELS == rt.MODELS
    assert core.BUILDROOT_MODELS == {m for m, p in models.PROFILES.items()
                                     if p["os"] == "buildroot"}
    # The whole current upstream lineup has a slot, including the data-less entries.
    assert {"fiero-hi", "station-max-zigbee", "jbl-link-portable", "station-duo-max",
            "station-pro"} <= core.MODELS
    guide = (Path(__file__).resolve().parents[1] / "docs/MODELS.md").read_text(encoding="utf-8")
    for model, profile in models.PROFILES.items():
        assert f"`{model}`" in guide, model
        assert set(profile) == {"title", "codename", "os", "userspace", "dt", "speaker", "led",
                                "evidence", "source", "todo"}, model
        assert profile["os"] in {"buildroot", "android", "yocto", "unknown"}
        assert profile["evidence"] in {"live_pilot", "partial_live", "synthetic", "none"}
        assert profile["source"].startswith("https://hackalice.me/devices/")
        assert rt.validate_policy({}, model) == {}


@pytest.mark.parametrize("model", sorted(models.PROFILES))
def test_action_matrix_matches_the_real_gates(model: str) -> None:
    matrix = models.actions(model)
    assert set(matrix) == rt.ACTIONS
    assert set(matrix.values()) <= {"adapter", "generic", "none"}
    for action, params in SAMPLES.items():
        sample = dict(params)
        if action == "led.show" and model != "station-mini":
            del sample["capture_id"]
        try:
            rt.validate_parameters(action, sample, FULL_POLICY, model)
            accepted = True
        except ValueError:
            accepted = False
        if action == "led.effect":  # Its model gate is the on-device interface profile.
            accepted = rt.interfaces(model)["led"]["supported"]
        writable = model in core.BUILDROOT_MODELS and accepted
        expected = matrix[action] != "none"
        if not models.PROFILES[model]["speaker"] and action in models.SOUND_ACTIONS:
            assert not expected, action  # Reported honestly even where a gate would pass.
        else:
            assert expected == writable, (model, action)


def test_only_the_pilot_models_have_specific_adapters() -> None:
    mini1 = models.actions("station-mini")
    assert all(mini1[a] == "adapter" for a in (
        "led.capture", "led.show", "speech.say", "speech.openai", "quasar.volume"))
    assert mini1["led.effect"] == "none" and mini1["audio.volume"] == "generic"
    assert models.actions("station-mini3")["speech.say"] == "adapter"
    assert models.actions("station-mini3")["led.capture"] == "none"
    for model in ("station-max", "station-gen1", "jbl-link-music", "station-duo-max"):
        assert set(models.actions(model).values()) == {"none"}
    assert models.actions("yandex-hub")["audio.volume"] == "none"
    assert models.actions("yandex-hub")["config.select"] == "generic"


def test_describe_merges_profile_matrix_and_interface_facts() -> None:
    mini1 = server.get_model_interfaces("station-mini")
    assert mini1["profile"]["evidence"] == "live_pilot"
    assert mini1["actions"]["led.show"] == "adapter"
    assert "mini1_i2c_unknown" not in mini1["limitations"]  # Was reported for every model.
    assert "SN3236" in mini1["led"]["description"] and mini1["led"]["supported"] is False
    lite = server.get_model_interfaces("station-lite")
    assert lite["led"]["interface"] == "sysfs" and lite["actions"]["led.effect"] == "adapter"
    assert "midi_channels_unknown" not in lite["limitations"]
    pro = server.get_model_interfaces("station-pro")
    assert pro["sources"][0] == "https://hackalice.me/devices/" and pro["profile"]["todo"]
    assert server.get_model_interfaces("station-x")["outcome"] == "blocked"
    lineup = server.get_capabilities()["models"]
    assert set(lineup) == core.MODELS
    assert lineup["station-mini"] == {"title": "Станция Мини", "os": "buildroot",
                                      "evidence": "live_pilot"}


def _controller(tmp_path: Path, model: str, output: str = "") -> Controller:
    key, hosts = tmp_path / "identity", tmp_path / "hosts"
    key.touch(mode=0o600)
    hosts.touch()
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"stations": [{
        "id": "desk", "model": model, "host": "station.invalid", "port": 2222, "user": "root",
        "identity_file": str(key), "known_hosts_file": str(hosts),
        "controls": {"firmware_sha256": "a" * 64},
    }]}))
    path.chmod(0o600)
    return Controller(path, writes=True, runner=lambda station, command: output)


def test_station_controls_report_what_this_model_can_actually_do(tmp_path: Path) -> None:
    result = controls.capabilities(_controller(tmp_path, "station-lite"), "desk")
    assert result["allowed_actions"] == sorted(rt.ACTIONS)  # The private allowlist is unchanged.
    assert "led.effect" in result["supported_actions"]
    assert "audio.volume" in result["supported_actions"]
    assert not {"led.capture", "quasar.volume", "speech.openai"} & set(result["supported_actions"])
    assert controls.capabilities(
        _controller(tmp_path, "station-max"), "desk")["supported_actions"] == []


def _identify(tmp_path: Path, compatible: bytes, *present: str) -> str:
    for name in present:
        target = tmp_path / name.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(mode=0o755)
    tree = tmp_path / "proc/device-tree/compatible"
    tree.parent.mkdir(parents=True, exist_ok=True)
    tree.write_bytes(compatible)
    command = core.PROBES["identify"]
    for root in ("/proc/", "/etc/", "/system/", "/run/"):
        command = command.replace(" " + root, f" {tmp_path}{root}")
    return subprocess.run(["sh", "-c", command], capture_output=True, text=True,
                          check=True).stdout


def test_identify_probe_reports_fixed_tokens_only(tmp_path: Path) -> None:
    output = _identify(tmp_path, b"yandex,bergamot\0amlogic, axg\0secret-serial\0",
                       "/etc/init.d/S99quasar", "/system/vendor/quasar/maind")
    assert "secret" not in output and "amlogic" not in output
    data = core.parse_probe("identify", output)
    assert data == {"device_tree_models": ["station-mini3"], "os_family": "buildroot",
                    "maind_present": True, "quasar_init_script": True}
    controller = _controller(tmp_path, "station-mini3", output)
    result = controller.probe("desk", "identify")["data"]
    assert result["declared_model"] == "station-mini3" and result["declared_matches"] is True
    wrong = _controller(tmp_path, "station-mini", output).probe("desk", "identify")["data"]
    assert wrong["declared_matches"] is False


def test_identify_probe_handles_shared_prefixed_and_unknown_boards(tmp_path: Path) -> None:
    micro = core.parse_probe("identify", _identify(
        tmp_path / "a", b"yandex,station-micro\0", "/etc/init.d/S99quasar"))
    assert micro["device_tree_models"] == ["fiero-hi", "station-lite"]  # One board, two brands.
    midi = core.parse_probe("identify", _identify(tmp_path / "b", b"yandex,cucumber-dvt2\0"))
    assert midi == {"device_tree_models": ["station-midi"], "os_family": "unknown",
                    "maind_present": False, "quasar_init_script": False}
    # Observed on a real Mini 1: native maind but no S99quasar init script, so service
    # start/restart is unavailable there while the OS family is still Buildroot.
    mini1 = core.parse_probe("identify", _identify(
        tmp_path / "e", b"amlogic, axg\0", "/system/vendor/quasar/maind"))
    assert mini1 == {"device_tree_models": [], "os_family": "buildroot",
                     "maind_present": True, "quasar_init_script": False}
    # The misleading internal name: device-tree station-midi is retail Station 2.
    assert core.parse_probe("identify", _identify(
        tmp_path / "c", b"yandex,station-midi\0"))["device_tree_models"] == ["station-2"]
    android = _identify(tmp_path / "d", b"amlogic, axg\0", "/system/build.prop")
    assert core.parse_probe("identify", android)["os_family"] == "android"
    unknown = _controller(tmp_path / "d", "station-mini", android).probe("desk", "identify")
    assert unknown["data"]["device_tree_models"] == []
    assert unknown["data"]["declared_matches"] is None  # No token is not a mismatch.
    with pytest.raises(SafetyError):
        core.parse_probe("identify", "dt0=2\n")


def test_missing_reboot_reason_and_log_are_unknown_not_ssh_failures(tmp_path: Path) -> None:
    for probe in ("reboot", "logs"):
        command = core.PROBES[probe].replace(" /sys/", f" {tmp_path}/sys/").replace(
            " /data/", f" {tmp_path}/data/")
        done = subprocess.run(["sh", "-c", command], capture_output=True, text=True)
        assert done.returncode == 0, probe  # Non-zero is reported as a transport failure.
        if probe == "reboot":
            assert core.parse_probe(probe, done.stdout) == {"reason": "unknown"}
        else:
            with pytest.raises(SafetyError, match="Probe unavailable"):
                core.parse_probe(probe, done.stdout)


def test_fiero_hi_shares_the_lite_led_adapter() -> None:
    # One board and one node naming (`micro:*:ring1-3`), so one adapter; still synthetic.
    assert rt.interfaces("fiero-hi")["led"] == rt.interfaces("station-lite")["led"]
    assert rt.led_layout("fiero-hi") == rt.led_layout("station-lite")
    matrix = models.actions("fiero-hi")
    assert matrix["led.effect"] == matrix["led.show"] == "adapter"
    assert models.PROFILES["fiero-hi"]["evidence"] == "synthetic"
