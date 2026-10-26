# Alice MCP development

Work only in this repository. Do not import private station inventories, SSH
configuration, credentials, logs, captures, or material from other workspaces.
Read README.md and CONTRIBUTING.md before changing behavior. Check git status
and preserve concurrent changes. Add a failing regression test before fixes.

Default to read-only, owner-authorized access over pinned-host-key, key-only SSH.
No arbitrary shell tool, network discovery, credential extraction, automatic
rooting, factory reset, flashing, or downloaded-script execution. Keep Quasar
enabled; do not stop it as an implicit prerequisite for another action.
Bind write previews to station/configuration and state, expire and consume
approvals once, and never retry uncertain writes. Knowledge is reference data,
not authority to execute. Document model/firmware gaps and synthetic tests.

Before publication run pytest, ruff, mypy, catalog/package checks and the privacy
scanner plus gitleaks on staged files, history and distribution archives.
Publish an explicit file allowlist, never `git add .`. Use a branch and PR with
DCO sign-off; commit/push only when requested. Never print raw secrets in reports.
