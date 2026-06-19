"""PU-bagging learned scorer (Mordelet & Vert 2014) — behind the ``ml`` extra.

Positive-unlabeled bagging: we only have prosecuted *positives* and a vast pool of
*unlabeled* loans (mostly non-fraud, some unprosecuted fraud). Each bag trains a base
classifier on all positives (label 1) plus a random bootstrap of the unlabeled treated
as negatives (label 0); a loan's score is averaged over the bags where it was
**out-of-bag** (never in that bag's training set). This needs no class-prior estimate,
is ranking-oriented, and averages out the noise from the (inevitably mislabeled)
unlabeled-as-negative draws — the right fit for prosecution-biased PU labels.

``scikit-learn`` is imported lazily; a missing ``ml`` extra raises a clear error. The
base estimator is injectable (default: a shallow decision tree — bagging shallow trees
is the classic PU-bagging recipe and keeps per-bag fitting fast on small positive sets).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np


def _default_base() -> Callable[[], Any]:
    """A fresh shallow decision tree per bag (lazy sklearn import)."""
    try:
        from sklearn.tree import DecisionTreeClassifier
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise RuntimeError(
            "The learned PU scorer needs the `ml` extra. Install it with "
            "`uv sync --extra ml`."
        ) from exc
    return lambda: DecisionTreeClassifier(
        max_depth=8, min_samples_leaf=5, class_weight="balanced"
    )


class PUBaggingScorer:
    """Bagged PU classifier producing an out-of-bag ranking score per loan.

    ``n_estimators`` bags; each draws ``max_samples`` unlabeled rows (default: as many
    as there are positives) as negatives. ``oob_scores_`` (set by :meth:`fit`) is the
    OOB-averaged P(positive) for every row — NaN for rows always in-bag (the
    positives). :meth:`predict_score` averages all bags for arbitrary X.
    """

    def __init__(
        self,
        *,
        n_estimators: int = 50,
        max_samples: int | None = None,
        random_state: int = 0,
        base: Callable[[], Any] | None = None,
    ) -> None:
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.random_state = random_state
        self._base = base or _default_base()
        self.estimators_: list[Any] = []
        self.oob_scores_: np.ndarray | None = None

    def fit(self, X: np.ndarray, positive_mask: np.ndarray) -> PUBaggingScorer:
        X = np.asarray(X, dtype=np.float64)
        positive_mask = np.asarray(positive_mask, dtype=bool)
        n = X.shape[0]
        p_idx = np.where(positive_mask)[0]
        u_idx = np.where(~positive_mask)[0]
        if len(p_idx) == 0 or len(u_idx) == 0:
            raise ValueError("need at least one positive and one unlabeled row")
        k = min(self.max_samples or len(p_idx), len(u_idx))

        rng = np.random.default_rng(self.random_state)
        score_sum = np.zeros(n)
        oob_count = np.zeros(n)
        self.estimators_ = []
        for _ in range(self.n_estimators):
            sampled = rng.choice(u_idx, size=k, replace=True)
            train_idx = np.concatenate([p_idx, sampled])
            y = np.concatenate([np.ones(len(p_idx)), np.zeros(len(sampled))])
            clf = self._base()
            clf.fit(X[train_idx], y)
            self.estimators_.append(clf)
            in_bag = np.zeros(n, dtype=bool)
            in_bag[sampled] = True
            in_bag[p_idx] = True
            oob = ~in_bag
            if oob.any():
                score_sum[oob] += clf.predict_proba(X[oob])[:, 1]
                oob_count[oob] += 1

        with np.errstate(invalid="ignore"):
            self.oob_scores_ = np.where(
                oob_count > 0, score_sum / np.maximum(oob_count, 1), np.nan
            )
        return self

    def predict_score(self, X: np.ndarray) -> np.ndarray:
        """Mean P(positive) over all bags (for rows not scored via OOB)."""
        if not self.estimators_:
            raise RuntimeError("fit() must be called before predict_score()")
        X = np.asarray(X, dtype=np.float64)
        return np.mean(
            [clf.predict_proba(X)[:, 1] for clf in self.estimators_], axis=0
        )
