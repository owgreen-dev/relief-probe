"""Forward positive-unlabeled validation — the centerpiece metric.

Rank loans by composite detector score, then measure how strongly DOJ-prosecuted
loans (entity-resolved into ``fraud_cases``) concentrate at the top of the ranking:
precision@k, lift over the base rate, recall@k, and a per-detector ablation showing
which detectors carry the signal.

This is out-of-time validation — the labels are prosecutions that post-date every
loan, so there is no leakage. Honest caveats (see RESPONSIBLE_USE.md): confirmed fraud
is a tiny (<0.1%), prosecution-biased sample of true fraud, so these are
**recall-on-known-fraud**, NOT a fraud rate — a lower bound conflated with what
enforcement happened to catch and resolve to a loan. A measured weak signal still
beats an unmeasured claim.
"""

from __future__ import annotations

import duckdb

from relief_probe.detectors.runner import run_all
from relief_probe.scoring import composite_ranking

DEFAULT_KS: tuple[int, ...] = (100, 250, 500, 1000, 2000, 5000)


def labeled_fraud_loans(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Distinct loan_numbers that resolved to a prosecution (the PU positives)."""
    return {
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT loan_number FROM fraud_cases "
            "WHERE loan_number IS NOT NULL"
        ).fetchall()
    }


def baseline_rankings(con: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """Whole-population baseline rankings to contrast against the composite detector.

    Unlike the composite (which ranks only flagged loans), these naive sorts rank the
    ENTIRE population — that contrast is the point: a reader sees whether the detector
    machinery beats a one-line SQL sort.

    - ``amount_per_job``: dollars-per-job descending (jobs >= 1, amount > 0), the
      crudest "too much money for too few jobs" heuristic.
    - ``raw_amount``: current approval amount descending (biggest loans first).
    """
    amount_per_job = [
        str(r[0])
        for r in con.execute(
            "SELECT loan_number FROM loans "
            "WHERE jobs_reported >= 1 AND current_approval_amount > 0 "
            "ORDER BY current_approval_amount / jobs_reported DESC, loan_number"
        ).fetchall()
    ]
    raw_amount = [
        str(r[0])
        for r in con.execute(
            "SELECT loan_number FROM loans "
            "WHERE current_approval_amount IS NOT NULL "
            "ORDER BY current_approval_amount DESC, loan_number"
        ).fetchall()
    ]
    return {"amount_per_job": amount_per_job, "raw_amount": raw_amount}


def ranking_metrics(
    ranked: list[str],
    positives: set[str],
    base_rate: float,
    ks: tuple[int, ...] = DEFAULT_KS,
) -> dict:
    """precision@k / lift / recall@k for a ranked loan list (denominator = k)."""
    out: dict[int, dict] = {}
    n_pos = len(positives)
    for k in ks:
        hits = sum(1 for ln in ranked[:k] if ln in positives)
        precision = hits / k if k else 0.0
        out[k] = {
            "hits": hits,
            "precision": round(precision, 5),
            "lift": round(precision / base_rate, 2) if base_rate else None,
            "recall": round(hits / n_pos, 4) if n_pos else None,
        }
    return out


def _slice_universe(
    con: duckdb.DuckDBPyConnection, min_amount: float | None
) -> set[str] | None:
    """Loan_numbers in the evaluation slice, or None for the whole population.

    The resolved labels live almost entirely in the public $150k+ disclosure slice,
    so ranking the full ~11.3M-loan population mechanically deflates the base rate
    and inflates lift (the same handful of hits over a 10x bigger haystack). The
    default benchmark therefore restricts evaluation to the *labelable* slice for an
    apples-to-apples lift; full-population recall is reported separately.
    """
    if min_amount is None:
        return None
    return {
        str(r[0])
        for r in con.execute(
            "SELECT loan_number FROM loans WHERE current_approval_amount >= ?",
            [min_amount],
        ).fetchall()
    }


def _restrict(ranked: list[str], universe: set[str] | None) -> list[str]:
    """Keep only loans in ``universe`` (order-preserving); identity if None."""
    if universe is None:
        return ranked
    return [ln for ln in ranked if ln in universe]


def run_benchmark(
    con: duckdb.DuckDBPyConnection,
    *,
    ks: tuple[int, ...] = DEFAULT_KS,
    rescore: bool = True,
    min_amount: float | None = 150_000.0,
) -> dict:
    """Rank loans by composite score, validate against resolved fraud_cases labels.

    ``min_amount`` restricts evaluation to the labelable slice (default: the $150k+
    disclosure slice). Pass ``None`` to evaluate the whole population.
    """
    if rescore:
        run_all(con)

    universe = _slice_universe(con, min_amount)
    all_positives = labeled_fraud_loans(con)
    if universe is None:
        population = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
        positives = all_positives
    else:
        population = len(universe)
        positives = all_positives & universe
    base_rate = (len(positives) / population) if population else 0.0

    ranking = composite_ranking(con)
    full_ranked = [str(x) for x in ranking["loan_number"].tolist()]
    ranked = _restrict(full_ranked, universe)
    overall = ranking_metrics(ranked, positives, base_rate, ks)

    # Per-detector ablation: rank by each detector's own score in isolation.
    ablation: dict[str, dict] = {}
    for (det,) in con.execute(
        "SELECT DISTINCT detector_id FROM signals"
    ).fetchall():
        det_ranked = _restrict(
            [
                str(r[0])
                for r in con.execute(
                    "SELECT loan_number FROM signals WHERE detector_id = ? "
                    "ORDER BY score DESC",
                    [det],
                ).fetchall()
            ],
            universe,
        )
        ablation[det] = {
            "n_flagged": len(det_ranked),
            "metrics": ranking_metrics(det_ranked, positives, base_rate, ks),
        }

    # Naive whole-population baselines, scored against the SAME positives/base_rate/ks.
    baselines: dict[str, dict] = {}
    for name, ranked_baseline in baseline_rankings(con).items():
        baselines[name] = {
            "metrics": ranking_metrics(
                _restrict(ranked_baseline, universe), positives, base_rate, ks
            )
        }

    # Full-population recall (denominator = ALL resolved labels), reported separately
    # so the slice restriction never hides labels that surface outside the slice.
    full_population = {
        "population": con.execute("SELECT COUNT(*) FROM loans").fetchone()[0],
        "n_labeled_fraud": len(all_positives),
        "metrics": ranking_metrics(full_ranked, all_positives, 0.0, ks),
    }

    return {
        "ks": list(ks),
        "slice": "all" if min_amount is None else f">=${int(min_amount):,}",
        "population": population,
        "n_labeled_fraud": len(positives),
        "base_rate": round(base_rate, 6),
        "n_ranked": len(ranked),
        "overall": overall,
        "ablation": ablation,
        "baselines": baselines,
        "full_population": full_population,
    }
