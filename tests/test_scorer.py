"""Tests for the learned PU scorer — features (offline) + PU-bagging (sklearn-gated).

Feature extraction is pure NumPy/pandas and runs with no extra. The PU-bagging test is
guarded by ``pytest.importorskip("sklearn")`` and uses a controlled synthetic set where
the positives are linearly separable, so the OOB scores must rank held-out positives
above noise.
"""

from __future__ import annotations

import numpy as np
import pytest

from relief_probe.scorer.features import _STRUCTURED, build_feature_matrix
from relief_probe.warehouse import connect


def _seed(con):
    con.executemany(
        "INSERT INTO loans (loan_number, current_approval_amount, jobs_reported, "
        "payroll_proceed, forgiveness_amount, term, sba_guaranty_pct, naics_code) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # in slice: $1M, 1 job, NAICS 72, round amount
            ("A", 1_000_000.0, 1.0, 900_000.0, 1_000_000.0, 60, 100.0, "722511"),
            ("B", 200_000.0, 10.0, 180_000.0, None, 24, 100.0, "238220"),
            ("SUB", 90_000.0, 5.0, 80_000.0, None, 24, 100.0, "722511"),  # < $150k
        ],
    )
    con.execute(
        "INSERT INTO signals (loan_number, detector_id, score, evidence_json) "
        "VALUES ('A', 'payroll_cap_exceedance', 7.5, '{}')"
    )


def test_build_feature_matrix_shape_and_values(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    X, lns, names = build_feature_matrix(con, min_amount=150_000.0)
    assert set(lns) == {"A", "B"}  # SUB excluded by the $150k threshold
    # Structured features present + one detector column from signals.
    assert names[: len(_STRUCTURED)] == list(_STRUCTURED)
    assert "det_payroll_cap_exceedance" in names
    assert X.shape == (2, len(_STRUCTURED) + 1)
    row = dict(zip(names, X[lns.index("A")], strict=True))
    assert row["jobs_is_one"] == 1.0
    assert row["round_amount"] == 1.0
    assert row["is_naics_72"] == 1.0
    assert row["det_payroll_cap_exceedance"] == 7.5  # the flagged detector score
    # The unflagged loan has a 0 detector column.
    assert dict(zip(names, X[lns.index("B")], strict=True))[
        "det_payroll_cap_exceedance"
    ] == 0.0


def test_build_feature_matrix_explicit_subset(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    X, lns, _ = build_feature_matrix(con, loan_numbers=["A"])
    assert lns == ["A"] and X.shape[0] == 1
    # Empty subset is graceful.
    X0, lns0, names0 = build_feature_matrix(con, loan_numbers=[])
    assert lns0 == [] and names0 == []


def test_pu_bagging_ranks_held_out_positives_above_noise():
    pytest.importorskip("sklearn")
    from relief_probe.scorer.pu_bagging import PUBaggingScorer

    rng = np.random.default_rng(0)
    # 40 "fraud-like" rows (feature 0 high) + 160 noise rows (feature 0 low).
    signal = np.column_stack([rng.normal(5, 1, 40), rng.normal(0, 1, 40)])
    noise = np.column_stack([rng.normal(0, 1, 160), rng.normal(0, 1, 160)])
    X = np.vstack([signal, noise])
    mask = np.zeros(200, dtype=bool)
    mask[:20] = True  # 20 of the signal group are KNOWN positives (train)
    held_out = slice(20, 40)  # the other 20 signal rows are unlabeled true positives
    noise_idx = slice(40, 200)

    scorer = PUBaggingScorer(n_estimators=40, random_state=0).fit(X, mask)
    oob = scorer.oob_scores_
    # Held-out true positives score higher (OOB) than the noise unlabeled.
    assert np.nanmean(oob[held_out]) > np.nanmean(oob[noise_idx]) + 0.1
    # predict_score works for arbitrary rows.
    assert scorer.predict_score(X[:5]).shape == (5,)


def test_pu_bagging_requires_positives_and_unlabeled():
    pytest.importorskip("sklearn")
    from relief_probe.scorer.pu_bagging import PUBaggingScorer

    X = np.random.default_rng(0).normal(size=(10, 2))
    with pytest.raises(ValueError):
        PUBaggingScorer().fit(X, np.zeros(10, dtype=bool))  # no positives
