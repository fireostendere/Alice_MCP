"""Owner-clip word segmentation on synthetic signals (needs numpy; skips without it)."""

import pytest

np = pytest.importorskip("numpy")

from scripts.voice.mww_train import word_segment  # noqa: E402


def clip(start_s: float, end_s: float, seconds: float = 3.0) -> "np.ndarray":
    rng = np.random.default_rng(1)
    audio = rng.normal(0, 1e-4, int(16000 * seconds))
    first, last = int(16000 * start_s), int(16000 * end_s)
    audio[first:last] += 0.05 * np.sin(2 * np.pi * 300 * np.arange(last - first) / 16000)
    return audio


def test_word_segment_finds_word_with_margin() -> None:
    segment = word_segment(clip(1.0, 1.8))
    assert segment is not None
    start, end = (x / 16000 for x in segment)
    assert 0.9 <= start <= 1.0 and 1.8 <= end <= 1.9


def test_word_segment_rejects_cut_or_tiny_words() -> None:
    assert word_segment(clip(2.4, 3.0)) is None  # touches the end: no post-word silence
    assert word_segment(clip(1.0, 1.2)) is None  # shorter than 0.3 s
