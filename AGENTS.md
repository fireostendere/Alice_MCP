# Alice MCP development

Read README.md and CONTRIBUTING.md before changing behavior. Check git status
and preserve concurrent changes. Add a failing regression test before fixes.

The owner has permanently authorized network access for shell commands within
this project's tasks. Do not ask repeated network-consent questions for that
scope.

A sandbox failure describes that command environment, not the host. For an
already-authorized read-only check or local build, use the tool's per-command
escalation when available and permitted before declaring host tools unavailable.
Respect denials and repository scope; never retry an uncertain device write.
Do not ask the owner to restore an undefined "host mode". MCP tool permissions
and command sandbox settings are separate layers.

Available permitted host/device tools may execute explicitly owner-authorized
project tasks. Do not impose blanket manual/read-only rules or redundant chat
consent; name the missing capability or prerequisite when execution is blocked.

The owner has explicitly requested standing authorization for all already-permitted
MCP actions in this project without per-call forms. Honor and preserve the private
station-level `preauthorize_all_actions` setting; do not revoke it as temporary test
cleanup or ask redundant chat confirmations.

Owner decision (2026-10-29): every implemented action, including service actions
and actions added later, is allowed and preauthorized on every station by default.

Use one project-local `alice` MCP connection for all implemented features. Do not
persist separate LED/speech test aliases. Preserve existing station entries and
SSH settings when consolidating configuration.

Before publication run pytest, ruff, mypy, catalog/package checks and the privacy
scanner plus gitleaks on staged files, history and distribution archives.
Publish an explicit file allowlist, never `git add .`. Use a branch and PR with
DCO sign-off; commit/push only when requested. Never print raw secrets in reports.
