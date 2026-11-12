"""Explicit owner-run check of the Gemini Live free tier from this PC; no station access.

Stage `key`: one Live session answers one typed Russian question that needs the declared
clock tool. Stage `token`: mints the one-use constrained token a station client would get
instead of the key and opens a session with it. Prints one JSON report of the wire facts a
station client port depends on; never the key, the token or audio.

    .venv/bin/python -m scripts.voice.gemini_probe [--wav answer.wav]
"""
from __future__ import annotations

import argparse
import base64
import json
import time
import wave
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

HOST = "generativelanguage.googleapis.com"
SERVICE = "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
MODEL = "gemini-3.8-live"
RATE = 24000  # Documented output rate; the report shows the MIME type actually sent.
UNTIMED = {"serverContent", "usageMetadata", "modelTurn", "outputTranscription"}


def setup(model: str) -> dict[str, Any]:
    return {"model": "models/" + model,
            "generationConfig": {"responseModalities": ["AUDIO"]},
            "systemInstruction": {"parts": [{"text": "Ты голосовой помощник колонки. Отвечай "
                                             "по-русски одной короткой фразой. Время узнавай "
                                             "только через get_time."}]},
            "tools": [{"functionDeclarations": [
                {"name": "get_time", "description": "Текущее местное время."}]}],
            "outputAudioTranscription": {}}


def absorb(report: dict[str, Any], audio: bytearray, frame: str | bytes,
           ms: int) -> list[dict[str, Any]]:
    """Fold one server frame into the report; returns the function calls to answer."""
    frames = report.setdefault("frames", {"binary": 0, "text": 0})
    frames["binary" if isinstance(frame, bytes) else "text"] += 1
    message = json.loads(frame)
    content = message.get("serverContent", {})
    marks = report.setdefault("ms", {})
    for key, value in (message | content).items():
        if key not in UNTIMED and value is not False:
            marks.setdefault(key, ms)  # Unknown events are kept: they are what we look for.
    report["keys"] = sorted(set(report.get("keys", [])) | (message.keys() - {"serverContent"})
                            | {"serverContent." + key for key in content})
    for part in content.get("modelTurn", {}).get("parts", []):
        if "inlineData" in part:
            audio += base64.b64decode(part["inlineData"]["data"])
            report["audio_mime"] = part["inlineData"].get("mimeType")
            marks.setdefault("audio", ms)
    if "outputTranscription" in content:
        report["said"] = report.get("said", "") + content["outputTranscription"].get("text", "")
    if "usageMetadata" in message:
        report["tokens"] = message["usageMetadata"].get("totalTokenCount")
    calls: list[dict[str, Any]] = message.get("toolCall", {}).get("functionCalls", [])
    if calls:
        report["tools"] = report.get("tools", []) + [call.get("name") for call in calls]
    return calls


def reply(calls: list[dict[str, Any]], result: str) -> dict[str, Any]:
    return {"toolResponse": {"functionResponses": [
        {"id": call.get("id"), "name": call.get("name"), "response": {"result": result}}
        for call in calls]}}


def session(url: str, model: str, question: str, seconds: float) -> tuple[dict[str, Any], bytes]:
    """One session; an empty question only proves that setup is accepted."""
    from websockets.exceptions import ConnectionClosed, InvalidStatus
    from websockets.sync.client import connect

    report: dict[str, Any] = {"ms": {}}
    audio = bytearray()
    start = time.monotonic()
    try:  # Errors carry provider text only; the URL (and its credential) is never reported.
        with connect(url, open_timeout=10, close_timeout=1, max_size=1 << 20, proxy=None) as ws:
            ws.send(json.dumps({"setup": setup(model)}))
            asked = False
            while "turnComplete" not in report["ms"]:
                left = seconds - (time.monotonic() - start)
                calls = absorb(report, audio, ws.recv(timeout=max(left, 0)),
                               int((time.monotonic() - start) * 1000))
                if calls:
                    ws.send(json.dumps(reply(calls, time.strftime("%H:%M"))))
                if "setupComplete" in report["ms"] and not asked:
                    if not question:
                        break
                    ws.send(json.dumps({"realtimeInput": {"text": question}}))
                    asked = True
    except InvalidStatus as error:
        report["error"] = {"http": error.response.status_code,
                           "body": (error.response.body or b"")[:300].decode("utf-8", "replace")}
    except ConnectionClosed as error:
        report["error"] = {"close": error.rcvd.code if error.rcvd else None,
                           "reason": error.rcvd.reason[:300] if error.rcvd else ""}
    except (OSError, TimeoutError, ValueError) as error:
        report["error"] = {"failed": type(error).__name__}
    return report, bytes(audio)


def mint(key: str, model: str) -> tuple[dict[str, Any], str]:
    """One-use token locked to the model. The docs name the lock field two ways: try both."""
    import httpx

    expire = (datetime.now(UTC) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    locks = {"liveConnectConstraints": {"model": "models/" + model,
                                        "config": {"responseModalities": ["AUDIO"]}},
             "bidiGenerateContentSetup": setup(model)}
    report: dict[str, Any] = {}
    for field, lock in locks.items():
        try:
            response = httpx.post(f"https://{HOST}/v1beta/auth_tokens", trust_env=False,
                                  headers={"x-goog-api-key": key}, timeout=15,
                                  json={"uses": 1, "expireTime": expire, field: lock})
        except httpx.HTTPError as error:
            return {"failed": type(error).__name__}, ""
        report = {"http": response.status_code, "lock_field": field}
        if response.status_code == 200:
            return report, str(response.json().get("name", ""))
        report["body"] = response.text[:300]
        if response.status_code != 400:
            break
    return report, ""


def main(argv: list[str] | None = None) -> int:
    from alice_mcp.core import SafetyError
    from alice_mcp.realtime_voice import api_key

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--question", default="Который час?")
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--wav", help="save the spoken answer here (keep it outside the repo)")
    args = parser.parse_args(argv)
    try:
        key = api_key("GEMINI_API_KEY")
    except SafetyError as error:
        key = None
        print(json.dumps({"error": str(error)}))
    if not key:
        print(json.dumps({"error": "GEMINI_API_KEY is not set in the environment or .env"}))
        return 2
    base = f"wss://{HOST}{SERVICE}"
    live, audio = session(base + "?key=" + quote(key), args.model, args.question, args.seconds)
    token, value = mint(key, args.model)
    if value:
        token |= session(base + "Constrained?" + urlencode({"access_token": value}),
                         args.model, "", args.seconds)[0]
    print(json.dumps({"model": args.model, "key": live, "token": token,
                      "audio_ms": len(audio) * 500 // RATE}, ensure_ascii=False, indent=1))
    if args.wav and audio:
        with wave.open(args.wav, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(audio)
    return 0 if "audio" in live["ms"] and "setupComplete" in token.get("ms", {}) else 1


if __name__ == "__main__":
    raise SystemExit(main())
