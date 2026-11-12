"""Offline contract checks for the source catalog and portable skills."""

import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/alice_mcp/data/catalog.json"
REVISION = "520fbccd68cb90a8c4f01d06bf38d9a7db915304"
EXPECTED_PATHS = {
    "docs/contributing.md",
    "docs/contributors.md",
    "docs/devices/fiero-hi.md",
    "docs/devices/index.md",
    "docs/devices/jbl-link-music.md",
    "docs/devices/jbl-link-portable.md",
    "docs/devices/station-2.md",
    "docs/devices/station-3.md",
    "docs/devices/station-gen1.md",
    "docs/devices/station-lite.md",
    "docs/devices/station-lite2.md",
    "docs/devices/station-max-zigbee.md",
    "docs/devices/station-max.md",
    "docs/devices/station-midi.md",
    "docs/devices/station-mini.md",
    "docs/devices/station-mini2.md",
    "docs/devices/station-mini3-pro.md",
    "docs/devices/station-mini3.md",
    "docs/devices/station-street.md",
    "docs/devices/yandex-hub.md",
    "docs/edit.md",
    "docs/glossary.md",
    "docs/index.md",
    "docs/internals/architecture.md",
    "docs/internals/calls.md",
    "docs/internals/directives.md",
    "docs/internals/gotchas.md",
    "docs/internals/network.md",
    "docs/internals/telemetry.md",
    "docs/internals/toolchain.md",
    "docs/internals/voice.md",
    "docs/internals/zigbee.md",
    "docs/recipes/airplay.md",
    "docs/recipes/bekend-self-hosted.md",
    "docs/recipes/bt-proxy.md",
    "docs/recipes/fastfetch.md",
    "docs/recipes/home-assistant.md",
    "docs/recipes/index.md",
    "docs/recipes/led.md",
    "docs/recipes/llm.md",
    "docs/recipes/microphone.md",
    "docs/recipes/midipult.md",
    "docs/recipes/packagemanagers.md",
    "docs/recipes/quasard.md",
    "docs/recipes/sounds.md",
    "docs/recipes/spotify.md",
    "docs/recipes/standalone.md",
    "docs/recipes/station1-debian.md",
    "docs/recipes/station1-privacy.md",
    "docs/recipes/station1-soundcloud.md",
    "docs/recipes/station1-spotify.md",
    "docs/recipes/station1-tv.md",
    "docs/recipes/upravlenie-golosom-alisy.md",
    "docs/recipes/usb-host.md",
    "docs/recipes/v3rt3p.md",
    "docs/recipes/youtube-music.md",
    "docs/start/index.md",
    "docs/start/packages.md",
    "docs/start/recovery.md",
    "docs/start/retail-ca-mini3.md",
    "docs/start/retail-ca.md",
    "docs/start/root-android.md",
    "docs/start/root-jbl-link-music.md",
    "docs/start/root.md",
    "docs/start/ssh.md",
}
EXPECTED_SKILLS = {
    "station-access",
    "station-airplay",
    "station-bringup",
    "station-community",
    "station-control",
    "station-devices",
    "station-fastfetch",
    "station-gen1",
    "station-integrations",
    "station-internals",
    "station-led",
    "station-llm",
    "station-microphone",
    "station-packages",
    "station-recovery",
    "station-sounds",
    "station-standalone",
    "station-streaming",
    "station-usb",
    "station-yam",
}
CARRIED_REVISION = "1ab048bf95c0e5e547a1ff6c801d2ed9deb193db"


def load_catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def skill_text(name):
    directory = ROOT / "skills" / name
    return "\n".join(path.read_text(encoding="utf-8") for path in directory.rglob("*.md"))


def test_catalog_schema_and_complete_pinned_coverage():
    catalog = load_catalog()
    assert set(catalog) == {"schema", "upstream", "pages"}
    assert catalog["schema"] == "alice.catalog.v1"
    upstream = catalog["upstream"]
    assert set(upstream) == {"url", "revision", "retrieved_at", "scope", "license_note",
                             "carried"}
    assert upstream["url"] == "https://hackalice.me/"
    assert upstream["revision"] == REVISION
    assert upstream["retrieved_at"] == "2026-11-11"
    # Pages not re-read at this revision are named, never silently presented as current.
    carried = upstream["carried"]
    assert set(carried) == {"revision", "pages", "not_catalogued"}
    assert carried["revision"] == CARRIED_REVISION
    assert len(carried["pages"]) == 10 and len(carried["not_catalogued"]) == 8
    assert not set(carried["not_catalogued"]) & EXPECTED_PATHS
    assert "sitemap" in upstream["scope"].lower()
    assert "no explicit" in upstream["license_note"].lower()
    pages = catalog["pages"]
    assert len(pages) == len(EXPECTED_PATHS) == 65
    assert set(carried["pages"]) <= {page["id"] for page in pages}
    assert {page["source_path"] for page in pages} == EXPECTED_PATHS
    assert len({page["id"] for page in pages}) == 65
    assert len({page["url"] for page in pages}) == 65
    referenced = set()
    for page in pages:
        assert set(page) == {
            "id",
            "title",
            "url",
            "source_path",
            "skills",
            "summary",
            "compatibility",
            "risk",
            "evidence",
        }
        for field in ("id", "title", "url", "source_path", "summary", "compatibility"):
            assert isinstance(page[field], str) and page[field].strip()
        path = Path(page["source_path"]).relative_to("docs").with_suffix("").as_posix()
        expected_id = path.removesuffix("/index")
        assert page["id"] == expected_id
        suffix = "" if expected_id == "index" else expected_id + "/"
        assert page["url"] == "https://hackalice.me/" + suffix
        assert page["risk"] in {"read_only", "manual_change", "high_risk"}
        assert page["evidence"] == "upstream_documentation"
        assert isinstance(page["skills"], list) and page["skills"]
        assert len(set(page["skills"])) == len(page["skills"])
        assert set(page["skills"]) <= EXPECTED_SKILLS
        for name in page["skills"]:
            assert page["url"] in skill_text(name), (page["id"], name)
        referenced.update(page["skills"])
    assert referenced == EXPECTED_SKILLS


def test_skills_are_self_contained_and_portable():
    skill_paths = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert {path.parent.name for path in skill_paths} == EXPECTED_SKILLS
    for path in skill_paths:
        text = path.read_text(encoding="utf-8")
        frontmatter = re.match(r"\A---\nname: ([^\n]+)\ndescription: ([^\n]+)\n---\n", text)
        assert frontmatter, path
        assert frontmatter[1] == path.parent.name
        assert len(frontmatter[2]) > 30
        for heading in (
            "Prerequisites",
            "Diagnosis",
            "Preview and confirmation",
            "Backup and rollback",
            "Verification",
            "Limitations",
        ):
            assert "## " + heading in text, (path, heading)
        assert "owner authorization" in text.lower(), path
        assert "quasar enabled" in text.lower(), path
        assert "guidance-only" in text.lower(), path
        # References stay inside the skill tree when hatch maps it into package data.
        for target in re.findall(r"\]\(([^\s)]+)\)", text):
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            resolved = (path.parent / parsed.path).resolve()
            assert resolved.is_relative_to((ROOT / "skills").resolve()), (path, target)
            assert resolved.is_file(), (path, target)


def test_device_matrix_and_evidence_limits_are_present():
    matrix = ROOT / "skills/station-devices/references/models.md"
    text = matrix.read_text(encoding="utf-8").lower()
    for page in load_catalog()["pages"]:
        if page["source_path"].startswith("docs/devices/"):
            assert page["url"] in text
    for term in ("orion", "pickle", "bergamot", "cucumber", "saturn", "armv7", "conflict"):
        assert term in text
    sources = (ROOT / "docs/SOURCES.md").read_text(encoding="utf-8")
    assert REVISION in sources
    assert CARRIED_REVISION in sources
    assert "65" in sources and "73" in sources and "sitemap" in sources.lower()
    assert "not device tests" in sources.lower()


def test_material_contains_no_addresses_keys_or_executable_installers():
    paths = [CATALOG, ROOT / "docs/SOURCES.md", *(ROOT / "skills").rglob("*.md")]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", text), path
        for candidate in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
            try:
                ipaddress.IPv4Address(candidate)
            except ipaddress.AddressValueError:
                continue  # A firmware version is not a network address.
            raise AssertionError(f"Address literal in {path}")
        assert "BEGIN " + "PRIVATE KEY" not in text, path
        assert "BEGIN " + "OPENSSH PRIVATE KEY" not in text, path
        assert not re.search(r"(?:curl|wget|irm)\b[^\n]*\|\s*(?:sh|bash|iex)", text), path
        assert "#!/bin/" not in text, path
        assert not re.search(r"\bdevmem\s+0x", text), path
