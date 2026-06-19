"""Tests for the rich, leakage-guarded feature composite (Loop 6).

``build_rich_feature_matrix`` layers four blocks on top of an at-origination
structured block (detector scores + evidence numerics, label-free graph
structure, a PLODI-style pay-ratio percentile, and LightGBM-native categoricals).
The two leakage guards are the whole point and are asserted explicitly:

* NO post-hoc / outcome columns (SIGN-014): no ``forgiveness``/``loan_status``.
* NO label-derived columns (SIGN-015): no ``is_fraud``, and the matrix is
  byte-for-byte identical whether ``fraud_cases`` is empty or populated.
"""

from __future__ import annotations

import pandas as pd

from relief_probe.scorer.features import (
    _CATEGORICAL_COLS,
    _EVIDENCE_COLS,
    _GRAPH_COLS,
    _PAY_RATIO_COLS,
    _RICH_STRUCTURED,
    build_rich_feature_matrix,
)
from relief_probe.warehouse import connect


def _seed_loans(con):
    con.executemany(
        "INSERT INTO loans (loan_number, current_approval_amount, "
        "initial_approval_amount, jobs_reported, payroll_proceed, utilities_proceed, "
        "rent_proceed, mortgage_interest_proceed, health_care_proceed, "
        "debt_interest_proceed, refinance_eidl_proceed, term, sba_guaranty_pct, "
        "naics_code, borrower_name, borrower_address, borrower_city, borrower_state, "
        "borrower_zip, project_county_name, processing_method, business_type, "
        "rural_urban_indicator, nonprofit, franchise_name, originating_lender, "
        "forgiveness_amount, loan_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?)",
        [
            (
                "A", 1_000_000.0, 1_000_000.0, 1.0, 900_000.0, 10_000.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 60, 100.0, "722511", "ABC CAFE LLC", "1 MAIN ST",
                "AUSTIN", "TX", "78701", "TRAVIS", "PPP", "LLC", "U", "N",
                None, "MEGA BANK", 1_000_000.0, "Paid in Full",
            ),
            (
                "B", 200_000.0, 180_000.0, 10.0, 180_000.0, 5_000.0, 5_000.0, 0.0,
                0.0, 0.0, 0.0, 24, 100.0, "238220", "XYZ PLUMBING", "2 OAK AVE",
                "DALLAS", "TX", "75201", "DALLAS", "PPS", "CORP", "R", "Y",
                "SUBWAY", "MEGA BANK", None, "Charged Off",
            ),
            (
                "SUB", 90_000.0, 90_000.0, 5.0, 80_000.0, 0.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 24, 100.0, "722511", "TINY DELI", "3 ELM ST",
                "AUSTIN", "TX", "78701", "TRAVIS", "PPP", "LLC", "U", "N",
                None, "SMALL BANK", None, "Paid in Full",
            ),
        ],
    )
    con.executemany(
        "INSERT INTO signals (loan_number, detector_id, score, evidence_json) "
        "VALUES (?, ?, ?, ?)",
        [
            ("A", "payroll_cap_exceedance", 7.5, '{"x_cap": 7.5}'),
            (
                "A", "naics_cohort_outlier", 4.2,
                '{"robust_z": 4.2, "x_cohort_median": 9.1, "cohort_size": 120}',
            ),
        ],
    )


def test_rich_matrix_shape_and_blocks(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_loans(con)
    X, lns, names, categoricals = build_rich_feature_matrix(con, min_amount=150_000.0)

    assert isinstance(X, pd.DataFrame)
    assert set(lns) == {"A", "B"}  # SUB excluded by the $150k slice threshold
    assert X.shape == (2, len(names))
    assert list(X.columns) == names

    # Every block is represented in feature_names.
    assert names[: len(_RICH_STRUCTURED)] == list(_RICH_STRUCTURED)
    for col in (*_EVIDENCE_COLS, *_GRAPH_COLS, *_PAY_RATIO_COLS, *_CATEGORICAL_COLS):
        assert col in names
    assert "det_payroll_cap_exceedance" in names  # per-detector score column

    # categorical_features is a subset of feature_names and carries category dtype.
    assert categoricals == list(_CATEGORICAL_COLS)
    assert set(categoricals).issubset(set(names))
    for col in categoricals:
        assert isinstance(X[col].dtype, pd.CategoricalDtype)

    # Evidence numerics flowed through from signals.evidence_json.
    row_a = X.loc[lns.index("A")]
    assert row_a["payroll_x_cap"] == 7.5
    assert row_a["cohort_robust_z"] == 4.2
    assert row_a["cohort_x_median"] == 9.1
    # Loan B never fired those detectors → 0.0.
    row_b = X.loc[lns.index("B")]
    assert row_b["payroll_x_cap"] == 0.0
    # Categorical sentinels / flags.
    assert row_a["cat_franchise"] == "N"
    assert row_b["cat_franchise"] == "Y"
    assert row_a["cat_naics_sector"] == "72"


def test_rich_matrix_has_no_leakage_columns(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_loans(con)
    _, _, names, _ = build_rich_feature_matrix(con, min_amount=150_000.0)
    joined = " ".join(names).lower()
    # SIGN-014: no post-hoc / outcome features.
    assert "forgiveness" not in joined
    assert "loan_status" not in joined
    # SIGN-015: no label-derived features.
    assert "is_fraud" not in joined
    assert "fraud" not in joined


def test_rich_matrix_is_identical_with_empty_fraud_cases(tmp_path):
    """Features are label-free: populating fraud_cases must not change the matrix."""
    con_empty = connect(tmp_path / "empty.duckdb")
    _seed_loans(con_empty)
    X_empty, lns_empty, names_empty, _ = build_rich_feature_matrix(con_empty)

    con_labeled = connect(tmp_path / "labeled.duckdb")
    _seed_loans(con_labeled)
    con_labeled.execute(
        "INSERT INTO fraud_cases (case_id, loan_number, defendant_name) "
        "VALUES ('c1', 'A', 'A SCHEMER')"
    )
    X_labeled, lns_labeled, names_labeled, _ = build_rich_feature_matrix(con_labeled)

    assert names_empty == names_labeled
    assert lns_empty == lns_labeled
    pd.testing.assert_frame_equal(X_empty, X_labeled)


def test_rich_matrix_empty_subset_is_graceful(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_loans(con)
    X, lns, names, cats = build_rich_feature_matrix(con, loan_numbers=[])
    assert lns == [] and names == [] and cats == []
    assert X.empty
