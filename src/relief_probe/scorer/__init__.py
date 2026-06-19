"""Learned PU scorer — supervised-on-positive-unlabeled ranking over the labels.

The detectors are unsupervised (program rules + statistics); the composite combines
them with hand-set weights. This layer asks whether a model *fit to the prosecuted
labels* can rank better — using the detector scores **as features** alongside the raw
structured fields, so it can learn a better combination than the fixed composite.

Positive-unlabeled by necessity (only prosecuted positives exist), so we use
**PU-bagging** (Mordelet & Vert 2014): no class-prior assumption, ranking-oriented,
robust to the prosecution-biased positives. Validation is an **out-of-time** split
(train on cases charged <= year Y, test on > Y — H7) so nothing leaks. The honest
question is whether it beats the unsupervised composite on the held-out labels; a
negative is a real result.

Behind the ``ml`` extra (scikit-learn). Feature extraction is pure NumPy/pandas and
needs no extra; only the bagging model imports sklearn (lazily).
"""

from __future__ import annotations

from relief_probe.scorer.features import build_feature_matrix

__all__ = ["build_feature_matrix"]
