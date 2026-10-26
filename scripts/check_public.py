#!/usr/bin/env python3
"""Fail-closed, stdlib-only source and distribution publication checks.

Reports contain relative paths, line numbers, and rule identifiers, never values.
This is a publication backstop, not a proof that arbitrary text is secret-free.
"""

from __future__ import annotations

import argparse
import ipaddress
import lzma
import math
import os
import re
import stat
import subprocess
import tarfile
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_MEMBERS = 5000
ROOT_FILES = {
    "README.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md",
    "LICENSE", "LICENSE.md", "LICENSE.txt", "NOTICE", "NOTICE.md",
    ".gitignore", ".gitattributes", ".editorconfig", "pyproject.toml", "MANIFEST.in",
}
TREE_EXCLUDES = {
    ".git", ".venv", "venv", ".tox", ".nox", "dist", "build", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache",
}
PRIVATE_DIRS = {
    "private", "local", "secrets", "captures", "dumps", "logs", "inventory",
    "inventories", "station-inventory", "station_inventory", "station-inventories",
    "station_inventories", ".ssh", ".aws", ".config",
}
PRIVATE_FILES = {
    "devices.json", "stations.json", "inventory.json", "station_inventory.json",
    "station-inventory.json", "ssh_config", "ssh-config", "authorized_keys",
}
PRIVATE_SUFFIXES = {
    ".pem", ".key", ".der", ".p12", ".pfx", ".log", ".dump", ".bin",
    ".pcap", ".pcapng", ".db", ".sqlite", ".sqlite3",
}
DIST_FILES = {
    "METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt",
    "PKG-INFO", "SOURCES.txt", "dependency_links.txt", "requires.txt",
    "not-zip-safe", "zip-safe", "licenses/LICENSE", "licenses/LICENSE.md",
    "licenses/LICENSE.txt", "licenses/NOTICE", "licenses/NOTICE.md",
}

IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
IPV6 = re.compile(
    r"(?<![\w:])(?:[a-fA-F0-9]{0,4}:){2,}[a-fA-F0-9:.]*"
    r"(?:%[a-zA-Z0-9_.-]+)?"
)
MAC = re.compile(
    r"(?<![\w])(?:[a-fA-F0-9]{2}[:-]){5}[a-fA-F0-9]{2}(?![\w])"
    r"|(?<![\w.])(?:[a-fA-F0-9]{4}\.){2}[a-fA-F0-9]{4}(?![\w.])"
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")
HOME = re.compile(
    r"(?i)(?<![\w])(?:/(?:home|users)/[^/\s'\"<>]+|/(?:root)(?:/|\b)"
    r"|/mnt/[a-z]/(?:users)/[^/\s'\"<>]+"
    r"|[a-z]:[\\/](?:users|documents and settings)[\\/][^\\/\s'\"<>]+)"
)
CREDENTIAL = re.compile(
    r"(?i)(?<![\w])[\"']?(?:[a-z0-9]+[_-])*"
    r"(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|"
    r"(?:access|refresh|id)[_-]?token|client[_-]?secret|authorization)"
    r"[\"']?\s*(?:=|:)\s*"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s,;#}\]]+)"
)
PROVIDER = re.compile(
    r"(?:sk-(?:proj-|ant-)?|gh[pousr]_|github_pat_|glpat-|"
    r"xox[baprs]-|hf_|npm_|y0_|ya29\.|SG\.|(?:sk|rk)_(?:live|test)_)[a-zA-Z0-9_.-]{20,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}(?![\w])"
    r"|AIza[a-zA-Z0-9_-]{30,}"
    r"|eyJ[a-zA-Z0-9_-]{12,}\.[a-zA-Z0-9_-]{12,}\.[a-zA-Z0-9_-]{12,}"
)
HTTP = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
QUERY_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:(?:api|access|refresh|client|oauth)[_-]?)?"
    r"(?:key|token|secret|password|passwd|signature|credential|authorization|auth|sig)"
    r"(?:$|[_-])"
)


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    rule: str


def valid_path(path: str) -> bool:
    return bool(
        path
        and not path.startswith("/")
        and "\\" not in path
        and ":" not in path
        and all(part not in {"", ".", ".."} for part in path.split("/"))
        and re.fullmatch(r"[a-zA-Z0-9_./-]+", path)
    )


def private_path(path: str) -> bool:
    parts = path.lower().split("/")
    return any(
        part in PRIVATE_DIRS
        or part in PRIVATE_FILES
        or part.startswith((".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
                            "known_hosts", "authorized_keys"))
        or Path(part).suffix in PRIVATE_SUFFIXES
        for part in parts
    )


def source_allowed(path: str) -> bool:
    parts = path.split("/")
    suffix = Path(path).suffix
    if len(parts) == 1:
        return path in ROOT_FILES
    if parts[0] in {"tests", "scripts"}:
        return suffix == ".py"
    if parts[0] in {"docs", "skills"}:
        return suffix == ".md"
    if parts[:2] == [".github", "workflows"]:
        return len(parts) == 3 and suffix in {".yml", ".yaml"}
    if parts[:2] == ["src", "alice_mcp"] and len(parts) > 2:
        return suffix in {".py", ".json"} or (
            (parts[2] == "skills" or parts[2:4] == ["data", "skills"])
            and suffix == ".md"
        )
    return False


def path_rule(path: str) -> str | None:
    if not valid_path(path):
        return "path-invalid"
    if private_path(path):
        return "path-private"
    return None if source_allowed(path) else "path-allowlist"


def placeholder(value: str) -> bool:
    value = value.strip().lower()
    if value in {"", "none", "null", "true", "false", "str", "bytes"}:
        return True
    if re.fullmatch(r"\$\{[a-z_][a-z0-9_]*\}", value):
        return True
    value = value.strip("<>[]")
    return bool(re.fullmatch(
        r"(?:(?:redacted|placeholder|example|sample|dummy|synthetic|test|your)"
        r"(?:[-_ ](?:api|access|client|key|token|password|secret|value|here|only|fixture))*"
        r"|changeme|change-me|replace-me|not-a-secret|not-used|unused)",
        value,
    ))


def entropy(value: str) -> float:
    return -sum(
        (count / len(value)) * math.log2(count / len(value))
        for count in Counter(value).values()
    )


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), 1):
        rules: set[str] = set()
        for pattern in (IPV4, IPV6):
            for match in pattern.finditer(line):
                try:
                    ipaddress.ip_address(match.group().split("%", 1)[0].rstrip("."))
                except ValueError:
                    continue
                rules.add("ip-address")
        for pattern, rule in (
            (MAC, "mac-address"), (PRIVATE_KEY, "private-key"), (HOME, "home-path")
        ):
            if pattern.search(line):
                rules.add(rule)
        for match in CREDENTIAL.finditer(line):
            value = match["value"]
            if path.endswith(".py") and value[0] not in "\"'":
                continue  # Python names/expressions are not hardcoded credentials.
            if not placeholder(value.strip("\"'")):
                rules.add("credential-assignment")
        if any(entropy(match.group()) >= 3.3 for match in PROVIDER.finditer(line)):
            rules.add("provider-token")
        for match in HTTP.finditer(line):
            try:
                url = urlsplit(match.group().rstrip(".,);]"))
                if url.username is not None or url.password is not None:
                    rules.add("http-credentials")
                for name, value in parse_qsl(url.query, keep_blank_values=True):
                    if QUERY_KEY.search(name) and not placeholder(value):
                        rules.add("http-query-secret")
            except ValueError:
                rules.add("http-invalid")
        findings.extend(Finding(path, number, rule) for rule in sorted(rules))
    return findings


def scan_bytes(path: str, data: bytes) -> list[Finding]:
    if len(data) > MAX_FILE:
        return [Finding(path, 0, "size-limit")]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [Finding(path, 0, "binary")]
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text):
        return [Finding(path, 0, "binary")]
    return scan_text(path, text)


def file_findings(root: Path, path: str) -> list[Finding]:
    if not valid_path(path):
        return [Finding(path, 0, "path-invalid")]
    candidate = root
    try:
        for part in path.split("/"):
            candidate /= part
            if candidate.is_symlink():
                return [Finding(path, 0, "symlink")]
        if rule := path_rule(path):
            return [Finding(path, 0, rule)]
        mode = candidate.stat().st_mode
        if not stat.S_ISREG(mode):
            return [Finding(path, 0, "special-file")]
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            return scan_bytes(path, source.read(MAX_FILE + 1))
    except OSError:
        return [Finding(path, 0, "file-unreadable")]


def scan_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []

    def walk_error(error: OSError) -> None:
        findings.append(Finding("<tree>", 0, "tree-unreadable"))

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        base = Path(directory)
        for name in list(dirs):
            candidate = base / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                findings.append(Finding(relative, 0, "symlink"))
                dirs.remove(name)
            elif name in TREE_EXCLUDES or name.endswith(".egg-info"):
                dirs.remove(name)
            elif private_path(relative):
                findings.append(Finding(relative, 0, "path-private"))
                dirs.remove(name)
        for name in files:
            if name == ".git":  # A worktree's Git administrative pointer.
                continue
            findings.extend(file_findings(root, (base / name).relative_to(root).as_posix()))
    return findings


def git_output(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
    ).stdout


def scan_git(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        top = git_output(root, "rev-parse", "--show-toplevel").decode().strip()
        root = Path(top)
        entries = git_output(root, "ls-files", "--stage", "-z").split(b"\0")
        for entry in entries:
            if not entry:
                continue
            fields, raw_path = entry.split(b"\t", 1)
            mode, object_id, stage = fields.decode("ascii").split()
            path = raw_path.decode("utf-8", errors="replace")
            if rule := path_rule(path):
                findings.append(Finding(path, 0, rule))
                continue
            if mode == "120000":
                findings.append(Finding(path, 0, "symlink"))
                continue
            if mode not in {"100644", "100755"} or stage != "0":
                findings.append(Finding(path, 0, "git-special-entry"))
                continue
            size = int(git_output(root, "cat-file", "-s", object_id))
            if size > MAX_FILE:
                findings.append(Finding(path, 0, "size-limit"))
            else:
                findings.extend(scan_bytes(path, git_output(root, "cat-file", "blob", object_id)))
            if (root / path).exists() or (root / path).is_symlink():
                findings.extend(file_findings(root, path))
    except (OSError, subprocess.CalledProcessError, UnicodeError, ValueError):
        findings.append(Finding("<git>", 0, "git-unavailable"))
    return findings


def archive_path_rule(path: str, wheel: bool) -> str | None:
    if not valid_path(path):
        return "path-invalid"
    if private_path(path):
        return "path-private"
    parts = path.split("/")
    if wheel:
        if re.fullmatch(
            r"alice_(?:station_)?mcp-[0-9][a-zA-Z0-9_.+!-]*\.dist-info", parts[0]
        ):
            return None if "/".join(parts[1:]) in DIST_FILES else "path-allowlist"
        if parts[0] == "alice_mcp" and len(parts) > 1:
            return path_rule("src/" + path)
        return "path-allowlist"
    if len(parts) < 2 or not re.fullmatch(
        r"alice[-_](?:station[-_])?mcp-[0-9][a-zA-Z0-9_.+!-]*", parts[0]
    ):
        return "path-allowlist"
    relative = "/".join(parts[1:])
    if relative in {"PKG-INFO", "setup.cfg"}:
        return None
    if relative.startswith("src/alice_mcp.egg-info/"):
        return None if "/".join(parts[3:]) in DIST_FILES else "path-allowlist"
    return path_rule(relative)


def scan_archive(archive: Path) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    total = 0
    limit_hit = False
    file_count = 0

    def check(name: str, size: int, special: str | None, directory: bool = False) -> bool:
        nonlocal total, limit_hit, file_count
        total += size
        if directory:
            rule = None if valid_path(name) else "path-invalid"
            if private_path(name):
                rule = "path-private"
        else:
            file_count += 1
            rule = archive_path_rule(name, archive.suffix == ".whl")
        rule = special or rule
        if name in seen:
            rule = "archive-duplicate"
        seen.add(name)
        if size > MAX_FILE or total > MAX_TOTAL or len(seen) > MAX_MEMBERS:
            rule = "size-limit"
            limit_hit = True
        if rule:
            findings.append(Finding(name, 0, rule))
        return rule is None

    try:
        if archive.is_symlink() or any(parent.is_symlink() for parent in archive.parents):
            return [Finding("<archive>", 0, "symlink")]
        if not archive.is_file():
            return [Finding("<archive>", 0, "archive-unreadable")]
        if archive.stat().st_size > MAX_TOTAL:
            return [Finding("<archive>", 0, "size-limit")]
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as source:
                for member in source.infolist():
                    name = member.filename.rstrip("/")
                    mode = member.external_attr >> 16
                    special = None
                    if member.flag_bits & 1:
                        special = "archive-encrypted"
                    elif stat.S_ISLNK(mode):
                        special = "symlink"
                    elif stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                        special = "special-file"
                    accepted = check(name, member.file_size, special, member.is_dir())
                    if limit_hit:
                        break
                    if accepted and not member.is_dir():
                        with source.open(member) as data:
                            findings.extend(scan_bytes(name, data.read(MAX_FILE + 1)))
        else:
            with tarfile.open(archive, "r:*") as tar_source:
                for tar_member in tar_source:
                    name = tar_member.name.rstrip("/")
                    special = None
                    if tar_member.issym() or tar_member.islnk():
                        special = "symlink"
                    elif not tar_member.isfile() and not tar_member.isdir():
                        special = "special-file"
                    accepted = check(name, tar_member.size, special, tar_member.isdir())
                    if limit_hit:
                        break
                    if accepted and not tar_member.isdir():
                        tar_data = tar_source.extractfile(tar_member)
                        if tar_data is None:
                            findings.append(Finding(name, 0, "archive-unreadable"))
                        else:
                            with tar_data:
                                findings.extend(scan_bytes(name, tar_data.read(MAX_FILE + 1)))
    except (OSError, tarfile.TarError, zipfile.BadZipFile, RuntimeError,
            NotImplementedError, ValueError, EOFError, zlib.error, lzma.LZMAError):
        findings.append(Finding("<archive>", 0, "archive-unreadable"))
    if not file_count and not findings:
        findings.append(Finding("<archive>", 0, "archive-empty"))
    return findings


def safe_display(path: str) -> str:
    if path in {"<git>", "<tree>", "<archive>"}:
        return path
    if not valid_path(path) or scan_text("<path>", path):
        return "<blocked-path>"
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tree", action="store_true", help="include untracked source files")
    mode.add_argument("--archive", type=Path, nargs="+", help="check wheels or sdists")
    args = parser.parse_args()
    if args.archive:
        findings = [item for archive in args.archive for item in scan_archive(archive)]
    elif args.tree:
        findings = scan_tree(Path.cwd())
    else:
        findings = scan_git(Path.cwd())
    for path, line, rule in sorted({
        (safe_display(item.path), item.line, item.rule) for item in findings
    }):
        print(f"{path}:{line}:{rule}")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
