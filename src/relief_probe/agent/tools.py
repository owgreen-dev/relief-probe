"""Read-only evidence-gathering tools over the warehouse.

These are the atomic facts an investigator (deterministic or LLM-driven) is
allowed to consult. Every function takes a live DuckDB connection plus a
``loan_number`` (the entity key — a string, *not* an NPI) and returns plain
Python dicts/lists. Nothing here writes to the warehouse.

Each tool degrades gracefully: a loan that does not exist, was never flagged,
or whose cohort is too thin to compare returns an explicit empty / "not
available" shape rather than raising, so the report builder can ground its
narrative on exactly what was (and was not) found.
"""

from __future__ import annotations

import json
from typing import Any

import duckdb

from relief_probe.scoring import CORROBORATION_WEIGHT

#: Minimum number of NAICS x state peers required before a peer comparison is
#: considered meaningful (mirrors the cohort-outlier detector default).
MIN_COHORT_SIZE = 30

#: Loan columns surfaced in the profile, in a stable order.
_PROFILE_FIELDS = (
    "borrower_name",
    "naics_code",
    "borrower_state",
    "current_approval_amount",
    "jobs_reported",
    "loan_status",
    "forgiveness_amount",
    "date_approved",
)


def loan_profile(con: duckdb.DuckDBPyConnection, loan_number: str) -> dict[str, Any]:
    """Return the loan's key fields, or ``{}`` if no such loan exists."""
    row = con.execute(
        f"SELECT {', '.join(_PROFILE_FIELDS)} FROM loans WHERE loan_number = ?",
        [loan_number],
    ).fetchone()
    if row is None:
        return {}
    profile: dict[str, Any] = dict(zip(_PROFILE_FIELDS, row, strict=True))
    # Dates are nicer to carry as ISO strings through the JSON-shaped pipeline.
    if profile.get("date_approved") is not None:
        profile["date_approved"] = str(profile["date_approved"])
    return profile


def loan_signals(
    con: duckdb.DuckDBPyConnection, loan_number: str
) -> list[dict[str, Any]]:
    """Return every detector firing on the loan, with parsed evidence."""
    rows = con.execute(
        """
        SELECT detector_id, score, evidence_json
        FROM signals
        WHERE loan_number = ?
        ORDER BY score DESC
        """,
        [loan_number],
    ).fetchall()
    return [
        {
            "detector_id": detector_id,
            "score": float(score) if score is not None else None,
            "evidence": json.loads(evidence_json) if evidence_json else {},
        }
        for detector_id, score, evidence_json in rows
    ]


def peer_comparison(
    con: duckdb.DuckDBPyConnection,
    loan_number: str,
    *,
    min_cohort_size: int = MIN_COHORT_SIZE,
) -> dict[str, Any]:
    """Compare the loan's dollars-per-job to its NAICS x state peers.

    Returns ``{'available': False, ...}`` when jobs/amount are missing or the
    cohort has fewer than ``min_cohort_size`` peers to compare against.
    """
    row = con.execute(
        """
        SELECT naics_code, borrower_state, current_approval_amount, jobs_reported
        FROM loans
        WHERE loan_number = ?
        """,
        [loan_number],
    ).fetchone()
    if row is None:
        return {"available": False, "reason": "loan_not_found"}

    naics_code, state, amount, jobs = row
    if not jobs or jobs < 1 or not amount or amount <= 0:
        return {"available": False, "reason": "missing_jobs_or_amount"}
    if naics_code is None or state is None:
        return {"available": False, "reason": "missing_cohort_key"}

    cohort = con.execute(
        """
        SELECT
            COUNT(*)                                              AS cohort_size,
            MEDIAN(current_approval_amount / jobs_reported)       AS cohort_median
        FROM loans
        WHERE naics_code = ?
          AND borrower_state = ?
          AND jobs_reported >= 1
          AND current_approval_amount > 0
        """,
        [naics_code, state],
    ).fetchone()
    cohort_size, cohort_median = int(cohort[0]), cohort[1]
    if cohort_size < min_cohort_size:
        return {
            "available": False,
            "reason": "cohort_too_small",
            "cohort_size": cohort_size,
        }

    amount_per_job = float(amount) / float(jobs)
    median = float(cohort_median) if cohort_median else None
    return {
        "available": True,
        "cohort": f"{naics_code} | {state}",
        "cohort_size": cohort_size,
        "amount_per_job": round(amount_per_job, 2),
        "cohort_median_amount_per_job": round(median, 2) if median else None,
        "x_cohort_median": round(amount_per_job / median, 2) if median else None,
    }


def fraud_case_check(
    con: duckdb.DuckDBPyConnection, loan_number: str
) -> dict[str, Any]:
    """Return whether the loan is linked to any resolved DOJ/OIG fraud case."""
    rows = con.execute(
        """
        SELECT case_id, defendant_name, business_name, alleged_amount,
               charge_date, source, source_url, match_method, match_confidence
        FROM fraud_cases
        WHERE loan_number = ?
        ORDER BY match_confidence DESC NULLS LAST
        """,
        [loan_number],
    ).fetchall()
    cases = [
        {
            "case_id": case_id,
            "defendant_name": defendant_name,
            "business_name": business_name,
            "alleged_amount": (
                float(alleged_amount) if alleged_amount is not None else None
            ),
            "charge_date": str(charge_date) if charge_date is not None else None,
            "source": source,
            "source_url": source_url,
            "match_method": match_method,
            "match_confidence": (
                float(match_confidence) if match_confidence is not None else None
            ),
        }
        for (
            case_id,
            defendant_name,
            business_name,
            alleged_amount,
            charge_date,
            source,
            source_url,
            match_method,
            match_confidence,
        ) in rows
    ]
    return {"labeled": bool(cases), "cases": cases}


def composite_for(
    con: duckdb.DuckDBPyConnection, loan_number: str
) -> dict[str, Any]:
    """Return the loan's composite score, or ``{'flagged': False}`` if unflagged.

    Mirrors :func:`relief_probe.scoring.composite_ranking` for a single loan:
    the strongest single signal plus a corroboration bonus per additional
    detector that fired.
    """
    row = con.execute(
        """
        SELECT
            MAX(score) + ? * (COUNT(*) - 1)   AS composite_score,
            COUNT(*)                          AS n_signals,
            LIST(DISTINCT detector_id)        AS detectors
        FROM signals
        WHERE loan_number = ?
        """,
        [CORROBORATION_WEIGHT, loan_number],
    ).fetchone()
    if row is None or row[1] == 0:
        return {"flagged": False}
    composite_score, n_signals, detectors = row
    return {
        "flagged": True,
        "composite_score": round(float(composite_score), 4),
        "n_signals": int(n_signals),
        "detectors": list(detectors),
    }


def gather_evidence(
    con: duckdb.DuckDBPyConnection, loan_number: str
) -> dict[str, Any]:
    """Bundle every read-only tool's output under stable keys for one loan."""
    return {
        "loan_number": loan_number,
        "profile": loan_profile(con, loan_number),
        "signals": loan_signals(con, loan_number),
        "peer_comparison": peer_comparison(con, loan_number),
        "fraud_case": fraud_case_check(con, loan_number),
        "composite": composite_for(con, loan_number),
    }


def similar_loans(
    con: duckdb.DuckDBPyConnection,
    loan_number: str,
    *,
    k: int = 10,
    min_amount: float = 150_000.0,
    embedder: Any = None,
    lexical: Any = None,
) -> dict[str, Any]:
    """Retrieve the ``k`` loans most similar to this one (read-only, opt-in).

    Delegates to :func:`relief_probe.similarity.core.find_similar` — a hybrid
    (name semantic + lexical + dollar/industry) look-alike finder for investigation.
    This is NOT a detector and emits no signals; a resemblance is a lead for review.

    Imported lazily and deliberately kept OUT of :func:`gather_evidence` so the
    default investigation path stays pure-Python, offline, and free of an embedding
    model load. ``embedder``/``lexical`` are injectable for tests; defaults load
    lazily inside the engine.
    """
    from relief_probe.similarity.core import find_similar

    return find_similar(
        con, loan_number, k=k, min_amount=min_amount,
        embedder=embedder, lexical=lexical,
    )
