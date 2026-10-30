"""Synthetic voice recording: fake loopback IPC and temporary private dirs, never a station."""

import hashlib
import json
import os
import shutil
import stat
import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_io_channel_pilot import SENTINEL, FakeIPC, audio, channel, device, field, station
from test_wake_pilot import TRAILER, build_client, local, report

from alice_mcp import io_channel_pilot as iop
from alice_mcp import voice_record as rec
from alice_mcp import wake_pilot
from alice_mcp.core import Controller, SafetyError

# Newline bytes inside the PCM must not confuse the host's framing.
CHUNKS = [[10, 2570] * 128, [-300, 300] * 128]
PCM = b"".join(struct.pack("<256h", *c) for c in CHUNKS)


@pytest.fixture(scope="module")
def client(tmp_path_factory: Any) -> bytes:
    return build_client(tmp_path_factory)


def setup(tmp_path: Path, client: bytes, port: int, monkeypatch: Any,
          ) -> tuple[Controller, rec.Take, Path]:
    if not shutil.which("dash") or not shutil.which("gawk"):
        pytest.skip("Synthetic shell checks need dash and gawk")
    env = device(tmp_path, port, "gawk")

    def runner(_: Any, command: str) -> str:
        return local(env, command, b"").decode()

    def transfer(_: Any, request: Any, *, limit: int) -> bytes:
        assert request.data == client and limit == 3 * 32000 + 8192
        return local(env, request.command, request.data)

    controller = Controller(None, writes=True, runner=runner)
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(rec, "_ssh", lambda item: item)
    monkeypatch.setattr(rec, "_transfer_bytes", transfer)
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(client).hexdigest())
    return controller, rec.Take("owner", "s1", "komp", 1, client, port=port), tmp_path / "train"


def test_one_take_exports_pcm_only_to_a_private_wav_with_manifest(
    tmp_path: Path, client: bytes, monkeypatch: Any,
) -> None:
    server = FakeIPC([audio(channel(6, b"vqe_0", c, duration=16), full_message=True)
                      for c in CHUNKS])
    controller, take, base = setup(tmp_path, client, server.port, monkeypatch)
    preview = rec.preview(controller, "desk", take, base)
    assert preview["cue"] == "скажи: компьютер" and preview["audio_exported"] is True
    assert "exported" in preview["impact"]
    result = rec.apply(controller, preview["plan_id"], preview["expected_sha256"], take, base)
    assert server.close() == [field(159, field(3, b"")), field(159, field(6, b""))]
    assert result["outcome"] == "complete", result
    assert result["audio_exported"] is True and result["clip_saved"] is True
    assert result["cleanup_verified"] and result["quasar_unchanged"] and result["do_not_retry"]
    folder = base / "owner"
    assert stat.S_IMODE(base.stat().st_mode) == stat.S_IMODE(folder.stat().st_mode) == 0o700
    clip = folder / result["clip"]["file"]
    assert stat.S_IMODE(clip.stat().st_mode) == 0o600
    with wave.open(str(clip), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.readframes(wav.getnframes()) == PCM
    entry = json.loads((folder / "manifest.jsonl").read_text())
    assert stat.S_IMODE((folder / "manifest.jsonl").stat().st_mode) == 0o600
    assert entry == result["clip"]
    assert entry["label"] == "positive" and entry["word"] == "komp"
    assert entry["speaker"] == "owner" and entry["session"] == "s1"
    assert entry["sha256"] == hashlib.sha256(clip.read_bytes()).hexdigest()
    assert entry["samples"] == len(PCM) // 2 and entry["timestamp"].endswith("Z")
    assert not any((tmp_path / "dev/data/opt").iterdir())
    with pytest.raises(SafetyError):  # One use.
        controller.take_plan(preview["plan_id"], preview["expected_sha256"])


def test_negative_take_cue_and_bounds() -> None:
    take = rec.Take("wife", "s1", "", 10, b"c")
    assert take.label == "negative" and take.cue == "говори что угодно, без слова"


@pytest.mark.parametrize("take", [
    rec.Take("owner", "s1", "komp", 4, b"c"), rec.Take("owner", "s1", "", 11, b"c"),
    rec.Take("owner", "s1", "komp", 0, b"c"), rec.Take("owner", "s1", "alisa", 2, b"c"),
    rec.Take("Owner", "s1", "komp", 2, b"c"), rec.Take("../x", "s1", "komp", 2, b"c"),
    rec.Take("owner", "s/1", "komp", 2, b"c"), rec.Take("owner", "s1", "komp", 2, b"x"),
    rec.Take("owner", "s1", "komp", True, b"c"),  # type: ignore[arg-type]
    rec.Take("owner", "s1", "komp", 2, b"c", port=80),
])
def test_take_inputs_and_pin_are_validated(monkeypatch: Any, take: rec.Take) -> None:
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"c").hexdigest())
    with pytest.raises(SafetyError):
        take.validate()


def gated(tmp_path: Path, monkeypatch: Any) -> Controller:
    controller = Controller(None, writes=True, runner=lambda *_: pytest.fail("no SSH"))
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(rec, "_ssh", lambda item: item)
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"c").hexdigest())
    return controller


def test_clips_never_go_into_the_checkout_and_are_always_git_ignored(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    take = rec.Take("owner", "s1", "komp", 2, b"c")
    with pytest.raises(SafetyError, match="checkout"):
        rec.preview(gated(tmp_path, monkeypatch), "desk", take, Path(__file__).parent / "voice")
    assert not (Path(__file__).parent / "voice").exists()
    (tmp_path / "home/.git").mkdir(parents=True)  # A dotfiles home is itself a repository.
    folder = rec._folder(tmp_path / "home/clips", "owner")
    assert (tmp_path / "home/clips/.gitignore").read_text() == "*\n"
    assert stat.S_IMODE((tmp_path / "home/clips/.gitignore").stat().st_mode) == 0o600
    assert folder == tmp_path / "home/clips/owner"
    (tmp_path / "home/clips/.gitignore").write_text("!*.wav\n")
    with pytest.raises(SafetyError, match="gitignore"):
        rec._folder(tmp_path / "home/clips", "owner")
    (tmp_path / "shared").mkdir(mode=0o755)
    os.chmod(tmp_path / "shared", 0o755)
    with pytest.raises(SafetyError, match="private"):
        rec._folder(tmp_path / "shared", "owner")


def test_a_session_holds_at_most_40_takes(tmp_path: Path, monkeypatch: Any) -> None:
    controller, take = gated(tmp_path, monkeypatch), rec.Take("owner", "s1", "komp", 2, b"c")
    base = tmp_path / "train"
    rec._folder(base, "owner")
    line = json.dumps({"session": "s1"}) + "\n"
    (base / "owner/manifest.jsonl").write_text(line * 40)
    with pytest.raises(SafetyError, match="40"):
        rec.preview(controller, "desk", take, base)
    (base / "owner/manifest.jsonl").write_text(line + "[1]\n")
    with pytest.raises(SafetyError, match="manifest"):
        rec.preview(controller, "desk", take, base)


def take_output(pcm: bytes, **changes: Any) -> bytes:
    value = report(bytes=len(pcm), channels=[dict(report()["channels"][0], bytes=len(pcm))])
    trailer = dict(TRAILER, detector_exit=-1, detector_vmhwm_kib=-1, detector_cpu_ticks=-1)
    value.update(changes.pop("report", {}))
    trailer.update(changes)
    return pcm + b"\n" + json.dumps(value).encode() + b"\n" + json.dumps(trailer).encode() + b"\n"


@pytest.mark.parametrize(("raw", "outcome", "code", "kept"), [
    (take_output(PCM), "complete", "none", True),
    (take_output(PCM + b"\n\n"), "complete", "none", True),  # Newlines in PCM are data.
    (take_output(PCM, quasar_unchanged=False), "unknown",
     "quasar_changed_or_cleanup_unverified", False),
    (take_output(PCM)[:1] + take_output(PCM)[3:], "unknown", "pcm_count_mismatch", False),
    (take_output(PCM, report={"outcome": "unknown", "error_code": "output_error"}), "unknown",
     "output_error", False),
    (take_output(PCM, report={"other": [{"x": SENTINEL.decode()}]}), "unknown",
     "helper_output_invalid", False),
    (take_output(PCM)[:-1], "unknown", "helper_output_invalid", False),
    (take_output(PCM, extra=1), "unknown", "helper_output_invalid", False),
    (json.dumps(dict(TRAILER, detector_exit=-1)).encode() + b"\n", "unknown",
     "helper_output_missing", False),
    (b'{"outcome":"blocked","error_code":"temporary_path_busy"}\n', "blocked",
     "temporary_path_busy", False),
    (SENTINEL + b"\n", "unknown", "helper_output_invalid", False),
])
def test_take_parser_keeps_pcm_only_for_a_verified_complete_take(
    raw: bytes, outcome: str, code: str, kept: bool,
) -> None:
    result, pcm = rec.parse_take(raw)
    assert result["outcome"] == outcome and result["error_code"] == code, result
    assert result["do_not_retry"] is True and bool(pcm) is kept
    assert SENTINEL.decode() not in json.dumps(result)
    if kept:
        assert pcm in (PCM, PCM + b"\n\n")


def test_delete_removes_only_the_speakers_clips_and_manifest(tmp_path: Path) -> None:
    base = tmp_path / "train"
    folder = rec._folder(base, "wife")
    for name in ("20261029T101010123456Z-positive-komp.wav", "manifest.jsonl"):
        (folder / name).write_bytes(b"x")
    assert rec.delete_speaker("wife", base) == {
        "outcome": "complete", "speaker": "wife", "deleted_clips": 1,
        "manifest_deleted": True, "remaining_files": 0}
    assert not folder.exists() and base.exists()
    folder = rec._folder(base, "wife")
    (folder / "notes.txt").write_bytes(b"x")
    assert rec.delete_speaker("wife", base)["outcome"] == "unknown"
    assert (folder / "notes.txt").exists()  # Foreign files are reported, never removed.
    assert rec.delete_speaker("nobody", base)["deleted_clips"] == 0
    with pytest.raises(SafetyError):
        rec.delete_speaker("../wife", base)


def test_operator_session_needs_no_typed_word_and_prints_the_notice(
    tmp_path: Path, monkeypatch: Any, capsys: Any,
) -> None:
    # Owner decision: launching the command is the consent; only Enter per take for timing.
    calls: list[str] = []
    monkeypatch.setattr(rec, "preview", lambda *a: calls.append("preview") or {
        "plan_id": "p", "expected_sha256": "e" * 64, "cue": "скажи: компьютер"})
    monkeypatch.setattr(rec, "apply", lambda *a: calls.append("apply") or {
        "outcome": "complete" if len(calls) < 4 else "unknown"})
    takes = [rec.Take("owner", "s1", "komp", 3, b"c")] * 3
    prompts: list[str] = []
    result = rec.operator_record(SimpleNamespace(), "desk", takes, tmp_path,
                                 ask=lambda p: prompts.append(p) or "")
    assert "exported to this host" in capsys.readouterr().out
    assert calls == ["preview", "apply", "preview", "apply"]  # Stops after an unknown take.
    assert result["outcome"] == "unknown" and len(result["takes"]) == 2
    assert len(prompts) == 2 and "скажи: компьютер" in prompts[-1]


def test_uncertain_transport_saves_nothing_and_never_retries(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    attempts: list[int] = []

    def transfer(*_: Any, **__: Any) -> bytes:
        attempts.append(1)
        raise SafetyError("Local PCM transfer uncertain; do not retry")

    controller = Controller(None, writes=True, runner=lambda *_: (
        '{"outcome":"ready","state_sha256":"' + "c" * 64 + '"}\n'))
    monkeypatch.setattr(controller, "station", lambda _: station(tmp_path))
    monkeypatch.setattr(rec, "_ssh", lambda item: item)
    monkeypatch.setattr(rec, "_transfer_bytes", transfer)
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"c").hexdigest())
    take, base = rec.Take("owner", "s1", "", 10, b"c"), tmp_path / "train"
    preview = rec.preview(controller, "desk", take, base)
    result = rec.apply(controller, preview["plan_id"], preview["expected_sha256"], take, base)
    assert result["outcome"] == "unknown" and result["error_code"] == "transport_uncertain"
    assert result["clip_saved"] is False and attempts == [1]
    assert list((base / "owner").iterdir()) == []
    again = rec.apply(controller, preview["plan_id"], preview["expected_sha256"], take, base)
    assert again["outcome"] == "blocked" and attempts == [1]


def test_record_purpose_is_bound_into_the_station_call(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(iop, "HELPER_SHA256", hashlib.sha256(b"c").hexdigest())
    request = rec._command(station(tmp_path), rec.Take("owner", "s1", "", 10, b"c"), "apply",
                           "e" * 64)
    assert request.command.endswith(" record") and request.data == b"c"
    assert wake_pilot.SCRIPT.count('[ "$purpose" = record ]') == 1
