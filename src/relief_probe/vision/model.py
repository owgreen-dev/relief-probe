"""Train / load / apply the ELA-based document-authenticity classifier."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from relief_probe.vision.datasets import iter_labeled_images
from relief_probe.vision.ela import ela_features

MODEL_VERSION = 1


def _feature_matrix(items: list[tuple[Path, int]]) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for path, label in items:
        with Image.open(path) as img:
            X.append(ela_features(img))
        y.append(label)
    return np.asarray(X), np.asarray(y)


def train(
    data_dir: Path | str, *, out_path: Path | str | None = None, seed: int = 0
) -> dict:
    """Train a classifier on ``authentic/``+``forged/`` images; optionally save it.

    Returns a summary with cross-validated accuracy. Imports scikit-learn lazily so the
    rest of the package works without the ``vision`` extra installed.
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    items = iter_labeled_images(data_dir)
    if len(items) < 4:
        raise ValueError(f"need >= 4 labeled images, found {len(items)} in {data_dir}")
    X, y = _feature_matrix(items)

    clf = make_pipeline(
        StandardScaler(),
        RandomForestClassifier(n_estimators=200, random_state=seed),
    )
    folds = int(min(5, np.bincount(y).min()))
    cv_acc = (
        cross_val_score(clf, X, y, cv=folds, scoring="accuracy")
        if folds >= 2
        else np.array([float("nan")])
    )
    clf.fit(X, y)

    if out_path is not None:
        import joblib

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": clf, "version": MODEL_VERSION}, out)

    return {
        "n_images": len(items),
        "n_authentic": int((y == 0).sum()),
        "n_forged": int((y == 1).sum()),
        "cv_folds": folds,
        "cv_accuracy_mean": float(np.nanmean(cv_acc)),
        "cv_accuracy_std": float(np.nanstd(cv_acc)),
        "out_path": str(out_path) if out_path else None,
    }


def load_model(path: Path | str):
    import joblib

    bundle = joblib.load(path)
    return bundle["model"]


def forgery_probability(model, img: Image.Image) -> float:
    """P(forged) in [0,1] for one image."""
    feats = ela_features(img).reshape(1, -1)
    return float(model.predict_proba(feats)[0, 1])
