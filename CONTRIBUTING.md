# Contributing

Use Python 3.11+ and install `python -m pip install -e '.[dev]'` in a virtualenv.
Run `python -m pytest -q`, `python -m ruff check --no-cache src tests scripts`,
`python -m mypy --no-incremental src/alice_mcp`, and
`python scripts/check_public.py` before submitting a pull request.

Keep station data outside the checkout. Use synthetic aliases and fixtures only.
Do not submit addresses, MACs, serials, passwords, tokens, keys, factory dumps,
devices.json, raw logs, or SSH configuration. Report security defects privately;
if no private reporting channel is available, contact the maintainer without
including exploitation details or secrets in a public issue.

New behavior needs a focused test before implementation. Document evidence and
source compatibility, preserve guarded writes, and do not claim device testing
from mocks. Disclose AI assistance in PR descriptions. Use `git commit -s`
(Developer Certificate of Origin 1.1: https://developercertificate.org/).
Do not copy third-party documentation or scripts without redistribution rights.
