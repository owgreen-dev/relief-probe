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


def run_benchmark(
    con: duckdb.DuckDBPyConnection,
    *,
    ks: tuple[int, ...] = DEFAULT_KS,
    rescore: bool = True,
) -> dict:
    """Rank loans by composite score, validate against resolved fraud_cases labels."""
    if rescore:
        run_all(con)

    population = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    positives = labeled_fraud_loans(con)
    base_rate = (len(positives) / population) if population else 0.0

    ranking = composite_ranking(con)
    ranked = [str(x) for x in ranking["loan_number"].tolist()]
    overall = ranking_metrics(ranked, positives, base_rate, ks)

    # Per-detector ablation: rank by each detector's own score in isolation.
    ablation: dict[str, dict] = {}
    for (det,) in con.execute(
        "SELECT DISTINCT detector_id FROM signals"
    ).fetchall():
        det_ranked = [
            str(r[0])
            for r in con.execute(
                "SELECT loan_number FROM signals WHERE detector_id = ? "
                "ORDER BY score DESC",
                [det],
            ).fetchall()
        ]
        ablation[det] = {
            "n_flagged": len(det_ranked),
            "metrics": ranking_metrics(det_ranked, positives, base_rate, ks),
        }

    return {
        "ks": list(ks),
        "population": population,
        "n_labeled_fraud": len(positives),
        "base_rate": round(base_rate, 6),
        "n_ranked": len(ranked),
        "overall": overall,
        "ablation": ablation,
    }
