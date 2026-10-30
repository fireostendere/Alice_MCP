"""Owner-typed confirmations: trimmed, still exact and case-sensitive, with a diagnostic."""

from alice_mcp.core import confirm


def test_confirmation_trims_whitespace_but_stays_exact_and_reports_what_was_received() -> None:
    assert confirm(" LEVELS\r\n", "LEVELS") is None and confirm("PLAY", "PLAY") is None
    assert confirm("levels", "LEVELS") == {"received": "'levels'"}
    assert confirm("LEVEL S", "LEVELS") == {"received": "'LEVEL S'"}
    assert confirm("x" * 40, "LEVELS") == {"received": repr("x" * 20)}
    assert confirm("", "LEVELS") == {"received": "''"}
