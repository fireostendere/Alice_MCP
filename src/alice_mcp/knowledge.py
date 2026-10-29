"""Bundled source metadata and original operational skills; no network fetching."""

import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from alice_mcp.core import SafetyError


def catalog() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        files("alice_mcp").joinpath("data/catalog.json").read_text(encoding="utf-8")
    )
    return data


def search_knowledge(query: str = "", limit: int = 10) -> dict[str, Any]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise SafetyError("Limit must be between 1 and 50")
    if len(query) > 256:
        raise SafetyError("Search query too long")
    words = query.casefold().split()
    pages = catalog()["pages"]
    matches = [page for page in pages if all(
        word in json.dumps(page, ensure_ascii=False).casefold() for word in words
    )]
    return {"evidence_class": "document", "total": len(matches), "pages": matches[:limit]}


def get_knowledge(page_id: str) -> dict[str, Any]:
    data = catalog()
    for page in data["pages"]:
        if page["id"] == page_id:
            return {"evidence_class": "document", "page": page, "upstream": data["upstream"]}
    raise SafetyError("Unknown source page")


def skill_root() -> Path:
    packaged = Path(str(files("alice_mcp").joinpath("data/skills")))
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "skills"


def list_skills() -> dict[str, Any]:
    skills = []
    pages = catalog()["pages"]
    for path in sorted(skill_root().glob("*/SKILL.md")):
        source_pages = [page["id"] for page in pages if path.parent.name in page["skills"]]
        skills.append({"name": path.parent.name, "source_pages": source_pages})
    return {"evidence_class": "document", "skills": skills}


def read_skill(name: str, reference: str = "SKILL.md") -> dict[str, Any]:
    if not re.fullmatch(r"station-[a-z0-9-]+", name):
        raise SafetyError("Unknown skill")
    if reference != "SKILL.md" and not re.fullmatch(r"references/[a-z0-9-]+\.md", reference):
        raise SafetyError("Unknown skill reference")
    root = (skill_root() / name).resolve()
    path = (root / reference).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise SafetyError("Unknown skill reference")
    return {"evidence_class": "document", "name": name, "reference": reference,
            "content": path.read_text(encoding="utf-8")}
