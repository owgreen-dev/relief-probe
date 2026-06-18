"""Tier-1 orchestration: select top-k composite leads, judge them, re-rank.

The cascade is cost-shaped: Tier 0 (the deterministic composite) ranks the whole
population for free; this module escalates only the **top-k** of that ranking to a
plausibility :class:`~relief_probe.triage.judge.Judge`, then folds the judge's 0-3
implausibility into a transparent re-rank:

    triage_score = composite_score + TRIAGE_WEIGHT * (implausibility / 3)

so a semantically-implausible lead rises within the shortlist while the composite
still carries the bulk of the ordering. A **hard cap** (:data:`MAX_TRIAGE`) bounds
how many loans can ever reach the judge — the cost ceiling, logged on every run.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from relief_probe.scoring import composite_ranking
from relief_probe.triage.judge import Judge, PlausibilityVerdict, heuristic_judge

#: Bonus weight for a maximally-implausible (3/3) lead. Matches the composite's
#: corroboration weight so Tier 1 nudges the shortlist without swamping Tier 0.
TRIAGE_WEIGHT = 0.5

#: Absolute ceiling on loans escalated to the judge, regardless of ``--top-k`` —
#: the cost bound. The LLM judge NEVER sees more than this many loans.
MAX_TRIAGE = 2000

#: Default size of the escalated shortlist.
DEFAULT_TOP_K = 100


@dataclass(frozen=True)
class LoanCandidate:
    """A top-k composite lead, with the public fields a judge is allowed to see."""

    loan_number: str
    borrower_name: str | None
    naics_code: str | None
    state: str | None
    amount: float | None
    jobs: float | None
    payroll_proceed: float | None
    composite_score: float
    n_signals: int
    detectors: list[str]


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate after judging — composite + verdict + the blended triage score."""

    candidate: LoanCandidate
    verdict: PlausibilityVerdict
    triage_score: float


def select_candidates(
    con: duckdb.DuckDBPyConnection, top_k: int
) -> list[LoanCandidate]:
    """Top-``top_k`` composite leads (cap-clamped) with their plausibility fields.

    Reads the composite ranking, then joins ``payroll_proceed`` from ``loans`` in
    one pass. ``top_k`` is clamped to :data:`MAX_TRIAGE`; callers should log when
    the cap bites (see :func:`triage`).
    """
    k = max(0, min(int(top_k), MAX_TRIAGE))
    if k == 0:
        return []
    ranking = composite_ranking(con, limit=k)
    if ranking.empty:
        return []

    loan_numbers = [str(x) for x in ranking["loan_number"].tolist()]
    placeholders = ", ".join("?" for _ in loan_numbers)
    payroll = {
        str(r[0]): (float(r[1]) if r[1] is not None else None)
        for r in con.execute(
            f"SELECT loan_number, payroll_proceed FROM loans "
            f"WHERE loan_number IN ({placeholders})",
            loan_numbers,
        ).fetchall()
    }

    candidates: list[LoanCandidate] = []
    for r in ranking.itertuples(index=False):
        ln = str(r.loan_number)
        candidates.append(
            LoanCandidate(
                loan_number=ln,
                borrower_name=r.borrower_name,
                naics_code=str(r.naics_code) if r.naics_code is not None else None,
                state=r.state,
                amount=float(r.amount) if r.amount is not None else None,
                jobs=float(r.jobs_reported) if r.jobs_reported is not None else None,
                payroll_proceed=payroll.get(ln),
                composite_score=float(r.composite_score),
                n_signals=int(r.n_signals),
                detectors=list(r.detectors),
            )
        )
    return candidates


def rerank(
    candidates: list[LoanCandidate], verdicts: list[PlausibilityVerdict]
) -> list[ScoredCandidate]:
    """Blend composite score + judge implausibility, sort highest-first (stable)."""
    if len(candidates) != len(verdicts):
        raise ValueError(
            f"candidates ({len(candidates)}) and verdicts ({len(verdicts)}) "
            "must align one-to-one"
        )
    scored = [
        ScoredCandidate(
            candidate=c,
            verdict=v,
            triage_score=c.composite_score + TRIAGE_WEIGHT * (v.implausibility / 3),
        )
        for c, v in zip(candidates, verdicts, strict=True)
    ]
    # Stable sort: ties keep the original composite order.
    scored.sort(key=lambda s: s.triage_score, reverse=True)
    return scored


def triage(
    con: duckdb.DuckDBPyConnection,
    *,
    top_k: int = DEFAULT_TOP_K,
    judge: Judge = heuristic_judge,
    model: str | None = None,
) -> dict:
    """Run the Tier-1 cascade over the top-``top_k`` composite leads.

    Returns ``{"ranked": [ScoredCandidate...], "telemetry": {...}}``. Telemetry
    records the requested vs judged count, whether the hard cap bit, and the judge
    used — so the cost and any truncation are always visible.
    """
    requested = int(top_k)
    candidates = select_candidates(con, requested)
    verdicts = judge(candidates) if candidates else []
    ranked = rerank(candidates, verdicts)

    telemetry = {
        "requested_top_k": requested,
        "max_triage": MAX_TRIAGE,
        "cap_hit": requested > MAX_TRIAGE,
        "n_candidates": len(candidates),
        "n_judged": len(candidates),
        # Non-zero only for the LLM judge: loans that fell back to a neutral
        # verdict after exhausting retries (so they were not truly judged).
        "n_errors": getattr(judge, "n_errors", 0),
        "judge": getattr(judge, "__name__", judge.__class__.__name__),
        "model": model,
    }
    return {"ranked": ranked, "telemetry": telemetry}
