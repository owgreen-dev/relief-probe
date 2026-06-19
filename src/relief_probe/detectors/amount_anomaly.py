"""Amount-anomaly detector — fabricated-looking loan amounts (per-loan, no joins).

What it finds
-------------
``naics_cohort_outlier`` and ``payroll_cap_exceedance`` ask whether a loan is *too
big* relative to peers or program math. This asks an orthogonal question about the
*shape* of the number itself: does ``current_approval_amount`` look **reverse-
engineered** rather than payroll-derived? Two documented sub-signals fire:

* **round-number** — a genuine 2.5x-average-monthly-payroll calculation almost never
  lands on an exact round figure; fabricated amounts cluster on round multiples of
  $1,000 / $5,000 / $10,000 (a Benford-adjacent tell). Graded so rounder is higher.
* **cap-maximization ("bunching")** — the implied per-employee loan
  (``amount / jobs_reported``) sits **at or just below** the program per-employee
  ceiling ($20,833 general; $29,167 for NAICS prefix ``72``), i.e. the borrower
  claimed the maximum allowable salary for *every* employee at once. This is the
  AT/just-below-cap band and is deliberately **distinct** from
  ``payroll_cap_exceedance`` (which flags strictly ABOVE the cap) so the two do not
  double-count the same loans.

Score is comparable *within* this detector: it is the sum of the two sub-signal
intensities (each in ``[0, 1]``), monotonic in how strongly each fires. A loan that
is both perfectly round and cap-maxed scores highest.

False-positive modes (documented, not hidden)
---------------------------------------------
* Plenty of legitimate loans are genuinely round (small businesses requesting tidy
  figures) or genuinely at the cap (well-paid small payrolls), so a hit is a review
  lead, never proof. This is why the detector ships EXPLORATORY, out of the default
  composite, pending real-data lift validation.

Read-only: the detector never writes to the warehouse.
"""

from __future__ import annotations

import duckdb

from relief_probe.detectors.base import Detector, Signal
from relief_probe.detectors.payroll_cap import (
    FIRST_DRAW_CAP,
    FOOD_ACCOMMODATION_CAP,
)

#: Round multiples to test, largest first, paired with the intensity weight a match
#: earns (a multiple of $10k is rounder/more suspicious than a multiple of $1k).
_ROUND_DIVISORS: tuple[tuple[int, float], ...] = (
    (10_000, 1.0),
    (5_000, 0.66),
    (1_000, 0.33),
)


class AmountAnomalyDetector(Detector):
    detector_id = "amount_anomaly"
    summary = (
        "Loan amount looks fabricated rather than payroll-derived: an exact round "
        "multiple and/or implied per-employee salary bunched at the program cap."
    )

    def __init__(self, *, cap_band: float = 0.05) -> None:
        # Cap-maximization fires when the implied per-employee loan sits within this
        # fraction BELOW the applicable ceiling (the at/just-below-cap "bunching"
        # band). Strictly above the cap is left to payroll_cap_exceedance.
        self.cap_band = cap_band

    @staticmethod
    def _round_score(amount: float) -> tuple[float, int | None]:
        """Roundness intensity in [0, 1] and the matched divisor (or None)."""
        cents = round(amount * 100)
        for divisor, weight in _ROUND_DIVISORS:
            if cents % (divisor * 100) == 0:
                return weight, divisor
        return 0.0, None

    def _cap_score(self, per_employee: float, cap: float) -> float:
        """Bunching intensity in [0, 1]: 0 at the band floor, 1 at the cap.

        Zero outside the band, including strictly above the cap (that region belongs
        to ``payroll_cap_exceedance``).
        """
        band_low = cap * (1.0 - self.cap_band)
        if not (band_low <= per_employee <= cap):
            return 0.0
        return (per_employee - band_low) / (cap - band_low)

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        rows = con.execute(
            """
            SELECT
                loan_number, borrower_name, naics_code, borrower_state,
                current_approval_amount, jobs_reported
            FROM loans
            WHERE current_approval_amount > 0
            """
        ).fetchall()

        signals: list[Signal] = []
        for loan_number, borrower_name, naics_code, state, amount, jobs in rows:
            amount = float(amount)
            round_score, divisor = self._round_score(amount)

            cap_score = 0.0
            per_employee: float | None = None
            cap: float | None = None
            if jobs is not None and float(jobs) >= 1:
                cap = (
                    FOOD_ACCOMMODATION_CAP
                    if (naics_code or "").startswith("72")
                    else FIRST_DRAW_CAP
                )
                per_employee = amount / float(jobs)
                cap_score = self._cap_score(per_employee, cap)

            score = round_score + cap_score
            if score <= 0:
                continue

            fired = []
            if round_score > 0:
                fired.append("round_number")
            if cap_score > 0:
                fired.append("cap_maximization")

            signals.append(
                Signal(
                    loan_number=str(loan_number),
                    detector_id=self.detector_id,
                    score=round(float(score), 4),
                    evidence={
                        "borrower_name": borrower_name,
                        "naics_code": naics_code,
                        "state": state,
                        "amount": round(amount, 2),
                        "jobs_reported": float(jobs) if jobs is not None else None,
                        "signals_fired": fired,
                        "round_divisor": divisor,
                        "round_score": round(float(round_score), 4),
                        "per_employee_amount": (
                            round(per_employee, 2) if per_employee is not None else None
                        ),
                        "per_employee_cap": (
                            round(float(cap), 2) if cap is not None else None
                        ),
                        "cap_maximization_score": round(float(cap_score), 4),
                    },
                )
            )
        return signals
