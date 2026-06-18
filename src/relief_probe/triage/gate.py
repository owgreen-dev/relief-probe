"""Validation gate — does Tier-1 re-ranking actually beat the composite alone?

Same discipline as every detector in this project: a new signal earns its place
only by measurably improving lift@k against the resolved DOJ labels — otherwise we
report the honest negative. Here the test is narrow and fair: Tier 1 only permutes
the **top-k shortlist**, so the gate compares two rankings that differ *only*
inside that head —

* ``composite`` — the Tier-0 ranking as-is;
* ``triage``    — the same ranking with its top-k re-ordered by the blended
  triage score, the tail untouched —

over the labelable $150k+ slice, at k values within the shortlist (re-ranking the
head cannot move lift@k for k >= top_k). If triage's lift@k is no better, Tier 1
adds nothing on the known labels and we say so.
"""

from __future__ import annotations

import duckdb

from relief_probe.benchmark.core import (
    _restrict,
    _slice_universe,
    labeled_fraud_loans,
    ranking_metrics,
)
from relief_probe.scoring import composite_ranking
from relief_probe.triage.core import rerank, select_candidates
from relief_probe.triage.judge import Judge, heuristic_judge


def _triage_ranking(
    con: duckdb.DuckDBPyConnection,
    top_k: int,
    judge: Judge,
    reranked_head: list[str] | None = None,
) -> list[str]:
    """Full population ranking with the top-``top_k`` head re-ordered by triage.

    ``reranked_head`` lets a caller pass the *already-judged* shortlist (the
    ``triage()`` result's order) so the gate never re-runs the LLM — judging the
    top-k twice would double the cost and, because the model is non-deterministic,
    score the two passes differently. When omitted (the deterministic/test path),
    the cheap heuristic judge is run inline.
    """
    full = [str(x) for x in composite_ranking(con)["loan_number"].tolist()]
    if reranked_head is None:
        candidates = select_candidates(con, top_k)
        verdicts = judge(candidates)
        reranked_head = [s.candidate.loan_number for s in rerank(candidates, verdicts)]
    # reranked_head is a permutation of full[:len(head)]; keep the tail as-is.
    return reranked_head + full[len(reranked_head):]


def _gate_ks(top_k: int) -> tuple[int, ...]:
    """k values worth scoring — only those inside the re-ranked shortlist."""
    return tuple(k for k in (25, 50, 100, 250, 500, 1000) if k <= top_k) or (top_k,)


def validation_gate(
    con: duckdb.DuckDBPyConnection,
    *,
    top_k: int = 100,
    judge: Judge = heuristic_judge,
    reranked_head: list[str] | None = None,
    min_amount: float | None = 150_000.0,
    rescore: bool = False,
) -> dict:
    """Compare composite vs triage-reranked lift@k on the resolved labels.

    Pass ``reranked_head`` (the ordered loan_numbers from a prior ``triage()``
    run) to reuse those verdicts instead of re-judging — essential on the LLM path
    so the gate adds no model cost. When omitted, ``judge`` is run inline (cheap
    for the deterministic heuristic). ``judge`` is still used for the report label
    either way.

    ``rescore`` re-runs the detectors first (off by default — assumes ``signals``
    is already populated, e.g. after ``benchmark`` or ``score``). Returns per-k
    composite/triage metrics, the lift delta, and a coarse ``verdict``
    (``improved`` / ``neutral`` / ``regressed``) summarising whether Tier 1 moved
    the top of the ranking toward the known fraud.
    """
    if rescore:
        from relief_probe.detectors.runner import run_all

        run_all(con)

    ks = _gate_ks(top_k)
    universe = _slice_universe(con, min_amount)
    all_positives = labeled_fraud_loans(con)
    if universe is None:
        population = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
        positives = all_positives
    else:
        population = len(universe)
        positives = all_positives & universe
    base_rate = (len(positives) / population) if population else 0.0

    composite_ranked = _restrict(
        [str(x) for x in composite_ranking(con)["loan_number"].tolist()], universe
    )
    triage_ranked = _restrict(
        _triage_ranking(con, top_k, judge, reranked_head), universe
    )

    composite_m = ranking_metrics(composite_ranked, positives, base_rate, ks)
    triage_m = ranking_metrics(triage_ranked, positives, base_rate, ks)

    per_k: dict[int, dict] = {}
    total_delta = 0.0
    for k in ks:
        c_lift = composite_m[k]["lift"]
        t_lift = triage_m[k]["lift"]
        delta = (
            None if c_lift is None or t_lift is None else round(t_lift - c_lift, 2)
        )
        if delta is not None:
            total_delta += delta
        per_k[k] = {
            "composite": composite_m[k],
            "triage": triage_m[k],
            "lift_delta": delta,
        }

    if total_delta > 0:
        verdict = "improved"
    elif total_delta < 0:
        verdict = "regressed"
    else:
        verdict = "neutral"

    return {
        "ks": list(ks),
        "slice": "all" if min_amount is None else f">=${int(min_amount):,}",
        "top_k": top_k,
        "population": population,
        "n_labeled_fraud": len(positives),
        "base_rate": round(base_rate, 6),
        "judge": getattr(judge, "__name__", judge.__class__.__name__),
        "per_k": per_k,
        "total_lift_delta": round(total_delta, 2),
        "verdict": verdict,
    }
