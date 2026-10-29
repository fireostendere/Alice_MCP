"""Publication checks use generated, non-device fixtures only."""

from __future__ import annotations

import io
import os
import random
import string
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

from scripts.check_public import path_rule, scan_archive, scan_text

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "check_public.py"


def noise() -> str:
    return "".join(random.Random(17).sample(string.ascii_letters + string.digits, 48))


def ipv4() -> str:
    return ".".join(str(part) for part in (10, 23, 45, 67))


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        (ipv4(), "ip-address"),
        (".".join(str(part) for part in (93, 184, 216, 34)), "ip-address"),
        (":".join(("2001", "db8", "1", "2", "3", "4", "5", "6")), "ip-address"),
        (":".join(("fe80", "", "1")) + "%eth0", "ip-address"),
        (":".join(("2001", "db8", "", "1")) + ".", "ip-address"),
        (":" * 2 + "ffff:" + ipv4(), "ip-address"),
        (":".join(("02", "11", "22", "33", "44", "55")), "mac-address"),
        ("-".join(("02", "11", "22", "33", "44", "55")), "mac-address"),
        (".".join(("0211", "2233", "4455")), "mac-address"),
        ("-" * 5 + "BEGIN " + "OPENSSH PRIVATE KEY" + "-" * 5, "private-key"),
        ("-" * 5 + "BEGIN " + "RSA PRIVATE KEY" + "-" * 5, "private-key"),
        ('pass' + 'word = "' + noise() + '"', "credential-assignment"),
        ('{"access_' + 'token' + '": "' + noise() + '"}', "credential-assignment"),
        ('{"access' + 'Token' + '": "' + noise() + '"}', "credential-assignment"),
        ("API_" + "KEY=" + noise(), "credential-assignment"),
        ("sk" + "-proj-" + noise(), "provider-token"),
        ("gh" + "p_" + noise(), "provider-token"),
        ("github" + "_pat_" + noise(), "provider-token"),
        ("xox" + "b-" + noise(), "provider-token"),
        ("hf" + "_" + noise(), "provider-token"),
        ("AI" + "za" + noise(), "provider-token"),
        ("sk" + "_live_" + noise(), "provider-token"),
        ("y0" + "_" + noise(), "provider-token"),
        ("ey" + "J" + noise() + "." + noise() + "." + noise(), "provider-token"),
        ("/" + "home/" + "synthetic-user/file", "home-path"),
        ("/" + "Users/" + "synthetic-user/file", "home-path"),
        ("C:" + "\\Users\\" + "synthetic-user\\file", "home-path"),
        ("/mnt/" + "c/Users/" + "synthetic-user/file", "home-path"),
        ("https://" + "alice:" + noise() + "@example.invalid/", "http-credentials"),
        ("https://example.invalid/?api_" + "key=" + noise(), "http-query-secret"),
        ("https://example.invalid/?%74oken=" + noise(), "http-query-secret"),
        ("https://example.invalid/?api" + "Key=" + noise(), "http-query-secret"),
        ("https://example.invalid/?author" + "ization=" + noise(), "http-query-secret"),
    ],
)
def test_detects_synthetic_leaks(value: str, rule: str) -> None:
    findings = scan_text("README.md", "safe\n" + value + "\n")
    assert rule in {finding.rule for finding in findings}
    assert all(finding.line == 2 for finding in findings)


@pytest.mark.parametrize(
    "value",
    [
        "safe public text — no station data",
        'token = "<redacted>"',
        'password = "change-me"',
        'api_key = "${API_KEY}"',
        'client_secret = "your-secret-here"',
        "https://example.invalid/?page=1",
        "https://example.invalid/?token=REDACTED",
        "999.999.999.999",
    ],
)
def test_public_text_and_placeholders(value: str) -> None:
    assert scan_text("README.md", value) == []


def test_python_dynamic_credentials_are_not_literals() -> None:
    assert scan_text("src/alice_mcp/example.py", "token = os.environ[name]") == []
    assert scan_text("src/alice_mcp/example.py", "password: str") == []


def test_python_slices_are_not_ipv6_but_addresses_in_strings_comments_and_bad_code_are() -> None:
    address = ":".join(("1", "", "2"))
    source = f"result = frames[{address}]\n"
    assert scan_text("tests/example.py", source) == []
    assert scan_text("tests/example.py", f"я = frames[{address}]\n") == []
    assert scan_text("tests/example.py", "result = frames[" + ":" * 2 + "2]\n") == []
    for text in (f'value = "{address}"', f'# {address}',
                 f'value = frames["{address}":]', 'value = "https' + f'://[{address}]/"',
                 source + "invalid ("):
        assert "ip-address" in {item.rule for item in scan_text("tests/example.py", text)}
    assert "ip-address" in {item.rule for item in scan_text("README.md", source)}


def test_placeholder_prefix_does_not_exempt_arbitrary_credential() -> None:
    value = 'pass' + 'word = "test-' + noise() + '"'
    assert "credential-assignment" in {item.rule for item in scan_text("README.md", value)}


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "SECURITY.md",
        "pyproject.toml",
        ".gitignore",
        "src/alice_mcp/server.py",
        "src/alice_mcp/native/asr_once.c",
        "src/alice_mcp/native/vqe_audit.c",
        "src/alice_mcp/native/pcm_player.c",
        "src/alice_mcp/native/agent/go.mod",
        "src/alice_mcp/native/agent/main.go",
        "src/alice_mcp/native/agent/mcp.go",
        "src/alice_mcp/native/agent/runtime.go",
        "src/alice_mcp/native/agent/speech.go",
        "src/alice_mcp/native/agent/pcm.go",
        "src/alice_mcp/native/agent/mcp_test.go",
        "src/alice_mcp/native/agent/runtime_test.go",
        "src/alice_mcp/data/sources.json",
        "src/alice_mcp/skills/read-only/SKILL.md",
        "src/alice_mcp/data/skills/read-only/SKILL.md",
        "skills/read-only/SKILL.md",
        "docs/SOURCES.md",
        "scripts/check_public.py",
        "tests/test_public.py",
        ".github/workflows/ci.yml",
    ],
)
def test_explicit_source_allowlist(path: str) -> None:
    assert path_rule(path) is None


@pytest.mark.parametrize(
    "path",
    [
        "private/README.md",
        "local/README.md",
        "docs/inventories/example.md",
        "src/alice_mcp/devices.json",
        "src/alice_mcp/stations.json",
        "docs/.env",
        "docs/.env.example",
        "docs/ssh_config",
        "docs/id_ed25519",
        "docs/known_hosts",
        "docs/capture.log",
        "docs/capture.dump",
        "docs/key.pem",
        "extra.md",
        "src/unrelated/file.py",
        "src/alice_mcp/readme.md",
        "src/alice_mcp/native/other.c",
        "src/alice_mcp/native/asr-once-armhf",
        "src/alice_mcp/native/agent/agent",
        "src/alice_mcp/native/agent/unreviewed.go",
        ".github/workflows/nested/ci.yml",
        "../README.md",
        "/README.md",
        "docs/../README.md",
        "docs\\README.md",
    ],
)
def test_nonpublication_paths_are_rejected(path: str) -> None:
    assert path_rule(path) is not None


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory(prefix=".public-test-", dir=ROOT) as directory:
        yield Path(directory)


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_CEILING_DIRECTORIES": str(repo.parent)},
    )


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def test_git_scans_staged_blobs_and_worktree_not_untracked(repo: Path) -> None:
    git(repo, "init", "-q")
    source = repo / "README.md"
    source.write_text(ipv4(), encoding="utf-8")
    git(repo, "add", "README.md")
    source.write_text("safe", encoding="utf-8")
    result = run(repo)
    assert result.returncode == 1
    assert "README.md:1:ip-address" in result.stdout
    assert ipv4() not in result.stdout + result.stderr
    git(repo, "add", "README.md")
    (repo / "untracked.md").write_text(ipv4(), encoding="utf-8")
    assert run(repo).returncode == 0
    source.write_text(ipv4(), encoding="utf-8")
    assert run(repo).returncode == 1


def test_git_failure_has_no_traceback_or_environment_details(repo: Path) -> None:
    result = run(repo)
    assert result.returncode != 0
    assert "git-unavailable" in result.stdout
    assert not result.stderr
    assert str(repo) not in result.stdout


def test_tree_includes_untracked_and_excludes_build_caches(repo: Path) -> None:
    (repo / "README.md").write_text("safe", encoding="utf-8")
    for name in (".git", ".venv", "venv", "dist", "build", "__pycache__"):
        directory = repo / name
        directory.mkdir()
        (directory / "private.txt").write_text(ipv4(), encoding="utf-8")
    assert run(repo, "--tree").returncode == 0
    (repo / "README.md").write_text(ipv4(), encoding="utf-8")
    assert run(repo, "--tree").returncode == 1


def test_tree_rejects_symlinks_without_reading_target(repo: Path) -> None:
    (repo / "README.md").symlink_to("missing-sensitive-target")
    result = run(repo, "--tree")
    assert result.returncode == 1
    assert "symlink" in result.stdout
    assert "missing-sensitive-target" not in result.stdout + result.stderr


def test_tree_rejects_directory_symlinks(repo: Path) -> None:
    (repo / "docs").symlink_to("missing-sensitive-target", target_is_directory=True)
    assert "symlink" in run(repo, "--tree").stdout


def test_binary_and_unsafe_filename_are_not_printed(repo: Path) -> None:
    (repo / "README.md").write_bytes(b"\x00\xff")
    assert "binary" in run(repo, "--tree").stdout
    (repo / (ipv4() + ".md")).write_text("safe", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / ("prefix_" + ipv4() + ".txt")).write_text("safe", encoding="utf-8")
    (repo / "unsafe\nname.md").write_text("safe", encoding="utf-8")
    result = run(repo, "--tree")
    assert ipv4() not in result.stdout + result.stderr
    assert "unsafe\nname" not in result.stdout + result.stderr
    assert "<blocked-path>" in result.stdout


def wheel(repo: Path, members: dict[str, bytes]) -> Path:
    archive = repo / "synthetic.whl"
    with zipfile.ZipFile(archive, "w") as output:
        for name, data in members.items():
            output.writestr(name, data)
    return archive


def test_wheel_allows_only_package_and_specific_distribution_metadata(repo: Path) -> None:
    archive = wheel(
        repo,
        {
            "alice_mcp/server.py": b"# safe\n",
            "alice_mcp/skills/inspect/SKILL.md": b"# Read only\n",
            "alice_mcp/data/skills/inspect/SKILL.md": b"# Read only\n",
            "alice_mcp-0.1.0.dist-info/METADATA": b"Name: alice-mcp\n",
            "alice_mcp-0.1.0.dist-info/RECORD": b"alice_mcp/server.py,,\n",
            "alice_mcp-0.1.0.dist-info/licenses/LICENSE": b"MIT\n",
            "alice_station_mcp-0.1.0.dist-info/METADATA": b"Name: alice-station-mcp\n",
        },
    )
    assert scan_archive(archive) == []
    assert run(repo, "--archive", str(archive)).returncode == 0


@pytest.mark.parametrize(
    ("name", "data", "rule"),
    [
        ("../README.md", b"safe", "path-invalid"),
        ("/README.md", b"safe", "path-invalid"),
        ("alice_mcp/../server.py", b"safe", "path-invalid"),
        ("alice_mcp/devices.json", b"{}", "path-private"),
        ("alice_mcp/server.py", b"\x00", "binary"),
        ("alice_mcp/server.py", ipv4().encode(), "ip-address"),
        ("other/server.py", b"safe", "path-allowlist"),
        ("alice_mcp-0.1.0.dist-info/arbitrary.txt", b"safe", "path-allowlist"),
    ],
)
def test_archive_rejects_unsafe_members(
    repo: Path, name: str, data: bytes, rule: str
) -> None:
    findings = scan_archive(wheel(repo, {name: data}))
    assert rule in {finding.rule for finding in findings}


def test_zip_symlinks_are_not_followed(repo: Path) -> None:
    archive = repo / "synthetic.whl"
    info = zipfile.ZipInfo("alice_mcp/server.py")
    info.create_system = 3
    info.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(info, "missing-sensitive-target")
    assert "symlink" in {finding.rule for finding in scan_archive(archive)}


def test_encrypted_archive_is_rejected_before_read(repo: Path) -> None:
    archive = wheel(repo, {"alice_mcp/server.py": b"safe"})
    data = bytearray(archive.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        location = data.index(signature) + offset
        data[location] |= 1
    archive.write_bytes(data)
    assert "archive-encrypted" in {item.rule for item in scan_archive(archive)}


def test_sdist_metadata_and_source_are_allowed(repo: Path) -> None:
    archive = repo / "synthetic.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for name in (
            "README.md", "pyproject.toml", "setup.cfg", "PKG-INFO",
            "src/alice_mcp/server.py", "src/alice_mcp.egg-info/SOURCES.txt",
        ):
            info = tarfile.TarInfo("alice_mcp-0.1.0/" + name)
            info.size = len(b"safe\n")
            output.addfile(info, io.BytesIO(b"safe\n"))
    assert scan_archive(archive) == []


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_sdist_links_and_special_files_are_rejected(repo: Path, kind: bytes) -> None:
    archive = repo / "synthetic.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo("alice_mcp-0.1.0/README.md")
        info.type = kind
        info.linkname = "missing-sensitive-target"
        output.addfile(info)
    assert {item.rule for item in scan_archive(archive)} & {"symlink", "special-file"}


def test_corrupt_archive_fails_closed(repo: Path) -> None:
    archive = repo / "synthetic.whl"
    archive.write_bytes(b"not an archive")
    result = run(repo, "--archive", str(archive))
    assert result.returncode == 1
    assert "archive-unreadable" in result.stdout
    assert not result.stderr


def test_corrupt_compressed_payload_fails_without_traceback(repo: Path) -> None:
    archive = repo / "synthetic.whl"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("alice_mcp/server.py", b"safe" * 50)
    data = bytearray(archive.read_bytes())
    with zipfile.ZipFile(archive) as source:
        entry = source.infolist()[0]
        offset = entry.header_offset + 30 + len(entry.filename.encode()) + len(entry.extra)
    data[offset] = 255
    archive.write_bytes(data)
    result = run(repo, "--archive", str(archive))
    assert result.returncode == 1
    assert "archive-unreadable" in result.stdout
    assert not result.stderr


def test_distribution_metadata_is_scanned_not_exempted(repo: Path) -> None:
    archive = wheel(repo, {"alice_station_mcp-0.1.0.dist-info/METADATA": ipv4().encode()})
    assert "ip-address" in {item.rule for item in scan_archive(archive)}


def test_archive_file_symlinks_are_rejected(repo: Path) -> None:
    original = wheel(repo, {"alice_mcp/server.py": b"safe"})
    linked = repo / "linked.whl"
    linked.symlink_to(original.name)
    assert "symlink" in {item.rule for item in scan_archive(linked)}


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_archive_empty_private_directories_are_rejected(repo: Path, kind: str) -> None:
    if kind == "zip":
        archive = wheel(repo, {"alice_mcp/private/": b""})
    else:
        archive = repo / "synthetic.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            info = tarfile.TarInfo("alice_mcp-0.1.0/private")
            info.type = tarfile.DIRTYPE
            output.addfile(info)
    assert "path-private" in {item.rule for item in scan_archive(archive)}


def test_archive_directory_count_is_bounded(repo: Path) -> None:
    archive = wheel(repo, {f"alice_mcp/directory-{i}/": b"" for i in range(5001)})
    assert "size-limit" in {item.rule for item in scan_archive(archive)}


def test_repeated_archive_names_and_oversized_members_are_rejected(repo: Path) -> None:
    archive = wheel(repo, {"alice_mcp/server.py": b"safe"})
    with zipfile.ZipFile(archive, "a") as output:
        with pytest.warns(UserWarning):
            output.writestr("alice_mcp/server.py", b"safe")
        output.writestr("alice_mcp/big.py", b"a" * (2 * 1024 * 1024 + 1))
    rules = {item.rule for item in scan_archive(archive)}
    assert {"archive-duplicate", "size-limit"} <= rules


@pytest.mark.skipif(os.name == "nt", reason="Git symlink mode is POSIX-specific")
def test_git_index_symlink_is_rejected_even_if_worktree_is_regular(repo: Path) -> None:
    git(repo, "init", "-q")
    source = repo / "README.md"
    source.symlink_to("missing-sensitive-target")
    git(repo, "add", "README.md")
    source.unlink()
    source.write_text("safe", encoding="utf-8")
    assert "symlink" in run(repo).stdout
