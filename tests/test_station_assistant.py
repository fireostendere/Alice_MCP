"""Bounded spoken facts: synthetic clock/provider responses, no network or station."""
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from alice_mcp import station_assistant as sa
from alice_mcp.core import SafetyError


@pytest.fixture(autouse=True)
def clear_weather_cache(monkeypatch: Any) -> None:
    monkeypatch.setattr(sa, "_weather_cache", None, raising=False)
    monkeypatch.setattr(sa, "_news_cache", None, raising=False)


@pytest.mark.parametrize("question", ["Сколько сейчас времени?", "Сколько время?", "Время?"])
def test_clock_answers_use_the_clock_not_a_language_model(monkeypatch: Any, question: str) -> None:
    from alice_mcp import realtime_voice as rv

    monkeypatch.setattr(rv, "api_key", lambda: pytest.fail("clock loaded cloud credentials"))
    now = datetime(2026, 11, 1, 14, 35, tzinfo=timezone(timedelta(hours=3)))
    result = sa.answer(question, now=now)
    assert "14:35" in result["text"] and "UTC+03:00" in result["text"]
    assert result["source"] == "host_clock" and result["sources"] == []


@pytest.mark.parametrize("question", ["", "x" * 1001, "привет\0", "a\u202eb"])
def test_invalid_questions_are_rejected_before_cloud_or_audio(question: str) -> None:
    with pytest.raises(SafetyError):
        sa.answer(question)


def test_weather_without_city_uses_belgrade_and_ai_search(monkeypatch: Any) -> None:
    from alice_mcp import realtime_voice as rv

    monkeypatch.setattr(rv, "api_key", lambda: "synthetic")
    calls: list[str] = []
    monkeypatch.setattr(sa, "search", lambda key, question, timeout:
                        calls.append(question) or {"text": "AI result", "source": "web_search"})
    assert sa.answer("Какая сегодня погода?")["source"] == "web_search"
    assert "Белград" in calls[0]


@pytest.mark.parametrize("bad", [None, "units", "temperature", "code", "old", "probability"])
def test_live_weather_is_validated_cached_and_never_served_stale(
        monkeypatch: Any, bad: str | None) -> None:
    import time

    import httpx

    data = {"timezone": "UTC", "current_units": {"temperature_2m": "°C"},
            "daily_units": {"temperature_2m_max": "°C", "temperature_2m_min": "°C",
                            "precipitation_probability_max": "%"},
            "current": {"time": datetime.now(UTC).isoformat(),
                        "temperature_2m": 9.6, "weather_code": 3},
            "daily": {"time": [datetime.now(UTC).date().isoformat()],
                      "temperature_2m_max": [10.3], "temperature_2m_min": [7.8],
                      "precipitation_probability_max": [59]}}
    if bad == "units":
        data["current_units"]["temperature_2m"] = "°F"
    elif bad == "temperature":
        data["current"]["temperature_2m"] = float("nan")
    elif bad == "code":
        data["current"]["weather_code"] = 999
    elif bad == "old":
        data["current"]["time"] = "2000-01-01T00:00"
    elif bad == "probability":
        data["daily"]["precipitation_probability_max"] = [101]
    calls: list[Any] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return data

    monkeypatch.setattr(httpx, "get", lambda url, **kw: calls.append((url, kw)) or Response())
    first = sa.weather()
    if bad:
        assert first["source"] == "unavailable" and sa.weather_snapshot() is None
        return
    assert first["city"] == "Белград, Сербия" and first["current"]["temperature_c"] == 9.6
    assert first["current"]["condition"] == "пасмурно"
    assert first["source"] == "open_meteo" and first["sources"]
    assert sa.weather() == first and len(calls) == 1
    first["current"]["temperature_c"] = 999
    assert sa.weather_snapshot()["current"]["temperature_c"] == 9.6
    assert calls[0][1]["trust_env"] is False
    future = time.monotonic() + 301
    monkeypatch.setattr(sa.time, "monotonic", lambda: future)
    assert sa.weather_snapshot() is None
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("PRIVATE_SENTINEL")))
    assert sa.weather()["source"] == "unavailable"


def test_news_snapshot_contains_only_fresh_sourced_headlines(monkeypatch: Any) -> None:
    import time
    from email.utils import format_datetime

    import httpx

    published = format_datetime(datetime.now(UTC))
    xml = ("<rss><channel>"
        "<item><title>Проверенная новость</title><link>https://www.bbc.com/russian/articles/test</link>"
        f"<pubDate>{published}</pubDate></item>"
        "<item><title>Старая новость</title><link>https://www.bbc.com/russian/old</link>"
        "<pubDate>Sat, 01 Jan 2000 12:00:00 GMT</pubDate></item>"
        "<item><title>Без даты</title><link>https://www.bbc.com/russian/unknown</link></item>"
        "<item><title>Непроверенная ссылка</title><link>https://example.com/</link>"
        f"<pubDate>{published}</pubDate></item></channel></rss>")
    calls: list[Any] = []

    class Response:
        content = xml.encode()

        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **k: calls.append(k) or Response())
    first = sa.news()
    assert first["source"] == "bbc_rss" and len(first["headlines"]) == 1
    assert first["headlines"][0]["title"] == "Проверенная новость"
    assert first["sources"] == ["https://www.bbc.com/russian/articles/test"]
    assert sa.news() == first and len(calls) == 1 and calls[0]["trust_env"] is False
    future = time.monotonic() + 301
    monkeypatch.setattr(sa.time, "monotonic", lambda: future)
    assert sa.news_snapshot() is None
    Response.content = b'<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss/>'
    assert sa.news()["source"] == "unavailable"


def test_fresh_facts_require_actual_search_and_keep_citations_separate(monkeypatch: Any) -> None:
    import httpx

    from alice_mcp import realtime_voice as rv

    sent: list[Any] = []
    monkeypatch.setattr(rv, "api_key", lambda: "sk-synthetic")

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"output": [{"type": "web_search_call", "status": "completed"},
                {"type": "message", "content": [{"type": "output_text",
                    "text": "В Москве **+3**. ([источник](https://example.com/weather))",
                    "annotations": [{"type": "url_citation", "url": "https://example.com/weather"}]
                }]}]}

    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.append(kw) or Response())
    result = sa.answer("Какая погода в Москве?")
    assert result["text"] == "В Москве +3."
    assert result["sources"] == ["https://example.com/weather"]
    assert sent[0]["json"]["store"] is False
    assert sent[0]["json"]["tool_choice"] == "required"
    assert "station_action" not in str(sent[0]["json"]["tools"])
    monkeypatch.setattr(Response, "json", lambda self: {"output": [
        {"type": "web_search_call", "status": "completed", "action": {"sources": [
            {"type": "url", "url": "https://example.com/news"}]}},
        {"type": "message", "content": [{"type": "output_text", "text": "Проверенная новость."}]}]})
    assert sa.answer("Новости технологий")["sources"] == ["https://example.com/news"]
    assert sent[-1]["json"]["include"] == ["web_search_call.action.sources"]
    monkeypatch.setattr(Response, "json", lambda self: {"output": [{"type": "message",
        "content": [{"type": "output_text", "text": "НЕПРОВЕРЕННЫЙ ОТВЕТ"}]}]})
    result = sa.answer("Расскажи последние новости")
    assert "НЕПРОВЕРЕННЫЙ" not in result["text"] and result["source"] == "unavailable"
