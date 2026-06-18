"""Shared robust-statistics helpers used by detectors.

Kept separate so the methodology (robust scoring, multiple-testing control) is
testable in isolation and reused identically across detectors.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

# MAD -> sigma consistency constant for normal data: 1 / Phi^-1(0.75).
MAD_TO_SIGMA = 0.6744897501960817


def robust_z(x: np.ndarray, *, min_mad: float = 0.0) -> np.ndarray:
    """One-sample robust z-score via median / MAD.

    ``z = 0.6745 * (x - median) / MAD``. Returns NaN where MAD == 0 (a
    degenerate cohort where a majority share the same value — no estimable
    dispersion), which callers treat as "no signal" rather than an error.

    ``min_mad`` floors the dispersion estimate (in the same units as ``x``). A
    *near*-degenerate cohort — almost every value identical, MAD ~ 1e-5 but not
    exactly 0 — otherwise turns a modest deviation into an absurd z (tens of
    thousands of sigma), which then dominates any downstream ranking. Flooring
    MAD says "we don't believe dispersion is reliably tighter than this," capping
    such artifacts while leaving well-dispersed cohorts untouched. Truly
    degenerate cohorts (raw MAD == 0) still yield NaN regardless of the floor.
    """
    x = np.asarray(x, dtype=float)
    med = np.nanmedian(x)
    mad = np.nanmedian(np.abs(x - med))
    if mad == 0 or np.isnan(mad):
        return np.full_like(x, np.nan)
    return MAD_TO_SIGMA * (x - med) / max(mad, min_mad)


def upper_tail_p(z: np.ndarray) -> np.ndarray:
    """One-sided upper-tail p-value for a (robust) z under the normal null."""
    z = np.asarray(z, dtype=float)
    p = stats.norm.sf(z)
    # NaN z (degenerate cohort) -> not significant.
    return np.where(np.isnan(z), 1.0, p)


def benjamini_hochberg(pvals: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted q-values (FDR control).

    Returns, for each input p-value, the smallest FDR level at which it would
    be rejected. Compare against a target q (e.g. flag where q <= 0.05).
    """
    p = np.asarray(pvals, dtype=float)
    n = p.size
    if n == 0:
        return p
    order = np.argsort(p)
    ranked = p[order]
    # q_(i) = min over j>=i of (n/j) * p_(j), enforced monotone from the tail.
    factors = n / np.arange(1, n + 1)
    q_sorted = np.minimum.accumulate((ranked * factors)[::-1])[::-1]
    q_sorted = np.clip(q_sorted, 0, 1)
    out = np.empty(n, dtype=float)
    out[order] = q_sorted
    return out
