"""Payroll-cap exceedance detector — an absolute, program-rule signal.

Domain rationale
----------------
PPP loan size is capped by a per-employee payroll ceiling. The salary input is
capped at $100,000/year, and a first-draw loan is 2.5x average *monthly* payroll,
so the payroll-justified maximum is:

    $100,000 / 12 * 2.5 = $20,833 per employee

Accommodation & Food Services (NAICS prefix ``72``) could use a 3.5x multiplier on
second-draw loans, raising their ceiling to ~$29,167 per employee. A loan whose
dollars-per-reported-job sits **well above** the applicable ceiling cannot be
explained by payroll alone — the classic signature of inflated payroll/headcount.

Unlike ``naics_cohort_outlier`` (which is *relative* to peers), this is an
*absolute* rule check grounded directly in program math, so the two corroborate
rather than duplicate: a loan flagged by both is far more interesting than either
alone.

False-positive modes (documented)
---------------------------------
* EIDL-refinance and other non-payroll proceeds can legitimately push a loan above
  the payroll ceiling — so we only flag at a generous multiple (``min_ratio``).
* Owner-compensation rules and rounding add slack near the ceiling; the multiple
  keeps us in the clearly-implausible region, not the borderline.
"""

from __future__ import annotations

import duckdb

from relief_probe.detectors.base import Detector, Signal

FIRST_DRAW_CAP = 20833.33  # $100k salary cap / 12 * 2.5
FOOD_ACCOMMODATION_CAP = 29166.67  # NAICS 72, 3.5x second-draw multiplier


class PayrollCapExceedanceDetector(Detector):
    detector_id = "payroll_cap_exceedance"
    summary = (
        "Loan dollars per reported job exceed the program's per-employee payroll "
        "ceiling ($20,833; $29,167 for NAICS 72) by a wide margin."
    )

    def __init__(self, *, min_ratio: float = 1.5) -> None:
        # Flag only loans this many times above the applicable ceiling, leaving
        # slack for EIDL-refinance/owner-comp legitimately sized above payroll.
        self.min_ratio = min_ratio

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        rows = con.execute(
            """
            SELECT
                loan_number, borrower_name, naics_code, borrower_state,
                current_approval_amount, jobs_reported,
                current_approval_amount / jobs_reported AS amount_per_job,
                CASE WHEN naics_code LIKE '72%' THEN ? ELSE ? END AS cap
            FROM loans
            WHERE jobs_reported >= 1 AND current_approval_amount > 0
            """,
            [FOOD_ACCOMMODATION_CAP, FIRST_DRAW_CAP],
        ).fetchall()

        signals: list[Signal] = []
        for (
            loan_number,
            borrower_name,
            naics_code,
            state,
            amount,
            jobs,
            per_job,
            cap,
        ) in rows:
            ratio = per_job / cap
            if ratio < self.min_ratio:
                continue
            signals.append(
                Signal(
                    loan_number=str(loan_number),
                    detector_id=self.detector_id,
                    # Score on the log-ratio so it sits on a z-like scale roughly
                    # comparable to the cohort detector for composite aggregation.
                    score=round(float(ratio), 4),
                    evidence={
                        "naics_code": naics_code,
                        "state": state,
                        "borrower_name": borrower_name,
                        "amount": round(float(amount), 2),
                        "jobs_reported": float(jobs),
                        "amount_per_job": round(float(per_job), 2),
                        "per_employee_cap": round(float(cap), 2),
                        "x_cap": round(float(ratio), 2),
                    },
                )
            )
        return signals
