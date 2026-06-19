"""LightGBM PU scorer (Loop 6) — behind the ``ml`` extra.

A regularized gradient-boosted retry of the row-wise *prediction* bet. We have only
prosecuted *positives* and a vast pool of *unlabeled* loans (mostly non-fraud, some
unprosecuted fraud). Following PLODI, we treat a **downsampled** random draw of the
unlabeled as negatives (default ~1:30, configurable) and train a single regularized
``LGBMClassifier``; the loan's score is ``P(positive)``. Gradient-boosted trees can
find the feature *interactions* that a linear composite + bagged shallow trees
(:class:`~relief_probe.scorer.pu_bagging.PUBaggingScorer`, the baseline to beat) miss.

The API mirrors :class:`PUBaggingScorer` so it slots into the same validation harness.
``lightgbm`` is imported lazily inside :meth:`fit` / :meth:`predict_score`; a missing
``ml`` extra raises a clear :class:`RuntimeError`. The anti-overfit levers are
*regularization* (shallow leaves, large ``min_child_samples``, sub-sampled features +
rows, L1/L2) and *downsampling* the unlabeled — the temporal holdout (SIGN-013) is what
ultimately keeps the verdict honest. Optional **monotone constraints** pin documented
"higher = more suspicious" features (``payroll_x_cap``, ``*robust_z``) to a
non-decreasing response, which only applies when ``X`` is a labelled DataFrame.
"""

from __future__ import annotations

from typing import Any

import numpy as np

#: Feature columns whose response is constrained non-decreasing (higher ⇒ more
#: suspicious). Only applied when ``X`` is a DataFrame (so columns are named).
_MONOTONE_INCREASING: tuple[str, ...] = (
    "payroll_x_cap",
    "cohort_robust_z",
    "lender_robust_z",
)


def _require_lightgbm() -> Any:
    """Import ``lightgbm`` lazily, or raise a clear ``ml``-extra error."""
    try:
        import lightgbm  # noqa: PLC0415 - lazy by design (keeps core env clean)
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise RuntimeError(
            "The LightGBM learned scorer needs the `ml` extra. Install it with "
            "`uv sync --extra ml`."
        ) from exc
    return lightgbm


class LgbmPuScorer:
    """Regularized LightGBM PU classifier producing ``P(positive)`` per loan.

    :meth:`fit` takes the feature matrix ``X`` (a NumPy array or a pandas DataFrame
    with ``category``-dtype columns) and a boolean ``positive_mask``; it downsamples
    the unlabeled rows to ``downsample_ratio`` negatives per positive, then fits one
    regularized ``LGBMClassifier`` with ``scale_pos_weight`` set from the resulting
    pos/neg balance. :meth:`predict_score` returns ``P(positive)`` for arbitrary rows.
    Deterministic for a fixed ``random_state``.
    """

    def __init__(
        self,
        *,
        downsample_ratio: int = 30,
        num_leaves: int = 31,
        min_child_samples: int = 50,
        learning_rate: float = 0.02,
        n_estimators: int = 400,
        feature_fraction: float = 0.7,
        bagging_fraction: float = 0.7,
        bagging_freq: int = 1,
        reg_lambda: float = 1.0,
        reg_alpha: float = 0.0,
        monotone: bool = True,
        random_state: int = 0,
    ) -> None:
        self.downsample_ratio = downsample_ratio
        self.num_leaves = num_leaves
        self.min_child_samples = min_child_samples
        self.learning_rate = learning_rate
        self.n_estimators = n_estimators
        self.feature_fraction = feature_fraction
        self.bagging_fraction = bagging_fraction
        self.bagging_freq = bagging_freq
        self.reg_lambda = reg_lambda
        self.reg_alpha = reg_alpha
        self.monotone = monotone
        self.random_state = random_state
        self.model_: Any | None = None
        self.feature_names_: list[str] | None = None

    def _monotone_constraints(self, feature_names: list[str]) -> list[int] | None:
        """``+1`` for each documented monotone-increasing feature, else ``0``."""
        if not self.monotone:
            return None
        cons = [1 if n in _MONOTONE_INCREASING else 0 for n in feature_names]
        return cons if any(cons) else None

    def fit(
        self,
        X: Any,
        positive_mask: np.ndarray,
        *,
        categorical: list[str] | None = None,
        group: np.ndarray | None = None,
    ) -> LgbmPuScorer:
        """Fit on positives + a downsampled draw of unlabeled-as-negative rows.

        ``categorical`` is the list of categorical column names (passed straight to
        LightGBM's ``categorical_feature=`` when ``X`` is a DataFrame). ``group`` is
        accepted for API symmetry with the validation harness (entity grouping is
        applied upstream during CV) and is not used by the classifier fit.
        """
        lgb = _require_lightgbm()
        import pandas as pd  # noqa: PLC0415 - lazy with lightgbm

        positive_mask = np.asarray(positive_mask, dtype=bool)
        is_frame = isinstance(X, pd.DataFrame)
        n = len(X) if is_frame else np.asarray(X).shape[0]
        if positive_mask.shape[0] != n:
            raise ValueError("positive_mask length must match X rows")

        p_idx = np.where(positive_mask)[0]
        u_idx = np.where(~positive_mask)[0]
        if len(p_idx) == 0 or len(u_idx) == 0:
            raise ValueError("need at least one positive and one unlabeled row")

        rng = np.random.default_rng(self.random_state)
        k = min(self.downsample_ratio * len(p_idx), len(u_idx))
        sampled = rng.choice(u_idx, size=k, replace=False)
        train_idx = np.concatenate([p_idx, sampled])
        train_idx.sort()  # preserve row order for reproducibility
        y = positive_mask[train_idx].astype(int)
        n_pos, n_neg = int(y.sum()), int((y == 0).sum())

        if is_frame:
            X_train = X.iloc[train_idx]
            self.feature_names_ = list(X.columns)
            cat = categorical or [
                c for c in X.columns if str(X[c].dtype) == "category"
            ]
            cat_arg: Any = cat or "auto"
        else:
            X_train = np.asarray(X, dtype=np.float64)[train_idx]
            self.feature_names_ = [f"f{i}" for i in range(X_train.shape[1])]
            cat_arg = "auto"

        monotone = self._monotone_constraints(self.feature_names_)
        self.model_ = lgb.LGBMClassifier(
            objective="binary",
            num_leaves=self.num_leaves,
            min_child_samples=self.min_child_samples,
            learning_rate=self.learning_rate,
            n_estimators=self.n_estimators,
            feature_fraction=self.feature_fraction,
            bagging_fraction=self.bagging_fraction,
            bagging_freq=self.bagging_freq,
            reg_lambda=self.reg_lambda,
            reg_alpha=self.reg_alpha,
            scale_pos_weight=(n_neg / n_pos) if n_pos else 1.0,
            monotone_constraints=monotone,
            random_state=self.random_state,
            deterministic=True,
            force_col_wise=True,
            n_jobs=1,
            verbosity=-1,
        )
        self.model_.fit(X_train, y, categorical_feature=cat_arg)
        return self

    def predict_score(self, X: Any) -> np.ndarray:
        """Return ``P(positive)`` for each row of ``X`` (a 1-D NumPy array)."""
        if self.model_ is None:
            raise RuntimeError("fit() must be called before predict_score()")
        import pandas as pd  # noqa: PLC0415 - lazy with lightgbm

        if not isinstance(X, pd.DataFrame):
            X = np.asarray(X, dtype=np.float64)
        return self.model_.predict_proba(X)[:, 1]
