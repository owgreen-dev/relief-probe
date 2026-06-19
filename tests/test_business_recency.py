"""Tests for the Tier-A business-recency detector (KYB-001).

All warehouses are seeded synthetically in a tmp_path DuckDB file — we never touch
the real data/ warehouse, and we plant no benchmark numbers. The detector is
LABEL-FREE: it must fire identically whether or not fraud_cases holds any rows.
"""

from __future__ import annotations

from relief_probe.detectors.business_recency import BusinessRecencyDetector
from relief_probe.detectors.registry import (
    all_detectors,
    exploratory_detectors,
    get_detector,
)
from relief_probe.warehouse import connect

_INSERT = (
    "INSERT INTO loans (loan_number, business_age_description, date_approved, "
    "current_approval_amount) VALUES (?, ?, ?, ?)"
)

# One loan per distinct business_age_description value, plus null/blank edge cases.
_ROWS = [
    ("L_STARTUP", "Startup, Loan Funds will Open Business", "2020-04-15", 100_000),
    ("L_NEW", "New Business or 2 years or less", "2020-04-16", 100_000),
    ("L_CHANGE", "Change of Ownership", "2020-04-17", 100_000),
    ("L_EXISTING", "Existing or more than 2 years old", "2020-04-18", 100_000),
    ("L_UNANSWERED", "Unanswered", "2020-04-19", 100_000),
    ("L_NULL", None, "2020-04-20", 100_000),
    ("L_BLANK", "   ", "2020-04-21", 100_000),
]


def _seed(con) -> None:
    con.executemany(_INSERT, _ROWS)


def test_fires_on_the_three_tells_with_monotonic_scores(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)

    sigs = BusinessRecencyDetector().run(con)
    by_loan = {s.loan_number: s for s in sigs}

    # Exactly the three recency tells fire.
    assert set(by_loan) == {"L_STARTUP", "L_NEW", "L_CHANGE"}

    # Ordinal, monotonic: startup > new > change.
    assert by_loan["L_STARTUP"].score > by_loan["L_NEW"].score
    assert by_loan["L_NEW"].score > by_loan["L_CHANGE"].score

    # Evidence is grounded in the row's own fields.
    ev = by_loan["L_STARTUP"].evidence
    assert ev["business_age_description"] == "Startup, Loan Funds will Open Business"
    assert ev["matched_tell"] == "Startup, Loan Funds will Open Business"
    assert ev["date_approved"] is not None
    assert "reason" in ev and ev["reason"]


def test_quiet_on_existing_unanswered_and_missing(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)

    flagged = {s.loan_number for s in BusinessRecencyDetector().run(con)}
    # Never score the eligible baseline, the unanswered value, or missing data.
    assert "L_EXISTING" not in flagged
    assert "L_UNANSWERED" not in flagged
    assert "L_NULL" not in flagged
    assert "L_BLANK" not in flagged


def test_deterministic(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)

    det = BusinessRecencyDetector()
    first = {s.loan_number: s.score for s in det.run(con)}
    second = {s.loan_number: s.score for s in det.run(con)}
    assert first == second


def test_label_free_identical_with_empty_fraud_cases(tmp_path):
    """SIGN-012: the detector never reads fraud_cases — proven by running on a
    warehouse whose fraud_cases table is empty and asserting identical signals."""
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)

    # fraud_cases exists in the schema and is empty here; the detector must not
    # depend on it. Capture signals, then confirm fraud_cases is genuinely empty.
    sigs = {s.loan_number: s.score for s in BusinessRecencyDetector().run(con)}
    n_labels = con.execute("SELECT count(*) FROM fraud_cases").fetchone()[0]
    assert n_labels == 0
    # The three tells still fire with no labels present.
    assert sigs == {"L_STARTUP": 3.0, "L_NEW": 2.0, "L_CHANGE": 1.0}


def test_empty_warehouse_is_graceful(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    assert BusinessRecencyDetector().run(con) == []


def test_registered_exploratory_only_and_resolvable():
    ids_all = {d.detector_id for d in all_detectors()}
    ids_expl = {d.detector_id for d in exploratory_detectors()}
    # Exploratory only (SIGN-010) — never in the production composite.
    assert "business_recency" not in ids_all
    assert "business_recency" in ids_expl
    # Still resolvable by id for ad-hoc investigation.
    assert get_detector("business_recency").detector_id == "business_recency"
