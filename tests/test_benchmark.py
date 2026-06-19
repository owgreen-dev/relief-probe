"""Tests for the forward PU benchmark."""

from __future__ import annotations

from relief_probe.benchmark.core import (
    baseline_rankings,
    bootstrap_lift_cis,
    labeled_fraud_loans,
    positive_rank_stats,
    ranking_metrics,
    reciprocal_rank_fusion,
    run_benchmark,
    temporal_label_split,
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


def test_positive_rank_stats_math():
    # 10 flagged loans; positives at ranks 2 and 4; population 100, so 0 unranked.
    ranked = [f"L{i}" for i in range(10)]
    positives = {"L1", "L3"}  # 0-based indices 1,3 -> 1-based ranks 2,4
    s = positive_rank_stats(ranked, positives, population=100)
    assert s["n_positives"] == 2
    assert s["n_ranked"] == 2
    assert s["n_unranked"] == 0
    assert s["n_in_ranking"] == 10
    assert s["mean_rank_ranked"] == 3.0  # (2+4)/2
    assert s["median_rank_ranked"] == 3.0
    # Concentration within the 10-long flagged list: mean rank 3 / 10 = 0.3 (< random).
    assert s["mean_percentile_in_ranking"] == 0.3


def test_positive_rank_stats_folds_unranked_at_worst():
    # One positive flagged at rank 1, one positive never flagged (unranked).
    ranked = ["L0"]
    positives = {"L0", "MISSING"}
    s = positive_rank_stats(ranked, positives, population=1000)
    assert s["n_ranked"] == 1
    assert s["n_unranked"] == 1
    # Conservative population percentile folds the unranked positive in at
    # rank=population (1000): mean of ranks {1, 1000} / 1000 = 500.5/1000.
    assert s["mean_percentile_population_conservative"] == round(500.5 / 1000, 4)


def test_reciprocal_rank_fusion_blends_by_rank_not_score():
    # A appears high in both lists -> wins; an item high in only one ranks lower.
    a = ["A", "B", "C"]
    b = ["A", "C", "D"]
    fused = reciprocal_rank_fusion([a, b], k=60)
    assert fused[0] == "A"  # rank-1 in both
    assert set(fused) == {"A", "B", "C", "D"}
    # Weighting list b to zero recovers list a's order over a's items.
    only_a = reciprocal_rank_fusion([a, b], weights=[1.0, 0.0])
    assert only_a[:3] == ["A", "B", "C"]


def test_reciprocal_rank_fusion_rejects_misaligned_weights():
    import pytest

    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["A"], ["B"]], weights=[1.0])


def test_temporal_label_split_by_charge_date(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    con.executemany(
        "INSERT INTO fraud_cases (case_id, loan_number, source, match_method, "
        "match_confidence, charge_date) VALUES (?, ?, 'doj', 'm', 1.0, ?)",
        [
            ("a", "OLD1", "2021-06-01"),
            ("b", "OLD2", "2023-12-31"),
            ("c", "NEW1", "2024-01-01"),
            ("d", "NEW2", "2025-03-15"),
            # Same loan charged twice; placed by its EARLIEST date (2022 -> train).
            ("e", "DUP", "2022-01-01"),
            ("f", "DUP", "2026-01-01"),
            ("g", "UNDATED", None),  # no charge_date -> dropped from both
        ],
    )
    train, test = temporal_label_split(con, holdout_year=2023)
    assert train == {"OLD1", "OLD2", "DUP"}
    assert test == {"NEW1", "NEW2"}
    assert "UNDATED" not in train and "UNDATED" not in test


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
    # min_amount=None: the synthetic loans are sub-$150k, so evaluate whole pop.
    res = run_benchmark(con, ks=(1, 5), min_amount=None)
    assert res["population"] == 41
    assert res["n_labeled_fraud"] == 1
    # The planted fraud loan is the rank-1 lead -> hit at k=1, big lift.
    assert res["overall"][1]["hits"] == 1
    assert res["overall"][1]["recall"] == 1.0
    assert res["overall"][1]["lift"] > 1.0
    # Ablation reports both detectors.
    assert set(res["ablation"]) == {"naics_cohort_outlier", "payroll_cap_exceedance"}
    # PU-honest rank stats are reported; the lone positive is the rank-1 lead.
    pr = res["positive_ranks"]
    assert pr["n_positives"] == 1
    assert pr["median_rank_ranked"] == 1.0


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
    res = run_benchmark(con, ks=(1, 5), min_amount=None)
    assert set(res["baselines"]) == {"amount_per_job", "raw_amount"}
    for name in ("amount_per_job", "raw_amount"):
        metrics = res["baselines"][name]["metrics"]
        # Documented shape: per-k dict of hits/precision/lift/recall.
        assert set(metrics) == {1, 5}
        assert set(metrics[1]) == {"hits", "precision", "lift", "recall"}
    # The planted high-$/job labeled loan tops the amount_per_job baseline.
    assert res["baselines"]["amount_per_job"]["metrics"][1]["hits"] == 1


def test_bootstrap_lift_cis_shape_and_bracketing():
    # 100 ranked loans, the first 10 are positives -> precision@10 = 1.0, lift = N.
    ranked = [f"L{i}" for i in range(100)]
    positives = {f"L{i}" for i in range(10)}
    base_rate = 0.10  # 10 positives / 100 loans
    ci = bootstrap_lift_cis(
        ranked, positives, base_rate, ks=(10, 50), n_boot=500, seed=1
    )
    assert set(ci) == {10, 50}
    assert set(ci[10]) == {"hits_ci", "lift_ci"}
    lo, hi = ci[10]["lift_ci"]
    assert lo <= hi
    # Point lift@10 = (10/10)/0.10 = 10x should sit inside the interval.
    assert lo <= 10.0 <= hi
    # CIs are reproducible under a fixed seed.
    ci2 = bootstrap_lift_cis(
        ranked, positives, base_rate, ks=(10, 50), n_boot=500, seed=1
    )
    assert ci2[10]["lift_ci"] == ci[10]["lift_ci"]


def test_bootstrap_lift_cis_one_hit_lower_bound_is_zero():
    # A single positive at the very top: a Poisson(1) resample drops it ~37% of the
    # time, so the 95% hits CI must reach 0 — the honest "rests on one loan" signal.
    ranked = [f"L{i}" for i in range(100)]
    positives = {"L0"}
    ci = bootstrap_lift_cis(ranked, positives, 0.01, ks=(100,), n_boot=1000, seed=0)
    assert ci[100]["hits_ci"][0] == 0.0


def test_run_benchmark_slice_restricts_evaluation(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    # FRAUD-1 is the only loan >= $150k; the 40 normals are sub-slice.
    res = run_benchmark(con, ks=(1, 5), min_amount=150_000.0)
    assert res["slice"] == ">=$150,000"
    assert res["population"] == 1  # only FRAUD-1 is in the labelable slice
    assert res["n_labeled_fraud"] == 1
    # Full-population recall is reported against ALL labels over ALL loans.
    fp = res["full_population"]
    assert fp["population"] == 41
    assert fp["n_labeled_fraud"] == 1
    assert fp["metrics"][1]["hits"] == 1
