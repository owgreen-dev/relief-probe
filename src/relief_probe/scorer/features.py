"""Feature extraction for the learned PU scorer (pure NumPy/pandas, no sklearn).

Each loan in the $150k+ slice becomes a numeric feature row combining:

* **Structured program fields** — log amount, log jobs, log dollars-per-job, the
  payroll-proceed and forgiveness shares, term, SBA guaranty %, and a few binary
  fraud tells (single reported job, exact round-number amount, NAICS-72).
* **The unsupervised detector scores** — one column per ``detector_id`` in the
  ``signals`` table (0 when that detector did not fire). The detectors are NOT fit to
  labels, so using their scores as features is leakage-free; it lets the learned
  scorer combine them better than the hand-weighted composite.

Returns a dense matrix plus aligned loan_numbers and feature names. NaNs/nulls map to
neutral values so the matrix is model-ready.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

#: Structured feature columns, in a stable order.
_STRUCTURED = (
    "log_amount",
    "log_jobs",
    "log_amount_per_job",
    "payroll_share",
    "forgiveness_ratio",
    "term",
    "sba_guaranty_pct",
    "jobs_is_one",
    "round_amount",
    "is_naics_72",
)


def _structured_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Engineer the structured features from a raw loans frame."""
    amount = df["current_approval_amount"].astype(float)
    jobs = df["jobs_reported"].astype(float)
    safe_amount = amount.where(amount > 0, np.nan)
    out = pd.DataFrame(index=df.index)
    out["log_amount"] = np.log1p(amount.clip(lower=0).fillna(0.0))
    out["log_jobs"] = np.log1p(jobs.clip(lower=0).fillna(0.0))
    out["log_amount_per_job"] = np.log1p(
        (amount / jobs.clip(lower=1)).clip(lower=0).fillna(0.0)
    )
    out["payroll_share"] = (
        (df["payroll_proceed"].astype(float).fillna(0.0) / safe_amount)
        .clip(0, 2).fillna(0.0)
    )
    out["forgiveness_ratio"] = (
        (df["forgiveness_amount"].astype(float).fillna(0.0) / safe_amount)
        .clip(0, 2).fillna(0.0)
    )
    out["term"] = df["term"].astype(float).fillna(0.0)
    out["sba_guaranty_pct"] = df["sba_guaranty_pct"].astype(float).fillna(0.0)
    out["jobs_is_one"] = (jobs == 1).astype(float)
    out["round_amount"] = (
        (amount > 0) & (amount.fillna(-1) % 10_000 == 0)
    ).astype(float)
    naics = df["naics_code"].astype("string").fillna("")
    out["is_naics_72"] = naics.str.startswith("72").astype(float)
    return out[list(_STRUCTURED)]


def build_feature_matrix(
    con: duckdb.DuckDBPyConnection,
    *,
    min_amount: float = 150_000.0,
    loan_numbers: list[str] | None = None,
) -> tuple[np.ndarray, list[str], list[str]]:
    """Return ``(X, loan_numbers, feature_names)`` for the labelable slice.

    ``min_amount`` restricts to the $150k+ slice (where the labels live). Pass
    ``loan_numbers`` to score a specific subset instead. Detector-score columns are
    added for every ``detector_id`` present in ``signals`` (0 where absent).
    """
    where = "current_approval_amount >= ?"
    params: list = [min_amount]
    if loan_numbers is not None:
        if not loan_numbers:
            return np.empty((0, 0)), [], []
        placeholders = ", ".join("?" for _ in loan_numbers)
        where = f"loan_number IN ({placeholders})"
        params = list(loan_numbers)

    loans = con.execute(
        f"""
        SELECT loan_number, current_approval_amount, jobs_reported, payroll_proceed,
               forgiveness_amount, term, sba_guaranty_pct, naics_code
        FROM loans
        WHERE {where}
        """,
        params,
    ).fetch_df()
    if loans.empty:
        return np.empty((0, 0)), [], []
    loans["loan_number"] = loans["loan_number"].astype(str)

    feats = _structured_frame(loans)

    # Per-detector score columns, pivoted from signals (0 where a detector didn't fire).
    detectors = [
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT detector_id FROM signals ORDER BY detector_id"
        ).fetchall()
    ]
    det_cols: list[str] = []
    if detectors:
        sig = con.execute(
            "SELECT loan_number, detector_id, MAX(score) AS score "
            "FROM signals GROUP BY loan_number, detector_id"
        ).fetch_df()
        sig["loan_number"] = sig["loan_number"].astype(str)
        pivot = (
            sig.pivot(index="loan_number", columns="detector_id", values="score")
            .reindex(loans["loan_number"])
            .fillna(0.0)
        )
        for det in detectors:
            col = f"det_{det}"
            feats[col] = (
                pivot[det].to_numpy() if det in pivot.columns
                else np.zeros(len(loans))
            )
            det_cols.append(col)

    feature_names = list(_STRUCTURED) + det_cols
    X = feats[feature_names].to_numpy(dtype=np.float64)
    return X, loans["loan_number"].tolist(), feature_names
