"""Tier-A business-recency detector — a LABEL-FREE external-evidence proxy (Loop 5).

Domain rationale
----------------
PPP required a business to have been *in operation on February 15, 2020* to be
eligible. The loans table already carries ``business_age_description`` (100%
populated), a borrower-declared self-classification of how new the business is.
Two of its values are, on their face, in tension with that eligibility rule, and a
third is a known fraud tell:

* ``"Startup, Loan Funds will Open Business"`` — the funds will OPEN the business,
  i.e. the business was *not yet operating*. The strongest, near-explicit
  Feb-15-2020 eligibility red flag.
* ``"New Business or 2 years or less"`` — formed shortly before the loan; the
  "business formed right before the loan" indicator validated by Griffin, Kruger &
  Mahajan (J. Finance 2023) and consistent with Benesch's finding that 53% of PPP
  fraud involved a fabricated or backdated business.
* ``"Change of Ownership"`` — a weaker recency tell (recently transferred control).

This is the cheapest, no-API slice of the KYB ("know your business") external-
evidence thesis: relief-probe has repeatedly found AI/ML wins at RETRIEVAL / bringing
NEW information, not row-wise prediction over the loan's own dollar fields. Business
recency is information *about the borrower's existence*, orthogonal to $/job.

Why label-free (and why that matters)
-------------------------------------
The detector NEVER reads ``fraud_cases`` or any label table. Its signal is a pure
borrower-declared program-eligibility tell, not a learned "which loans got
prosecuted" pattern. That keeps the prosecuted-fraud labels an independent validator
(see ``scripts/validate_business_recency.py``), free of leakage and prosecution bias.

What it deliberately does NOT fire on
-------------------------------------
* ``"Existing or more than 2 years old"`` — the eligible, non-recent baseline.
* ``"Unanswered"`` (and null/blank) — NEVER score missing-as-suspicious; that would
  manufacture lift from a data-quality artifact, not a fraud signal.

The ``score`` is an ordinal, label-free intensity (3 > 2 > 1) monotonic in recency
strength and comparable *within this detector*. Read-only; never writes.

DISPOSITION: EXPLORATORY only (SIGN-010). It lives in
:func:`relief_probe.detectors.registry.exploratory_detectors` and is NEVER in
``all_detectors()`` / the production composite; promotion is a MANUAL human decision
after real-data validation against the prosecuted-fraud labels — an honest NEGATIVE
(recency is an eligibility tell, not necessarily a fraud tell) is an acceptable
outcome.
"""

from __future__ import annotations

import duckdb

from relief_probe.detectors.base import Detector, Signal

#: The recency tells, mapped to an ordinal label-free score and a grounded reason.
#: Strongest first. Keys match ``business_age_description`` verbatim (case-folded at
#: lookup). "Existing or more than 2 years old" and "Unanswered" are deliberately
#: absent — they must NOT fire.
RECENCY_TELLS: dict[str, tuple[float, str]] = {
    "startup, loan funds will open business": (
        3.0,
        "Borrower declared the loan funds will OPEN the business — a near-explicit "
        "tell the business was not operating by the Feb-15-2020 PPP eligibility date.",
    ),
    "new business or 2 years or less": (
        2.0,
        "Borrower declared a new business (<=2 years) — formed shortly before the "
        "loan, a validated 'business formed right before the loan' recency indicator.",
    ),
    "change of ownership": (
        1.0,
        "Borrower declared a recent change of ownership — a weaker recency tell.",
    ),
}


class BusinessRecencyDetector(Detector):
    detector_id = "business_recency"
    summary = (
        "Borrower-declared business_age_description is a recency tell — 'Startup, "
        "Loan Funds will Open Business' (Feb-15-2020 eligibility red flag), 'New "
        "Business or 2 years or less', or 'Change of Ownership'. Label-free Tier-A "
        "KYB proxy; does NOT fire on 'Existing…' or 'Unanswered'."
    )

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        rows = con.execute(
            """
            SELECT loan_number, business_age_description, date_approved
            FROM loans
            WHERE business_age_description IS NOT NULL
              AND TRIM(business_age_description) <> ''
            """
        ).fetchall()

        signals: list[Signal] = []
        for loan_number, age_desc, date_approved in rows:
            tell = RECENCY_TELLS.get(str(age_desc).strip().casefold())
            if tell is None:
                # "Existing or more than 2 years old", "Unanswered", or any other
                # non-recency value: never fire (no missing-as-suspicious).
                continue
            score, reason = tell
            signals.append(
                Signal(
                    loan_number=str(loan_number),
                    detector_id=self.detector_id,
                    score=score,
                    evidence={
                        "business_age_description": age_desc,
                        "date_approved": date_approved,
                        "matched_tell": age_desc,
                        "reason": reason,
                    },
                )
            )
        return signals
