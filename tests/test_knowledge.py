import pytest

from alice_mcp import knowledge
from alice_mcp.core import SafetyError


def test_catalog_search_and_skill_references() -> None:
    result = knowledge.search_knowledge("", 50)
    assert result["total"] == len(knowledge.catalog()["pages"])
    assert result["evidence_class"] == "document"
    for entry in result["pages"]:
        assert knowledge.get_knowledge(entry["id"])["page"] == entry
    skills = knowledge.list_skills()["skills"]
    assert skills
    for skill in skills:
        assert knowledge.read_skill(skill["name"])["content"].startswith("---\n")
        assert skill["source_pages"]


@pytest.mark.parametrize("name,reference", [
    ("../outside", "SKILL.md"), ("station-access", "../../pyproject.toml"),
    ("station-access", "/etc/passwd"), ("station-nonexistent", "SKILL.md"),
])
def test_no_arbitrary_file_reads(name: str, reference: str) -> None:
    with pytest.raises(SafetyError):
        knowledge.read_skill(name, reference)


@pytest.mark.parametrize("limit", [0, 51, True])
def test_search_limit_validation(limit: int) -> None:
    with pytest.raises(SafetyError):
        knowledge.search_knowledge("", limit)


def test_search_query_limit_and_unknown_page() -> None:
    with pytest.raises(SafetyError):
        knowledge.search_knowledge("x" * 257)
    with pytest.raises(SafetyError):
        knowledge.get_knowledge("missing")
