"""Validate whether multi-relational RING/community structure concentrates
prosecuted loans — the relational thesis (read-only).

The row-wise detectors ask "is this loan implausible?"; the graph asks "is this
loan embedded in a coordinated structure?". This script builds the
multi-relational loan graph over the $150k+ slice (address + entity + similarity
edges), computes a LABEL-FREE structural ring score per loan
(``log1p(distinct_borrowers) + log1p(community_size)``), ranks every loan in the
slice by it, and measures whether DOJ-prosecuted loans concentrate near the top
versus the base rate — and versus the production composite on the SAME labels.

Out-of-time by construction (H7): we evaluate only on prosecutions charged
*after* a holdout year (:func:`benchmark.temporal_label_split`), which the
structure never used anyway — the score is fully label-free, so the split just
makes the evaluation an honest forward test.

Honest scope (mirrors scripts/validate_naics_mismatch.py): the structural score
is LABEL-FREE — labels are used ONLY to evaluate, never to build the graph or
rank. Read the CONTRAST (does ring structure beat the base rate and the
composite on the held-out labels?), not absolute lift. The address-alone signal
(``duplicate_address_ring``) was already validated NULL because legitimate
co-location dominates; the bet here is that COMBINING edge types + community
detection separates real rings from benign clustering. An honest NEGATIVE — ring
structure no better than chance or the composite — is a valid, documented
outcome. Building over the full ~965k-node slice with pure-python NetworkX is
heavy; raise ``MIN_AMOUNT`` to shrink the slice for a tractable (documented)
run. A higher-amount slice (not a random sample) is used so edges/rings stay
intact — random sampling would shatter rings and understate the structure.

Run: `uv run --extra graph --extra embeddings python scripts/validate_ring_graph.py`.
"""

from __future__ import annotations

import math

from relief_probe.benchmark.core import (
    positive_rank_stats,
    ranking_metrics,
    temporal_label_split,
)
from relief_probe.graph.build import build_loan_graph
from relief_probe.graph.features import graph_structural_features
from relief_probe.scoring import composite_ranking
from relief_probe.warehouse import connect

#: Slice floor — raise it (e.g. 1_000_000) to shrink the graph for a tractable run.
MIN_AMOUNT = 150_000.0
#: Evaluate only on prosecutions charged AFTER this year (H7 out-of-time holdout).
HOLDOUT_YEAR = 2023
KS = (50, 100, 250, 500, 1000)


def ring_score(feat: dict) -> float:
    """LABEL-FREE structural ring strength for one loan's structural features.

    Monotonic in how many distinct borrowers its component weaves together and
    how large its community is — both pure graph-shape quantities, never labels.
    """
    return math.log1p(feat["distinct_borrowers"]) + math.log1p(feat["community_size"])


def rank_loans_by_structure(features: dict[str, dict]) -> list[str]:
    """Rank loan_numbers by descending :func:`ring_score` (ties by loan_number)."""
    return [
        ln
        for ln, _ in sorted(
            features.items(), key=lambda kv: (-ring_score(kv[1]), kv[0])
        )
    ]


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
        print(f"Building multi-relational graph over the ${int(MIN_AMOUNT):,}+ slice…")
        graph = build_loan_graph(con, min_amount=MIN_AMOUNT)
        feats = graph_structural_features(graph)
        slice_nodes = set(graph.nodes)
        population = len(slice_nodes)
        ranked_struct = rank_loans_by_structure(feats)

        # Out-of-time labels: only prosecutions charged AFTER the holdout year, and
        # only those falling inside the slice (the graph's universe).
        _train, test = temporal_label_split(con, HOLDOUT_YEAR)
        positives = test & slice_nodes
        base_rate = len(positives) / population if population else 0.0
        print(
            f"slice: {population:,} loans · held-out positives "
            f"(charged > {HOLDOUT_YEAR}) in slice: {len(positives)} · "
            f"base rate {base_rate:.4%}\n"
        )

        if not positives:
            print("No held-out positives in the slice — cannot evaluate. Verdict: N/A.")
            return

        _report("ring structure (label-free)", ranked_struct, positives,
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
                "run `relief-probe run` first to populate it.\n"
            )


if __name__ == "__main__":
    main()
