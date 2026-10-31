"""Operator pilot tests use only generated WAV and a synthetic SSH peer."""

import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import wave
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import pcm_pilot
from alice_mcp.core import Controller, SafetyError


def test_transfer_delivers_lines_before_the_remote_process_finishes(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    marker = tmp_path / "received"
    # The peer cannot finish until its first line has reached the callback.
    code = ("import pathlib,sys,time; sys.stdin.buffer.read(); "
            "print('ready',flush=True); p=pathlib.Path(sys.argv[1]); "
            "deadline=time.monotonic()+3\n"
            "while not p.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
            "assert p.exists(); print('done',flush=True)")
    monkeypatch.setattr(pcm_pilot, "ssh_argv", lambda *_: [sys.executable, "-c", code,
                                                          str(marker)])
    seen = []

    def receive(line: bytes) -> None:
        seen.append(line)
        marker.touch()

    result = pcm_pilot._transfer(None, pcm_pilot._Transfer("synthetic", b"payload"),
                                 limit=32, on_line=receive)  # type: ignore[arg-type]
    assert seen == [b"ready\n", b"done\n"] and result == "ready\ndone\n"


@pytest.mark.parametrize("body", [b"x" * 33, b"unterminated"])
def test_stream_transfer_never_delivers_oversized_or_unterminated_lines(
    monkeypatch: Any, body: bytes,
) -> None:
    code = "import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(" + repr(body) + ")"
    monkeypatch.setattr(pcm_pilot, "ssh_argv", lambda *_: [sys.executable, "-c", code])
    seen = []
    with pytest.raises(SafetyError, match="uncertain"):
        pcm_pilot._transfer(None, pcm_pilot._Transfer("synthetic", b""),
                            limit=32, on_line=seen.append)  # type: ignore[arg-type]
    assert seen == []


@pytest.mark.parametrize("change", ["none", "expire", "revoke", "artifact", "uncertain",
                                   "quasar_changed", "short_frames", "private", "audio",
                                   "source", "policy", "trust"])
def test_pcm_pilot_is_pinned_single_use_and_has_no_quasar_or_cloud_action(
    tmp_path: Path, monkeypatch: Any, change: str,
) -> None:
    key, hosts, inventory = tmp_path / "identity", tmp_path / "hosts", tmp_path / "inventory.json"
    key.touch(mode=0o600)
    hosts.touch()
    data = {"stations": [{"id": "desk", "model": "station-mini", "host": "station.invalid",
                           "port": 2222, "user": "root", "identity_file": str(key),
                           "known_hosts_file": str(hosts), "preauthorize_all_actions": True,
                           "controls": {"firmware_sha256": "a" * 64,
                                        "allowed_actions": ["speech.say"]}}]}
    inventory.write_text(json.dumps(data))
    inventory.chmod(0o600)
    binary = b"synthetic reviewed player"
    monkeypatch.setattr(pcm_pilot, "PLAYER_SHA256", hashlib.sha256(binary).hexdigest())
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x01\x00" * 88200)
    item = pcm_pilot.Candidate("b" * 32, binary, audio.getvalue(), "c" * 64, "d" * 64)
    calls = []

    def runner(station: Any, command: str) -> str:
        assert not station.native_mcp
        assert "inspect" in command
        return json.dumps({"outcome": "ready", "state_sha256": "e" * 64})

    def transfer(station: Any, request: Any) -> str:
        assert not station.native_mcp
        assert request.data == item.player + item.audio
        assert len(request.data) > 120 * 1024
        assert "base64" not in request.command
        calls.append(1)
        if change == "uncertain":
            raise SafetyError("PRIVATE_SENTINEL")
        result = {"outcome": "complete", "playback_started": True,
                  "playback_completed": True, "cleanup_verified": True,
                   "frames_written": 88199 if change == "short_frames" else 88200,
                   "sample_rate": 22050, "input_frames": 88200,
                    "stage": "complete", "alsa_return_code": 0,
                    "hardware_parameters_verified": True, "playback_elapsed_ms": 4000}
        if change == "private":
            result["private"] = "PRIVATE_SENTINEL"
        return json.dumps(result) + "\n" + json.dumps({
            "outcome": "cleanup", "cleanup_verified": True,
            "quasar_running": True, "quasar_unchanged": change != "quasar_changed"})

    monkeypatch.setattr(pcm_pilot, "_transfer", transfer)
    controller = Controller(inventory, writes=True, runner=runner)
    preview = pcm_pilot.preview(controller, "desk", item)
    if change == "expire":
        monkeypatch.setattr(controller, "clock", lambda: float("inf"))
    elif change == "revoke":
        data["stations"][0]["preauthorize_all_actions"] = False
        inventory.write_text(json.dumps(data))
    elif change == "artifact":
        item = replace(item, player=b"unreviewed")
    elif change == "audio":
        item = replace(item, audio=item.audio[:-2] + b"\x02\x00")
    elif change == "source":
        monkeypatch.setattr(pcm_pilot, "PIDS", pcm_pilot.PIDS + " ")
    elif change == "policy":
        data["stations"][0]["controls"]["allowed_actions"] = []
        inventory.write_text(json.dumps(data))
    elif change == "trust":
        hosts.write_text("changed synthetic trust")
    result = pcm_pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)
    dispatched = change not in {"expire", "revoke", "artifact", "audio", "source",
                                "policy", "trust"}
    expected = "applied" if change == "none" else "unknown" if dispatched else "blocked"
    assert result["outcome"] == expected
    assert "PRIVATE_SENTINEL" not in str(result)
    assert len(calls) == int(dispatched)
    assert pcm_pilot.apply(controller, preview["plan_id"], preview["expected_sha256"], item)[
        "outcome"] == "blocked"


@pytest.mark.parametrize("mode", ["complete", "missing", "invalid_input", "check", "absent_node",
                                  "truncated_player", "truncated_audio", "extra_audio",
                                  "firmware_changed", "config_changed", "library_changed",
                                  "boot_changed", "pid_changed", "claim_busy"])
def test_actual_shell_pipeline_retains_independent_cleanup_and_exit_metadata(
    tmp_path: Path, monkeypatch: Any, mode: str,
) -> None:
    """Run the generated shell and a tiny fake player, never ALSA or SSH."""
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("Synthetic shell pipeline needs an existing compiler")
    program = tmp_path / "player.c"
    program.write_text(r'''
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv) {
#if MODE == 3
    if (argc != 2 || strcmp(argv[1], "--check") || getchar() != EOF) return 90;
    puts("{\"outcome\":\"ready\",\"diagnostic_only\":true,\"stage\":\"ready\","
         "\"library_loaded\":true,\"configuration_parsed\":true,\"alsa_return_code\":0,"
         "\"pcm_opened\":false,\"audio_played\":false}"); return 0;
#else
    unsigned char data[1920045];
    size_t count = fread(data, 1, sizeof(data), stdin);
    if (argc != 2 || strcmp(argv[1], "--play") || count != 1920044 ||
        memcmp(data, "RIFF", 4) || memcmp(data + 8, "WAVE", 4)) return 90;
#if MODE == 1
    fputs("PRIVATE_SENTINEL", stderr); return 127;
#elif MODE == 2
    puts("{\"outcome\":\"blocked\",\"playback_started\":false,"
         "\"playback_completed\":false,\"cleanup_verified\":true,"
         "\"frames_written\":0,\"sample_rate\":0,\"error_code\":\"invalid_wav\"}");
    return 1;
#else
    puts("{\"outcome\":\"complete\",\"playback_started\":true,"
          "\"playback_completed\":true,\"cleanup_verified\":true,"
          "\"frames_written\":960000,\"sample_rate\":48000,"
          "\"input_frames\":960000,\"stage\":\"complete\",\"alsa_return_code\":0,"
          "\"hardware_parameters_verified\":true,\"playback_elapsed_ms\":20000}"); return 0;
#endif
#endif
}
''')
    executable = tmp_path / "player"
    variant = {"missing": 1, "invalid_input": 2, "check": 3}.get(mode, 0)
    subprocess.run([compiler, "-Os", "-s", f'-DMODE={variant}',
                    str(program), "-o", str(executable)], capture_output=True, check=True,
                   timeout=30)
    binary = executable.read_bytes()
    monkeypatch.setattr(pcm_pilot, "PLAYER_SHA256", hashlib.sha256(binary).hexdigest())
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x01\x00" * 960000)
    audio = stream.getvalue()
    data = tmp_path / "data"
    (data / "opt").mkdir(parents=True)
    playback_node = tmp_path / "synthetic-playback-node"
    if mode not in {"absent_node", "check"}:
        playback_node.touch()
    firmware, config, library = (tmp_path / name for name in ("maind", "alsa", "libalsa"))
    firmware.write_bytes(b"\x7fELF\x01\x01" + b"\0" * 12 + b"\x28\0")
    config.write_bytes(b"synthetic ALSA configuration")
    library.write_bytes(b"synthetic library identity")
    boot = tmp_path / "boot"
    boot.write_text("synthetic boot A")
    shims = tmp_path / "shims"
    shims.mkdir()
    pidof = shims / "pidof"
    pidof.write_text("#!/bin/sh\nprintf '17\\n'\n")
    pidof.chmod(0o700)
    environment = {**os.environ, "PATH": str(shims) + ":" + os.environ["PATH"]}
    item = pcm_pilot.Candidate("b" * 32, binary, b"" if mode == "check" else audio,
                               hashlib.sha256(config.read_bytes()).hexdigest(),
                               hashlib.sha256(library.read_bytes()).hexdigest(),
                               diagnostic=mode == "check")
    station = type("Station", (), {"controls": {
        "firmware_sha256": hashlib.sha256(firmware.read_bytes()).hexdigest(),
    }})()

    def run(expected: str = "") -> str:
        request = pcm_pilot._command(station, item, expected)
        args = shlex.split(request.command)
        source = args[2].replace("OWNER_UID=0", f"OWNER_UID={os.getuid()}")
        source = source.replace('[ -c /dev/snd/pcmC0D0p ]',
                                '[ -f ' + shlex.quote(str(playback_node)) + ' ]')
        source = source.replace("DATA=/data", "DATA=" + str(data))
        for old, path in [("/system/vendor/quasar/maind", firmware),
                          ("/etc/asound.conf", config), (pcm_pilot.LIBRARY, library),
                          ("/proc/sys/kernel/random/boot_id", boot),
                          ("/bkupgrade/data/opt/alice-mcp-vqe-audit-active", data / "inactive")]:
            source = source.replace(old, str(path))
        args[2] = source
        payload = request.data
        if expected:
            if mode == "truncated_player":
                payload = payload[:len(binary) - 1]
            elif mode == "truncated_audio":
                payload = payload[:-1]
            elif mode == "extra_audio":
                payload += b"x"
        monkeypatch.setenv("PATH", environment["PATH"])
        monkeypatch.setattr(pcm_pilot, "ssh_argv", lambda station, command: args)
        if mode.endswith("_changed") or mode == "claim_busy":
            # Inspect shell refusals independently: closing stdin early makes the binary
            # transport conservatively uncertain (broken pipe), never retryable.
            peer = subprocess.run(args, input=payload, capture_output=True, check=True,
                                  env=environment, timeout=5)
            result = peer.stdout.decode()
        else:
            result = pcm_pilot._transfer(station, replace(request, data=payload))
        assert "PRIVATE_SENTINEL" not in result
        return result

    state = json.loads(run())
    if mode == "absent_node":
        assert state["outcome"] == "blocked"
        assert list((data / "opt").iterdir()) == []
        return
    changed_file = {"firmware_changed": firmware, "config_changed": config,
                    "library_changed": library, "boot_changed": boot}.get(mode)
    if changed_file:
        changed_file.write_bytes(changed_file.read_bytes() + b"changed")
    elif mode == "pid_changed":
        pidof.write_text("#!/bin/sh\nprintf '18\\n'\n")
    elif mode == "claim_busy":
        (data / "opt/alice-mcp-asr-active").mkdir()
    raw = run(state["state_sha256"])
    if changed_file or mode in {"pid_changed", "claim_busy"}:
        assert pcm_pilot._result(raw, audio)["outcome"] == "blocked"
        assert not (data / "opt" / ("alice-mcp-pcm-" + item.identifier)).exists()
        return
    result = pcm_pilot._result(raw, audio, diagnostic=mode == "check")
    assert result["helper_cleanup_verified"] and result["quasar_running"]
    assert not (data / "opt" / ("alice-mcp-pcm-" + item.identifier)).exists()
    assert not (data / "opt/alice-mcp-asr-active").exists()
    assert list((data / "opt").iterdir()) == []
    if mode == "check":
        assert result["outcome"] == "ready" and result["diagnostic_only"] is True
        assert not result["pcm_opened"] and not result["audio_played"]
    elif mode == "complete":
        assert result["outcome"] == "applied" and result["cleanup_verified"]
    else:
        assert result["outcome"] == "unknown" and result["do_not_retry"]
        assert result["cleanup_verified"] is False
        assert result["helper_stage"] == ("staging" if mode == "truncated_player"
                                          else "player_returned")
        assert result["player_exit_status"] == dict(missing=127, invalid_input=1,
            truncated_player=-1, truncated_audio=90, extra_audio=90)[mode]
        assert result["error_code"] == ("player_output_missing" if mode != "invalid_input"
                                         else "player_output_invalid")


def test_bad_player_stdout_keeps_validated_helper_metadata_without_private_output() -> None:
    trailer = {"outcome": "cleanup", "cleanup_verified": True, "quasar_running": True,
               "quasar_unchanged": True, "helper_stage": "player_returned", "player_exit_status": 1}
    for prefix in ["PRIVATE_SENTINEL", "PRIVATE_SENTINEL\nPRIVATE_SENTINEL"]:
        result = pcm_pilot._result(prefix + "\n" + json.dumps(trailer), b"")
        assert result["outcome"] == "unknown" and result["helper_cleanup_verified"]
        assert result["error_code"] == "player_output_invalid"
        assert "PRIVATE_SENTINEL" not in str(result)


def test_diagnostic_accepts_the_same_nonnegative_success_codes_as_the_c_client() -> None:
    report = {"outcome": "ready", "diagnostic_only": True, "stage": "ready",
              "library_loaded": True, "configuration_parsed": True, "alsa_return_code": 1,
              "pcm_opened": False, "audio_played": False}
    trailer = {"outcome": "cleanup", "cleanup_verified": True, "quasar_running": True,
               "quasar_unchanged": True, "helper_stage": "player_returned", "player_exit_status": 0}
    raw = json.dumps(report) + "\n" + json.dumps(trailer)
    result = pcm_pilot._result(raw, b"", diagnostic=True)
    assert result["outcome"] == "ready" and result["alsa_return_code"] == 1
    report["configuration_parsed"] = False
    rejected = pcm_pilot._result(json.dumps(report) + "\n" + json.dumps(trailer), b"",
                                 diagnostic=True)
    assert rejected["outcome"] == "unknown"
    assert rejected["validation_code"] == "diagnostic_correlation"


def test_playback_failure_keeps_input_frame_count_and_safe_alsa_stage() -> None:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x01\x00" * 2205)
    trailer = {"outcome": "cleanup", "cleanup_verified": True, "quasar_running": True,
               "quasar_unchanged": True, "helper_stage": "player_returned", "player_exit_status": 1}
    report = {"outcome": "unknown", "playback_started": True, "playback_completed": False,
              "cleanup_verified": True, "frames_written": 2205, "sample_rate": 22050,
              "input_frames": 2205, "stage": "drain", "alsa_return_code": -5,
              "hardware_parameters_verified": True, "playback_elapsed_ms": 100}
    result = pcm_pilot._result(json.dumps(report) + "\n" + json.dumps(trailer), stream.getvalue())
    assert result["outcome"] == "unknown" and result["do_not_retry"]
    assert result["input_frames"] == 2205 and result["stage"] == "drain"
    assert result["alsa_return_code"] == -5
    for key, value in [("stage", "PRIVATE_SENTINEL"), ("input_frames", 2206),
                       ("alsa_return_code", True), ("hardware_parameters_verified", False),
                       ("playback_elapsed_ms", 25001)]:
        rejected = pcm_pilot._result(json.dumps(dict(report, **{key: value})) + "\n" +
                                     json.dumps(trailer), stream.getvalue())
        assert rejected["outcome"] == "unknown" and rejected["do_not_retry"]
        assert rejected["error_code"] == "player_output_invalid"
        assert "PRIVATE_SENTINEL" not in str(rejected)


def test_payload_and_artifact_bounds(monkeypatch: Any) -> None:
    binary = b"synthetic player"
    monkeypatch.setattr(pcm_pilot, "PLAYER_SHA256", hashlib.sha256(binary).hexdigest())
    item = pcm_pilot.Candidate("b" * 32, binary, b"", "c" * 64, "d" * 64, diagnostic=True)
    assert item.source(inspect=False)
    for invalid in [replace(item, player=b"x" * (pcm_pilot.MAX_TRANSFER + 1)),
                    replace(item, audio=b"x"), replace(item, diagnostic=False)]:
        with pytest.raises(SafetyError):
            invalid.source(inspect=False)
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x01\x00" * (48000 * 20 + 1))
    for payload in [audio.getvalue(), audio.getvalue()[:-3]]:
        with pytest.raises(SafetyError):
            replace(item, diagnostic=False, audio=payload).source(inspect=False)


@pytest.mark.parametrize("mode", ["oversize", "output_limit", "nonzero", "timeout"])
def test_transfer_is_bounded_and_never_retries(
    monkeypatch: Any, mode: str,
) -> None:
    calls = []

    class Peer:
        stdin = io.BytesIO()
        stdout = io.BytesIO(b"x" * 4097 if mode == "output_limit" else b"{}")

        def __enter__(self) -> "Peer":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def kill(self) -> None:
            calls.append("kill")

        def wait(self, timeout: int) -> int:
            if mode == "timeout" and timeout == 120:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return 1 if mode == "nonzero" else 0

    def popen(*args: Any, **kwargs: Any) -> Peer:
        calls.append("start")
        return Peer()

    monkeypatch.setattr(pcm_pilot.subprocess, "Popen", popen)
    monkeypatch.setattr(pcm_pilot, "ssh_argv", lambda station, command: ["synthetic"])
    request = pcm_pilot._Transfer("fixed", b"x" * (pcm_pilot.MAX_TRANSFER + 1)
                                 if mode == "oversize" else b"payload")
    with pytest.raises(SafetyError):
        pcm_pilot._transfer(None, request)  # type: ignore[arg-type]
    assert calls.count("start") == (0 if mode == "oversize" else 1)
