# Security policy

## Threat model and boundaries

Alice MCP is a local development preview for an owner-authorized, trusted
process. Anyone who can run it or modify its configuration has the privileges
of that local account. It is not a multi-tenant service, a sandbox for hostile
clients, or an authorization boundary against a compromised workstation.
MCP clients and model-generated requests are not evidence of device ownership.
Source catalog entries and third-party guides are reference material, not
permission to execute commands or proof of model/firmware compatibility.

Store private configuration, station inventories, addresses, credentials,
known-host records, and SSH identity files outside the checkout and distribution
packages. Use restrictive local file permissions. Use synthetic aliases in
examples and tests; do not paste device output into issues, prompts, or fixtures.
Never log raw SSH stdout/stderr, command output, configuration, or secret values.
Log only bounded, reviewed status/error identifiers and synthetic station aliases.

## Access and development rules

- Require owner authorization, pinned host keys, and key-only SSH. Reject unknown
  or changed host keys; never silently enroll keys or disable host-key checking.
- Password authentication, credential extraction, network discovery, arbitrary
  shell execution, and downloaded-script execution are outside the supported
  development workflow. Do not use ADB or change a station while developing or
  testing this repository. Keep Quasar enabled.
- Read-only behavior is the default. Never infer write permission from a skill,
  a reference page, or a read approval. Quasar start/restart is explicitly opt-in,
  binds previews to station/configuration/state and requires native human
  elicitation. Approvals expire, are single-use and never retry uncertain writes.
- POSIX SSH files and ancestors reject symlinks, untrusted owners and group/world
  writability except trusted sticky directories. Private identities reject all
  group/other access. Preview binding includes identity ctime and other metadata
  rather than reading key material; this is not a cryptographic key-identity
  attestation or protection against root/compromised local accounts. Native
  Windows service writes are unavailable until equivalent ACL checks exist.
- Post-checks establish command success and launcher presence only; a no-op
  restart can also satisfy them. `restart_verified=false` discloses this boundary.
- Use generated fixtures and mocks, not live stations, for automated tests.
  Passing mocked tests does not establish device compatibility or safety.

These are requirements and guidance, not a claim that all transports, guarded
writes, rooting, recovery, or firmware procedures are implemented or verified.
Check the current API and capability descriptions before use. Unsupported
actions must remain unavailable rather than falling back to unrestricted SSH.

## Publication allowlist and checks

`scripts/check_public.py` uses only the Python standard library. Its source
allowlist is intentionally explicit:

- Root: `README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`,
  `LICENSE`, `LICENSE.md`, `LICENSE.txt`, `NOTICE`, `NOTICE.md`, `.gitignore`,
  `.gitattributes`, `.editorconfig`, `pyproject.toml`, and `MANIFEST.in`.
- `src/alice_mcp/`: Python and JSON; Markdown only inside `skills/` or
  `data/skills/`.
- `docs/` and `skills/`: Markdown only.
- `tests/` and `scripts/`: Python only.
- `.github/workflows/`: immediate YAML files only.

Private paths, inventories, environment files (including example environment
files), SSH configuration/key files, raw logs/dumps, symlinks, special files,
non-UTF-8/binary files, and files outside this allowlist fail publication.
The scanner detects address literals, MACs, private-key headers, credential
assignments, common high-entropy provider tokens, home paths, and HTTP credential
or secret-query values. Obvious placeholders are permitted for credential values;
this does not exempt addresses or private-key material. Synthetic leak tests
assemble their fixtures from pieces so the tests remain publishable themselves.

```sh
python scripts/check_public.py                 # index blobs AND tracked worktree files
python scripts/check_public.py --tree          # pre-staging, including untracked files
python -m build
python scripts/check_public.py --archive dist/*.whl dist/*.tar.gz
```

Default mode does not inspect untracked files or Git history. Tree mode omits
Git administration, virtualenvs, caches, build outputs, distribution outputs,
and generated egg-info directories; tracked files receive no such exclusions.
Archive mode examines members without extracting them, checks package paths
and specific generated distribution metadata, and rejects traversal, links,
encryption, duplicate files, binary members, and oversized inputs. Generated
metadata is still scanned for secrets. Diagnostics are only sanitized relative
path, line number, and rule; never the matching content or exception text.

Run pytest, Ruff, mypy, source checks, and both archive checks before release.
Also run a separately installed, reviewed secret scanner such as Gitleaks against
the staged change and complete history; this repository does not automatically
download or execute it. Review the exact publishable file set and artifact
contents. Never use blanket staging as a substitute for that review.

No scanner can prove absence of secrets. Encoding, split strings, unfamiliar
credential formats, innocuous-looking identifiers, and allowed prose can evade
heuristics. False positives require replacing sensitive examples or improving a
reviewed rule, not adding broad ignore directives. These checks are not atomic
against concurrent filesystem changes: scan again after the final modification.
They do not scrub Git history or revoke leaked credentials.

## Reporting a vulnerability or accidental disclosure

Contact the maintainer through an available private security-reporting channel.
If none is advertised, request a private contact without including exploit
details, station identifiers, command output, or credentials in a public issue.
Do not upload device captures to demonstrate a report.

If a secret is exposed, revoke or rotate it first, restrict further disclosure,
and coordinate removal from published artifacts and history. Removing a file or
passing this scanner does not make the exposed value safe again. No response
time or supported-release guarantee is currently promised for this preview.
