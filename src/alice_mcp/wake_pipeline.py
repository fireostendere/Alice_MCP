"""Host streaming wake routing: synthetic-tested only, NOT live wake acceptance.

A PCM source is any iterable of ``(mono S16LE bytes, station_speaking)`` chunks at
one fixed rate; ``station_speaking`` must come from the station's own playback
state. A recognizer endpoints utterances; routing reuses assistant_profile prefix
matching. Events and transcripts are UNTRUSTED data, never action permission.
Nothing here opens a microphone, station, network or action. Dispatch is
disabled unless routes are explicitly enabled.
"""

from __future__ import annotations

import math
import secrets
import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from alice_mcp.assistant_profile import _normalized, route_transcript, validate_profile
from alice_mcp.core import SafetyError

# The station's own detector handles its stock names; a word starting with these
# stems (e.g. "алису", "окей яндекс") could make stock Alice actuate as well.
# ponytail: conservative stem heuristic; replace with the station's selected word if exposed.
STOCK_STEMS = ("алис", "яндекс")


class Recognizer(Protocol):
    """Endpointed streaming ASR returning {"text", "start", "end"} stream seconds."""

    def accept(self, pcm: bytes) -> dict[str, Any] | None: ...

    def flush(self) -> dict[str, Any] | None: ...


class WakePipeline:
    """Feed chunks; get one decision per endpointed utterance. Fails closed."""

    def __init__(self, profile: Any, recognizer: Recognizer, *, rate: int = 16000,
                 retrigger_s: float = 1.5, echo_guard_s: float = 0.5,
                 max_utterance_s: float = 10.0) -> None:
        if type(rate) is not int or not 8000 <= rate <= 48000:
            raise SafetyError("Unsupported PCM rate")
        self.profile = validate_profile(profile)
        self.recognizer, self.rate = recognizer, rate
        self.retrigger_s, self.guard = retrigger_s, echo_guard_s
        self.max_samples = round(max_utterance_s * rate)
        self.samples = self.since_endpoint = self.count = 0
        self.speaking: list[list[float]] = []  # merged [start, end] runs of station output
        self.last_wake_end = -math.inf

    def feed(self, pcm: bytes, station_speaking: bool = False) -> list[dict[str, Any]]:
        if (type(pcm) is not bytes or len(pcm) % 2 or not 0 < len(pcm) <= 2 * self.rate
                or type(station_speaking) is not bool):
            raise SafetyError("Invalid PCM chunk")
        start = self.samples / self.rate
        self.samples += len(pcm) // 2
        self.since_endpoint += len(pcm) // 2
        now = self.samples / self.rate
        if station_speaking:
            if self.speaking and start - self.speaking[-1][1] <= 2 * self.guard:
                self.speaking[-1][1] = now
            else:
                self.speaking.append([start, now])
        horizon = now - (self.max_samples / self.rate) - 2 * self.guard - 2
        self.speaking = [run for run in self.speaking if run[1] > horizon]
        result = self.recognizer.accept(pcm)
        if result is None and self.since_endpoint >= self.max_samples:
            result = self.recognizer.flush()  # forced endpoint bounds utterance length
            self.since_endpoint = 0
        return self._decide(result)

    def finish(self) -> list[dict[str, Any]]:
        return self._decide(self.recognizer.flush())

    def _decide(self, result: dict[str, Any] | None) -> list[dict[str, Any]]:
        if result is None:
            return []
        self.since_endpoint = 0
        now = self.samples / self.rate
        if set(result) != {"text", "start", "end"}:
            raise SafetyError("Invalid recognizer result")
        text, start, end = result["text"], result["start"], result["end"]
        if not text:
            return []
        # NaN/inf fail the range comparison; bool is rejected by the exact type check.
        if (type(text) is not str or len(text) > 4096
                or not all(type(t) in (int, float) for t in (start, end))
                or not 0 <= start <= end <= now + 0.1):
            raise SafetyError("Invalid recognizer result")
        self.count += 1
        event: dict[str, Any] = {"kind": "wake_utterance", "utterance_id": self.count,
                                 "trusted": False, "start_s": round(start, 3),
                                 "end_s": round(end, 3), "decided_s": round(now, 3),
                                 "decision": "no_activation"}
        route = route_transcript(self.profile, text)
        if route is None:
            return [event]  # unactivated speech: timing only, transcript discarded
        if any(a - self.guard < end and b + self.guard > start for a, b in self.speaking):
            event["decision"] = "suppressed_echo"
        elif start - self.last_wake_end < self.retrigger_s:
            event["decision"] = "suppressed_retrigger"
            self.last_wake_end = end
        else:
            event.update(decision="routed", route=route["route"], phrase=route["phrase"],
                         text=route["text"])
            self.last_wake_end = end
        return [event]


class McpInbox:
    """Bounded RAM inbox; text is released once, like the one-shot transcript lifecycle.

    The client agent reads it as untrusted data; existing action policy still applies.
    """

    def __init__(self, evidence_class: str, *, ttl_s: float = 120.0, limit: int = 16,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.evidence_class, self.ttl, self.limit, self.clock = evidence_class, ttl_s, limit, clock
        self.items: dict[str, tuple[float, dict[str, Any]]] = {}

    def _expire(self) -> None:
        now = self.clock()
        self.items = {k: v for k, v in self.items.items() if v[0] > now}

    def deliver(self, event: dict[str, Any]) -> str | None:
        self._expire()
        if len(self.items) >= self.limit:
            return None  # never evict an unread event
        capture_id = secrets.token_hex(16)
        self.items[capture_id] = (self.clock() + self.ttl, {
            "kind": "wake_route", "outcome": "complete", "capture_id": capture_id,
            "capture_ready": False, "untrusted_text": True, "do_not_retry": True,
            "evidence_class": self.evidence_class, "route": "mcp",
            "phrase": event["phrase"], "text": event["text"]})
        return capture_id

    def status(self, capture_id: str) -> dict[str, Any]:
        self._expire()
        item = self.items.pop(capture_id, None)
        if item is None:
            raise SafetyError("Wake event unknown, expired or already released")
        return item[1]


class Dispatcher:
    """At most one destination per utterance; stock and MCP never both actuate."""

    def __init__(self, inbox: McpInbox, enabled_routes: Iterable[str] = ()) -> None:
        self.inbox, self.enabled = inbox, frozenset(enabled_routes) & {"mcp", "alice"}
        self.last_id = 0

    def dispatch(self, event: dict[str, Any]) -> dict[str, Any]:
        uid, route = event.get("utterance_id"), event.get("route")
        receipt: dict[str, Any] = {"utterance_id": uid, "route": route, "delivered": False}
        if type(uid) is not int or uid <= self.last_id:
            return receipt | {"reason": "duplicate_utterance"}
        self.last_id = uid
        words = _normalized(f"{event.get('phrase', '')} {event.get('text', '')}").split()
        stock = any(w.startswith(STOCK_STEMS) for w in words)
        if event.get("decision") != "routed":
            reason = "not_routed"
        elif route not in self.enabled:
            reason = "route_disabled"
        elif route == "alice":
            # No documented, guarded text submission to stock dialogue: see WAKE_PIPELINE.md.
            reason = ("stock_handles_own_activation"
                      if str(event["phrase"]).startswith(STOCK_STEMS)
                      else "no_documented_text_submission")
        elif stock:
            reason = "stock_may_also_respond"
        else:
            capture_id = self.inbox.deliver(event)
            if capture_id is None:
                reason = "inbox_full"
            else:
                return receipt | {"delivered": True, "reason": "mcp_inbox",
                                  "capture_id": capture_id}
        return receipt | {"reason": reason}
