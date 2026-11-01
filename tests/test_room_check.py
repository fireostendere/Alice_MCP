"""Synthetic optical/audio metrics only; tests never open a PC device."""
import struct

from scripts.voice import room_check as rc


def test_metrics_return_intervals_and_colours_without_retaining_recordings() -> None:
    pcm = b"\0\0" * 1600 + struct.pack("<h", 1000) * 3200 + b"\0\0" * 1600
    assert rc.audio_metrics(pcm, 16000, 100)["active_intervals_ms"] == [[100, 300]]
    frames = bytes([255, 0, 0, 128, 0, 128, 0, 0, 255, 0, 0, 0])
    assert rc.colour_metrics(frames, 2, 2, 10) == [
        {"ms": 0, "red": 1, "purple": 1, "blue": 1, "green": 0}]
