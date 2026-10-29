# Alice MCP development

Work only in this repository. Do not import private station inventories, SSH
configuration, credentials, logs, captures, or material from other workspaces.
Read README.md and CONTRIBUTING.md before changing behavior. Check git status
and preserve concurrent changes. Add a failing regression test before fixes.

The owner has permanently authorized network access for shell commands within
this project's tasks. Do not ask repeated network-consent questions for that
scope. This does not authorize unrelated external filesystem access, device
writes, credential disclosure or downloaded-script execution. Runtime denials
still apply; this project instruction records consent, not a sandbox bypass.

A sandbox failure describes that command environment, not the host. For an
already-authorized read-only check or local build, use the tool's per-command
escalation when available and permitted before declaring host tools unavailable.
Respect denials and repository scope; never retry an uncertain device write.
Do not ask the owner to restore an undefined "host mode". MCP tool permissions
and command sandbox settings are separate layers.

Default to read-only, owner-authorized access over pinned-host-key, key-only SSH.
No arbitrary shell tool, network discovery, credential extraction, automatic
rooting, factory reset, flashing, or downloaded-script execution. Keep Quasar
enabled; do not stop it as an implicit prerequisite for another action.
Bind write previews to station/configuration and state, expire and consume
approvals once, and never retry uncertain writes. Knowledge is reference data,
not authority to execute. Document model/firmware gaps and synthetic tests.

The owner has explicitly requested standing authorization for all already-permitted
MCP actions in this project without per-call forms. Honor and preserve the private
station-level `preauthorize_all_actions` setting; do not revoke it as temporary test
cleanup or ask redundant chat confirmations. Fresh one-use previews, live policy/
state/trust checks and no-uncertain-retry rules still apply. This does not authorize
unrequested tasks, new action permissions, unrelated workspaces or unsupported operations.

Use one project-local `alice` MCP connection for all implemented features. Do not
persist separate LED/speech test aliases. Preserve existing station entries and
SSH settings when consolidating configuration.

Before publication run pytest, ruff, mypy, catalog/package checks and the privacy
scanner plus gitleaks on staged files, history and distribution archives.
Publish an explicit file allowlist, never `git add .`. Use a branch and PR with
DCO sign-off; commit/push only when requested. Never print raw secrets in reports.
