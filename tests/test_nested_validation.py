"""Tests for the nested LightGBM validation harness (Loop 6, SCORER-003).

Gated by ``pytest.importorskip("lightgbm")``. A small seeded warehouse with a
temporal split (some prosecutions charged <=2023, some >2023) must yield a
well-formed comparison dict: lift@k / recall@k / rank-stats for all four rankings
(lgbm, pu_bagging, composite, rrf_fusion) plus feature importances and a verdict.

The harness is methodologically the core of the loop: the grouped k-fold CV only
TUNES (SIGN-016) and the temporal holdout is the only honest headline (SIGN-013).
These tests use a tmp_path warehouse and NEVER touch the real warehouse (SIGN-007).
"""

from __future__ import annotations

import pytest

from relief_probe.warehouse import connect


def _seed(con):
    """30 in-slice loans + signals; 6 train positives (<=2023), 4 test (>2023)."""
    train_pos = {f"L{i:03d}" for i in range(0, 6)}  # charged 2022 -> train
    test_pos = {f"L{i:03d}" for i in range(6, 10)}  # charged 2024 -> test
    positives = train_pos | test_pos

    rows = []
    for i in range(30):
        ln = f"L{i:03d}"
        is_pos = ln in positives
        # Positives look "fraud-like": a big loan for very few jobs.
        amount = 1_000_000.0 if is_pos else 200_000.0 + i * 1_000
        jobs = 1.0 if is_pos else 20.0
        rows.append(
            (
                ln,
                f"Borrower {i} LLC",
                f"{i} Main St",
                "Austin",
                "TX",
                f"7870{i % 10}",
                "722511",
                amount,
                jobs,
                900_000.0 if is_pos else 100_000.0,
            )
        )
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, borrower_address, "
        "borrower_city, borrower_state, borrower_zip, naics_code, "
        "current_approval_amount, jobs_reported, payroll_proceed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    # Give every loan a couple of detector signals so the composite + feature
    # matrices have something to rank (higher score on the positives).
    sig_rows = []
    for i in range(30):
        ln = f"L{i:03d}"
        hi = ln in positives
        sig_rows.append(
            (ln, "payroll_cap_exceedance", 9.0 if hi else 1.0, '{"x_cap": 5.0}')
        )
        sig_rows.append(
            (ln, "naics_cohort_outlier", 8.0 if hi else 1.5, '{"robust_z": 4.0}')
        )
    con.executemany(
        "INSERT INTO signals (loan_number, detector_id, score, evidence_json) "
        "VALUES (?, ?, ?, ?)",
        sig_rows,
    )

    fraud_rows = [
        (f"c{ln}", ln, "2022-06-01") for ln in sorted(train_pos)
    ] + [(f"c{ln}", ln, "2024-06-01") for ln in sorted(test_pos)]
    con.executemany(
        "INSERT INTO fraud_cases (case_id, loan_number, source, match_method, "
        "match_confidence, charge_date) VALUES (?, ?, 'doj', 'm', 1.0, ?)",
        fraud_rows,
    )


def test_nested_lgbm_validation_well_formed(tmp_path):
    pytest.importorskip("lightgbm")
    from relief_probe.scorer.validate import run_nested_lgbm_validation

    con = connect(tmp_path / "wh.duckdb")
    _seed(con)

    result = run_nested_lgbm_validation(
        con, holdout_year=2023, min_amount=150_000.0, n_splits=3, random_state=0
    )

    assert result["holdout_year"] == 2023
    assert result["n_train_positives"] == 6
    assert result["n_test_positives"] == 4
    assert result["population"] > 0

    # All four rankings present, each with metrics + rank-stats.
    for name in ("lgbm", "pu_bagging", "composite", "rrf_fusion"):
        assert name in result["rankings"]
        block = result["rankings"][name]
        assert set(result["ks"]) == set(block["metrics"])
        assert "ranks" in block

    # ks never exceed the population (acceptance).
    assert result["ks"]
    assert all(k <= result["population"] for k in result["ks"])

    # Feature importances + a tuned-param record + an honest verdict.
    assert result["feature_importance"]
    assert all(isinstance(n, str) for n, _ in result["feature_importance"])
    assert "num_leaves" in result["tuned_params"]
    assert result["verdict"] in {"improved", "neutral", "regressed"}
