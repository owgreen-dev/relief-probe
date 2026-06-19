"""Tests for the LightGBM PU scorer — gated by ``pytest.importorskip("lightgbm")``.

Mirrors ``test_scorer.py::test_pu_bagging_ranks_held_out_positives_above_noise`` on a
controlled synthetic set where the positives are linearly separable on feature 0, so
the model must rank held-out true positives above noise. Also checks the
positives+unlabeled requirement and determinism under a fixed ``random_state``.
"""

from __future__ import annotations

import numpy as np
import pytest


def _separable_set():
    """40 signal rows (feature 0 high) + 160 noise rows; 20 known positives."""
    rng = np.random.default_rng(0)
    signal = np.column_stack([rng.normal(5, 1, 40), rng.normal(0, 1, 40)])
    noise = np.column_stack([rng.normal(0, 1, 160), rng.normal(0, 1, 160)])
    X = np.vstack([signal, noise])
    mask = np.zeros(200, dtype=bool)
    mask[:20] = True  # 20 of the signal group are KNOWN positives (train)
    return X, mask


def test_lgbm_ranks_held_out_positives_above_noise():
    pytest.importorskip("lightgbm")
    from relief_probe.scorer.lgbm import LgbmPuScorer

    X, mask = _separable_set()
    held_out = slice(20, 40)  # the other 20 signal rows are unlabeled true positives
    noise_idx = slice(40, 200)

    scorer = LgbmPuScorer(downsample_ratio=8, random_state=0).fit(X, mask)
    scores = scorer.predict_score(X)
    assert scores.shape == (200,)
    assert scores[held_out].mean() > scores[noise_idx].mean() + 0.1


def test_lgbm_requires_positives_and_unlabeled():
    pytest.importorskip("lightgbm")
    from relief_probe.scorer.lgbm import LgbmPuScorer

    X = np.random.default_rng(0).normal(size=(10, 2))
    with pytest.raises(ValueError):
        LgbmPuScorer().fit(X, np.zeros(10, dtype=bool))  # no positives
    with pytest.raises(ValueError):
        LgbmPuScorer().fit(X, np.ones(10, dtype=bool))  # no unlabeled


def test_lgbm_predict_requires_fit():
    pytest.importorskip("lightgbm")
    from relief_probe.scorer.lgbm import LgbmPuScorer

    with pytest.raises(RuntimeError):
        LgbmPuScorer().predict_score(np.zeros((3, 2)))


def test_lgbm_is_deterministic():
    pytest.importorskip("lightgbm")
    from relief_probe.scorer.lgbm import LgbmPuScorer

    X, mask = _separable_set()
    a = LgbmPuScorer(downsample_ratio=8, random_state=0).fit(X, mask).predict_score(X)
    b = LgbmPuScorer(downsample_ratio=8, random_state=0).fit(X, mask).predict_score(X)
    np.testing.assert_array_equal(a, b)


def test_lgbm_module_imports_without_lightgbm():
    # The module itself must import with no lightgbm present (lazy import in fit).
    import importlib

    mod = importlib.import_module("relief_probe.scorer.lgbm")
    assert hasattr(mod, "LgbmPuScorer")
