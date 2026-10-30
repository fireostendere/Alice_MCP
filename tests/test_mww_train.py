"""Offline dataset labels only; no synthesis, model training or network."""

import re

import pytest

from scripts.voice import mww_train as recipe


@pytest.mark.parametrize(("word", "phrase"), [("komp", "компьютер"),
                                            ("okey_dom", "окей дом"), ("marusya", "маруся")])
def test_exact_wake_phrase_is_never_a_negative_label(word: str, phrase: str) -> None:
    spec = recipe.training_phrases(word)
    for text in spec["near_miss"]:
        normalized = " " + " ".join(re.findall(r"\w+", text.casefold())) + " "
        assert " " + phrase + " " not in normalized
    if word == "marusya":
        assert "моя Маруся" in spec["positive"]
        assert "Марусю" in spec["near_miss"]
    if word == "komp":
        assert "мой компьютер" in spec["positive"]
        assert "компот" in spec["near_miss"]
