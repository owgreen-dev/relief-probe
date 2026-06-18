"""Shared cohort-scoring helpers for peer-relative detectors.

One implementation of the methodology — robust median/MAD z-scoring (optionally in
log space, because dollar ratios are right-skewed ~log-normal) and BH-FDR flagging
with a dual statistical + effect-size gate — so detectors cannot silently drift
from the documented method.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from relief_probe.stats import benjamini_hochberg, robust_z, upper_tail_p


def cohort_robust_z(
    df: pd.DataFrame,
    value_col: str,
    cohort_col: str = "cohort",
    *,
    log: bool = True,
    min_mad: float = 0.0,
) -> pd.Series:
    """Per-cohort robust z-score of ``value_col``, aligned to ``df`` index.

    Within each ``cohort_col`` group, score values with median/MAD ``robust_z``.
    When ``log=True`` the values are ``log1p``-transformed first (the right space
    for right-skewed dollar ratios). Degenerate cohorts (MAD == 0) yield NaN for
    their members, which callers treat as "no signal".

    ``min_mad`` floors the per-cohort dispersion (in the *transformed* units — log
    space when ``log=True``), so dense near-uniform cohorts cannot manufacture
    astronomical z-scores from tiny deviations. See ``stats.robust_z``.
    """
    values = df[value_col]
    if log:
        values = np.log1p(values.clip(lower=0))
    return values.groupby(df[cohort_col]).transform(
        lambda s: robust_z(s.to_numpy(), min_mad=min_mad)
    )


def fdr_flag(
    df: pd.DataFrame,
    score_col: str = "score",
    *,
    fdr: float,
    min_z: float,
) -> pd.DataFrame:
    """Add ``pvalue``/``qvalue``/``flagged`` columns via upper-tail p + BH-FDR.

    Each score becomes a one-sided upper-tail p-value, Benjamini-Hochberg adjusted
    across all rows, and flagged only when it clears BOTH gates: statistical
    significance (``qvalue <= fdr``) and a practical effect-size floor
    (``score >= min_z``). Mutates and returns ``df``.
    """
    df["pvalue"] = upper_tail_p(df[score_col].to_numpy())
    df["qvalue"] = benjamini_hochberg(df["pvalue"].to_numpy())
    df["flagged"] = (df["qvalue"] <= fdr) & (df[score_col] >= min_z)
    return df
