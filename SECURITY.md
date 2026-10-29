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
  testing this repository. The fixed bundled runtime helper is not a user-supplied
  shell or a downloaded script. Keep Quasar enabled; no rooting or installation is
  an implicit prerequisite.
- Read-only behavior is the default. Never infer write permission from a skill,
  a reference page, or a read approval. Service and experimental device actions
  require explicit local policy, station/configuration/state-bound previews and
  native human elicitation unless the bounded action is explicitly delegated in
  private `controls.preauthorized_actions`. Only `led.effect`, `audio.volume` and
  `audio.play` can be delegated through that list and must also be in `allowed_actions`.
  Alternatively, station-level `preauthorize_all_actions=true` explicitly delegates
  every already-permitted service/device action, including capture, TTS and persistent
  edits, without native forms. It defaults to false, is for trusted local agents only
  and does not expand action/asset/option allowlists or authorize unrelated tasks.
  This local delegation persists until removed; choose the volume ceiling and registered
  media for unattended use. It does not enable writes or weaken validation/state checks.
  Previews expire after 300 seconds and are consumed
  once, including on cancellation. Never retry uncertain writes.
- POSIX SSH files and ancestors reject symlinks, untrusted owners and group/world
  writability except trusted sticky directories. Private identities reject all
  group/other access. Preview binding includes identity ctime and other metadata
  rather than reading key material; this is not a cryptographic key-identity
  attestation or protection against root/compromised local accounts. Native
  Windows service and device writes are unavailable until equivalent ACL checks exist.
- Service post-checks establish command success and launcher presence only; a
  no-op restart can also satisfy them. `restart_verified=false` discloses this
  boundary. Device file/control post-checks do not establish visible/audible
  effects, backend compatibility or config reload; `effect_observed` and
  `physical_effects_verified` remain false.
- Use generated fixtures and mocks, not live stations, for automated tests.
  Passing mocked tests does not establish device compatibility or safety.

These are requirements and guidance, not a claim that all transports, guarded
writes, rooting, recovery, or firmware procedures are implemented or verified.
Check the current API and capability descriptions before use. Unsupported
actions must remain unavailable rather than falling back to unrestricted SSH.

## Optional resident transport

The [native agent](docs/NATIVE_AGENT.md) exposes only fixed MCP tools over TLS 1.3
with a private CA and a per-device bearer file. Keep it on an owner-controlled
network, not a public interface or port-forwarded root service. It is not a
multi-tenant sandbox. Local root or a stolen bearer can exercise its privately
allowed actions. Rotate/revoke the credential in owner-only configuration;
it is re-read on requests and bound into action policy snapshots.

The host router intersects its own action allowlist with device policy; native
failures never fall back to SSH. Existing SSH is retained only for owner-explicit
provisioning/recovery. The operator-only installer accepts pinned project-built
artifacts and a canonical bounded archive, consumes a five-minute state-bound
preview once, and refuses existing destinations. Its separate startup hook does
not edit vendor launch profiles, restart Quasar, remount filesystems or change
SELinux. No automatic update/reinstallation endpoint exists. Persistence was
registered, not verified by a reboot. See the native transport's narrower evidence
and authorization limits before deployment.

The optional source-tested local speech mode accepts only bounded canonical WAV
over the authenticated resident channel, never a URL or executable. A hash-pinned
playback child cannot select a capture device or modify the mixer. Local-only
selection blocks cloud text TTS; host synthesis failure never triggers a fallback.
Audio/text remain in RAM. This mode has not yet been installed or qualified on a
Station; ALSA drain/child exit are not evidence of audible output.

## Experimental runtime controls

The [control contract](docs/CONTROLS.md) documents the implemented bounded API,
not a hardware qualification. Automated integration checks are offline/synthetic;
scoped live speech/native-agent observations are documented separately.
`get_model_interfaces` is source evidence;
`get_station_controls` returns declared policy aliases, not verified availability.
`preview_station_action` is read-only remote inspection, not write authorization.

Device actions share the service controller's secure SSH, approval registry,
state/trust revalidation and serialization. They require the global write opt-in,
per-station action allowlist and reviewed `/system/vendor/quasar/maind` SHA-256.
The helper needs Python 3.8+ already installed at one of its fixed candidate paths.
It never installs packages, roots the device, opens ADB or accepts arbitrary shell,
file path, URL, GPIO, I2C, memory or register-write arguments. Unsupported drivers,
unknown frame formats and unexpected firmware fail closed. Quasar stays enabled.
Bounded playback may stop only the process group that the helper itself started,
never all `maind` processes.
Playback consumes an unlinked owner-only snapshot of the hash-verified media through
an inherited descriptor, so replacing the asset path cannot change those bytes.
The native player/procfs descriptor path is not yet qualified on real firmware;
there is no unchecked-path fallback.

Private `controls` policy and backups may contain sensitive data. Keep policy
outside Git and chat, use nonsensitive aliases, and never publish endpoint values
or keys. JSON config adapters select an existing leaf from operator-reviewed
bindings; they are not certified vendor schemas, backend installation or universal
native Alice voice replacement. Native `quasar.cfg` bindings are limited to
`uniProxyUrl` and `uniProxyQuicUrl`. HTTPS/WSS is the default; HTTP/WS needs explicit
per-binding `allow_insecure` consent. Credentials, query strings and fragments are
forbidden even with that opt-in. Other trust-store/DNS changes remain guidance-only.

Uploads are limited to 49,152 decoded bytes and a preregistered asset SHA-256,
not an arbitrary transfer facility. Before persistent edits, owner-only records
are stored at `/data/opt/alice-mcp/backups/<id>.json`. Restore compares the current
target with the recorded post-edit state and refuses changes it observes. It may
delete a newly created file only under that file's own restore record. There is
no broad rollback or factory reset. Keep an independent owner-managed recovery
path; a local restore record is not a full backup strategy.

State checks are optimistic, not atomic compare-and-swap against external writers.
The remote `flock` serializes cooperating helper invocations only. An external
writer can race after the last comparison and before replace, deletion or mixer
mutation; its update can be lost even when the post-check passes. Keep external
file/mixer writers idle during apply/restore, or do not apply. Never stop Quasar
to obtain exclusivity. Hard protection requires a transactional target interface
or a lock honored by every writer; this runtime does not provide that guarantee.
Synthetic race-characterization tests demonstrate the gap rather than solve it.

Rootfs edits fail on read-only mounts by default. Only explicit per-station
`allow_rootfs_remount=true` permits guarded temporary read-write mounting, followed
by restoration of the original read-only state. A failed action or remount cleanup
can have an `unknown` outcome: inspect locally and never auto-retry. Transient
effects restore captured state, but an interrupted effect is not proof that cleanup
finished. Configuration changes never imply reload; service restart needs its own
preview and approval (or explicit standing preauthorization) and may interrupt voice/playback.

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
