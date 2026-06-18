"""Tests for the amount_anomaly detector on seeded tmp_path warehouses.

A perfectly round, cap-maxed loan fires; a normal payroll-derived amount (odd
cents, mid-cohort per-employee) stays quiet; a loan strictly ABOVE the cap is left
to payroll_cap_exceedance (not flagged here on the cap sub-signal).
"""

from __future__ import annotations

from relief_probe.detectors.amount_anomaly import AmountAnomalyDetector
from relief_probe.detectors.payroll_cap import FOOD_ACCOMMODATION_CAP
from relief_probe.warehouse import connect

ROUND_CAPPED = "RC-1"  # round AND cap-maxed
NORMAL = "NORM-1"  # odd cents, mid-cohort per-employee
ABOVE_CAP = "ABOVE-1"  # implied per-employee strictly above the cap


def _seed(con):
    rows = [
        # $350,000 / 12 jobs = $29,166.67 == NAICS-72 cap exactly, and $350k is an
        # exact multiple of $10,000 -> both sub-signals fire.
        (ROUND_CAPPED, "Maxed Diner LLC", "722511", "TX", 350_000.0, 12.0),
        # Odd-cents amount, ~$8.7k per job -> neither sub-signal fires.
        (NORMAL, "Honest Cafe", "722511", "TX", 87_234.57, 10.0),
        # ~$200k per job, far above the cap, and a non-round amount -> amount_anomaly
        # stays quiet; this belongs to payroll_cap_exceedance.
        (ABOVE_CAP, "Inflated Eats", "722511", "TX", 1_000_003.0, 5.0),
    ]
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code, "
        "borrower_state, current_approval_amount, jobs_reported) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def test_round_capped_loan_fires_both_subsignals(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    sigs = AmountAnomalyDetector().run(con)
    by_loan = {s.loan_number: s for s in sigs}

    assert ROUND_CAPPED in by_loan
    ev = by_loan[ROUND_CAPPED].evidence
    assert set(ev["signals_fired"]) == {"round_number", "cap_maximization"}
    assert ev["round_divisor"] == 10_000
    assert ev["per_employee_cap"] == FOOD_ACCOMMODATION_CAP
    # Implied per-employee bunched right at the cap.
    assert abs(ev["per_employee_amount"] - FOOD_ACCOMMODATION_CAP) < 1.0
    # Both sub-signals firing scores above a single sub-signal could.
    assert by_loan[ROUND_CAPPED].score > 1.0


def test_normal_loan_stays_quiet(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    sigs = AmountAnomalyDetector().run(con)
    assert NORMAL not in {s.loan_number for s in sigs}


def test_above_cap_loan_left_to_payroll_cap(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    sigs = AmountAnomalyDetector().run(con)
    flagged = {s.loan_number for s in sigs}
    # A loan above the cap with a non-round amount is not the focus of this detector.
    assert ABOVE_CAP not in flagged


def test_cap_maximization_distinct_from_exceedance(tmp_path):
    """The cap sub-signal fires only at/just-below the cap, never strictly above."""
    con = connect(tmp_path / "wh.duckdb")
    # Just-below-cap but odd-cents (so round_number cannot fire): isolates the band.
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code, "
        "borrower_state, current_approval_amount, jobs_reported) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [("BAND-1", "Band Co", "722511", "TX", 349_111.11, 12.0)],
    )
    sigs = AmountAnomalyDetector().run(con)
    by_loan = {s.loan_number: s for s in sigs}
    assert "BAND-1" in by_loan
    ev = by_loan["BAND-1"].evidence
    assert ev["signals_fired"] == ["cap_maximization"]
    assert ev["round_divisor"] is None
    assert ev["per_employee_amount"] <= ev["per_employee_cap"]
