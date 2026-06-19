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
    bootstrap_lift_cis,
    positive_rank_stats,
    ranking_metrics,
    reciprocal_rank_fusion,
    temporal_label_split,
)
from relief_probe.detectors._entity import entity_key
from relief_probe.scoring import composite_ranking

#: Inner-CV hyperparameter grid for the LightGBM scorer. Small + regularized by
#: design (SIGN-016: CV only tunes, it never produces the reported number). Each
#: combo's ``n_estimators`` doubles as the "early-stopping rounds" we select by
#: grouped CV — picking the boosting budget on held folds is the deterministic
#: stand-in for live early stopping and avoids overfitting the train positives.
_LGBM_PARAM_GRID: tuple[dict, ...] = (
    {"num_leaves": 15, "min_child_samples": 20, "n_estimators": 100},
    {"num_leaves": 31, "min_child_samples": 50, "n_estimators": 200},
    {"num_leaves": 31, "min_child_samples": 50, "n_estimators": 400},
)


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


def _entity_groups(
    con: duckdb.DuckDBPyConnection, min_amount: float
) -> dict[str, str]:
    """Map each slice loan_number to its :func:`entity_key` group (SIGN-016).

    Unkeyable loans (blank name/address) get a private ``__loan__<n>`` group so
    they are never co-grouped — the same borrower never spans a CV fold, but two
    unrelated unkeyable loans never merge either.
    """
    rows = con.execute(
        "SELECT loan_number, borrower_name, borrower_address, borrower_city, "
        "borrower_state, borrower_zip FROM loans WHERE current_approval_amount >= ?",
        [min_amount],
    ).fetchall()
    groups: dict[str, str] = {}
    for ln, name, addr, city, state, zip_code in rows:
        key = entity_key(name, addr, city, state, zip_code)
        groups[str(ln)] = key or f"__loan__{ln}"
    return groups


def _grouped_cv_tune(
    X,
    pos_mask: np.ndarray,
    groups: np.ndarray,
    *,
    n_splits: int,
    random_state: int,
    downsample_ratio: int,
) -> dict:
    """Pick LightGBM hyperparameters by entity-grouped k-fold CV (tuning ONLY).

    Grouped k-fold over the ``charges<=holdout_year`` train population so one
    borrower never spans a train/validation fold (SIGN-016). Each candidate combo
    is scored by mean held-fold ROC-AUC (positives vs the downsampled unlabeled,
    the PU proxy). Returns the best combo; falls back to the grid's first entry
    when too few groups / classes exist to cross-validate. This NEVER touches the
    holdout period and NEVER produces the reported number (SIGN-013).
    """
    from sklearn.metrics import roc_auc_score  # noqa: PLC0415 - lazy with the ml extra
    from sklearn.model_selection import GroupKFold  # noqa: PLC0415

    from relief_probe.scorer.lgbm import LgbmPuScorer  # noqa: PLC0415

    n_groups = len(np.unique(groups))
    splits = min(n_splits, n_groups)
    if splits < 2 or pos_mask.sum() < 2:
        return dict(_LGBM_PARAM_GRID[0])

    folds = list(GroupKFold(n_splits=splits).split(X, pos_mask, groups))
    best_combo, best_auc = dict(_LGBM_PARAM_GRID[0]), -1.0
    for combo in _LGBM_PARAM_GRID:
        aucs: list[float] = []
        for train_idx, val_idx in folds:
            tr_mask = pos_mask[train_idx]
            val_mask = pos_mask[val_idx]
            # Need both classes to fit and to score the fold.
            if tr_mask.sum() == 0 or (~tr_mask).sum() == 0:
                continue
            if val_mask.sum() == 0 or (~val_mask).sum() == 0:
                continue
            X_tr = X.iloc[train_idx] if hasattr(X, "iloc") else X[train_idx]
            X_val = X.iloc[val_idx] if hasattr(X, "iloc") else X[val_idx]
            # downsample_ratio is huge here: the fold is already small, so use all
            # of its unlabeled rows (no further downsampling within CV).
            scorer = LgbmPuScorer(
                downsample_ratio=10**9, random_state=random_state, **combo
            ).fit(X_tr, tr_mask)
            scores = scorer.predict_score(X_val)
            aucs.append(float(roc_auc_score(val_mask.astype(int), scores)))
        mean_auc = float(np.mean(aucs)) if aucs else -1.0
        if mean_auc > best_auc:
            best_combo, best_auc = dict(combo), mean_auc
    best_combo["cv_auc"] = round(best_auc, 4) if best_auc >= 0 else None
    best_combo["cv_n_splits"] = splits
    return best_combo


def run_nested_lgbm_validation(
    con: duckdb.DuckDBPyConnection,
    *,
    holdout_year: int = 2023,
    min_amount: float = 150_000.0,
    n_splits: int = 5,
    random_state: int = 0,
    downsample_ratio: int = 30,
    top_n: int = 15,
    n_boot: int = 2_000,
    ks: tuple[int, ...] = DEFAULT_KS,
) -> dict:
    """Nested validation of the LightGBM scorer — CV-tune, temporal-holdout report.

    INNER: grouped k-fold CV grouped by ``entity_key`` over the ``charges<=holdout_year``
    train positives + downsampled unlabeled, used ONLY to pick LightGBM
    hyperparameters + boosting rounds (SIGN-016). OUTER: train the tuned
    :class:`LgbmPuScorer` on all train positives, score the whole slice, and evaluate
    on the ``>holdout_year`` test positives over the slice MINUS the known train
    positives — the temporal holdout, the only honest headline (SIGN-013).

    Returns a comparison dict with lift@k / recall@k / rank-stats for ``lgbm``,
    ``pu_bagging`` (the M10 baseline), ``composite`` (the hand-weighted ranking), and
    ``rrf_fusion`` (RRF of lgbm+composite — does the model ADD even if it doesn't win
    alone?), plus ``feature_importance`` (LightGBM gain, top-N) and a ``verdict`` in
    ``{'improved','neutral','regressed'}`` (lgbm vs composite on summed recall@ks).
    An honest negative is a valid, documented outcome.
    """
    from relief_probe.scorer.features import (  # noqa: PLC0415
        build_feature_matrix,
        build_rich_feature_matrix,
    )
    from relief_probe.scorer.lgbm import LgbmPuScorer  # noqa: PLC0415
    from relief_probe.scorer.pu_bagging import PUBaggingScorer  # noqa: PLC0415

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

    # ---- Rich feature matrix (LightGBM) over the slice ----------------------
    Xr, rich_lns, feature_names, _cat = build_rich_feature_matrix(
        con, min_amount=min_amount
    )
    rich_idx = {ln: i for i, ln in enumerate(rich_lns)}
    train_mask = np.array([ln in train_pos for ln in rich_lns], dtype=bool)

    population = [ln for ln in rich_lns if ln not in train_pos]
    n_pop = len(population)
    # ks never exceed the population (acceptance); fall back to the population size.
    eff_ks = tuple(k for k in ks if k <= n_pop) or ((n_pop,) if n_pop else (1,))
    base_rate = (len(test_pos) / n_pop) if n_pop else 0.0

    # ---- INNER: grouped CV to tune (charges<=holdout_year train only) -------
    group_map = _entity_groups(con, min_amount)
    groups = np.array([group_map.get(ln, f"__loan__{ln}") for ln in rich_lns])
    best = _grouped_cv_tune(
        Xr,
        train_mask,
        groups,
        n_splits=n_splits,
        random_state=random_state,
        downsample_ratio=downsample_ratio,
    )
    tuned = {
        k: best[k] for k in ("num_leaves", "min_child_samples", "n_estimators")
        if k in best
    }

    # ---- OUTER: train tuned LightGBM on all train positives, score the slice -
    lgbm = LgbmPuScorer(
        downsample_ratio=downsample_ratio, random_state=random_state, **tuned
    ).fit(Xr, train_mask)
    lgbm_scores = lgbm.predict_score(Xr)
    lgbm_ranked = sorted(population, key=lambda ln: (-lgbm_scores[rich_idx[ln]], ln))

    # ---- PU-bagging baseline on the M10 numeric matrix ----------------------
    Xp, pu_lns, _ = build_feature_matrix(con, min_amount=min_amount)
    pu_idx = {ln: i for i, ln in enumerate(pu_lns)}
    pu_mask = np.array([ln in train_pos for ln in pu_lns], dtype=bool)
    pu = PUBaggingScorer(random_state=random_state).fit(Xp, pu_mask)
    pu_scores = pu.oob_scores_.copy()
    nan = np.isnan(pu_scores)
    if nan.any():
        pu_scores[nan] = pu.predict_score(Xp[nan])
    pu_ranked = sorted(population, key=lambda ln: (-pu_scores[pu_idx[ln]], ln))

    # ---- Composite baseline over the same population ------------------------
    comp_df = composite_ranking(con)
    comp_flagged = [
        ln for ln in (str(x) for x in comp_df["loan_number"].tolist())
        if ln in slice_universe and ln not in train_pos
    ]
    flagged_set = set(comp_flagged)
    comp_tail = sorted(ln for ln in population if ln not in flagged_set)
    composite_ranked = comp_flagged + comp_tail

    # ---- RRF fusion of lgbm + composite -------------------------------------
    rrf_ranked = reciprocal_rank_fusion([lgbm_ranked, composite_ranked])

    def _evaluate(ranked: list[str]) -> dict:
        # Poisson bootstrap CIs on the top of the ranking. LightGBM scores every
        # loan > 0 (no natural flagged/unflagged cut), so resampling the full 965k
        # is wasteful — a loan ranked beyond ~5x the largest k effectively cannot
        # reach the top k after resampling, so truncate the head for speed.
        head_n = max(eff_ks) * 5 if eff_ks else n_pop
        return {
            "metrics": ranking_metrics(ranked, test_pos, base_rate, eff_ks),
            "ranks": positive_rank_stats(ranked, test_pos, n_pop),
            "cis": bootstrap_lift_cis(
                ranked[:head_n], test_pos, base_rate, eff_ks,
                n_boot=n_boot, seed=random_state,
            ),
        }

    rankings = {
        "lgbm": _evaluate(lgbm_ranked),
        "pu_bagging": _evaluate(pu_ranked),
        "composite": _evaluate(composite_ranked),
        "rrf_fusion": _evaluate(rrf_ranked),
    }

    # ---- Feature importance (LightGBM gain) ---------------------------------
    booster = lgbm.model_.booster_
    gains = booster.feature_importance(importance_type="gain")
    names = lgbm.feature_names_ or feature_names
    feature_importance = [
        (n, round(float(v), 4))
        for n, v in sorted(
            zip(names, gains, strict=True), key=lambda t: -t[1]
        )[:top_n]
    ]

    # ---- Verdict: lgbm vs composite on summed recall@ks (honest either way) -
    lgbm_recall = sum(rankings["lgbm"]["metrics"][k]["hits"] for k in eff_ks)
    comp_recall = sum(rankings["composite"]["metrics"][k]["hits"] for k in eff_ks)
    if lgbm_recall > comp_recall:
        verdict = "improved"
    elif lgbm_recall < comp_recall:
        verdict = "regressed"
    else:
        verdict = "neutral"

    return {
        "holdout_year": holdout_year,
        "ks": list(eff_ks),
        "n_train_positives": len(train_pos),
        "n_test_positives": len(test_pos),
        "population": n_pop,
        "base_rate": round(base_rate, 6),
        "tuned_params": best,
        "rankings": rankings,
        "feature_importance": feature_importance,
        "verdict": verdict,
    }
