"""Human-run, one-shot local playback qualification, not a native-to-SSH fallback.

The normal speech route remains resident MCP. This development pilot transfers
one pinned player temporarily, never WAV files; it does not install or upgrade an
agent, change mixer/Quasar, accept URLs, or retry an uncertain playback.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any

from alice_mcp import local_tts, native_transport, speech_capture
from alice_mcp.core import MAX_OUTPUT, Controller, SafetyError, Station, ssh_argv
from alice_mcp.vqe_experiment import _ssh

PLAYER_SHA256 = "123d9d4221aa2054ff150202c0f4f5a36b008c93335d7c80cd76846d1bcd6087"
LIBRARY = "/system/vendor/quasar/libs/libasound.so.2"
PIDS = "pidof maind | tr ' ' '\\n' | sort -n | sha256sum | cut -d ' ' -f 1"
MAX_TRANSFER = 20 * 1024 * 1024


@dataclass(frozen=True)
class _Transfer:
    command: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class Candidate:
    identifier: str
    player: bytes = field(repr=False)
    audio: bytes = field(repr=False)
    alsa_config_sha256: str
    library_sha256: str
    diagnostic: bool = False

    def source(self, *, inspect: bool) -> bytes:
        try:
            if (type(self.diagnostic) is not bool
                or type(self.player) is not bytes or type(self.audio) is not bytes
                or not 0 < len(self.player) <= MAX_TRANSFER - len(self.audio)
                or not re.fullmatch("[a-f0-9]{32}", self.identifier)
                or any(not re.fullmatch("[a-f0-9]{64}", v) for v in
                       (self.alsa_config_sha256, self.library_sha256))
                or hashlib.sha256(self.player).hexdigest() != PLAYER_SHA256):
                raise ValueError
            if self.diagnostic:
                if self.audio != b"":
                    raise ValueError
            else:
                local_tts.wav_parameters(self.audio)
            source = speech_capture.SCRIPT.split('if [ "$action" = speech.say ]; then')[0]
            source = source.replace("for c in ", "for c in dd env tr sort ", 1)
            source = source.replace("OUT=$DATA/opt/alice-mcp-asr-", "OUT=$DATA/opt/alice-mcp-pcm-")
            checks = (
                '[ ! -e /bkupgrade/data/opt/alice-mcp-vqe-audit-active ] || blocked\n'
                f'[ "$(sha256sum /etc/asound.conf | cut -d \' \' -f 1)" = '
                f'"{self.alsa_config_sha256}" ] || blocked\n'
                f'[ "$(sha256sum {LIBRARY} | cut -d \' \' -f 1)" = '
                f'"{self.library_sha256}" ] || blocked\n'
                f'pids=$({PIDS})\n'
            )
            if not self.diagnostic:
                checks += '[ -c /dev/snd/pcmC0D0p ] || blocked\n'
            source = source.replace("ipc || blocked", checks)
            source = source.replace('"$action" "$input_sha" |', '"$action" "$input_sha" "$pids" |')
            if inspect:
                source = source[:source.index('[ "$mode" = apply ]')]
            else:
                source = source.replace('own_out=0; own_claim=0; child=',
                                        'own_out=0; own_claim=0; child=\n'
                                        'pilot_stage=staging; pilot_exit=-1')
                # bs=1 prevents dd reading ahead into the WAV; only the executable is saved.
                source = source.replace("printf '__CLIENT_OCTAL__'",
                                        f"dd bs=1 count={len(self.player)}")
                running = 'running=false; if pidof maind >/dev/null; then running=true; fi'
                source = source.replace(running, running + '\n'
                                        f'    unchanged=false; [ "$pids" != "$({PIDS})" ] || '
                                        'unchanged=true')
                source = source.replace('if [ -n "$child" ]; then kill "$child";',
                                        'if [ -n "$child" ]; then\n'
                                        '    if [ "$(readlink /proc/$child/exe)" = '
                                        '"$OUT/client" ]; '
                                        'then kill -TERM "$child"; fi;')
                source = source.replace('"quasar_running":%s}',
                                        '"quasar_running":%s,"quasar_unchanged":%s,'
                                        '"helper_stage":"%s","player_exit_status":%s}')
                source = source.replace('"$clean" "$running"',
                                        '"$clean" "$running" "$unchanged" '
                                        '"$pilot_stage" "$pilot_exit"')
                source += "pilot_stage=artifact_verified\n"
                if self.diagnostic:
                    launch = '"$OUT/client" --check </dev/null'
                else:
                    # Async shell commands otherwise receive /dev/null as stdin.
                    source += 'exec 3<&0\n'
                    launch = '"$OUT/client" --play <&3 3<&-'
                source += 'pilot_stage=player_dispatching\n' + (
                    'env -i PATH=/usr/bin:/bin LANG=C LC_ALL=C HOME=/nonexistent '
                    'ALSA_PLUGIN_DIR=/nonexistent ' + launch + ' &\n'
                ) + r'''
child=$!
wait "$child"
pilot_exit=$?
child=
pilot_stage=player_returned
exit 0
'''
            result = source.encode()
            if len(result) > 16384:
                raise ValueError
            return result
        except Exception:
            raise SafetyError("Local PCM pilot artifact/pins/WAV invalid; no transfer") from None

    @property
    def digest(self) -> str:
        digest = hashlib.sha256(self.source(inspect=False))
        digest.update(self.source(inspect=True))
        digest.update(self.player)
        digest.update(self.audio)
        return digest.hexdigest()


def _permitted(controller: Controller, station: Station) -> None:
    if (not controller.writes or not station.preauthorize_all_actions
        or station.model != "station-mini"
        or "speech.say" not in station.controls.get("allowed_actions", [])):
        raise SafetyError("Local playback pilot is not authorized")


def _command(station: Station, item: Candidate, expected: str = "") -> _Transfer:
    args = ["apply" if expected else "inspect", item.identifier, "20",
            station.controls["firmware_sha256"], PLAYER_SHA256, item.digest, expected,
            "speech.say", hashlib.sha256(item.audio).hexdigest()]
    return _Transfer("sh -c " + shlex.quote(item.source(inspect=not bool(expected)).decode())
                     + " sh " + " ".join(shlex.quote(v) for v in args),
                     item.player + item.audio if expected else b"")


def _transfer(station: Station, request: _Transfer) -> str:
    """Bounded binary stdin over existing pinned SSH; no bootstrap result-contract reuse."""
    if len(request.data) > MAX_TRANSFER or len(request.command.encode()) > 20000:
        raise SafetyError("Local PCM transfer exceeds reviewed bounds")
    with subprocess.Popen(ssh_argv(station, request.command), stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        assert process.stdin is not None and process.stdout is not None
        sink, source = process.stdin, process.stdout
        output: list[bytes] = []
        failed: list[bool] = []

        def write() -> None:
            try:
                with sink:
                    sink.write(request.data)
            except OSError:
                failed.append(True)

        def read() -> None:
            body = source.read(4097)
            output.append(body)
            if len(body) > 4096:
                process.kill()

        writer = threading.Thread(target=write, daemon=True)
        reader = threading.Thread(target=read, daemon=True)
        writer.start()
        reader.start()
        try:
            status = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
            raise SafetyError("Local PCM transfer uncertain; do not retry") from None
        finally:
            writer.join(timeout=2)
            reader.join(timeout=2)
        if (status or failed or writer.is_alive() or reader.is_alive() or not output
            or len(output[0]) > 4096):
            raise SafetyError("Local PCM transfer uncertain; do not retry")
        return output[0].decode("utf-8", errors="strict")


def preview(controller: Controller, station_id: str, item: Candidate) -> dict[str, Any]:
    station = controller.station(station_id)
    _permitted(controller, station)
    ssh = _ssh(station)
    result = json.loads(controller.runner(ssh, _command(station, item).command))
    if (set(result) != {"outcome", "state_sha256"} or result["outcome"] != "ready"
        or not re.fullmatch("[a-f0-9]{64}", result["state_sha256"])):
        raise SafetyError("Local playback pilot state is not ready")
    request = {"candidate": item.digest, "ssh_trust": ssh.trust_sha256}
    digest = hashlib.sha256(repr((station, request, result["state_sha256"])).encode()).hexdigest()
    identifier = controller.store_plan(station, "local_pcm.pilot", digest, request=request,
                                        remote_sha256=result["state_sha256"])
    return {"plan_id": identifier, "expected_sha256": digest, "expires_in_seconds": 300,
            "impact": ("Silent ALSA load/config check; no PCM open, audio input or playback"
                       if item.diagnostic else
                        "One quiet local clip up to 20 seconds; temporary pinned player, "
                        "RAM-only WAV, no Quasar stop")}


def _result(raw: str, audio: bytes, *, diagnostic: bool = False) -> dict[str, Any]:
    from alice_mcp.device_runtime import _json

    lines = raw.splitlines()
    if not 1 <= len(lines) <= 64 or len(raw) > MAX_OUTPUT:
        raise ValueError
    trailer = _json(lines[-1].encode())
    if len(lines) == 1 and type(trailer) is dict and set(trailer) == {
        "outcome", "capture_ready", "error_code",
    } and trailer["outcome"] == "blocked" and trailer["capture_ready"] is False and (
        trailer["error_code"] in speech_capture.ERRORS | speech_capture.PROFILE_ERRORS
    ):
        return {"outcome": "blocked", "error": "Local playback pilot runtime blocked action"}
    required = {"outcome", "cleanup_verified", "quasar_running", "quasar_unchanged"}
    optional = {"helper_stage", "player_exit_status"}
    if (type(trailer) is not dict or not required <= trailer.keys() <= required | optional
        or trailer["outcome"] != "cleanup"
        or any(type(trailer[k]) is not bool for k in required - {"outcome"})
        or trailer.get("helper_stage", "staging") not in {
            "staging", "artifact_verified", "player_dispatching", "player_returned",
        }
        or type(trailer.get("player_exit_status", -1)) is not int
        or not -1 <= trailer.get("player_exit_status", -1) <= 255):
        raise ValueError
    metadata = {"helper_cleanup_verified": trailer["cleanup_verified"],
                "quasar_running": trailer["quasar_running"],
                "quasar_unchanged": trailer["quasar_unchanged"],
                **{k: trailer[k] for k in optional if k in trailer}}
    if not diagnostic:
        metadata["input_frames"] = max(0, (len(audio) - 44) // 2)
    failure = {"outcome": "unknown", "do_not_retry": True, "cleanup_verified": False,
               "physical_effects_verified": False, "player_stdout_lines": len(lines) - 1,
               **metadata}
    if len(lines) == 1:
        return dict(failure, error_code="player_output_missing")
    if len(lines) != 2:
        return dict(failure, error_code="player_output_invalid",
                    validation_code="player_line_count")
    validation = "player_json"
    try:
        result = _json(lines[0].encode())
        if diagnostic:
            validation = "diagnostic_shape"
            if (type(result) is not dict or set(result) != {
                "outcome", "diagnostic_only", "stage", "library_loaded", "configuration_parsed",
                "alsa_return_code", "pcm_opened", "audio_played",
            }):
                raise ValueError
            validation = "diagnostic_flags"
            if (result["diagnostic_only"] is not True or result["pcm_opened"] is not False
                or result["audio_played"] is not False
                or any(type(result[k]) is not bool for k in
                       ("library_loaded", "configuration_parsed"))):
                raise ValueError
            validation = "diagnostic_state"
            if (result["outcome"] not in {"ready", "blocked", "unknown"}
                or result["stage"] not in {
                    "request", "library", "symbols", "configuration", "cleanup", "ready",
                } or type(result["alsa_return_code"]) is not int
                or not -4095 <= result["alsa_return_code"] <= 2**31 - 1):
                raise ValueError
            validation = "diagnostic_correlation"
            if (result["configuration_parsed"] and not result["library_loaded"]
                or result["outcome"] == "ready" and (
                    result["stage"] != "ready" or not result["configuration_parsed"]
                    or result["alsa_return_code"] < 0 or trailer.get("player_exit_status") != 0
                    or not all(trailer[k] for k in
                               ("cleanup_verified", "quasar_running", "quasar_unchanged"))
                )):
                raise ValueError
            return {**result, **metadata, "cleanup_verified": trailer["cleanup_verified"],
                    "do_not_retry": True, "physical_effects_verified": False}
        validation = "playback_metadata"
        if (type(result) is not dict or set(result) != {
            "outcome", "playback_started", "playback_completed", "cleanup_verified",
            "frames_written", "sample_rate", "input_frames", "stage", "alsa_return_code",
        } or result["sample_rate"] != int.from_bytes(audio[24:28], "little")
            or result["input_frames"] != metadata["input_frames"]):
            raise ValueError
        if result["outcome"] == "complete":
            if (result["frames_written"] != (len(audio) - 44) // 2
                or trailer.get("player_exit_status", 0) != 0):
                raise ValueError
            result["outcome"] = "applied"
        if not all(trailer[k] for k in ("cleanup_verified", "quasar_running", "quasar_unchanged")):
            result.update(outcome="unknown", playback_completed=False, cleanup_verified=False)
        result.update(quasar_running=trailer["quasar_running"], do_not_retry=True,
                      physical_effects_verified=False, evidence_class="analytical",
                      tts_backend="local_pcm")
        return {**native_transport._pcm_result(result), **metadata}
    except Exception:
        return dict(failure, error_code="player_output_invalid", validation_code=validation)


def apply(controller: Controller, plan_id: str, expected: str, item: Candidate) -> dict[str, Any]:
    try:
        plan = controller.take_plan(plan_id, expected)
        with controller._execution_lock:
            station = controller.station(plan.station.id)
            _permitted(controller, station)
            ssh = _ssh(station)
            if (plan.action != "local_pcm.pilot" or station != plan.station
                or controller.clock() >= plan.expires
                or plan.request != {"candidate": item.digest, "ssh_trust": ssh.trust_sha256}):
                raise SafetyError("Local playback pilot preview changed or expired")
            try:
                return _result(_transfer(ssh, _command(station, item, plan.remote_sha256)),
                                item.audio, diagnostic=item.diagnostic)
            except Exception:
                return {"outcome": "unknown", "do_not_retry": True,
                        "error": "Local playback or cleanup unverified; never replay automatically"}
    except (SafetyError, ValueError):
        return {"outcome": "blocked", "error": "Local PCM pilot policy/preview/artifact changed"}
