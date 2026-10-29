"""Streaming wake routing on a scripted recognizer; synthetic data, no audio or device."""

from typing import Any

import pytest
from test_assistant_profile import settings

from alice_mcp.core import SafetyError
from alice_mcp.wake_pipeline import Dispatcher, McpInbox, WakePipeline

RATE = 16000
CHUNK = b"\0\0" * (RATE // 10)  # 100 ms


class Scripted:
    """Returns each scripted result once the stream reaches its endpoint second."""

    def __init__(self, results: list[tuple[float, str, float, float]]) -> None:
        self.results, self.samples, self.flushes = list(results), 0, 0

    def accept(self, pcm: bytes) -> dict[str, Any] | None:
        self.samples += len(pcm) // 2
        if self.results and self.samples / RATE >= self.results[0][0]:
            _, text, start, end = self.results.pop(0)
            return {"text": text, "start": start, "end": end}
        return None

    def flush(self) -> dict[str, Any] | None:
        self.flushes += 1
        return None


def run(results: list[tuple[float, str, float, float]], seconds: float,
        speaking: tuple[float, float] = (-1, -1), **options: Any) -> list[dict[str, Any]]:
    pipeline = WakePipeline(settings(), Scripted(results), rate=RATE, **options)
    events: list[dict[str, Any]] = []
    for i in range(round(seconds * 10)):
        events += pipeline.feed(CHUNK, speaking[0] <= i / 10 < speaking[1])
    return events + pipeline.finish()


def test_routes_untrusted_command_and_discards_unactivated_speech() -> None:
    events = run([(2.0, "алёна включи свет", 0.5, 1.4), (5.0, "включи свет", 3.5, 4.2)], 6)
    assert events[0] == {"kind": "wake_utterance", "utterance_id": 1, "trusted": False,
                         "start_s": 0.5, "end_s": 1.4, "decided_s": 2.0,
                         "decision": "routed", "route": "mcp", "phrase": "алёна",
                         "text": "включи свет"}
    # Speech without an activation phrase keeps timing only: no transcript leaves.
    assert events[1] == {"kind": "wake_utterance", "utterance_id": 2, "trusted": False,
                         "start_s": 3.5, "end_s": 4.2, "decided_s": 5.0,
                         "decision": "no_activation"}


def test_station_speech_suppresses_overlapping_and_trailing_activation() -> None:
    during = run([(3.0, "алёна стоп", 1.0, 2.0)], 4, speaking=(0.5, 1.5))
    tail = run([(3.0, "алёна стоп", 1.8, 2.5)], 4, speaking=(0.5, 1.5))
    later = run([(4.0, "алёна стоп", 2.6, 3.2)], 5, speaking=(0.5, 1.5))
    assert [e["decision"] for e in during + tail + later] == [
        "suppressed_echo", "suppressed_echo", "routed"]
    assert "text" not in during[0] and "text" not in tail[0]


def test_retrigger_window_suppresses_quick_repeat_only() -> None:
    quick = run([(2.0, "алёна", 0.5, 1.0), (4.0, "алёна стоп", 2.0, 2.8)], 5)
    spaced = run([(2.0, "алёна", 0.5, 1.0), (5.0, "алёна стоп", 3.0, 3.8)], 6)
    assert [e["decision"] for e in quick] == ["routed", "suppressed_retrigger"]
    assert [e["decision"] for e in spaced] == ["routed", "routed"]


def test_max_utterance_forces_endpoint_and_input_is_bounded() -> None:
    recognizer = Scripted([])
    pipeline = WakePipeline(settings(), recognizer, rate=RATE, max_utterance_s=1.0)
    for _ in range(25):
        pipeline.feed(CHUNK)
    assert recognizer.flushes == 2
    for bad in [b"", b"\0", b"\0\0" * (RATE + 1), bytearray(CHUNK)]:
        with pytest.raises(SafetyError):
            pipeline.feed(bad)  # type: ignore[arg-type]
    with pytest.raises(SafetyError):
        pipeline.feed(CHUNK, 1)  # type: ignore[arg-type]
    with pytest.raises(SafetyError):
        WakePipeline(settings(), recognizer, rate=96000)


def routed(uid: int, route: str, phrase: str, text: str) -> dict[str, Any]:
    return {"kind": "wake_utterance", "utterance_id": uid, "trusted": False,
            "decision": "routed", "route": route, "phrase": phrase, "text": text}


def test_dispatch_is_disabled_by_default_and_one_destination_per_utterance() -> None:
    inbox = McpInbox("synthetic")
    assert Dispatcher(inbox).dispatch(routed(1, "mcp", "алёна", "свет")) == {
        "utterance_id": 1, "route": "mcp", "delivered": False, "reason": "route_disabled"}
    dispatcher = Dispatcher(inbox, {"mcp", "alice"})
    receipt = dispatcher.dispatch(routed(1, "mcp", "алёна", "включи свет"))
    assert receipt["delivered"] is True and receipt["reason"] == "mcp_inbox"
    duplicate = dispatcher.dispatch(routed(1, "mcp", "алёна", "включи свет"))
    assert duplicate["reason"] == "duplicate_utterance"
    # Stock Alice hears its own wake word: the host never resubmits it.
    assert dispatcher.dispatch(routed(2, "alice", "алиса", "погода"))["reason"] == (
        "stock_handles_own_activation")
    assert dispatcher.dispatch(routed(3, "alice", "компьютер", "погода"))["reason"] == (
        "no_documented_text_submission")
    # A stock activation word inside an MCP utterance could actuate both: hold it.
    for uid, text in [(4, "спроси алису погоду"), (5, "окей яндекс стоп")]:
        assert dispatcher.dispatch(routed(uid, "mcp", "алёна", text))["reason"] == (
            "stock_may_also_respond")
    assert dispatcher.dispatch({"utterance_id": 6, "decision": "suppressed_echo"})[
        "reason"] == "not_routed"
    assert len(inbox.items) == 1


def test_mcp_inbox_releases_untrusted_text_once_and_expires() -> None:
    now = [0.0]
    inbox = McpInbox("synthetic", limit=2, clock=lambda: now[0])
    first = inbox.deliver(routed(1, "mcp", "алёна", "включи свет"))
    assert first is not None
    assert inbox.status(first) == {
        "kind": "wake_route", "outcome": "complete", "capture_id": first,
        "capture_ready": False, "untrusted_text": True, "do_not_retry": True,
        "evidence_class": "synthetic", "route": "mcp", "phrase": "алёна",
        "text": "включи свет"}
    with pytest.raises(SafetyError):
        inbox.status(first)  # released once
    second = inbox.deliver(routed(2, "mcp", "алёна", "a"))
    assert inbox.deliver(routed(3, "mcp", "алёна", "b")) is not None
    assert inbox.deliver(routed(4, "mcp", "алёна", "c")) is None  # bounded, no eviction
    now[0] = 121.0
    with pytest.raises(SafetyError):
        inbox.status(second or "")
    assert inbox.items == {}
