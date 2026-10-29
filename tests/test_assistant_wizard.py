"""The wizard must never imply synthetic adapters qualify real hardware."""

from typing import Any

import pytest
from test_assistant_profile import settings

from alice_mcp.assistant_wizard import activation_status, main, self_test, settings_only
from alice_mcp.core import SafetyError


def test_settings_and_real_activation_are_effect_free() -> None:
    result = settings_only(settings())
    assert result["status"] == "settings_only"
    assert result["hardware_verified"] is False
    assert activation_status(settings()) == {
        "status": "blocked", "effects": [], "hardware_verified": False,
        "missing_verified": ["pcm_capture", "acoustic_detector", "rgb", "playback"],
    }
    # Caller-provided claims cannot enable activation.
    value: dict[str, Any] = settings() | {"hardware_verified": True}
    with pytest.raises(SafetyError):
        activation_status(value)


def test_synthetic_self_test_and_cli(capsys: Any) -> None:
    result = self_test()
    assert result["synthetic"] is True
    assert result["hardware_verified"] is False
    assert result["checks"] == ["unicode_phrase", "word_boundary", "mcp_route", "alice_route",
                                "activation_color", "local_tts_contract", "blocked_live"]
    assert main(["--simulate"]) == 0
    assert '"synthetic": true' in capsys.readouterr().out
