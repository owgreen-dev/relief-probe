"""Tests for the forward PU benchmark."""

from __future__ import annotations

from relief_probe.benchmark.core import (
    baseline_rankings,
    labeled_fraud_loans,
    ranking_metrics,
    run_benchmark,
)
from relief_probe.warehouse import connect


def test_ranking_metrics_math():
    ranked = ["a", "b", "c", "d"]
    positives = {"a", "c"}
    base_rate = 0.25  # 1 in 4
    m = ranking_metrics(ranked, positives, base_rate, ks=(2, 4))
    # top-2 = [a, b] -> 1 hit; precision 0.5; lift 0.5/0.25 = 2x; recall 1/2.
    assert m[2]["hits"] == 1
    assert m[2]["precision"] == 0.5
    assert m[2]["lift"] == 2.0
    assert m[2]["recall"] == 0.5
    # top-4 -> both hits; recall 1.0.
    assert m[4]["hits"] == 2
    assert m[4]["recall"] == 1.0


def _seed(con):
    # 40 normal restaurant loans + one $/job outlier (both detectors fire on it).
    rows = [
        (f"N{i:03d}", f"Normal Diner {i}", "722511", "TX", (9000 + i * 75) * 10, 10)
        for i in range(40)
    ]
    rows.append(("FRAUD-1", "Suspicious Eats LLC", "722511", "TX", 1_000_000, 5))
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code, "
        "borrower_state, current_approval_amount, jobs_reported) VALUES (?,?,?,?,?,?)",
        rows,
    )
    # Label the outlier as prosecuted.
    con.execute(
        "INSERT INTO fraud_cases (case_id, loan_number, source, match_method, "
        "match_confidence) VALUES ('c1', 'FRAUD-1', 'doj', 'name+state+amount', 1.0)"
    )


def test_labeled_fraud_loans(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    assert labeled_fraud_loans(con) == {"FRAUD-1"}


def test_run_benchmark_ranks_labeled_loan_at_top(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    res = run_benchmark(con, ks=(1, 5))
    assert res["population"] == 41
    assert res["n_labeled_fraud"] == 1
    # The planted fraud loan is the rank-1 lead -> hit at k=1, big lift.
    assert res["overall"][1]["hits"] == 1
    assert res["overall"][1]["recall"] == 1.0
    assert res["overall"][1]["lift"] > 1.0
    # Ablation reports both detectors.
    assert set(res["ablation"]) == {"naics_cohort_outlier", "payroll_cap_exceedance"}


def test_baseline_rankings_ordering(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    bl = baseline_rankings(con)
    assert set(bl) == {"amount_per_job", "raw_amount"}
    # Whole population is ranked (no flagged-only restriction).
    assert len(bl["amount_per_job"]) == 41
    assert len(bl["raw_amount"]) == 41
    # FRAUD-1 has the highest $/job (200k vs <12k) and the largest loan ($1M).
    assert bl["amount_per_job"][0] == "FRAUD-1"
    assert bl["raw_amount"][0] == "FRAUD-1"
    # raw_amount is sorted by amount descending.
    amounts = [
        con.execute(
            "SELECT current_approval_amount FROM loans WHERE loan_number = ?", [ln]
        ).fetchone()[0]
        for ln in bl["raw_amount"]
    ]
    assert amounts == sorted(amounts, reverse=True)


def test_run_benchmark_includes_baselines(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    res = run_benchmark(con, ks=(1, 5))
    assert set(res["baselines"]) == {"amount_per_job", "raw_amount"}
    for name in ("amount_per_job", "raw_amount"):
        metrics = res["baselines"][name]["metrics"]
        # Documented shape: per-k dict of hits/precision/lift/recall.
        assert set(metrics) == {1, 5}
        assert set(metrics[1]) == {"hits", "precision", "lift", "recall"}
    # The planted high-$/job labeled loan tops the amount_per_job baseline.
    assert res["baselines"]["amount_per_job"]["metrics"][1]["hits"] == 1
