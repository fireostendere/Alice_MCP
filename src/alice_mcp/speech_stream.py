"""speech.say for stations without the resident agent.

The host synthesizes (private `local_tts`), then streams one canonical WAV over pinned
SSH into the firmware's own GStreamer player: the `audio.play` pipeline with a pipe
instead of a file. Nothing is installed or written on the station, the text never
leaves the host, and Quasar keeps running. Needs only stock shell tools, no Python.
"""

from __future__ import annotations

import hashlib
import shlex
from typing import Any

from alice_mcp import device_runtime as rt
from alice_mcp import local_tts
from alice_mcp.core import Controller, SafetyError, SSHStream, Station

ERRORS = frozenset({"not_owner", "runtime_tools_missing", "firmware_mismatch",
                    "quasar_not_running", "preview_state_changed"})
# shortcut: the 26 s watchdog and run_ssh's fixed 30 s leave little room around a 20 s
# utterance; give this path its own SSH timeout if long phrases end as `unknown`.
SCRIPT = r'''OWNER_UID=0
Q=/system/vendor/quasar
exec 2>/dev/null
LC_ALL=C; export LC_ALL
mode=$1; firmware=$2; binding=$3; expected=$4
no() { printf '{"outcome":"blocked","error_code":"%s"}\n' "$1"; exit 0; }
[ "$(id -u)" = "$OWNER_UID" ] || no not_owner
for c in awk kill pidof sha256sum sleep; do
    command -v "$c" >/dev/null 2>&1 || no runtime_tools_missing
done
[ -f "$Q/maind" ] && [ ! -L "$Q/maind" ] || no firmware_mismatch
actual=$(sha256sum "$Q/maind" | awk '{ print $1 }')
[ "$actual" = "$firmware" ] || no firmware_mismatch
pidof maind >/dev/null || no quasar_not_running
boot=$(sha256sum /proc/sys/kernel/random/boot_id | awk '{ print $1 }')
state=$(printf '%s\n' "$actual" "$boot" "$binding" | sha256sum | awk '{ print $1 }')
if [ "$mode" = inspect ]; then
    printf '{"outcome":"ready","state_sha256":"%s"}\n' "$state"
    exit 0
fi
[ "$mode" = apply ] && [ "$expected" = "$state" ] || no preview_state_changed
cd "$Q" || no firmware_mismatch
exec 3<&0  # A background job's own stdin is /dev/null: hand it the SSH stream explicitly.
LD_LIBRARY_PATH=$Q:$Q/libs ./maind --gst-launch \
    "fdsrc fd=0 ! decodebin ! audioconvert ! audioresample ! alsasink device=default" \
    <&3 3<&- >/dev/null &
player=$!
exec 3<&-
( sleep 26; kill -9 "$player" ) >/dev/null &
guard=$!
wait "$player"; code=$?
kill "$guard"
running=false; if pidof maind >/dev/null; then running=true; fi
printf '{"outcome":"played","player_exit":%s,"quasar_running":%s}\n' "$code" "$running"
'''


def command(request: dict[str, Any], audio: bytes = b"") -> SSHStream:
    """Only validated internal requests from the existing approval orchestrator."""
    mode, expected = request["mode"], request.get("expected_sha256", "")
    policy = rt.validate_policy(request["policy"], request["model"])
    params = rt.validate_parameters("speech.say", request["params"], policy, request["model"])
    if (mode not in {"inspect", "apply"} or request["action"] != "speech.say"
        or request["schema"] != rt.SCHEMA or "local_tts" not in policy
        or not rt.allows(policy, "speech.say")
        or not rt._match(rt.HEX64, policy.get("firmware_sha256"))
        or (mode == "apply") != bool(audio)
        or (mode == "apply") != rt._match(rt.HEX64, expected)):
        raise SafetyError("Invalid speech stream request")
    # The preview is bound to the policy and the text; the text itself stays on the host.
    binding = hashlib.sha256(rt._encode(policy) + params["text"].encode()).hexdigest()
    args = [mode, policy["firmware_sha256"], binding, expected]
    return SSHStream("sh -c " + shlex.quote(SCRIPT) + " sh "
                     + " ".join(shlex.quote(a) for a in args), audio)


def _result(raw: str) -> dict[str, Any]:
    """Flags only; anything else from the station is never success."""
    try:
        if len(raw) > 1024:
            raise ValueError
        result = rt._json(raw.encode("utf-8"))
        if (rt._keys(result, {"outcome", "error_code"}) and result["outcome"] == "blocked"
                and result["error_code"] in ERRORS):
            return {"outcome": "blocked", "error_code": result["error_code"]}
        if (rt._keys(result, {"outcome", "state_sha256"}) and result["outcome"] == "ready"
                and rt._match(rt.HEX64, result["state_sha256"])):
            return dict(result)
        if (not rt._keys(result, {"outcome", "player_exit", "quasar_running"})
            or result["outcome"] != "played" or type(result["player_exit"]) is not int
            or type(result["quasar_running"]) is not bool):
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise SafetyError("Speech stream result invalid; inspect locally") from None
    done = result["player_exit"] == 0
    return {"outcome": "applied" if done else "unknown", "playback_completed": done,
            "quasar_running": result["quasar_running"], "tts_backend": "host_stream"}


def invoke(controller: Controller, station: Station, request: dict[str, Any]) -> dict[str, Any]:
    if request["mode"] == "inspect":
        result = _result(controller.runner(station, command(request)))
        if result["outcome"] != "ready":
            raise SafetyError("Speech preview blocked: " + result.get("error_code", "state"))
        return result
    try:
        audio = local_tts.speak(request["params"]["text"], station.controls["local_tts"])
        local_tts.wav_parameters(audio)
    except local_tts.SpeechCommandError as error:
        return {"outcome": "blocked", "error": f"{error}; nothing was sent to the station"}
    except Exception:
        return {"outcome": "blocked",
                "error": "Local synthesis unavailable; nothing was sent to the station"}
    result = _result(controller.runner(station, command(request, audio)))
    if result["outcome"] == "ready":
        raise SafetyError("Station did not acknowledge playback")
    return result
