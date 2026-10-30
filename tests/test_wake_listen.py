"""Synthetic PC wake listener: scripted Vosk and mww stand-ins, no station, model or audio."""

import hashlib
import json
import os
import wave
from pathlib import Path
from typing import Any

import pytest

from alice_mcp import wake_listen as wl
from alice_mcp.core import SafetyError
from alice_mcp.wake_pipeline import McpInbox

RATE = 16000


class Vosk:
    """Scripted endpointing: each (start, end, text[, words]) is returned 0.5 s after its end.

    Without words the listener spreads the text evenly; "" is Vosk's silence endpoint."""

    def __init__(self, script: list[tuple[Any, ...]]) -> None:
        self.script, self.samples = list(script), 0
        self.words: list[tuple[str, float, float]] | None = None

    def _pop(self) -> dict[str, Any]:
        start, end, text, *words = self.script.pop(0)
        self.words = words[0] if words else None
        return {"text": text, "start": start, "end": end}

    def accept(self, pcm: bytes) -> dict[str, Any] | None:
        self.samples += len(pcm) // 2
        if self.script and self.samples / RATE >= self.script[0][1] + 0.5:
            return self._pop()
        return None

    def flush(self) -> dict[str, Any] | None:
        if self.script:
            return self._pop()
        return {"text": "", "start": 0.0, "end": 0.0}


def said(*words: tuple[str, float, float]) -> tuple[Any, ...]:
    """One Vosk utterance with word times, as VoskRecognizer reports them."""
    return (words[0][1], words[-1][2], " ".join(w for w, _, _ in words), list(words))


class Mww:
    """Scripted detector: komp q values per 30 ms and wake events, released as audio arrives."""

    def __init__(self, q: dict[int, int] | None = None, wakes: tuple[int, ...] = ()) -> None:
        self.q, self.wakes, self.audio_ms, self.sent = q or {}, wakes, 0, 0

    def feed(self, pcm: bytes) -> None:
        self.audio_ms += len(pcm) // 32

    def poll(self) -> list[tuple[Any, ...]]:
        out: list[tuple[Any, ...]] = []
        for t in range(self.sent + 30, self.audio_ms + 1, 30):
            out.append(("p", "komp", t, self.q.get(t, 2)))
            if t in self.wakes:
                out.append(("wake", "komp", t))
            self.sent = t
        return out

    def alive(self) -> bool:
        return True

    def close(self) -> list[tuple[Any, ...]]:
        return self.poll()


def listen(script: list[tuple[Any, ...]], mode: str = "either", seconds: float = 12,
           q: dict[int, int] | None = None, wakes: tuple[int, ...] = (),
           until: float | None = None,
           ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], McpInbox]:
    """Feeds `seconds` of audio; `until` stops early without finish(), as a live stream."""
    emitted: list[dict[str, Any]] = []
    logged: list[dict[str, Any]] = []
    inbox = McpInbox("analytical")
    listener = wl.Listener("компьютер", mode, Vosk(script), lambda: Mww(q, wakes),
                           emit=emitted.append, log=logged.append, inbox=inbox)
    pcm = b"\0\1" * int((until or seconds) * RATE)
    for i in range(0, len(pcm), 3200 * 7):  # Odd-sized feeds, like network reads.
        listener.feed(pcm[i:i + 3200 * 7])
    if until is None:
        listener.finish()
    return emitted, logged, inbox


NAME = [(1.0, 2.6, "компьютер включи свет")]
HIGH = {t: 255 for t in range(1500, 1800, 30)}


def test_early_wake_fires_on_detector_hit_before_utterance_endpoint() -> None:
    early: list[Any] = []
    final: list[Any] = []
    listener = wl.Listener("компьютер", "either", Vosk(NAME),
                           lambda: Mww(wakes=(1650,)), emit=final.append,
                           on_wake=lambda: early.append(listener.samples))
    for _ in range(20):
        listener.feed(b"\0\1" * 1600)
    assert len(early) == 1 and not final
    for _ in range(15):
        listener.feed(b"\0\1" * 1600)
    listener.finish()
    assert len(early) == 1


def test_partial_prefix_wakes_without_mww_but_embedded_word_does_not() -> None:
    class Partial(Vosk):
        text = "мой компьютер"

        def partial(self) -> str:
            return self.text

    recognizer, early = Partial([]), []
    listener = wl.Listener("компьютер", "vosk", recognizer, None,
                           emit=lambda _: None, on_wake=lambda: early.append(True))
    listener.feed(b"\0\1" * 1600)
    assert not early
    recognizer.text = "компьютер который"
    listener.feed(b"\0\1" * 1600)
    listener.feed(b"\0\1" * 1600)
    assert early == [True]


def test_both_sources_fire_once_with_the_transcript() -> None:
    emitted, logged, inbox = listen(NAME, q=HIGH, wakes=(1650,))
    assert emitted == [{"event": "wake", "word": "компьютер", "sources": ["mww", "vosk"],
                        "command": "включи свет", "mww_max_q": 255, "t": 1.65,
                        "trusted": False}]
    assert [item["text"] for _, item in inbox.items.values()] == ["включи свет"]
    assert [e["fired"] for e in logged] == [True]
    assert "компьютер" not in json.dumps(logged, ensure_ascii=False)  # Scores only, no text.


@pytest.mark.parametrize(("mode", "fires"), [
    ("either", True), ("vosk", True), ("mww", False), ("both", False)])
def test_vosk_only_hit_follows_the_mode(mode: str, fires: bool) -> None:
    emitted, logged, _ = listen(NAME, mode=mode)
    assert bool(emitted) is fires and logged[0]["vosk_hit"] and not logged[0]["mww_hit"]
    if fires:
        assert emitted[0]["sources"] == ["vosk"] and emitted[0]["mww_max_q"] == 2


@pytest.mark.parametrize(("mode", "fires"), [
    ("either", True), ("mww", True), ("vosk", False), ("both", False)])
def test_mww_only_hit_keeps_the_utterance_transcript(mode: str, fires: bool) -> None:
    emitted, _, _ = listen([(1.0, 2.6, "компьютерный стол")], mode=mode, q=HIGH,
                           wakes=(1650,))
    assert bool(emitted) is fires
    if fires:
        assert emitted[0]["sources"] == ["mww"]
        assert emitted[0]["command"] == "стол"  # The word heard at the hit is the name.


def test_mww_hit_without_any_transcript_fires_after_the_wait() -> None:
    emitted, logged, _ = listen([], q=HIGH, wakes=(1650,), seconds=12)
    assert [e["sources"] for e in emitted] == [["mww"]] and emitted[0]["command"] == ""
    assert logged[0]["vosk"] == "none"


LIVE = said(("учимся", 0.5, 0.9), ("компьютер", 1.0, 1.6), ("какая", 1.7, 2.0),
            ("погода", 2.0, 2.4), ("компьютер", 2.6, 3.2), ("как", 3.3, 3.5),
            ("дела", 3.5, 3.9))


def test_command_is_only_what_follows_the_name_up_to_the_next_name() -> None:
    # Live regression: the whole 8 s transcript used to be the event's text.
    emitted, _, inbox = listen([LIVE], q={1710: 250, 3300: 250}, wakes=(1710, 3300))
    assert [(e["command"], e["sources"]) for e in emitted] == [
        ("какая погода", ["mww"]), ("как дела", ["mww"])]
    assert "учимся" not in json.dumps(emitted + [i for _, i in inbox.items.values()],
                                      ensure_ascii=False)


def test_a_pause_ends_the_command() -> None:
    emitted, _, _ = listen([said(("компьютер", 1.0, 1.5), ("включи", 1.6, 1.9),
                                 ("свет", 1.9, 2.2), ("и", 3.0, 3.1), ("музыку", 3.1, 3.6))])
    assert [e["command"] for e in emitted] == ["включи свет"]


def test_repeating_the_name_gives_one_event_across_sources() -> None:
    script = [said(("компьютер", 1.0, 1.5), ("компьютер", 1.7, 2.2), ("включи", 2.3, 2.6),
                   ("свет", 2.6, 3.0))]
    emitted, _, _ = listen(script, q=HIGH, wakes=(1620, 2280))
    assert [(e["command"], e["sources"]) for e in emitted] == [("включи свет", ["mww", "vosk"])]


def test_a_late_mww_hit_after_a_vosk_wake_is_the_same_wake() -> None:
    # Vosk routed at 1.0-2.6; mww fires 1.1 s after the name, past every utterance window.
    emitted, _, _ = listen(NAME, q={3690: 255}, wakes=(3690,))
    assert [(e["command"], e["sources"]) for e in emitted] == [("включи свет", ["vosk"])]


def test_name_alone_takes_the_next_utterance_as_its_command() -> None:
    script = [said(("компьютер", 1.0, 1.6)), said(("включи", 4.0, 4.4), ("свет", 4.4, 4.8))]
    early, _, _ = listen(script, until=4.0)
    assert early == []  # Held while the follow-up may still come.
    emitted, _, _ = listen(script)
    assert [(e["command"], e["sources"], e["t"]) for e in emitted] == [
        ("включи свет", ["vosk"], 1.6)]


def test_name_alone_then_the_name_again_with_a_command_is_one_event() -> None:
    script = [said(("компьютер", 1.0, 1.6)),
              said(("компьютер", 3.0, 3.6), ("который", 3.7, 4.0), ("час", 4.0, 4.3))]
    emitted, _, _ = listen(script)
    assert [e["command"] for e in emitted] == ["который час"]


def test_name_alone_then_silence_fires_with_an_empty_command() -> None:
    # Vosk's own 5 s silence endpoint (empty text) ends the wait, before any finish().
    emitted, _, _ = listen([said(("компьютер", 1.0, 1.6)), (6.6, 6.6, "")], until=7.4)
    assert [(e["command"], e["sources"]) for e in emitted] == [("", ["vosk"])]


def test_speech_starting_too_late_is_not_the_command() -> None:
    script = [said(("компьютер", 1.0, 1.6)), said(("который", 7.0, 7.3), ("час", 7.3, 7.6))]
    emitted, logged, _ = listen(script)
    assert [e["command"] for e in emitted] == [""]
    assert [d["fired"] for d in logged] == [True, False]


def test_ordinary_speech_fires_nothing() -> None:
    emitted, logged, inbox = listen([(1.0, 2.6, "который час")])
    assert emitted == [] and not inbox.items and logged[0]["fired"] is False


def test_unknown_word_or_mode_is_refused() -> None:
    with pytest.raises(SafetyError):
        wl.Listener("алиса", "either", Vosk([]), Mww, emit=print)
    with pytest.raises(SafetyError):
        wl.Listener("компьютер", "any", Vosk([]), Mww, emit=print)


def stand_in() -> bytes:
    ready = json.dumps({"event": "ready", "rate": 16000, "threshold": 0.98, "window": 5,
                        "cooldown_ms": 2500, "gain_db": 0, "models": [
                            {"name": "komp", "sha256": "a" * 64, "stride": 3,
                             "arena_bytes": 24624}]})
    return f'''#!/bin/sh
[ "$*" = "--trace --max-seconds 700 --gain-db 0" ] || exit 9
echo '{ready}'
head -c 3200 >/dev/null
echo '{{"event":"p","model":"komp","t_ms":50,"q":7}}'
printf '%s%s\\n' '{{"event":"wake","model":"komp","t_ms":80,"probability":0.99,' \\
    '"peak":1.0,"last":1.0,"trusted":false}}'
cat >/dev/null
echo '{{"event":"end","reason":"eof","audio_ms":100,"detections":[1]}}'
'''.encode()


def test_mww_process_is_pinned_and_parsed_strictly(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "mww"
    path.write_bytes(stand_in())
    path.chmod(0o700)
    with pytest.raises(SafetyError):
        wl.Mww(path)  # Not the pinned build.
    monkeypatch.setitem(wl.MWW_PINS, "ft", hashlib.sha256(stand_in()).hexdigest())
    mww = wl.Mww(path)
    mww.feed(b"\0" * 3200)
    events = mww.close()
    assert ("p", "komp", 50, 7) in events and ("wake", "komp", 80) in events
    assert ("end", "eof", 100) in events and mww.audio_ms == 100
    path.write_bytes(stand_in().replace(b'"q":7', b'"q":7,"x":"SENSITIVE"'))
    monkeypatch.setitem(wl.MWW_PINS, "ft", hashlib.sha256(path.read_bytes()).hexdigest())
    bad = wl.Mww(path)
    bad.feed(b"\0" * 3200)
    assert ("invalid",) in bad.close() and not bad.alive()


def clip(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"\0\1" * int(seconds * RATE))


def test_offline_evaluation_scores_each_mode_per_label(tmp_path: Path) -> None:
    names = {"20261029T100000000001Z-positive-komp.wav": ([(0.5, 1.2, "компьютер")], ()),
             "20261029T100000000002Z-positive-komp.wav": ([(0.5, 1.2, "компот")], (1110,)),
             "20261029T100000000003Z-positive-komp.wav": ([], ()),
             "20261029T100000000004Z-negative-none.wav": ([(0.5, 3.0, "мой компьютер")],
                                                          (900,))}
    for name in names:
        clip(tmp_path / name, 3)
    (tmp_path / "manifest.jsonl").write_text("".join(
        json.dumps({"file": n, "label": n.split("-")[1], "word": None if "none" in n else "komp"})
        + "\n" for n in names))
    scripts = iter(names.values())
    current: list[Any] = []

    def recognizer() -> Vosk:
        current[:] = [next(scripts)]
        return Vosk(current[0][0])

    result = wl.evaluate(tmp_path, "компьютер", recognizer,
                         lambda: Mww({t: 255 for t in current[0][1]}, current[0][1]),
                         heldout=(1, 1))
    assert result["positives"] == {"files": 3, "vosk": 1, "mww": 1, "either": 2, "both": 0}
    assert result["negatives"] == {"files": 1, "vosk": 0, "mww": 1, "either": 1, "both": 0}
    # The latest clips per label were never trained on: reported on their own.
    assert result["heldout"]["positives"] == {"files": 1, "vosk": 0, "mww": 0, "either": 0,
                                              "both": 0}
    assert result["heldout"]["negatives"]["mww"] == 1
    assert result["trained"]["positives"]["either"] == 2
    assert result["trained"]["negatives"]["files"] == 0
    assert result["audio_s"] > 12 and result["resources"]["cpu_s"] >= 0
    assert all(set(row) == {"file", "label", "vosk", "mww", "mww_max_q"}
               for row in result["files"])


@pytest.mark.skipif(not all(os.environ.get(k) for k in (
    "ALICE_MCP_MWW_HELPER", "ALICE_MCP_WAKE_LIBVOSK", "ALICE_MCP_WAKE_VOSK_ZIP")),
    reason="set the pinned host mww helper, libvosk and Vosk model ZIP")
def test_real_engines_run_offline_on_a_silent_clip(tmp_path: Path, monkeypatch: Any) -> None:
    clip(tmp_path / "20261029T100000000001Z-negative-none.wav", 2)
    with wl.engines(Path(os.environ["ALICE_MCP_WAKE_LIBVOSK"]),
                    Path(os.environ["ALICE_MCP_WAKE_VOSK_ZIP"]),
                    Path(os.environ["ALICE_MCP_MWW_HELPER"])) as (recognizer, mww):
        result = wl.evaluate(tmp_path, "компьютер", recognizer, mww)
    assert result["negatives"]["either"] == 0 and result["resources"]["peak_rss_mib"] > 0
    assert 0 < result["resources"]["mww_vmhwm_kib"] < 16384  # The detector, not a fork.
