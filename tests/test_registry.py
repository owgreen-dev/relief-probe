"""Tests that the Loop 1 detectors are registered EXPLORATORY, not promoted.

amount_anomaly and multiple_funded_loans are discoverable and runnable on an
opt-in basis, but they stay OUT of the default composite (``all_detectors()``)
until a human validates real-data lift and manually promotes them — exactly the
H6 disposition the duplicate_address_ring detector already follows. All warehouses
are seeded synthetically in a tmp_path DuckDB file; we never touch the real data.
"""

from __future__ import annotations

from relief_probe.detectors.registry import (
    all_detectors,
    exploratory_detectors,
    get_detector,
)
from relief_probe.detectors.runner import run_all
from relief_probe.warehouse import connect

NEW_IDS = {"amount_anomaly", "multiple_funded_loans"}


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


def test_new_detectors_are_exploratory_not_in_default_composite():
    prod_ids = {d.detector_id for d in all_detectors()}
    expl_ids = {d.detector_id for d in exploratory_detectors()}
    # Held in the exploratory holding area...
    assert NEW_IDS <= expl_ids
    # ...and NOT auto-promoted into the headline composite.
    assert NEW_IDS.isdisjoint(prod_ids)
    # all_detectors() is unchanged: still only the two validated $/job detectors.
    assert prod_ids == {"naics_cohort_outlier", "payroll_cap_exceedance"}


def test_get_detector_resolves_both_new_ids():
    for detector_id in NEW_IDS:
        assert get_detector(detector_id).detector_id == detector_id


def test_default_run_all_excludes_new_detectors(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    counts = run_all(con)
    for detector_id in NEW_IDS:
        assert detector_id not in counts


def test_explicit_run_all_includes_new_detectors(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    counts = run_all(con, detectors=[*all_detectors(), *exploratory_detectors()])
    assert counts["amount_anomaly"] >= 1
    assert counts["multiple_funded_loans"] >= 1
