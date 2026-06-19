"""Establishment-overcount detector — implausible PPP loan DENSITY per industry-geo.

Domain rationale
----------------
Griffin, Kruger & Mahajan (J.Finance 2023) found that roughly a fifth of first-draw
PPP loans were "excess" relative to the number of business establishments that
actually exist in the borrower's industry-geography (much higher for fintech-
originated loans). The fraud tell is not loan *size* (that is what the $/job
detectors measure) but loan *count*: far more PPP loans in a (ZIP x NAICS) cell than
there are real businesses there to plausibly receive them.

This detector is deliberately ORTHOGONAL to ``naics_cohort_outlier`` /
``payroll_cap_exceedance``: it ignores dollars entirely and measures density. We
join PPP loans to Census ZIP Business Patterns (ZBP) establishment counts directly
on ``loans.borrower_zip`` (no zip->county crosswalk; see ingest/establishments.py)
and flag every loan in a cell where

    ratio = ppp_loan_count / max(establishment_count, 1)

exceeds ``min_ratio``. The score is ``log(ratio)`` — monotonic in the overcount, so
denser-than-plausible cells rank above marginally-over ones.

NAICS granularity (documented choice)
-------------------------------------
ZBP publishes establishment counts at several NAICS rollups (2/4/6-digit) in one
file. We truncate each loan's 6-digit ``naics_code`` to ``naics_digits`` and match
the establishments row at exactly that code, so the establishments table must be
loaded at the same granularity. The default is the full 6-digit code; coarser
truncation (4- or 2-digit) trades specificity for denser establishment coverage and
is a constructor parameter so real-data tuning is a one-line change.

Coverage / safety
-----------------
A cell with NO matching establishments row is *skipped*, not flagged: a missing ZBP
row means we have no establishment count to compare against, and treating "absent"
as "zero establishments" would falsely flag every cell outside our loaded slice.
This is a deliberate false-negative-over-false-positive tradeoff.

False-positive modes (documented, not hidden)
---------------------------------------------
* ZBP vintage predates 2020–21 growth, so genuinely new establishments are missing.
* Home-based / online / gig businesses legitimately operate without a local
  establishment, so dense ZIPs of sole proprietors can over-fire.
* Thin cells (1–2 establishments) make the ratio jumpy; ``min_ratio`` is the guard.

Returns ``[]`` gracefully when the establishments table is empty or missing (so the
detector is safe before the Census data is ingested). Loans with a null/blank ZIP or
NAICS are skipped. Read-only: the detector never writes to the warehouse.
"""

from __future__ import annotations

import math
from collections import defaultdict

import duckdb

from relief_probe.detectors.base import Detector, Signal


class EstablishmentOvercountDetector(Detector):
    detector_id = "establishment_overcount"
    summary = (
        "More PPP loans in a ZIP x NAICS cell than there are business "
        "establishments that exist there (Census ZBP density overcount)."
    )

    def __init__(self, *, min_ratio: float = 4.0, naics_digits: int = 6) -> None:
        #: Flag a cell when ppp_loan_count / max(establishments, 1) >= min_ratio.
        #: 4x is a deliberately conservative default — a cell with four times more
        #: loans than businesses is hard to explain by ZBP vintage/coverage alone.
        self.min_ratio = min_ratio
        #: NAICS rollup used to form cells. Loan naics_code is truncated to this many
        #: leading digits and matched against establishments.naics at the same width.
        self.naics_digits = naics_digits

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        # Establishment counts keyed by (zip, naics-at-loaded-granularity). Guard the
        # table being absent or empty so the detector is safe pre-ingest.
        try:
            est_rows = con.execute(
                "SELECT zip, naics, establishments FROM establishments"
            ).fetchall()
        except duckdb.CatalogException:
            return []
        est_map: dict[tuple[str, str], int] = {}
        for zip_code, naics, establishments in est_rows:
            if zip_code is None or naics is None or establishments is None:
                continue
            est_map[(zip_code, naics)] = int(establishments)
        if not est_map:
            return []

        loans = con.execute(
            """
            SELECT loan_number, borrower_zip, naics_code
            FROM loans
            WHERE borrower_zip IS NOT NULL AND borrower_zip <> ''
              AND naics_code IS NOT NULL AND naics_code <> ''
            """
        ).fetchall()

        # Bucket loans into (zip5, naics-cell). Census ZBP keys on the 5-digit ZIP,
        # but loans.borrower_zip is a MIX of 5-digit ("90240") and ZIP+4
        # ("92627-3582") in the real data, so we truncate to the first 5 digits to
        # join (otherwise every ZIP+4 loan silently fails to match). naics_digits
        # truncation lets a 6-digit loan code line up with a coarser ZBP rollup if one
        # was loaded.
        cells: dict[tuple[str, str], list[str]] = defaultdict(list)
        for loan_number, zip_code, naics_code in loans:
            zip5 = zip_code[:5]
            cell = naics_code[: self.naics_digits]
            cells[(zip5, cell)].append(str(loan_number))

        signals: list[Signal] = []
        for (zip_code, cell), loan_numbers in cells.items():
            establishment_count = est_map.get((zip_code, cell))
            if establishment_count is None:  # no ZBP row → can't compare → skip
                continue
            ppp_loan_count = len(loan_numbers)
            ratio = ppp_loan_count / max(establishment_count, 1)
            if ratio < self.min_ratio:  # plausible density → quiet
                continue

            score = round(math.log(ratio), 4)  # monotonic in the overcount
            evidence = {
                "zip": zip_code,
                "naics_cell": cell,
                "naics_digits": self.naics_digits,
                "ppp_loan_count": ppp_loan_count,
                "establishment_count": establishment_count,
                "ratio": round(ratio, 4),
                "min_ratio": self.min_ratio,
            }
            for loan_number in loan_numbers:
                signals.append(
                    Signal(
                        loan_number=loan_number,
                        detector_id=self.detector_id,
                        score=score,
                        evidence=evidence,
                    )
                )
        return signals
