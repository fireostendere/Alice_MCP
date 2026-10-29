"""Private settings and UNTRUSTED transcript-prefix routing, not wake detection.

No inventory access, action execution, listener or device activation. Piper paths
and pins are declarations; only local_tts.synthesize verifies actual model files.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import stat
import unicodedata
from pathlib import Path
from typing import Any

from alice_mcp.core import SafetyError, _unique


def _text(value: Any, limit: int) -> str:
    if (type(value) is not str or not 0 < len(value) <= limit or not value.strip()
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise SafetyError("Invalid assistant text")
    return unicodedata.normalize("NFC", value).strip()


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _word(c: str) -> bool:
    return c == "_" or unicodedata.category(c)[0] in "LNM"


def _prefix(text: str, phrase: str) -> bool:
    return text.startswith(phrase) and (len(text) == len(phrase) or not _word(text[len(phrase)]))


def validate_profile(value: Any) -> dict[str, Any]:
    """Validate exact version-1 schema and return an independent canonical copy."""
    try:
        if (type(value) is not dict
                or set(value) != {"version", "wake_phrases", "activation_color", "tts"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError
        color = value["activation_color"]
        if type(color) is not str or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError
        phrases = value["wake_phrases"]
        if type(phrases) is not list or not 1 <= len(phrases) <= 16:
            raise ValueError
        canonical: list[dict[str, str]] = []
        for item in phrases:
            if type(item) is not dict or set(item) != {"phrase", "route"}:
                raise ValueError
            phrase = _normalized(_text(item["phrase"], 80))
            if (len(phrase) > 80 or not phrase[0].isalpha()
                    or not all(unicodedata.category(c)[0] in "LM" or c == " " for c in phrase)
                    or type(item["route"]) is not str or item["route"] not in {"mcp", "alice"}
                    or any(_prefix(phrase, p["phrase"]) or _prefix(p["phrase"], phrase)
                           for p in canonical)):
                raise ValueError
            canonical.append({"phrase": phrase, "route": item["route"]})
        tts = value["tts"]
        if (type(tts) is not dict or set(tts) != {
                "backend", "model", "model_sha256", "config_sha256", "volume"}
                or type(tts["backend"]) is not str or tts["backend"] != "piper"
                or type(tts["volume"]) not in {int, float}
                or not math.isfinite(tts["volume"]) or not 0 < tts["volume"] <= 0.25):
            raise ValueError
        model = _text(tts["model"], 1024)
        if not Path(model).is_absolute() or not model.endswith(".onnx"):
            raise ValueError
        for key in ("model_sha256", "config_sha256"):
            if type(tts[key]) is not str or not re.fullmatch("[a-f0-9]{64}", tts[key]):
                raise ValueError
        return {"version": 1, "wake_phrases": canonical, "activation_color": color.lower(),
                "tts": dict(tts, model=model)}
    except (ValueError, TypeError, KeyError, OverflowError):
        raise SafetyError("Invalid assistant profile") from None


def route_transcript(profile: Any, transcript: Any) -> dict[str, Any] | None:
    """Return data only. Never treat this event or its text as action authority."""
    settings = validate_profile(profile)
    text = " ".join(_text(transcript, 4096).split())
    normalized = _normalized(text)
    for item in settings["wake_phrases"]:
        phrase = item["phrase"]
        if _prefix(normalized, phrase):
            # Match the original prefix by normalization, not casefolded string offsets
            # (casefold can expand a Unicode character into multiple characters).
            end = next(i for i in range(1, len(text) + 1)
                       if _normalized(text[:i]) == phrase)
            return {"kind": "transcript_route", "trusted": False, "route": item["route"],
                    "phrase": phrase, "text": text[end:].lstrip(" ,:;.!?—-"),
                    "activation_color": settings["activation_color"], "tts_backend": "piper"}
    return None


def synthesize_reply(profile: Any, text: Any) -> bytes:
    """Explicit local Piper synthesis only; never called by routing or activation.

    Returns in-memory WAV, not playback. Existing local_tts enforces model/config
    pins, trusted files, Russian voice and bounded quiet audio; no cloud fallback.
    """
    from alice_mcp.local_tts import synthesize

    tts = validate_profile(profile)["tts"]
    return synthesize(_text(text, 500), Path(tts["model"]), tts["model_sha256"],
                      tts["config_sha256"], volume=tts["volume"])


def _private_parent(path: Path) -> int:
    """Open pinned, non-symlink ancestors and require an owner-only final directory."""
    if os.name != "posix" or not path.is_absolute() or ".." in path.parts:
        raise SafetyError("Private persistence requires an absolute POSIX path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if (info.st_uid not in {0, os.getuid()}
                    or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
                raise SafetyError("Untrusted profile directory")
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SafetyError("Profile directory must be owner-only")
        return fd
    except BaseException:
        os.close(fd)
        raise


def save_profile(path: Path, profile: Any) -> None:
    """Atomically CREATE a new private file; never replace inventory or existing files."""
    data = json.dumps(validate_profile(profile), ensure_ascii=False).encode("utf-8")
    try:
        parent = _private_parent(path)
        temporary = ".assistant-" + secrets.token_hex(16)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent,
                        follow_symlinks=False)
                os.fsync(parent)
            finally:
                os.unlink(temporary, dir_fd=parent)
        finally:
            os.close(parent)
    except OSError:
        raise SafetyError("Could not create private assistant profile") from None


def load_profile(path: Path) -> dict[str, Any]:
    """Read one bounded owner-only regular file; no environment/inventory lookup."""
    try:
        parent = _private_parent(path)
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise SafetyError("Assistant profile must be an owner-only regular file")
            data = source.read(16385)
        if len(data) > 16384:
            raise ValueError
        return validate_profile(json.loads(data, object_pairs_hook=_unique))
    except (OSError, ValueError, UnicodeError):
        raise SafetyError("Could not load private assistant profile") from None
