"""Owner-run LED calibration/acceptance flows on fakes only; no station, SSH or TLS peer."""

import json
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import device_runtime as rt
from alice_mcp import led_parity, native_transport
from alice_mcp.core import Controller, SafetyError


def inventory(tmp_path: Path, model: str = "station-mini", native: bool = False,
              **controls: Any) -> Path:
    entry: dict[str, Any] = {"id": "desk", "model": model, "preauthorize_all_actions": True,
                             "controls": {"firmware_sha256": "a" * 64,
                                          "allowed_actions": ["led.show"], **controls}}
    if native:
        ca, token = tmp_path / "ca", tmp_path / "token"
        ca.write_text("synthetic")
        token.write_text("a" * 64)
        token.chmod(0o600)
        entry["native_mcp"] = {"url": "https://station.invalid:18443/mcp",
                               "ca_file": str(ca), "token_file": str(token)}
    else:
        key, hosts = tmp_path / "identity", tmp_path / "hosts"
        key.write_text("synthetic")
        key.chmod(0o600)
        hosts.write_text("synthetic")
        entry.update(host="station.invalid", port=22, user="root",
                     identity_file=str(key), known_hosts_file=str(hosts))
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"stations": [entry]}))
    path.chmod(0o600)
    return path


def mini1_answers() -> list[tuple[str, int]]:
    # Hardware unlike the paper model: GRB triplets, ring wired counter-clockwise from 2 o'clock.
    clock = [2, 12, 10, 8, 6, 4]
    return [(("green", "red", "blue")[j % 3], clock[j // 3]) for j in range(18)]


def test_owner_answers_become_a_policy_ready_led_map() -> None:
    led_map = led_parity.derive_map("station-mini", mini1_answers())
    assert led_map is not None
    rt.validate_led_map("station-mini", led_map)
    # Logical zone 0 starts at 12 o'clock and proceeds clockwise.
    assert led_map["channels"][3:6] == [[0, "green"], [0, "red"], [0, "blue"]]
    assert led_map["channels"][0] == [1, "green"]


@pytest.mark.parametrize("damage", ["missing_zone", "duplicate", "short"])
def test_inconsistent_answers_never_produce_a_map(damage: str) -> None:
    answers = mini1_answers()
    if damage == "missing_zone":
        answers = [(c, 2 if p == 4 else p) for c, p in answers]
    elif damage == "duplicate":
        answers[1] = answers[0]
    else:
        answers = answers[:17]
    assert led_parity.derive_map("station-mini", answers) is None


def scripted(*answers: str) -> Any:
    queue = list(answers)
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return queue.pop(0)

    ask.prompts = prompts  # type: ignore[attr-defined]
    return ask


def test_calibration_lights_one_channel_per_fresh_action_and_reports_a_map(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    controller = Controller(inventory(tmp_path, "station-lite"), writes=True)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(led_parity, "_once", lambda c, s, p: calls.append(p) or {
        "outcome": "applied", "yielded": False})
    answers = ["CALIBRATE"]
    for j in range(9):
        answers += [("r", "g", "b")[j // 3], ("12", "4", "8")[j % 3]]
    report = led_parity.calibrate(controller, "desk", ask=scripted(*answers))
    assert report["outcome"] == "complete" and report["do_not_retry"] is True
    assert [c["channel"] for c in calls] == list(range(9))
    assert all(c["effect"] == "calibrate" and c["duration_seconds"] == 2 for c in calls)
    assert report["led_map"]["channels"] == rt.led_layout("station-lite")
    assert report["physical_effects_verified"] is False


def test_calibration_needs_the_exact_word_and_stops_on_uncertain_write(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    controller = Controller(inventory(tmp_path), writes=True)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(led_parity, "_once", lambda c, s, p: calls.append(p) or {
        "outcome": "unknown", "do_not_retry": True})
    assert led_parity.calibrate(controller, "desk", "a" * 32,
                                ask=scripted("calibrate"))["outcome"] == "cancelled"
    assert not calls
    report = led_parity.calibrate(controller, "desk", "a" * 32, ask=scripted("CALIBRATE"))
    assert report["outcome"] == "unknown" and len(calls) == 1
    assert calls[0]["capture_id"] == "a" * 32 and "led_map" not in report


def test_show_previews_by_default_and_live_run_records_owner_answers(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    controller = Controller(inventory(tmp_path, "station-lite"), writes=True)
    previewed: list[str] = []
    monkeypatch.setattr(led_parity.controls, "preview", lambda c, s, a, p: previewed.append(
        p["effect"]) or {"plan_id": "x", "expected_sha256": "y" * 64})
    monkeypatch.setattr(led_parity, "_once", lambda *a: pytest.fail("preview must not apply"))
    report = led_parity.show(controller, "desk", ["rainbow", "static"], color=(8, 0, 8),
                             ask=scripted())
    assert previewed == ["rainbow", "static"] and report["outcome"] == "previewed"
    assert all("impact" in item for item in report["effects"])
    ran: list[str] = []
    results = iter([{"outcome": "applied"}, {"outcome": "unknown"}])
    monkeypatch.setattr(led_parity, "_once", lambda c, s, p: ran.append(p["effect"])
                        or next(results))
    report = led_parity.show(controller, "desk", ["rainbow", "police", "off"], live=True,
                             ask=scripted("SHOW", "yes"))
    assert ran == ["rainbow", "police"]  # Stops after the uncertain one, never retries.
    assert report["effects"][0]["owner_observed"] is True
    assert report["outcome"] == "unknown" and report["do_not_retry"] is True


def test_live_show_without_exact_word_writes_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    controller = Controller(inventory(tmp_path, "station-lite"), writes=True)
    monkeypatch.setattr(led_parity, "_once", lambda *a: pytest.fail("no confirmation"))
    report = led_parity.show(controller, "desk", ["off"], live=True, ask=scripted("show"))
    assert report["outcome"] == "cancelled"


def test_display_probe_is_fixed_read_only_and_typed(tmp_path: Path) -> None:
    commands: list[Any] = []

    def runner(station: Any, command: Any) -> str:
        commands.append(command)
        return "bergamot=1\nclock_led_entries=30\nclock_settings_file=0\n"

    controller = Controller(inventory(tmp_path, "station-mini3"), runner=runner)
    report = led_parity.display_probe(controller, "desk")
    assert report == {"model": "station-mini3", "bergamot_compatible": True,
                      "clock_led_entries": 30, "clock_settings_file": False,
                      "display_writes_supported": False, "evidence_class": "analytical"}
    assert commands == [led_parity.DISPLAY_PROBE]
    assert not any(word in led_parity.DISPLAY_PROBE for word in
                   (">", "mount", "echo 2", "brightness", "gcc", "cat "))
    for raw in ("bergamot=1\n", "bergamot=2\nclock_led_entries=1\nclock_settings_file=0\n",
                "bergamot=1\nclock_led_entries=999\nclock_settings_file=0\n"):
        with pytest.raises(SafetyError):
            led_parity.display_probe(Controller(controller.inventory,
                                                runner=lambda s, c, raw=raw: raw), "desk")


def test_native_led_contract_sends_bounded_frames_and_validates_the_reply(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    led_map = {"channels": [[j // 3, ("green", "red", "blue")[j % 3]] for j in range(18)]}
    controller = Controller(inventory(tmp_path, native=True, led_map=led_map), writes=True)
    sent: list[dict[str, Any]] = []
    reply = {"outcome": "applied", "command_succeeded": True,
             "restoration_command_succeeded": True, "restoration_verified": False,
             "quasar_running": True, "do_not_retry": True, "evidence_class": "analytical",
             "physical_effects_verified": False}

    def call(station: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "get_station_controls":
            return {"station_id": "desk", "model": "station-mini", "writes_enabled": True,
                    "allowed_actions": ["led.show"], "preauthorize_all_actions": True,
                    "preauthorized_actions": ["led.show"], "transport": "streamable_http_tls",
                    "state_contract": "alice.native.state.v1", "state_sha256": "c" * 64}
        sent.append(arguments)
        return dict(reply)

    monkeypatch.setattr(native_transport, "call", call)
    params = {"effect": "static", "red": 12, "green": 4, "blue": 0, "duration_seconds": 3,
              "capture_id": "a" * 32}
    result = native_transport.run_action(controller, controller.station("desk"), "led.show",
                                         params)
    assert result["outcome"] == "applied" and result["effect_observed"] is False
    assert sent[0]["action"] == "led.show" and sent[0]["expected_state_sha256"] == "c" * 64
    # The resident receives only bounded mapped PWM frames, never effect code or a register.
    assert sent[0]["parameters"] == {"capture_id": "a" * 32, "interval_ms": 3000,
                                     "frames": [("040c00" * 6)]}
    reply["unexpected"] = "SENSITIVE_SENTINEL"
    result = native_transport.run_action(controller, controller.station("desk"), "led.show",
                                         params)
    assert result["outcome"] == "unknown" and "SENSITIVE_SENTINEL" not in json.dumps(result)


@pytest.mark.parametrize("change", [{"outcome": "applied", "command_succeeded": False},
                                    {"restoration_verified": True},
                                    {"do_not_retry": False}, {"outcome": "complete"}])
def test_native_led_reply_cannot_overclaim(change: dict[str, Any]) -> None:
    reply = {"outcome": "applied", "command_succeeded": True,
             "restoration_command_succeeded": True, "restoration_verified": False,
             "quasar_running": True, "do_not_retry": True, "evidence_class": "analytical",
             "physical_effects_verified": False, **change}
    with pytest.raises(SafetyError):
        led_parity.native_result(reply)
    assert led_parity.native_result({"outcome": "blocked", "error": "x"})["outcome"] == "blocked"
