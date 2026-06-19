"""Lender-concentration detector — an UNSUPERVISED, peer-relative LENDER signal.

Domain rationale
----------------
GAO and academic work on PPP fraud found that a handful of nonbank / fintech
lenders running near-automatic approval originated a disproportionate share of the
loans that were later charged as fraud. The tell is not any single loan but the
*shape of a lender's whole book*: a lender whose loans are unusually often
program-rule-implausible is structurally suspect, and even its individually-clean
loans deserve a second look — exactly the loans the per-loan ``$/job`` detectors
miss.

Why label-free (and why that matters)
-------------------------------------
The detector deliberately NEVER reads ``fraud_cases`` or any label table. Training a
"which lenders are bad" signal on prosecuted-fraud labels would both leak the
benchmark's answer and inherit prosecution bias (who got charged, not who offended).
Instead the suspicious predicate is a pure PROGRAM RULE: a loan whose dollars per
reported job exceeds the per-employee payroll ceiling ($20,833; $29,167 for NAICS
prefix ``72``) — the same absolute rule the ``payroll_cap`` detector uses. That makes
the per-lender suspicious *rate* a label-free statistic.

Why peer-relative (rate, not volume)
------------------------------------
Big banks legitimately originate enormous *volumes*; raw counts would just rank the
largest lenders. The anomaly is the RATE of cap-busting loans within a lender's book,
scored relative to its peers. We robust-z each qualifying lender's suspicious rate
across all lenders (median/MAD via :func:`relief_probe.stats.robust_z`, with a
``min_mad`` floor so a near-degenerate peer set can't manufacture an absurd z) and
flag every loan from a lender in the extreme upper tail (``z >= min_z``). The score is
the lender's z — comparable *within this detector* and monotonic in lender anomaly.

False-positive modes (documented, not hidden)
---------------------------------------------
* A high cap-busting rate can reflect a lender's legitimate INDUSTRY MIX, not fraud:
  a book concentrated in owner-heavy or NAICS-72 businesses sits naturally higher on
  ``$/job``. The per-NAICS cap softens but does not erase this.
* EIDL-refinance and other non-payroll proceeds push individual loans above the
  ceiling, inflating a lender's rate without any wrongdoing.
* Thin lenders are noisy, so we require ``min_loans`` loans before a lender's rate is
  considered (others are skipped, not flagged).

Read-only and label-free: works unchanged on a warehouse whose ``fraud_cases`` table
is empty (the tests prove exactly this). Loans with a null/blank originating lender or
unusable jobs/amount are skipped gracefully.
"""

from __future__ import annotations

from collections import defaultdict

import duckdb
import numpy as np

from relief_probe.detectors.base import Detector, Signal
from relief_probe.detectors.payroll_cap import FIRST_DRAW_CAP, FOOD_ACCOMMODATION_CAP
from relief_probe.stats import robust_z


class LenderConcentrationDetector(Detector):
    detector_id = "lender_concentration"
    summary = (
        "Loan originated by a lender whose book has an unusually high RATE of "
        "cap-busting ($/job over the per-employee payroll ceiling) loans, "
        "robust-z scored across lenders (peer-relative, label-free)."
    )

    def __init__(
        self,
        *,
        min_loans: int = 100,
        min_z: float = 3.0,
        min_mad: float = 0.01,
    ) -> None:
        #: Minimum usable loans a lender must have before its suspicious rate is
        #: stable enough to score. Thinner lenders are skipped, not flagged.
        self.min_loans = min_loans
        #: Flag every loan from a lender whose peer robust-z >= this.
        self.min_z = min_z
        #: Floor on the cross-lender MAD (in rate units, 0..1). A near-degenerate
        #: peer set — almost every lender at the same rate — would otherwise turn a
        #: modest gap into an absurd z; this caps that artifact while leaving a
        #: well-dispersed lender population untouched.
        self.min_mad = min_mad

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        rows = con.execute(
            """
            SELECT originating_lender, loan_number, naics_code,
                   current_approval_amount, jobs_reported
            FROM loans
            WHERE originating_lender IS NOT NULL AND originating_lender <> ''
              AND jobs_reported >= 1 AND current_approval_amount > 0
            """
        ).fetchall()
        if not rows:
            return []

        # Group usable loans by lender. Per loan we keep its number and whether it
        # busts the applicable per-employee cap — a label-free program-rule predicate.
        by_lender: dict[str, list[tuple[str, bool]]] = defaultdict(list)
        for lender, loan_number, naics, amount, jobs in rows:
            cap = FOOD_ACCOMMODATION_CAP if _is_naics_72(naics) else FIRST_DRAW_CAP
            busts_cap = (float(amount) / float(jobs)) >= cap
            by_lender[lender].append((str(loan_number), busts_cap))

        # Per-lender suspicious RATE (not volume — big banks have huge volume), only
        # for lenders with enough loans for the rate to be stable.
        lenders: list[str] = []
        rates: list[float] = []
        for lender, loans in by_lender.items():
            if len(loans) < self.min_loans:
                continue
            suspicious = sum(1 for _, busts in loans if busts)
            lenders.append(lender)
            rates.append(suspicious / len(loans))
        if not lenders:
            return []

        # Robust-z the rate across the qualifying lenders; the extreme upper tail is
        # the anomaly. NaN (degenerate peer set) -> no signal.
        zs = robust_z(np.asarray(rates), min_mad=self.min_mad)

        signals: list[Signal] = []
        for lender, rate, z in zip(lenders, rates, zs, strict=True):
            if np.isnan(z) or z < self.min_z:
                continue
            loans = by_lender[lender]
            suspicious = sum(1 for _, busts in loans if busts)
            score = round(float(z), 4)
            evidence = {
                "originating_lender": lender,
                "lender_loan_count": len(loans),
                "lender_suspicious_count": suspicious,
                "lender_suspicious_rate": round(rate, 4),
                "lender_robust_z": score,
                "min_loans": self.min_loans,
                "min_z": self.min_z,
            }
            # Flag EVERY loan from the structurally-bad book, including the
            # individually-clean ones the per-loan detectors never see.
            for loan_number, _ in loans:
                signals.append(
                    Signal(
                        loan_number=loan_number,
                        detector_id=self.detector_id,
                        score=score,
                        evidence=evidence,
                    )
                )
        return signals


def _is_naics_72(naics: str | None) -> bool:
    """Accommodation & Food Services (NAICS prefix 72) — higher second-draw cap."""
    return bool(naics) and str(naics).startswith("72")
