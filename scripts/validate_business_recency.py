"""Validate whether borrower-declared business RECENCY concentrates prosecuted
loans — the Tier-A KYB thesis (read-only).

The row-wise dollar detectors ask "is this loan's $/job implausible?"; this asks a
different, orthogonal question: "did the borrower itself declare the business was
brand-new (or not yet operating)?". PPP required a business to have been operating
on February 15, 2020, so the ``business_age_description`` tells "Startup, Loan Funds
will Open Business" (strongest), "New Business or 2 years or less", and "Change of
Ownership" are program-eligibility recency tells. This script ranks the $150k+ slice
by a LABEL-FREE ordinal recency score (:func:`recency_score`), then measures whether
DOJ-prosecuted loans concentrate near the top versus the base rate — and versus the
production composite on the SAME held-out labels.

Out-of-time by construction (H7): we evaluate only on prosecutions charged *after* a
holdout year (:func:`benchmark.temporal_label_split`), which the score never used
anyway — the score is fully label-free, so the split just makes the evaluation an
honest forward test.

Honest scope (mirrors scripts/validate_ring_graph.py): the recency score is
LABEL-FREE — labels are used ONLY to evaluate, never to build the ranking. Read the
CONTRAST (does recency beat the base rate and the composite on the held-out
labels?), not absolute lift. Two caveats specific to this signal: (1) the score is a
COARSE 4-level ordinal, so most of the slice ties at 0 and the firing loans share
just three score tiers — read recall@k and the firing-tier concentration, not fine
ordering; (2) business recency is a program-ELIGIBILITY tell, not by itself a fraud
tell, so an honest NEGATIVE — recency no better than chance or the composite at
surfacing prosecuted loans — is a valid, documented outcome (the whole point of an
exploratory detector is that the labels get to say no). The motivation is Benesch's
finding that 53% of PPP fraud involved a fabricated/backdated business and Griffin,
Kruger & Mahajan (J. Finance 2023); those are MOTIVATION, never our results.

Read-only: opens the warehouse with ``read_only=True`` and never writes.

Run: `uv run python scripts/validate_business_recency.py`.
"""

from __future__ import annotations

from relief_probe.benchmark.core import (
    positive_rank_stats,
    ranking_metrics,
    temporal_label_split,
)
from relief_probe.detectors.business_recency import RECENCY_TELLS
from relief_probe.scoring import composite_ranking
from relief_probe.warehouse import connect

#: Disclosure slice the labels live in — keep the base rate apples-to-apples.
MIN_AMOUNT = 150_000.0
#: Evaluate only on prosecutions charged AFTER this year (H7 out-of-time holdout).
HOLDOUT_YEAR = 2023
KS = (50, 100, 250, 500, 1000)


def recency_score(age_description: str | None) -> float:
    """LABEL-FREE ordinal business-recency strength for one declared age value.

    Shares :data:`RECENCY_TELLS` with the detector so the script and the detector
    can never drift: "Startup, Loan Funds will Open Business" (3) >
    "New Business or 2 years or less" (2) > "Change of Ownership" (1); every other
    value — "Existing or more than 2 years old", "Unanswered", null/blank — scores
    0.0 (never score missing-as-suspicious). Pure and deterministic, never labels.
    """
    if age_description is None:
        return 0.0
    tell = RECENCY_TELLS.get(str(age_description).strip().casefold())
    return tell[0] if tell else 0.0


def rank_slice_by_recency(rows: list[tuple[str, str | None]]) -> list[str]:
    """Rank ``(loan_number, business_age_description)`` rows by descending
    :func:`recency_score` (ties broken deterministically by loan_number)."""
    scored = [(str(ln), recency_score(age)) for ln, age in rows]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    return [ln for ln, _ in scored]


def _report(label: str, ranked: list[str], positives: set[str],
            base_rate: float, population: int) -> None:
    """Print the concentration verdict + recall@k for one ranking."""
    m = ranking_metrics(ranked, positives, base_rate, ks=KS)
    pr = positive_rank_stats(ranked, positives, population)
    pct = pr["mean_percentile_in_ranking"]
    verdict = "BETTER than random" if (pct or 1) < 0.5 else "no better than random"
    print(f"=== {label} ===")
    print(
        f"  ranked {len(ranked):,} loans · "
        f"concentration: mean percentile {pct} "
        f"({verdict}; 0.5 = random, lower = top-concentrated)"
    )
    for k in KS:
        print(
            f"    @{k}: {m[k]['hits']} hits · recall {m[k]['recall']} · "
            f"lift {m[k]['lift']}x"
        )
    print()


def main() -> None:
    with connect(read_only=True) as con:
        rows = con.execute(
            "SELECT loan_number, business_age_description FROM loans "
            "WHERE current_approval_amount >= ?",
            [MIN_AMOUNT],
        ).fetchall()
        slice_nodes = {str(r[0]) for r in rows}
        population = len(slice_nodes)
        ranked_recency = rank_slice_by_recency(rows)
        n_firing = sum(1 for _, age in rows if recency_score(age) > 0)
        print(
            f"slice: {population:,} loans (${int(MIN_AMOUNT):,}+) · "
            f"{n_firing:,} fire a recency tell"
        )

        # Out-of-time labels: only prosecutions charged AFTER the holdout year, and
        # only those falling inside the slice (the ranking's universe).
        _train, test = temporal_label_split(con, HOLDOUT_YEAR)
        positives = test & slice_nodes
        base_rate = len(positives) / population if population else 0.0
        print(
            f"held-out positives (charged > {HOLDOUT_YEAR}) in slice: "
            f"{len(positives)} · base rate {base_rate:.4%}\n"
        )

        if not positives:
            print("No held-out positives in the slice — cannot evaluate. Verdict: N/A.")
            return

        _report("business recency (label-free)", ranked_recency, positives,
                base_rate, population)

        # Compare against the production composite on the SAME held-out labels.
        # Read the existing signals table (read-only); skip if it was never built.
        try:
            n_signals = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        except Exception:
            n_signals = 0
        if n_signals:
            comp = composite_ranking(con)
            comp_ranked = [
                str(x) for x in comp["loan_number"].tolist() if str(x) in slice_nodes
            ]
            _report("composite (production)", comp_ranked, positives,
                    base_rate, population)
        else:
            print(
                "composite comparison skipped: the `signals` table is empty — "
                "run `relief-probe score` first to populate it.\n"
            )


if __name__ == "__main__":
    main()
