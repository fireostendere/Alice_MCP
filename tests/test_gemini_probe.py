"""Synthetic wire messages only; tests never contact the provider."""
from pathlib import Path
from typing import Any

from alice_mcp import realtime_voice as rv
from scripts.voice import gemini_probe as gp


def test_named_key_is_read_from_the_same_private_dotenv(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / ".env"
    path.write_text("REAL_TIME_API_" + "KEY=one\nGEMINI_API_" + "KEY=two\n")
    path.chmod(0o600)
    monkeypatch.delenv("REAL_TIME_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ALICE_MCP_REALTIME_ENV", str(path))
    assert rv.api_key() == "one" and rv.api_key("GEMINI_API_KEY") == "two"


def test_report_keeps_wire_facts_and_never_audio_or_credentials() -> None:
    report: dict[str, Any] = {}
    audio = bytearray()
    assert gp.absorb(report, audio, b'{"setupComplete":{}}', 10) == []
    calls = gp.absorb(report, audio, '{"toolCall":{"functionCalls":['
                      '{"id":"c1","name":"get_time"}]}}', 20)
    assert gp.reply(calls, "12:34") == {"toolResponse": {"functionResponses": [
        {"id": "c1", "name": "get_time", "response": {"result": "12:34"}}]}}
    gp.absorb(report, audio, '{"serverContent":{"modelTurn":{"parts":[{"inlineData":'
              '{"mimeType":"audio/pcm;rate=24000","data":"AAAAAA=="}}]},'
              '"outputTranscription":{"text":"Сейчас "}}}', 30)
    gp.absorb(report, audio, '{"serverContent":{"turnComplete":true,'
              '"outputTranscription":{"text":"полдень"}},'
              '"usageMetadata":{"totalTokenCount":7}}', 40)
    assert bytes(audio) == b"\0\0\0\0"
    assert report == {
        "frames": {"binary": 1, "text": 3}, "ms": {"setupComplete": 10, "toolCall": 20,
                                                   "audio": 30, "turnComplete": 40},
        "keys": ["serverContent.modelTurn", "serverContent.outputTranscription",
                 "serverContent.turnComplete", "setupComplete", "toolCall", "usageMetadata"],
        "audio_mime": "audio/pcm;rate=24000", "tools": ["get_time"],
        "said": "Сейчас полдень", "tokens": 7}


def test_setup_asks_for_speech_and_declares_only_the_clock_tool() -> None:
    setup = gp.setup("gemini-3.8-live")
    assert setup["model"] == "models/gemini-3.8-live"
    assert setup["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert [d["name"] for tool in setup["tools"] for d in tool["functionDeclarations"]] == [
        "get_time"]
