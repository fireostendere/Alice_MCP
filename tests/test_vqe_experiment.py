"""Operator-only temporary profile; generated structures, never device configuration."""

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from alice_mcp import vqe_experiment as experiment
from alice_mcp.core import Controller, SafetyError


def stock() -> dict[str, Any]:
    return {"nodes": [{"tasks": [
        {"arg": ["--platformd"], "envs": ["EXAMPLE=PRIVATE_SENTINEL"]},
        {"arg": ["--mediad"], "envs": []},
    ]}]}


def phase_statistics(times: list[int]) -> dict[str, Any]:
    stats: dict[str, Any] = {name: 0 for name in [
        "packets", "rejected", "types_bitmap", "type6_calls", "type6_frames", "nonfinite",
        "outside", "peak_millionths", "mean_square_billionths",
    ]}
    stats.update(outcome="complete", metadata_only=True, cleanup_verified=True,
                 started_monotonic_ms=1000, samples=[{
                     "t_ms": ms, "calls": index + 1, "peak_millionths": 1,
                     "mean_square_billionths": 1,
                 } for index, ms in enumerate(times)])
    return stats


def test_vqe_profile_modifies_only_one_platform_environment() -> None:
    original = stock()
    snapshot = copy.deepcopy(original)
    profile = experiment.profile(original, "a" * 32)
    tasks = profile["nodes"][0]["tasks"]
    assert original == snapshot
    assert tasks[1] == snapshot["nodes"][0]["tasks"][1]
    assert tasks[0]["envs"][0] == "EXAMPLE=PRIVATE_SENTINEL"
    assert len(tasks[0]["envs"]) == 3
    assert tasks[0]["envs"][1].endswith("/audit.so")
    assert tasks[0]["envs"][2].endswith("/stats.sock")
    assert "quasar.cfg" not in json.dumps(profile)


@pytest.mark.parametrize("change", ["preload", "duplicate", "missing", "nonce"])
def test_vqe_profile_refuses_conflicting_or_unknown_state(change: str) -> None:
    value = stock()
    nonce = "a" * 32
    if change == "preload":
        value["nodes"][0]["tasks"][0]["envs"].append("LD_PRELOAD=unapproved")
    elif change == "duplicate":
        value["nodes"][0]["tasks"].append(copy.deepcopy(value["nodes"][0]["tasks"][0]))
    elif change == "missing":
        value["nodes"][0]["tasks"].pop(0)
    else:
        nonce = "invalid"
    with pytest.raises(SafetyError):
        experiment.profile(value, nonce)


def test_vqe_guard_has_fixed_rollback_without_vendor_file_editing() -> None:
    for source in [experiment.INSPECT, experiment.GUARD, experiment.APPLY, experiment.STATUS]:
        subprocess.run(["sh", "-n"], input=source.encode(), check=True, capture_output=True)
    assert experiment.GUARD.count('"$Q/q" restart') == 1
    assert "killall" not in experiment.GUARD + experiment.APPLY
    assert "mount -o" not in experiment.GUARD + experiment.APPLY
    assert "sleep 60" in experiment.GUARD
    assert "metadata_only" in experiment.APPLY


@pytest.mark.parametrize("change", ["none", "expire", "revoke", "artifact", "uncertain"])
def test_vqe_start_is_fresh_bound_single_use_and_never_retried(
    tmp_path: Path, monkeypatch: Any, change: str,
) -> None:
    key, hosts, inventory = tmp_path / "identity", tmp_path / "hosts", tmp_path / "inventory.json"
    key.touch(mode=0o600)
    hosts.touch()
    value = {"stations": [{"id": "desk", "model": "station-mini", "host": "station.invalid",
                           "port": 2222, "user": "root", "identity_file": str(key),
                           "known_hosts_file": str(hosts), "preauthorize_all_actions": True,
                           "controls": {"firmware_sha256": "a" * 64,
                                        "allowed_actions": ["speech.say"]}}]}
    inventory.write_text(json.dumps(value))
    inventory.chmod(0o600)
    audit, collector = b"synthetic observer", b"synthetic collector"
    monkeypatch.setattr(experiment, "AUDIT_SHA256", hashlib.sha256(audit).hexdigest())
    monkeypatch.setattr(experiment, "COLLECTOR_SHA256", hashlib.sha256(collector).hexdigest())
    executions = []

    def runner(station: Any, request: Any) -> str:
        assert not station.native_mcp
        if (request.data.startswith(experiment.INSPECT.encode())
            and b'mkdir "$CLAIM"' not in request.data):
            return json.dumps({"outcome": "ready", "state_sha256": "b" * 64})
        executions.append(1)
        if change == "uncertain":
            raise SafetyError("PRIVATE_SENTINEL")
        return json.dumps({"outcome": "capturing", "capture_ready": False,
                           "metadata_only": True, "do_not_retry": True})

    controller = Controller(inventory, writes=True, runner=runner)
    item = experiment.candidate(controller.station("desk"), json.dumps(stock()).encode(),
                                audit, collector)
    preview = experiment.preview(controller, "desk", item)
    assert "PRIVATE_SENTINEL" not in str(preview) + repr(item)
    assert not executions
    if change == "expire":
        monkeypatch.setattr(controller, "clock", lambda: float("inf"))
    elif change == "revoke":
        value["stations"][0]["preauthorize_all_actions"] = False
        inventory.write_text(json.dumps(value))
    elif change == "artifact":
        from dataclasses import replace
        item = replace(item, audit=b"unreviewed")
    result = experiment.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    assert "PRIVATE_SENTINEL" not in str(result)
    assert result["outcome"] == {"none": "capturing", "uncertain": "unknown"}.get(change, "blocked")
    assert len(executions) == (1 if change in {"none", "uncertain"} else 0)
    assert experiment.apply(controller, preview["plan_id"], preview["expected_sha256"],
                            item)["outcome"] == "blocked"
    assert len(executions) <= 1


@pytest.mark.parametrize("runtime", ["stock", "owned", "other", "changed_pin"])
@pytest.mark.parametrize("deferred", [False, True])
def test_vqe_guard_restores_only_its_own_profile(
    tmp_path: Path, runtime: str, deferred: bool,
) -> None:
    root = tmp_path / ("audit-" + "a" * 32)
    root.mkdir()
    if deferred:
        (root / "finish").touch()
    q = tmp_path / "vendor"
    q.mkdir()
    controller = q / "q"
    controller.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "' + str(q / "actions") + '"\n')
    controller.chmod(0o700)
    launch = q / "launch.json"
    launch.write_text("synthetic stock")
    commandline = tmp_path / "commandline"
    value = "launch.json" if runtime == "stock" else (
        str(root / "launch.audit.json") if runtime in {"owned", "changed_pin"}
        else "another-owner.json"
    )
    commandline.write_bytes(b"quasar_launcher2\x00" + value.encode() + b"\x00")
    source = experiment.guard_source(deferred).replace("Q=/system/vendor/quasar", "Q=" + str(q))
    source = source.replace("/bkupgrade/data/opt/alice-mcp-vqe-audit-", str(tmp_path / "audit-"))
    source = source.replace("sleep 60", ":").replace("sleep 3", ":")
    source = source.replace("$(pidof quasar_launcher2)", "12345")
    source = source.replace("/proc/$1/cmdline", str(commandline))
    pin = hashlib.sha256(controller.read_bytes()).hexdigest()
    if runtime == "changed_pin":
        pin = "b" * 64
    subprocess.run(["sh", "-s", "--", str(root), pin,
                    hashlib.sha256(launch.read_bytes()).hexdigest()], input=source.encode(),
                   capture_output=True, check=True, timeout=5)
    result = json.loads((root / "restored.json").read_text())
    assert result["guard_finished"]
    assert result["rollback_requested"] == (runtime == "owned")
    assert (q / "actions").exists() == (runtime == "owned")
    if runtime == "owned":
        assert (q / "actions").read_text() == "restart\n"


@pytest.mark.parametrize("clean", [False, True])
def test_mute_probe_drops_state_when_cleanup_is_not_verified(clean: bool) -> None:
    observed = {"outcome": "complete", "mute_state": "unmuted",
                "current_observation_only": True, "audio_captured": False}
    trailer = {"outcome": "cleanup", "cleanup_verified": clean, "quasar_running": True}
    result = experiment.parse_mute_result(json.dumps(observed) + "\n" + json.dumps(trailer))
    assert result["mute_state"] == ("unmuted" if clean else "unknown")
    assert result["cleanup_verified"] == clean
    observed["private"] = "PRIVATE_SENTINEL"
    with pytest.raises(SafetyError):
        experiment.parse_mute_result(json.dumps(observed) + "\n" + json.dumps(trailer))


def test_vqe_level_samples_are_bounded_metadata_not_arbitrary_payload(monkeypatch: Any) -> None:
    statistics = {name: 0 for name in ["packets", "rejected", "types_bitmap", "type6_calls",
                                       "type6_frames", "nonfinite", "outside", "peak_millionths",
                                       "mean_square_billionths"]}
    statistics.update(outcome="complete", metadata_only=True, cleanup_verified=True,
                      started_monotonic_ms=1000, samples=[{
                          "t_ms": 1, "calls": 1, "peak_millionths": 100000,
                          "mean_square_billionths": 1000000,
                      }])
    payload = {"outcome": "complete", "stock_launcher": True, "quasar_running": True,
               "module_unmapped": True, "metadata_only": True, "statistics": statistics,
               "guard": {"guard_finished": True, "policy_unchanged": True,
                         "rollback_requested": True, "command_succeeded": True}}
    controller = SimpleNamespace(station=lambda _: object(), runner=lambda *_: json.dumps(payload))
    monkeypatch.setattr(experiment, "_ssh", lambda station: station)
    result = experiment.status(controller, "desk", "a" * 32)
    assert result["restoration_verified"] and len(result["statistics"]["samples"]) == 1
    statistics["samples"][0]["private"] = "PRIVATE_SENTINEL"
    rejected = experiment.status(controller, "desk", "a" * 32)
    assert rejected["outcome"] == "unknown" and "PRIVATE_SENTINEL" not in str(rejected)


def test_vqe_phase_comparison_does_not_claim_electrical_proof_or_reuse_transition_samples() -> None:
    start = 1000000
    samples = [{"t_ms": ms, "calls": 1, "peak_millionths": peak,
                "mean_square_billionths": power}
               for ms, peak, power in [(500, 999999, 999999999), (2000, 0, 0), (4000, 0, 0),
                                       (6000, 0, 0), (22000, 100000, 1000000),
                                       (24000, 100000, 1000000), (26000, 100000, 1000000)]]
    report = {"restoration_verified": True, "statistics": {"started_monotonic_ms": start,
               "samples": samples, "nonfinite": 0, "outside": 0}}
    phases = {"muted_phase_start_ms": start, "muted_phase_end_ms": start + 10000,
              "unmuted_phase_start_ms": start + 20000,
              "unmuted_phase_end_ms": start + 30000,
              "owner_confirmed_muted_red": True, "owner_confirmed_unmuted_red_off": True}
    result = experiment.compare_mute_levels(report, phases)
    assert result["outcome"] == "complete"
    assert result["muted"]["sample_count"] == 3
    assert result["muted"]["peak_millionths_max"] == 0
    assert result["unmuted"]["peak_millionths_max"] == 100000
    assert result["level_relation"] == "muted_below_resolution_unmuted_nonzero"
    assert result["electrical_power_cut_verified"] is False
    phases["unmuted_phase_start_ms"] = start + 44000
    assert experiment.compare_mute_levels(report, phases)["outcome"] == "unknown"


def test_deferred_vqe_profile_does_not_start_a_collector_before_owner_confirmation(
    monkeypatch: Any,
) -> None:
    audit, collector = b"synthetic observer", b"synthetic collector"
    monkeypatch.setattr(experiment, "AUDIT_SHA256", hashlib.sha256(audit).hexdigest())
    monkeypatch.setattr(experiment, "COLLECTOR_SHA256", hashlib.sha256(collector).hexdigest())
    item = experiment.candidate(SimpleNamespace(model="station-mini"), json.dumps(stock()).encode(),
                                audit, collector, deferred=True)
    assert item.deferred is True
    assert b'setsid "$ROOT/collector" "$id"' not in item.source
    guard = experiment.guard_source(True)
    subprocess.run(["sh", "-n"], input=guard.encode(), capture_output=True, check=True)
    assert '"$ROOT/finish"' in guard and '"$elapsed" -lt 900' in guard
    assert "/proc/uptime" in guard and "elapsed=$((now-start))" in guard
    assert "elapsed=$((elapsed+1))" not in guard
    assert guard.count('"$Q/q" restart') == 1


def test_deferred_phase_is_a_fixed_owned_window_without_quasar_restart(monkeypatch: Any) -> None:
    stats = {name: 0 for name in ["packets", "rejected", "types_bitmap", "type6_calls",
                                  "type6_frames", "nonfinite", "outside", "peak_millionths",
                                  "mean_square_billionths"]}
    stats.update(outcome="complete", metadata_only=True, cleanup_verified=True,
                 started_monotonic_ms=1000, samples=[])
    calls = []

    def runner(station: Any, request: Any) -> str:
        calls.append(request)
        assert b"sleep 8" in request.data and b"LD_PRELOAD" not in request.data
        assert b'"$Q/q" stop' not in request.data and b'"$Q/q" restart' not in request.data
        return json.dumps(stats)

    station = SimpleNamespace(model="station-mini", preauthorize_all_actions=True,
                              controls={"firmware_sha256": "a" * 64})
    controller = SimpleNamespace(writes=True, station=lambda _: station, runner=runner)
    monkeypatch.setattr(experiment, "_ssh", lambda value: value)
    result = experiment.measure_phase(controller, "desk", "a" * 32, "muted")
    assert result["outcome"] == "complete" and result["phase"] == "muted"
    with pytest.raises(SafetyError):
        experiment.measure_phase(controller, "desk", "a" * 32, "../unapproved")
    assert len(calls) == 1
    for source in [experiment.MEASURE_PHASE, experiment.FINISH]:
        subprocess.run(["sh", "-n"], input=source.encode(), capture_output=True, check=True)


def test_separate_phase_windows_do_not_depend_on_human_response_latency() -> None:
    def window(phase: str, peak: int) -> dict[str, Any]:
        stats = {name: 0 for name in ["packets", "rejected", "types_bitmap", "type6_calls",
                                      "type6_frames", "nonfinite", "outside", "peak_millionths",
                                      "mean_square_billionths"]}
        stats.update(outcome="complete", metadata_only=True, cleanup_verified=True,
                     started_monotonic_ms=1000 if phase == "muted" else 500000,
                     samples=[{"t_ms": ms, "calls": i + 1, "peak_millionths": peak,
                               "mean_square_billionths": peak}
                              for i, ms in enumerate([2000, 4000, 6000])])
        return {"outcome": "complete", "phase": phase, "statistics": stats}

    muted, unmuted = window("muted", 0), window("unmuted", 100)
    result = experiment.compare_phase_levels(muted, unmuted, owner_confirmed=True)
    assert result["outcome"] == "complete" and result["muted"]["sample_count"] == 3
    assert result["level_relation"] == "muted_below_resolution_unmuted_nonzero"
    assert result["electrical_power_cut_verified"] is False
    declined = experiment.compare_phase_levels(muted, unmuted, owner_confirmed=False)
    assert declined["outcome"] == "unknown"


@pytest.mark.parametrize("mode", ["complete", "interrupt", "baseline_not_ready"])
def test_operator_workflow_always_finishes_before_comparison_and_never_repeats_a_phase(
    monkeypatch: Any, mode: str,
) -> None:
    interrupt = mode == "interrupt"
    events = []
    item = SimpleNamespace(identifier="a" * 32)
    controller = SimpleNamespace(station=lambda _: SimpleNamespace(model="station-mini"),
                                 runner=lambda *_: json.dumps(stock()))
    monkeypatch.setattr(experiment, "_ssh", lambda s: s)
    monkeypatch.setattr(experiment, "candidate", lambda *a, **kw: item if kw["deferred"] else None)
    monkeypatch.setattr(experiment, "preview", lambda *_: {
        "plan_id": "plan", "expected_sha256": "b",
    })
    monkeypatch.setattr(experiment, "apply", lambda *_: {"outcome": "prepared"})

    def measure(*args: Any) -> dict[str, Any]:
        events.append(args[-1])
        times = [13000, 15000, 17000] if args[-1] == "baseline" else [2000, 4000, 6000]
        if mode == "baseline_not_ready":
            times = [1000, 2000, 3000]
        return {"outcome": "complete", "phase": args[-1], "statistics": phase_statistics(
            times,
        )}

    def finish(*_: Any) -> dict[str, str]:
        events.append("finish")
        return {"outcome": "finish_requested"}

    def cleanup(*_: Any) -> dict[str, bool]:
        events.append("cleanup")
        return {"cleanup_verified": True}

    monkeypatch.setattr(experiment, "measure_phase", measure)
    monkeypatch.setattr(experiment, "request_finish", finish)
    monkeypatch.setattr(experiment, "status", lambda *_: {"outcome": "complete",
                                                        "restoration_verified": True})
    monkeypatch.setattr(experiment, "cleanup", cleanup)
    monkeypatch.setattr(experiment, "compare_phase_levels", lambda *a, **kw: {
        "outcome": "complete",
    })
    replies = iter(["START 900", "BASELINE", "MUTED"] if interrupt else
                   ["START 900", "BASELINE", "MUTED", "UNMUTED"])

    def ask(_: str) -> str:
        try:
            return next(replies)
        except StopIteration:
            raise EOFError from None

    report = experiment.operator_levels(controller, "desk", b"audit", b"collector", ask=ask,
                                         wait=lambda _: None)
    expected = ["baseline"]
    if mode != "baseline_not_ready":
        expected += ["muted"] if interrupt else ["muted", "unmuted"]
    assert events == expected + ["finish", "cleanup"]
    assert report["restoration_verified"] and report["cleanup_verified"]
    assert report["outcome"] == ("complete" if mode == "complete" else "unknown")
    if mode == "baseline_not_ready":
        assert report["error_code"] == "baseline_not_ready"


def test_operator_cli_cannot_be_started_by_piped_agent_confirmations(monkeypatch: Any) -> None:
    monkeypatch.setattr(experiment.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(experiment.sys, "argv", ["vqe", "--station", "desk",
                                                "--audit", "absent", "--collector", "absent"])
    with pytest.raises(SystemExit) as result:
        experiment.main()
    assert result.value.code == 2


def test_missing_phase_coverage_keeps_both_counts_and_bounded_diagnostics() -> None:
    muted = {"outcome": "complete", "phase": "muted", "statistics": phase_statistics([])}
    unmuted = {"outcome": "complete", "phase": "unmuted",
               "statistics": phase_statistics([2000, 4000, 6000])}
    result = experiment.compare_phase_levels(muted, unmuted, owner_confirmed=True)
    assert result["outcome"] == "unknown" and not result["electrical_power_cut_verified"]
    assert result["sample_counts"] == {"muted": 0, "unmuted": 3}
    assert result["phase_diagnostics"]["muted"]["first_sample_ms"] is None
    assert result["phase_diagnostics"]["unmuted"]["last_sample_ms"] == 6000
    muted["statistics"]["samples"] = [{"private": "PRIVATE_SENTINEL"}]
    rejected = experiment.compare_phase_levels(muted, unmuted, owner_confirmed=True)
    assert "PRIVATE_SENTINEL" not in str(rejected)


def test_baseline_needs_fresh_valid_samples_not_just_a_bound_socket() -> None:
    baseline = {"outcome": "complete", "phase": "baseline",
                "statistics": phase_statistics([1000, 2000, 3000])}
    assert experiment.baseline_ready(baseline) is False
    baseline["statistics"] = phase_statistics([13000, 15000, 17000])
    assert experiment.baseline_ready(baseline) is True
    baseline["statistics"]["outside"] = 1
    assert experiment.baseline_ready(baseline) is False
