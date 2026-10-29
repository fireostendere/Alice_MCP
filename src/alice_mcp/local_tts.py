"""Optional offline Russian Piper synthesis; no listener, download or device action.

The operator installs Piper and hash-pinned model/config separately. Generated
text/audio stay in RAM; this module does not provide station playback or claim
that the room heard anything.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import io
import json
import logging
import re
import struct
import wave
from pathlib import Path

from alice_mcp import device_runtime as rt
from alice_mcp.core import SafetyError, _check_ssh_file, _unique


def wav_parameters(audio: bytes) -> dict[str, str]:
    """Only bounded, quiet, canonical WAV crosses the resident TLS boundary."""
    try:
        if type(audio) is not bytes or not 46 <= len(audio) <= 1920044:
            raise ValueError
        (riff, size, wave_id, fmt, fmt_size, codec, channels, rate, byte_rate, align, bits,
         data, pcm) = struct.unpack("<4sI4s4sIHHIIHH4sI", audio[:44])
        if ((riff, wave_id, fmt, fmt_size, codec, channels, align, bits, data)
            != (b"RIFF", b"WAVE", b"fmt ", 16, 1, 1, 2, 16, b"data")
            or size != len(audio) - 8 or pcm != len(audio) - 44 or pcm % 2
            or not 16000 <= rate <= 48000 or byte_rate != rate * 2 or pcm > rate * 2 * 20
            or any(abs(sample) > 8191 for (sample,) in struct.iter_unpack("<h", audio[44:]))):
            raise ValueError
        return {"wav_base64": base64.b64encode(audio).decode("ascii"),
                "sha256": hashlib.sha256(audio).hexdigest()}
    except Exception:
        raise SafetyError("Local PCM invalid or outside playback bounds") from None


def synthesize(text: str, model: Path, model_sha256: str, config_sha256: str,
               *, volume: float = 0.15) -> bytes:
    """Return <=20s mono S16 WAV from an already installed Russian voice."""
    try:
        rt.validate_parameters("speech.say", {"text": text}, {"tts_client": {}}, "station-mini")
        if (type(volume) not in {int, float} or not 0 < volume <= 0.25
            or not re.fullmatch("[a-f0-9]{64}", model_sha256)
            or not re.fullmatch("[a-f0-9]{64}", config_sha256)):
            raise ValueError
        config = model.with_name(model.name + ".json")
        _check_ssh_file(model)
        _check_ssh_file(config)
        with model.open("rb") as model_file, config.open("rb") as config_file:
            model_bytes = model_file.read(100 * 1024 * 1024 + 1)
            config_bytes = config_file.read(65537)
            if (len(model_bytes) > 100 * 1024 * 1024 or len(config_bytes) > 65536
                or hashlib.sha256(model_bytes).hexdigest() != model_sha256
                or hashlib.sha256(config_bytes).hexdigest() != config_sha256):
                raise ValueError
            settings = json.loads(config_bytes, object_pairs_hook=_unique)
            rate = settings["audio"]["sample_rate"]
            if (settings["espeak"]["voice"] != "ru" or type(rate) is not int
                or not 16000 <= rate <= 48000):
                raise ValueError
            del model_bytes
            # Piper's debug messages include text/phonemes; never enable those logs here.
            logging.getLogger("piper.voice").disabled = True
            piper = importlib.import_module("piper")
            # Fixed open descriptors prevent a replaced pathname selecting another model.
            voice = piper.PiperVoice.load(
                f"/proc/self/fd/{model_file.fileno()}",
                config_path=f"/proc/self/fd/{config_file.fileno()}", use_cuda=False,
            )
            options = piper.SynthesisConfig(volume=volume, normalize_audio=False)
            pcm = bytearray()
            for count, chunk in enumerate(voice.synthesize(text, options), start=1):
                data = chunk.audio_int16_bytes
                if (count > 16 or chunk.sample_rate != rate or chunk.sample_width != 2
                    or chunk.sample_channels != 1 or len(data) % 2
                    or len(pcm) + len(data) > rate * 2 * 20):
                    raise ValueError
                pcm.extend(data)
        if not pcm:
            raise ValueError
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm)
        return output.getvalue()
    except Exception:
        raise SafetyError("Local TTS unavailable, changed or outside safety bounds") from None
