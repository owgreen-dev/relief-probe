"""Out-of-time validation of the learned PU scorer vs the unsupervised composite.

Train the PU-bagging scorer on prosecutions charged <= ``holdout_year`` and validate on
those charged > ``holdout_year`` — the H7 temporal holdout, so nothing leaks. Both the
learned scorer AND the composite baseline are evaluated on the SAME held-out positives
over the SAME population (the $150k+ slice minus the training positives, which are
already known), so the comparison is fair. The honest question: does fitting to labels
beat the hand-weighted composite on *future* enforcement? A negative is a real result.
"""

from __future__ import annotations

import duckdb
import numpy as np

from relief_probe.benchmark.core import (
    DEFAULT_KS,
    positive_rank_stats,
    ranking_metrics,
    temporal_label_split,
)
from relief_probe.scoring import composite_ranking


def run_holdout_validation(
    con: duckdb.DuckDBPyConnection,
    *,
    holdout_year: int = 2023,
    min_amount: float = 150_000.0,
    n_estimators: int = 50,
    random_state: int = 0,
    ks: tuple[int, ...] = DEFAULT_KS,
) -> dict:
    """Compare the learned PU scorer to the composite on the temporal holdout."""
    from relief_probe.scorer.features import build_feature_matrix
    from relief_probe.scorer.pu_bagging import PUBaggingScorer

    slice_universe = {
        str(r[0])
        for r in con.execute(
            "SELECT loan_number FROM loans WHERE current_approval_amount >= ?",
            [min_amount],
        ).fetchall()
    }
    train_all, test_all = temporal_label_split(con, holdout_year)
    train_pos = train_all & slice_universe
    test_pos = test_all & slice_universe

    X, loan_numbers, feature_names = build_feature_matrix(con, min_amount=min_amount)
    idx = {ln: i for i, ln in enumerate(loan_numbers)}
    mask = np.array([ln in train_pos for ln in loan_numbers], dtype=bool)

    scorer = PUBaggingScorer(
        n_estimators=n_estimators, random_state=random_state
    ).fit(X, mask)
    scores = scorer.oob_scores_.copy()
    nan = np.isnan(scores)
    if nan.any():  # rows never out-of-bag -> fall back to the all-bags mean
        scores[nan] = scorer.predict_score(X[nan])

    # Rank the population MINUS the known training positives (fair to both rankers).
    population = [ln for ln in loan_numbers if ln not in train_pos]
    base_rate = (len(test_pos) / len(population)) if population else 0.0

    learned_ranked = sorted(population, key=lambda ln: (-scores[idx[ln]], ln))

    # Composite baseline over the same population: flagged loans by composite score,
    # then the unflagged remainder (deterministic loan_number order).
    comp_df = composite_ranking(con)
    comp_flagged = [
        ln for ln in (str(x) for x in comp_df["loan_number"].tolist())
        if ln in slice_universe and ln not in train_pos
    ]
    flagged_set = set(comp_flagged)
    comp_tail = sorted(ln for ln in population if ln not in flagged_set)
    composite_ranked = comp_flagged + comp_tail

    learned_metrics = ranking_metrics(learned_ranked, test_pos, base_rate, ks)
    composite_metrics = ranking_metrics(composite_ranked, test_pos, base_rate, ks)
    learned_ranks = positive_rank_stats(learned_ranked, test_pos, len(population))
    composite_ranks = positive_rank_stats(composite_ranked, test_pos, len(population))

    # Average decision-tree feature importances across the bags (interpretability).
    importances = np.mean(
        [est.feature_importances_ for est in scorer.estimators_], axis=0
    )
    top = sorted(
        zip(feature_names, importances, strict=True), key=lambda t: -t[1]
    )[:8]

    # Verdict: total recall@k across the ks band (learned vs composite).
    learned_recall = sum(learned_metrics[k]["hits"] for k in ks)
    composite_recall = sum(composite_metrics[k]["hits"] for k in ks)
    if learned_recall > composite_recall:
        verdict = "learned BEATS composite"
    elif learned_recall < composite_recall:
        verdict = "composite beats learned"
    else:
        verdict = "tie"

    return {
        "holdout_year": holdout_year,
        "ks": list(ks),
        "n_train_positives": len(train_pos),
        "n_test_positives": len(test_pos),
        "population": len(population),
        "base_rate": round(base_rate, 6),
        "learned": {"metrics": learned_metrics, "ranks": learned_ranks},
        "composite": {"metrics": composite_metrics, "ranks": composite_ranks},
        "top_features": [(n, round(float(v), 4)) for n, v in top],
        "verdict": verdict,
    }
