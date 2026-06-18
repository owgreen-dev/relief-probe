"""Duplicate-address ring detector — a link-analysis (co-location) signal.

What it finds
-------------
Many *distinct borrowers* keyed to the same physical building. Where the
dollars-per-job detectors (``naics_cohort_outlier``, ``payroll_cap_exceedance``)
ask "is this loan's size implausible?", this asks an orthogonal question: "are a
suspicious number of unrelated borrowers all filing from one address?" — the
classic signature of a coordinated ring or a single bad actor spawning shell
companies.

This makes corroboration meaningful: a loan flagged by a $/job detector *and* the
ring detector is implicated by two genuinely independent views, not two
restatements of the same ratio.

Grouping rule
-------------
Each loan is keyed to a building via :func:`normalize_address` (case/punctuation/
suffix-normalized, unit/suite stripped). A *ring* is an address key shared by at
least ``min_ring_size`` **distinct borrower names** — distinct *borrowers*, so a
single borrower holding many loans at one address is **not** a ring. Loans whose
address cannot be keyed (``None``) are excluded rather than mis-grouped.

False-positive modes (documented, not hidden)
---------------------------------------------
* Shared office buildings, coworking spaces, and strip malls legitimately host
  many unrelated businesses at one street address.
* Registered-agent / mail-forwarding services appear on thousands of filings.

So a flagged ring is a **review lead**, never proof — a human adjudicates.
"""

from __future__ import annotations

import math
from collections import defaultdict

import duckdb

from relief_probe.detectors._address import normalize_address
from relief_probe.detectors.base import Detector, Signal

#: How many borrower names to keep in the evidence sample (avoid huge blobs).
_SAMPLE_CAP = 10


class DuplicateAddressRingDetector(Detector):
    detector_id = "duplicate_address_ring"
    summary = (
        "Many distinct borrowers file from one building (a co-location / "
        "link-analysis ring), a signal independent of dollars-per-job."
    )

    def __init__(self, *, min_ring_size: int = 3) -> None:
        # Minimum number of DISTINCT borrowers at one address to call it a ring.
        self.min_ring_size = min_ring_size

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        rows = con.execute(
            """
            SELECT
                loan_number, borrower_name, borrower_address,
                borrower_city, borrower_state, borrower_zip,
                current_approval_amount
            FROM loans
            """
        ).fetchall()

        # Bucket loans by their normalized building key.
        buckets: dict[str, list[tuple]] = defaultdict(list)
        for (
            loan_number,
            borrower_name,
            address,
            city,
            state,
            zip_code,
            amount,
        ) in rows:
            key = normalize_address(address, city, state, zip_code)
            if key is None:  # unkeyable → excluded from ring grouping
                continue
            buckets[key].append(
                (str(loan_number), borrower_name, float(amount or 0.0))
            )

        signals: list[Signal] = []
        for key, loans in buckets.items():
            distinct_borrowers = {name for _, name, _ in loans if name}
            ring_size = len(distinct_borrowers)
            if ring_size < self.min_ring_size:
                continue

            total_amount = round(sum(amt for _, _, amt in loans), 2)
            n_loans = len(loans)
            # Monotonic in both ring size and total dollars (log1p-scaled so a
            # handful of huge loans does not swamp a genuinely dense ring); score
            # is comparable WITHIN this detector.
            score = round(math.log1p(ring_size) + math.log1p(total_amount), 4)
            sample = sorted(distinct_borrowers)[:_SAMPLE_CAP]

            for loan_number, _, _ in loans:
                signals.append(
                    Signal(
                        loan_number=loan_number,
                        detector_id=self.detector_id,
                        score=score,
                        evidence={
                            "normalized_address": key,
                            "ring_size": ring_size,
                            "n_loans": n_loans,
                            "total_ring_amount": total_amount,
                            "borrower_names_sample": sample,
                        },
                    )
                )
        return signals
