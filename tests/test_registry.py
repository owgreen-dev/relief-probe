"""Tests for the Loop 1 detector registration + post-validation disposition.

After real-data validation, ``multiple_funded_loans`` showed genuine independent
lift and was PROMOTED into the default composite (``all_detectors()``), while
``amount_anomaly`` was weak and stays EXPLORATORY (opt-in only) alongside
``duplicate_address_ring`` — the same H6 discipline (promote only what validates).
All warehouses are seeded synthetically in a tmp_path DuckDB file; we never touch
the real data.
"""

from __future__ import annotations

from relief_probe.detectors.registry import (
    all_detectors,
    exploratory_detectors,
    get_detector,
)
from relief_probe.detectors.runner import run_all
from relief_probe.warehouse import connect

PROMOTED_ID = "multiple_funded_loans"
EXPLORATORY_ID = "amount_anomaly"
OVERCOUNT_ID = "establishment_overcount"


def _seed(con):
    rows = [
        # Round + cap-maxed NAICS-72 loan -> amount_anomaly fires.
        ("AA-1", "Maxed Diner LLC", "1 Main St", "Austin", "TX", "78701",
         "722511", "PPP", 350_000.0, 12.0),
        # Same resolved entity, second same-draw loan -> multiple_funded_loans
        # fires (and this odd-cents amount keeps amount_anomaly quiet on it).
        ("MF-1a", "Reload Co", "9 Elm St", "Reno", "NV", "89501",
         "722511", "PPP", 87_234.57, 10.0),
        ("MF-1b", "RELOAD", "9 Elm Street", "Reno", "NV", "89501",
         "722511", "PPP", 91_111.13, 11.0),
        # A plain non-round, mid-cohort, single-loan borrower -> stays quiet.
        ("OK-1", "Honest Cafe", "5 Oak Ave", "Reno", "NV", "89501",
         "722511", "PPP", 73_456.91, 9.0),
    ]
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, borrower_address, "
        "borrower_city, borrower_state, borrower_zip, naics_code, "
        "processing_method, current_approval_amount, jobs_reported) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def test_disposition_after_validation():
    prod_ids = {d.detector_id for d in all_detectors()}
    expl_ids = {d.detector_id for d in exploratory_detectors()}
    # multiple_funded_loans validated (independent lift) -> promoted to composite.
    assert PROMOTED_ID in prod_ids
    assert PROMOTED_ID not in expl_ids
    # amount_anomaly was weak -> stays exploratory, out of the composite.
    assert EXPLORATORY_ID in expl_ids
    assert EXPLORATORY_ID not in prod_ids
    # The production set is exactly the two $/job detectors plus the promoted one.
    assert prod_ids == {
        "naics_cohort_outlier",
        "payroll_cap_exceedance",
        "multiple_funded_loans",
    }


def test_get_detector_resolves_both_new_ids():
    for detector_id in (PROMOTED_ID, EXPLORATORY_ID):
        assert get_detector(detector_id).detector_id == detector_id


def test_default_run_all_includes_promoted_excludes_exploratory(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    counts = run_all(con)
    assert PROMOTED_ID in counts  # promoted -> default composite
    assert EXPLORATORY_ID not in counts  # exploratory -> opt-in only


def test_explicit_run_all_includes_exploratory(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    counts = run_all(con, detectors=[*all_detectors(), *exploratory_detectors()])
    assert counts["amount_anomaly"] >= 1
    assert counts["multiple_funded_loans"] >= 1


# --- L2-003: establishment_overcount stays EXPLORATORY (SIGN-010) ---------------


def test_establishment_overcount_is_exploratory_not_promoted():
    prod_ids = {d.detector_id for d in all_detectors()}
    expl_ids = {d.detector_id for d in exploratory_detectors()}
    # Built + tested but unvalidated on real data -> exploratory only, NOT composite.
    assert OVERCOUNT_ID in expl_ids
    assert OVERCOUNT_ID not in prod_ids


def test_get_detector_resolves_establishment_overcount():
    assert get_detector(OVERCOUNT_ID).detector_id == OVERCOUNT_ID


def test_default_run_all_omits_establishment_overcount(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_overcount(con)
    counts = run_all(con)
    assert OVERCOUNT_ID not in counts  # exploratory -> opt-in only


def test_explicit_run_all_includes_establishment_overcount(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_overcount(con)
    counts = run_all(con, detectors=[*all_detectors(), *exploratory_detectors()])
    # Seeded cell (29150 x 325510): 6 loans, 1 establishment -> overcount fires.
    assert counts[OVERCOUNT_ID] >= 1


def _seed_overcount(con):
    """Seed loans + establishments so the overcount detector has a cell to fire on."""
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_zip, naics_code) VALUES (?, ?, ?)",
        [(f"OC-{i}", "29150", "325510") for i in range(6)],
    )
    con.executemany(
        "INSERT INTO establishments (zip, naics, establishments) VALUES (?, ?, ?)",
        [("29150", "325510", 1)],
    )
