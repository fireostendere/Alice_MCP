"""Owner-run voice recording for wake-word training (docs/IO_CHANNEL.md); not an MCP tool.

The one place where room audio leaves the station, on purpose: each prompted take runs
the pinned IO-channel client in --stream mode for one bounded window (positives <= 3 s,
negatives <= 10 s) and returns Quasar's vqe_0 PCM to this host. Clips are written only to
a private directory outside every Git checkout (default
~/.local/share/alice-mcp/voice-train/<speaker>/, 0700; WAV and manifest 0600). At most 40
takes per session. Quasar is not stopped or reconfigured, the temporary helper is
removed and verified, and uncertain takes are never retried or saved.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import stat
import sys
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alice_mcp import io_channel_pilot as iop
from alice_mcp import wake_pilot as wp
from alice_mcp.core import Controller, SafetyError, Station
from alice_mcp.pcm_pilot import _Transfer, _transfer_bytes
from alice_mcp.vqe_experiment import _ssh

ACTION = "voice.record"
DEFAULT_DIR = Path.home() / ".local" / "share" / "alice-mcp" / "voice-train"
SOURCE_TREE = Path(__file__).resolve().parents[2]  # The checkout (or site-packages' parent).
MAX_TAKES = 40
# W5's model names (docs/MICRO_WAKE_WORD.md) and the cue shown before each take.
CUES = {"komp": "скажи: компьютер", "okey_dom": "скажи: окей дом", "marusya": "скажи: маруся"}
NEGATIVE_CUE = "говори что угодно, без слова"
ALIAS = re.compile("[a-z][a-z0-9_]{0,31}")
SESSION = re.compile("[a-z0-9_-]{1,32}")
CLIP = re.compile(r"\d{8}T\d{12}Z-(positive|negative)-[a-z_]+\.wav")
MANIFEST = "manifest.jsonl"


@dataclass(frozen=True)
class Take:
    speaker: str  # Private alias such as "owner"; never a name or identifier.
    session: str
    word: str  # A CUES key for a positive, "" for a negative.
    seconds: int
    client: bytes = field(repr=False)
    port: int = 9850
    nonce: str = field(default_factory=lambda: secrets.token_hex(16))

    @property
    def label(self) -> str:
        return "positive" if self.word else "negative"

    @property
    def cue(self) -> str:
        return CUES[self.word] if self.word else NEGATIVE_CUE

    def validate(self) -> None:
        if (type(self.speaker) is not str or not ALIAS.fullmatch(self.speaker)
            or type(self.session) is not str or not SESSION.fullmatch(self.session)
            or (self.word not in CUES and self.word != "") or type(self.seconds) is not int
            or not 1 <= self.seconds <= (3 if self.word else 10)
            or type(self.port) is not int or not 1024 <= self.port <= 65535
            or not re.fullmatch("[a-f0-9]{32}", self.nonce) or type(self.client) is not bytes
            or hashlib.sha256(self.client).hexdigest() != iop.HELPER_SHA256):
            raise SafetyError("Voice take input or reviewed helper invalid; no transfer")

    @property
    def digest(self) -> str:
        return hashlib.sha256(repr((
            wp.SCRIPT, self.speaker, self.session, self.word, self.seconds, self.port,
            self.nonce, hashlib.sha256(self.client).hexdigest(),
        )).encode()).hexdigest()


def _private(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077):
        raise SafetyError("Voice clip directory must be a private (0700) directory you own")


def _folder(directory: Path, speaker: str) -> Path:
    """The speaker's private clip folder: never in this checkout, always Git-ignored."""
    if not ALIAS.fullmatch(speaker):
        raise SafetyError("Speaker alias invalid")
    base = directory.expanduser().absolute()
    resolved = base.resolve()
    if resolved == SOURCE_TREE or SOURCE_TREE in resolved.parents:
        raise SafetyError("Voice clips must stay outside the alice-mcp checkout")
    base.parent.mkdir(parents=True, exist_ok=True)
    _private(base)
    # An enclosing repository (a dotfiles home, say) must never pick the clips up.
    ignore = base / ".gitignore"
    try:
        with os.fdopen(os.open(ignore, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                               0o600), "w") as handle:
            handle.write("*\n")
    except FileExistsError:
        if ignore.is_symlink() or ignore.read_text() != "*\n":
            raise SafetyError("Voice clip directory has an unexpected .gitignore") from None
    _private(base / speaker)
    return base / speaker


def _count(folder: Path, session: str) -> int:
    path = folder / MANIFEST
    if not path.exists():
        return 0
    with path.open("rb") as source:
        data = source.read(4 * 1024 * 1024 + 1)
    try:
        if len(data) > 4 * 1024 * 1024:
            raise ValueError
        return sum(json.loads(line).get("session") == session for line in data.splitlines())
    except (ValueError, AttributeError):
        raise SafetyError("Voice manifest unreadable or over bounds; inspect it") from None


def _command(station: Station, take: Take, mode: str, expected: str = "") -> _Transfer:
    take.validate()
    return wp.shell(station, mode, expected, "record", nonce=take.nonce, port=take.port,
                    seconds=take.seconds, first=take.client)


def _request(take: Take, ssh: Station, folder: Path) -> dict[str, str]:
    return {"take": take.digest, "ssh_trust": ssh.trust_sha256, "folder": str(folder)}


def preview(controller: Controller, station_id: str, take: Take,
            directory: Path = DEFAULT_DIR) -> dict[str, Any]:
    station = controller.station(station_id)
    iop._permitted(controller, station)
    take.validate()
    folder = _folder(directory, take.speaker)
    if _count(folder, take.session) >= MAX_TAKES:
        raise SafetyError(f"Session already holds {MAX_TAKES} takes; start a new session")
    ssh = _ssh(station)
    state = iop.ready_state(controller.runner(ssh, _command(station, take, "inspect").command))
    request = _request(take, ssh, folder)
    digest = hashlib.sha256(repr((station, request, state)).encode()).hexdigest()
    plan_id = controller.store_plan(station, ACTION, digest, request=request,
                                    remote_sha256=state)
    return {"plan_id": plan_id, "expected_sha256": digest, "expires_in_seconds": 300,
            "cue": take.cue, "label": take.label, "seconds": take.seconds,
            "audio_exported": True,
            "impact": f"Room audio (Quasar vqe_0, port {take.port}) for {take.seconds} s is "
                      "exported to this host and saved as a private WAV for wake-word "
                      "training; temporary pinned helper, no Quasar stop or configuration "
                      "change"}


def parse_take(raw: bytes) -> tuple[dict[str, Any], bytes]:
    """PCM, a newline, the client's report and the trailer. PCM is kept only when complete."""
    base: dict[str, Any] = {"outcome": "unknown", "do_not_retry": True, "audio_exported": False}
    try:
        if raw.count(b"\n") == 1 and raw.endswith(b"\n"):
            value = wp._json(raw[:-1].decode())
            if (type(value) is dict and set(value) == {"outcome", "error_code"}
                and value["outcome"] == "blocked"
                and str(value["error_code"]) in iop.PROFILE_ERRORS):
                return dict(base, outcome="blocked", error_code=value["error_code"]), b""
            final = wp.trailer(value)
            return dict(base, error_code="helper_output_missing",
                        **{k: final[k] for k in wp.FLAGS}), b""
        pcm, line, last, tail = raw.rsplit(b"\n", 3)
        if tail:
            raise ValueError
        final = wp.trailer(wp._json(last.decode()))
    except (ValueError, TypeError):
        return dict(base, error_code="helper_output_invalid", audio_exported=bool(raw)), b""
    flags = {k: final[k] for k in wp.FLAGS}
    base.update(audio_exported=bool(pcm))
    try:
        report = wp.stream_report(wp._json(line.decode()))
    except (ValueError, TypeError, KeyError):
        return dict(base, error_code="helper_output_invalid", **flags), b""
    channel = report["channels"][0] if report["channels"] else None
    if not all(flags.values()):
        code = "quasar_changed_or_cleanup_unverified"
    elif report["outcome"] != "complete":
        code = report["error_code"]
    elif channel is None or len(pcm) != channel["bytes"] or len(pcm) % 2:
        code = "pcm_count_mismatch"
    else:
        code = "none"
    result = dict(base, outcome="complete" if code == "none" else "unknown", error_code=code,
                  evidence_class="analytical", io=wp.io_summary(report), **flags)
    return result, pcm if code == "none" else b""


def _save(folder: Path, take: Take, pcm: bytes, level: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    name = f"{now:%Y%m%dT%H%M%S%f}Z-{take.label}-{take.word or 'none'}.wav"
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    data = buffer.getvalue()
    entry = {"file": name, "label": take.label, "word": take.word or None,
             "speaker": take.speaker, "session": take.session,
             "timestamp": now.isoformat().replace("+00:00", "Z"), "seconds": take.seconds,
             "samples": len(pcm) // 2, "sha256": hashlib.sha256(data).hexdigest(),
             "rms_dbfs": level["rms_dbfs"], "peak_dbfs": level["peak_dbfs"]}
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    clip = os.open(folder / name, flags | os.O_EXCL, 0o600)
    try:
        with os.fdopen(clip, "wb") as handle:
            handle.write(data)
        with os.fdopen(os.open(folder / MANIFEST, flags | os.O_APPEND, 0o600), "a",
                       encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        (folder / name).unlink(missing_ok=True)
        raise
    return entry


def apply(controller: Controller, plan_id: str, expected: str, take: Take,
          directory: Path = DEFAULT_DIR) -> dict[str, Any]:
    try:
        plan = controller.take_plan(plan_id, expected)
        with controller._execution_lock:
            station = controller.station(plan.station.id)
            iop._permitted(controller, station)
            ssh = _ssh(station)
            folder = _folder(directory, take.speaker)
            if (plan.action != ACTION or station != plan.station
                or controller.clock() >= plan.expires
                or plan.request != _request(take, ssh, folder)
                or _count(folder, take.session) >= MAX_TAKES):
                raise SafetyError("Voice take preview changed or expired")
            request = _command(station, take, "apply", plan.remote_sha256)
            try:
                # Room for the window plus 2 s of slack; anything larger is uncertain.
                raw = _transfer_bytes(ssh, request, limit=(take.seconds + 2) * 32000 + 8192)
            except Exception:
                return {"outcome": "unknown", "error_code": "transport_uncertain",
                        "do_not_retry": True, "audio_exported": True, "clip_saved": False}
            result, pcm = parse_take(raw)
            if pcm:
                try:
                    result["clip"] = _save(folder, take, pcm, result["io"]["input"])
                except OSError:
                    result.update(outcome="unknown", error_code="clip_write_failed")
            result["clip_saved"] = "clip" in result
            return result
    except (SafetyError, ValueError):
        return {"outcome": "blocked", "error_code": "preview_changed_or_expired",
                "do_not_retry": True, "audio_exported": False, "clip_saved": False}


def delete_speaker(speaker: str, directory: Path = DEFAULT_DIR) -> dict[str, Any]:
    """Remove one speaker's clips and manifest; any other file is reported, never removed."""
    if not ALIAS.fullmatch(speaker):
        raise SafetyError("Speaker alias invalid")
    folder = directory.expanduser().absolute() / speaker
    clips, manifest, remaining = 0, False, 0
    try:
        info = folder.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise SafetyError("Speaker folder is not a directory you own")
        for path in folder.iterdir():
            if CLIP.fullmatch(path.name):
                path.unlink()
                clips += 1
            elif path.name == MANIFEST:
                path.unlink()
                manifest = True
            else:
                remaining += 1
        if not remaining:
            folder.rmdir()
    return {"outcome": "unknown" if remaining else "complete", "speaker": speaker,
            "deleted_clips": clips, "manifest_deleted": manifest, "remaining_files": remaining}


def operator_record(controller: Controller, station_id: str, takes: list[Take],
                    directory: Path = DEFAULT_DIR, *, ask: Any = input) -> dict[str, Any]:
    """Human-run session: per take a fresh preview, the cue, Enter for timing and one apply.

    Owner decision (AGENTS.md): launching the command is the consent; no typed word.
    """
    print(f"{len(takes)} takes: Mini 1 room audio (vqe_0) is exported to this host and saved "
          "as private WAV files for wake-word training. Everyone recorded must agree. "
          "Ctrl+C stops.", flush=True)
    results: list[dict[str, Any]] = []
    for take in takes:
        plan = preview(controller, station_id, take, directory)
        try:
            ask(f"Take {len(results) + 1}/{len(takes)}: {plan['cue']} "
                f"(press Enter, then speak within {take.seconds} s) ")
        except (EOFError, KeyboardInterrupt):
            try:
                controller.take_plan(plan["plan_id"], plan["expected_sha256"])  # Consume it.
            except SafetyError:
                pass
            break
        results.append(apply(controller, plan["plan_id"], plan["expected_sha256"], take,
                             directory))
        if results[-1]["outcome"] != "complete":
            break  # Never retry; inspect before a new session.
    done = len(results) == len(takes) and all(r["outcome"] == "complete" for r in results)
    return {"outcome": "complete" if done else "unknown", "do_not_retry": True,
            "audio_exported": bool(results), "takes": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-run voice recording for wake training")
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="Prompted takes from Mini 1 into a private dir")
    record.add_argument("--station", required=True, help="Private station alias")
    record.add_argument("--client", required=True, type=Path,
                        help="Already-built IO-channel client matching its pinned SHA-256")
    record.add_argument("--speaker", required=True, help="Private alias, e.g. owner")
    record.add_argument("--session", required=True, help="Session label, e.g. 20261029a")
    record.add_argument("--word", choices=sorted(CUES), default="komp")
    record.add_argument("--positives", type=int, choices=range(0, MAX_TAKES + 1), default=20)
    record.add_argument("--negatives", type=int, choices=range(0, MAX_TAKES + 1), default=10)
    delete = commands.add_parser("delete", help="Remove one speaker's clips and manifest")
    delete.add_argument("--speaker", required=True)
    for command in (record, delete):
        command.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)
    try:
        if args.command == "delete":
            result = delete_speaker(args.speaker, args.dir)
        else:
            if not sys.stdin.isatty():
                parser.error("Use a terminal, or call preview()/apply() from Python")
            if not 0 < args.positives + args.negatives <= MAX_TAKES:
                parser.error(f"A session holds 1 to {MAX_TAKES} takes")
            inventory = os.environ.get("ALICE_MCP_INVENTORY")
            if not inventory:
                parser.error("Set ALICE_MCP_INVENTORY privately; never paste its contents")
            with args.client.open("rb") as source:
                client = source.read(64 * 1024 + 1)
            takes = ([Take(args.speaker, args.session, args.word, 3, client)
                      for _ in range(args.positives)]
                     + [Take(args.speaker, args.session, "", 10, client)
                        for _ in range(args.negatives)])
            controller = Controller(Path(inventory),
                                    writes=os.environ.get("ALICE_MCP_ALLOW_WRITES") == "1")
            result = operator_record(controller, args.station, takes, args.dir)
    except SafetyError as error:
        result = {"outcome": "blocked", "error": str(error)}
    except OSError:
        result = {"outcome": "blocked", "error_code": "operator_inputs_unavailable"}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if result["outcome"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
