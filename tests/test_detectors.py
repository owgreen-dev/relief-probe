"""Tests for the M2 detectors, runner, and composite scoring.

We plant a clear outlier in a synthetic NAICS x state cohort and assert both
detectors fire on it (and corroborate in the composite ranking) while the normal
peers stay quiet.
"""

from __future__ import annotations

from relief_probe.detectors.naics_cohort_outlier import NaicsCohortOutlierDetector
from relief_probe.detectors.payroll_cap import PayrollCapExceedanceDetector
from relief_probe.detectors.runner import run_all
from relief_probe.scoring import composite_ranking
from relief_probe.warehouse import connect

OUTLIER = "OUTLIER-1"


def _seed(con):
    rows = []
    # 40 normal restaurants in TX at ~$9k-$12k per job (jobs=10).
    for i in range(40):
        amount = (9000 + i * 75) * 10
        rows.append((f"N{i:03d}", f"Normal Diner {i}", "722511", "TX", amount, 10))
    # One loan claiming $200k per job — impossible for payroll.
    rows.append((OUTLIER, "Suspicious Eats LLC", "722511", "TX", 1_000_000, 5))
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code, "
        "borrower_state, current_approval_amount, jobs_reported) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def test_naics_cohort_flags_only_the_outlier(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    sigs = NaicsCohortOutlierDetector().run(con)
    flagged = {s.loan_number for s in sigs}
    assert flagged == {OUTLIER}
    ev = sigs[0].evidence
    assert ev["cohort"] == "722511 | TX"
    assert ev["x_cohort_median"] > 5  # far above the cohort median $/job


def test_payroll_cap_flags_exceedance(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    sigs = PayrollCapExceedanceDetector().run(con)
    flagged = {s.loan_number for s in sigs}
    assert OUTLIER in flagged
    assert all(s.evidence["x_cap"] >= 1.5 for s in sigs)
    # NAICS 72 uses the higher 3.5x ceiling.
    assert sigs[0].evidence["per_employee_cap"] == 29166.67


def test_runner_and_composite_rank_outlier_first(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    counts = run_all(con)
    assert counts["naics_cohort_outlier"] >= 1
    assert counts["payroll_cap_exceedance"] >= 1

    ranking = composite_ranking(con, limit=5)
    top = ranking.iloc[0]
    assert top["loan_number"] == OUTLIER
    # Flagged by both detectors -> corroboration bonus.
    assert top["n_signals"] == 2
    assert set(top["detectors"]) == {"naics_cohort_outlier", "payroll_cap_exceedance"}
