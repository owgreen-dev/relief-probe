"""Multiple-funded-loans detector — duplicate funding via entity resolution.

What it finds
-------------
The PPP rules let one borrower hold **at most one first-draw** loan
(``processing_method='PPP'``) and **at most one second-draw** loan (``'PPS'``).
A resolved entity holding more than that — two of the same draw, or more than two
funded loans total — drew program money it was not eligible for. GAO flagged on the
order of tens of thousands of recipients with multiple funded loans.

Where ``duplicate_address_ring`` finds *many distinct borrowers* at one building,
this finds *one resolved borrower* spread across many loans. The two are
deliberately distinct views: a ring is co-location of unrelated names; this is a
single entity (same normalized name **and** building, via :func:`entity_key`)
double-dipping.

Grouping rule
-------------
Loans are grouped by :func:`entity_key` (normalized name + building address).
Loans whose entity is unkeyable (``entity_key`` returns ``None`` — blank name or
blank address) are **skipped** rather than mis-merged. For each entity we count
loans per ``processing_method``; the *excess* over the one-per-draw allowance is

    excess = max( sum_draw(max(0, count_draw - 1)),  n_loans - 2 )

so two same-draw loans, or any third funded loan, yields ``excess >= 1``. A legit
one-PPP-plus-one-PPS borrower has ``excess == 0`` and never fires. Every loan in a
flagged entity emits a Signal scored by ``excess`` (monotonic in the count of
extra loans).

False-positive modes (documented, not hidden)
---------------------------------------------
* Entity resolution can over-merge two genuinely different businesses that share a
  name *and* a building (a rebrand, a franchise reusing an address).
* Reapplications, cancellations, and re-disbursements can leave more than one row
  for what is operationally one loan; this detector counts rows, not net funding.

So a hit is a **review lead**, never proof — which is why the detector ships
EXPLORATORY, out of the default composite, pending real-data lift validation.

Read-only: the detector never writes to the warehouse.
"""

from __future__ import annotations

from collections import defaultdict

import duckdb

from relief_probe.detectors._entity import entity_key
from relief_probe.detectors.base import Detector, Signal

#: Legitimate per-entity allowance: one first draw + one second draw.
_ALLOWED_TOTAL = 2

#: Cap the loan_numbers stored in evidence to keep the blob bounded.
_SAMPLE_CAP = 25


class MultipleFundedLoansDetector(Detector):
    detector_id = "multiple_funded_loans"
    summary = (
        "One resolved borrower (normalized name + building) holds more funded "
        "loans than the one-per-draw PPP/PPS rule allows."
    )

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        rows = con.execute(
            """
            SELECT
                loan_number, borrower_name, borrower_address,
                borrower_city, borrower_state, borrower_zip,
                processing_method, current_approval_amount
            FROM loans
            """
        ).fetchall()

        # Bucket loans by their resolved entity key.
        buckets: dict[str, list[tuple]] = defaultdict(list)
        for (
            loan_number,
            borrower_name,
            address,
            city,
            state,
            zip_code,
            method,
            amount,
        ) in rows:
            key = entity_key(borrower_name, address, city, state, zip_code)
            if key is None:  # unkeyable entity → excluded from grouping
                continue
            buckets[key].append(
                (str(loan_number), method, float(amount or 0.0))
            )

        signals: list[Signal] = []
        for key, loans in buckets.items():
            n_loans = len(loans)

            # Per-draw counts (None method kept as its own bucket so an extra
            # method-less row still counts toward the total-loans rule).
            per_draw: dict[str | None, int] = defaultdict(int)
            for _, method, _ in loans:
                per_draw[method] += 1

            same_draw_excess = sum(max(0, c - 1) for c in per_draw.values())
            total_excess = max(0, n_loans - _ALLOWED_TOTAL)
            excess = max(same_draw_excess, total_excess)
            if excess <= 0:  # legit (<=1 per draw, <=2 total) → quiet
                continue

            total_amount = round(sum(amt for _, _, amt in loans), 2)
            loan_numbers = sorted(ln for ln, _, _ in loans)[:_SAMPLE_CAP]
            # Score is monotonic in the count of extra loans, comparable WITHIN
            # this detector. total_amount is recorded for triage, not scoring.
            score = round(float(excess), 4)

            for loan_number, _, _ in loans:
                signals.append(
                    Signal(
                        loan_number=loan_number,
                        detector_id=self.detector_id,
                        score=score,
                        evidence={
                            "entity_key": key,
                            "n_loans": n_loans,
                            "excess_loans": excess,
                            "per_draw_counts": dict(sorted(
                                (str(m), c) for m, c in per_draw.items()
                            )),
                            "total_amount": total_amount,
                            "loan_numbers": loan_numbers,
                        },
                    )
                )
        return signals
