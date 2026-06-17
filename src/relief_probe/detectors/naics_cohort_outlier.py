"""NAICS-cohort outlier detector — the quant flagship.

Domain rationale
----------------
The most general PPP-loan anomaly is "this loan is sized unlike its peers." A
full-service restaurant in Texas should be compared to other full-service
restaurants in Texas, not to the national average across all industries. We form
peer cohorts on ``NAICS x state`` and score each loan on:

    amount_per_job = current_approval_amount / jobs_reported

PPP loan size was supposed to track payroll (~2.5x average monthly payroll), so
dollars-per-reported-job is a direct proxy for "is the loan plausible for the
workforce claimed?" The suspicious tail is *high* amount-per-job: more dollars per
reported employee than industry-and-geography peers.

Why robust statistics + log space
----------------------------------
Loan-per-job is heavy-tailed and right-skewed; a naive mean/sigma z-score would be
inflated by the very outliers we hunt and would flag the whole natural upper tail.
We robust-score on ``log1p(amount_per_job)`` with median/MAD (the 0.6745 factor
rescales MAD to sigma), so the z measures genuine departure from the peer shape.
(Evidence reports the human-readable raw medians, not the logged ones.)

Multiple-testing / FDR control
------------------------------
With ~1M loans, any fixed z cutoff flags many by chance. We convert each loan's
robust z to an upper-tail p-value and apply Benjamini-Hochberg across the scored
population, flagging at a target false-discovery rate, plus a minimum effect-size
floor. The benchmark (Layer 3) is the real arbiter of whether the ranking carries
signal; BH here is principled threshold selection on heavy-tailed data.

False-positive modes (documented, not hidden)
---------------------------------------------
* High-wage industries / owner-heavy small firms can legitimately sit high.
* EIDL-refinance proceeds folded into a PPP loan inflate amount above payroll.
* Thin cohorts — we require ``min_cohort_size`` peers and skip the rest.
* Degenerate cohorts (MAD == 0) — yield no signal, by design.
"""

from __future__ import annotations

import duckdb
import numpy as np

from relief_probe.detectors._cohort import cohort_robust_z, fdr_flag
from relief_probe.detectors.base import Detector, Signal


class NaicsCohortOutlierDetector(Detector):
    detector_id = "naics_cohort_outlier"
    summary = (
        "Loan dollars per reported job far above NAICS x state peers "
        "(robust median/MAD z-score on log1p, BH-FDR controlled)."
    )

    def __init__(
        self, *, min_cohort_size: int = 30, fdr: float = 0.01, min_z: float = 3.0
    ) -> None:
        self.min_cohort_size = min_cohort_size
        self.fdr = fdr
        self.min_z = min_z

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        df = con.execute(
            """
            SELECT loan_number, borrower_name, naics_code, borrower_state,
                   current_approval_amount, jobs_reported
            FROM loans
            WHERE jobs_reported >= 1
              AND current_approval_amount > 0
              AND naics_code IS NOT NULL
              AND borrower_state IS NOT NULL
            """
        ).fetch_df()
        if df.empty:
            return []

        df["amount_per_job"] = df["current_approval_amount"] / df["jobs_reported"]
        df["cohort"] = df["naics_code"] + " | " + df["borrower_state"]
        df["cohort_size"] = df.groupby("cohort")["loan_number"].transform("size")
        df = df[df["cohort_size"] >= self.min_cohort_size].copy()
        if df.empty:
            return []

        df["score"] = (
            cohort_robust_z(df, "amount_per_job", log=True).clip(lower=0).fillna(0.0)
        )
        df["cohort_median"] = df.groupby("cohort")["amount_per_job"].transform("median")
        df = fdr_flag(df, "score", fdr=self.fdr, min_z=self.min_z)

        flagged = df[df["flagged"]]
        return [
            Signal(
                loan_number=str(row.loan_number),
                detector_id=self.detector_id,
                score=round(float(row.score), 4),
                evidence=self._evidence(row),
            )
            for row in flagged.itertuples(index=False)
        ]

    def _evidence(self, row) -> dict:
        median = float(row.cohort_median)
        per_job = float(row.amount_per_job)
        return {
            "cohort": row.cohort,
            "cohort_size": int(row.cohort_size),
            "naics_code": row.naics_code,
            "state": row.borrower_state,
            "borrower_name": row.borrower_name,
            "amount": round(float(row.current_approval_amount), 2),
            "jobs_reported": float(row.jobs_reported),
            "amount_per_job": round(per_job, 2),
            "cohort_median_amount_per_job": round(median, 2),
            "x_cohort_median": round(per_job / median, 2) if median else None,
            "robust_z": round(float(row.score), 4),
            "p_value": _round(row.pvalue, 6),
            "q_value": _round(row.qvalue, 6),
            "fdr_target": self.fdr,
        }


def _round(x, n: int = 4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return round(float(x), n)
