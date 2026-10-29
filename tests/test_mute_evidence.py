"""Synthetic physical-mute evidence runner: read-only snapshots, no audio or Quasar change."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import mute_evidence
from alice_mcp.core import SafetyError

SENTINEL = "SENSITIVE_SENTINEL"


def _write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)


def _tree(tmp_path: Path, *, mute_irq: int, gpio: str, led: int, hw_ptr: str) -> None:
    _write(tmp_path / "proc/interrupts",
           "           CPU0       CPU1\n"
           f"  9:     {mute_irq}          0   GIC-0  40 Level     gpio-keys\n"
           " 30:     500        700   GIC-0  30 Level     arch_timer\n"
           f"IPI0:     5          6   Rescheduling interrupts;{SENTINEL}\n")
    _write(tmp_path / "sys/kernel/debug/gpio",
           "gpiochip0: GPIOs 412-511, parent: platform/bank, periphs-banks:\n"
           f" gpio-420 (                    |mic_mute            ) in  {gpio} IRQ\n"
           " gpio-421 (                    |spk_mute            ) out lo    \n")
    _write(tmp_path / "sys/class/leds/mute_red/brightness", f"{led}\n")
    _write(tmp_path / "sys/class/regulator/regulator.1/name", "vcc_mic\n")
    _write(tmp_path / "sys/class/regulator/regulator.1/state", "enabled\n")
    pcm = tmp_path / "proc/asound/card0/pcm2c"
    _write(pcm / "info", "card: 0\ndevice: 2\nstream: CAPTURE\n")
    _write(pcm / "sub0/status", f"state: RUNNING\nhw_ptr      : {hw_ptr}\n")


def _snapshot(tmp_path: Path) -> dict[str, Any]:
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    (shims / "pidof").write_text('#!/bin/sh\n[ "$1" = maind ] && echo "456 123"\n')
    (shims / "pidof").chmod(0o700)
    # The device helper samples hw_ptr twice with one second between reads.
    (shims / "sleep").write_text(
        '#!/bin/sh\nf="$TEST_STATUS"; sed "s/hw_ptr.*/hw_ptr      : $TEST_PTR2/" "$f" > "$f.n"'
        ' && mv "$f.n" "$f"\n')
    (shims / "sleep").chmod(0o700)
    command = mute_evidence.SNAPSHOT
    for prefix in ("/proc/", "/sys/"):
        command = command.replace(prefix, str(tmp_path) + prefix)
    env = {**os.environ, "PATH": str(shims) + ":" + os.environ["PATH"],
           "TEST_STATUS": str(tmp_path / "proc/asound/card0/pcm2c/sub0/status"),
           "TEST_PTR2": os.environ.get("TEST_PTR2", "2000")}
    output = subprocess.run(["sh"], input=command, text=True, capture_output=True, env=env,
                            timeout=20, check=True).stdout
    assert SENTINEL not in output
    return mute_evidence.parse_snapshot(output)


def test_snapshot_command_is_fixed_and_read_only() -> None:
    command = mute_evidence.SNAPSHOT
    subprocess.run(["sh", "-n", "-c", command], check=True)
    for forbidden in ("arecord", "amixer", "kill", "mount", "rm ", "q stop", "q restart",
                      "/brightness >", "echo 1", "i2cset", "LD_PRELOAD", "mkdir"):
        assert forbidden not in command


def test_snapshot_parses_synthetic_interfaces(tmp_path: Path) -> None:
    _tree(tmp_path, mute_irq=10, gpio="lo", led=0, hw_ptr="1000")
    before = (tmp_path / "sys/kernel/debug/gpio").read_bytes()
    snap = _snapshot(tmp_path)
    assert (tmp_path / "sys/kernel/debug/gpio").read_bytes() == before
    assert snap["maind_pids"] == [123, 456]
    assert snap["irqs"]["9"] == {"name": "GIC-0_40_Level_gpio-keys", "total": 10}
    assert snap["irqs"]["IPI0"]["total"] == 11
    assert snap["gpio_readable"] is True
    assert snap["gpios"]["gpio-420"] == {"label": "mic_mute", "direction": "in", "value": "lo"}
    assert snap["leds"] == {"mute_red": 0}
    assert snap["regulators"] == {"vcc_mic": "enabled"}
    assert snap["pcms"] == {"c0d2": {"state": "running", "advancing": True}}


@pytest.mark.parametrize("text", ["", "maind_pids=1,x\n",
                                  "maind_pids=1\ngpio_readable=1\ngpio_readable=0\n",
                                  "maind_pids=1\ngpio_readable=2\n"])
def test_snapshot_rejects_bad_output(text: str) -> None:
    with pytest.raises(SafetyError):
        mute_evidence.parse_snapshot(text)


def _synthetic(irq: int, gpio: str, led: int, *, advancing: bool = True,
               pids: tuple[int, ...] = (123, 456), noisy: int = 0) -> dict[str, Any]:
    return {"maind_pids": list(pids), "gpio_readable": True,
            "irqs": {"9": {"name": "gpio-keys", "total": irq},
                     "30": {"name": "arch_timer", "total": 1000 + noisy}},
            "gpios": {"gpio-420": {"label": "mic_mute", "direction": "in", "value": gpio},
                      "gpio-421": {"label": "spk_mute", "direction": "out", "value": "lo"}},
            "leds": {"mute_red": led}, "regulators": {"vcc_mic": "enabled"},
            "pcms": {"c0d2": {"state": "running", "advancing": advancing}}}


def test_analysis_finds_correlated_signals_only() -> None:
    phases = {"unmuted_a": _synthetic(10, "lo", 0), "control": _synthetic(10, "lo", 0, noisy=5),
              "muted": _synthetic(12, "hi", 255, noisy=9),
              "unmuted_b": _synthetic(14, "lo", 0, noisy=20)}
    result = mute_evidence.analyze(phases)
    assert result["button_irq_candidates"] == [{"irq": "9", "name": "gpio-keys",
                                                "deltas": [0, 2, 2]}]
    assert result["gpio_mute_candidates"] == [{"line": "gpio-420", "label": "mic_mute",
                                               "unmuted": "lo", "muted": "hi"}]
    assert result["led_mute_candidates"] == [{"led": "mute_red", "unmuted": 0, "muted": 255}]
    assert result["regulator_mute_candidates"] == []
    assert result["capture_stream_continues_while_muted"] is True
    assert result["quasar_unchanged"] is True
    assert result["mute_signal_observable"] is True
    for key in ("audio_captured", "electrical_power_cut_verified", "physical_effects_verified"):
        assert result[key] is False


def test_analysis_reports_absence_as_not_observed_and_detects_quasar_change() -> None:
    same = _synthetic(10, "lo", 0)
    phases = {"unmuted_a": same, "control": same,
              "muted": _synthetic(10, "lo", 0, advancing=False, pids=(123, 789)),
              "unmuted_b": same}
    result = mute_evidence.analyze(phases)
    assert result["mute_signal_observable"] is False
    assert result["button_irq_candidates"] == []
    assert result["capture_stream_continues_while_muted"] is False
    assert result["quasar_unchanged"] is False


def test_runner_requires_typed_phase_confirmations() -> None:
    answers = iter(["UNMUTED", "MUTED", "unmuted"])
    calls: list[str] = []

    def snapshot() -> dict[str, Any]:
        calls.append("snap")
        return _synthetic(10, "lo", 0)

    result = mute_evidence.run(snapshot, ask=lambda _: next(answers), wait=lambda _: None)
    assert result["outcome"] == "cancelled" and result["phase"] == "unmuted_b"
    assert calls == ["snap", "snap", "snap"]
    assert result["audio_captured"] is False


def test_runner_never_snapshots_before_first_confirmation() -> None:
    calls: list[str] = []
    result = mute_evidence.run(lambda: calls.append("snap") or {}, ask=lambda _: "no",
                               wait=lambda _: None)
    assert result["outcome"] == "cancelled" and calls == []


def test_runner_complete_flow_and_snapshot_failure() -> None:
    answers = iter(["UNMUTED", "MUTED", "UNMUTED"])
    sequence = iter([_synthetic(10, "lo", 0), _synthetic(10, "lo", 0),
                     _synthetic(12, "hi", 255), _synthetic(14, "lo", 0)])
    waits: list[float] = []
    result = mute_evidence.run(lambda: next(sequence), ask=lambda _: next(answers),
                               wait=waits.append)
    assert result["outcome"] == "complete" and waits == [10]
    assert result["analysis"]["gpio_mute_candidates"][0]["line"] == "gpio-420"

    def broken() -> dict[str, Any]:
        raise SafetyError("SSH unavailable")

    failed = mute_evidence.run(broken, ask=lambda _: "UNMUTED", wait=lambda _: None)
    assert failed["outcome"] == "unknown" and failed["phase"] == "unmuted_a"


def test_main_refuses_non_interactive_use(monkeypatch: Any) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit):
        mute_evidence.main(["--station", "desk"])
