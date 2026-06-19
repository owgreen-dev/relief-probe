"""Hybrid similar-loan retrieval engine — blocking-first, RRF-fused, explainable.

``find_similar`` answers "which loans most resemble this one?" for investigation. It
is **read-only**, **not a detector** (emits no signals), and deliberately cheap:

1. **Block** the candidate pool with one SQL pass — same ``borrower_state`` (when
   ``same_state``), ``current_approval_amount`` within a +/- ``amount_tol`` band and
   at/above ``min_amount`` (the dollar threshold), self excluded, capped to the
   ``max_pool`` *closest-by-dollar* loans. This is the whole "never embed millions of
   names" guarantee — we only ever embed this pool.
2. **Rank** the pool three ways: name **semantic** similarity (a torch-free
   :class:`~relief_probe.embeddings.Model2VecEmbedder` by default), name **lexical**
   similarity (:class:`~relief_probe.embeddings.HashingEmbedder`), and a deterministic
   **structured**-proximity ranking (dollar delta, same NAICS, same ZIP5).
3. **Fuse** the three rankings with
   :func:`~relief_probe.benchmark.core.reciprocal_rank_fusion` — rank-based, so no
   component's scale can swamp another (the additive-blend lesson from
   ``docs/LLM_RESEARCH.md``).

Area and the dollar band are **hard** blocks; NAICS is a **soft** signal (exposed +
fused, never a filter) because rings re-file the same fabricated business under
varied/wrong NAICS codes — filtering on it would hide the most interesting neighbors.
Every neighbor carries its component scores and an ``is_fraud`` flag, so the result
explains itself. A resemblance is a *lead for review*, not proof.
"""

from __future__ import annotations

from typing import Any

import duckdb
import numpy as np

from relief_probe.benchmark.core import labeled_fraud_loans, reciprocal_rank_fusion
from relief_probe.embeddings import Embedder

#: Carried on every result. See RESPONSIBLE_USE.md.
SIMILARITY_DISCLAIMER = (
    "Similar-case retrieval surfaces loans that resemble this one (by business name "
    "and structured proximity) for investigation — not a fraud determination. A "
    "neighbor in fraud_cases is a prosecuted case; an unlabeled neighbor is "
    "unlabeled, not innocent. See RESPONSIBLE_USE.md."
)

#: RRF fusion weights for [semantic, lexical, structured]. Name-semantic leads;
#: lexical is a cheaper proxy; structured grounds it in dollar/industry proximity.
#: The single tunable knob — Cormack's k=60 needs no tuning.
_FUSION_WEIGHTS = [1.0, 0.6, 0.8]

#: Loan columns the engine reads (target + every pool candidate).
_FIELDS = (
    "loan_number",
    "borrower_name",
    "borrower_city",
    "borrower_state",
    "borrower_zip",
    "naics_code",
    "current_approval_amount",
    "jobs_reported",
    "date_approved",
)


def _zip5(zip_code: str | None) -> str | None:
    """First 5 digits of a (possibly ZIP+4) borrower_zip, or None."""
    z = (zip_code or "").strip()[:5]
    return z or None


def _row_to_loan(row: tuple) -> dict[str, Any]:
    """Map a `_FIELDS`-ordered row to a dict, with a derived `zip5`."""
    loan = dict(zip(_FIELDS, row, strict=True))
    loan["zip5"] = _zip5(loan.get("borrower_zip"))
    if loan.get("date_approved") is not None:
        loan["date_approved"] = str(loan["date_approved"])
    if loan.get("current_approval_amount") is not None:
        loan["current_approval_amount"] = float(loan["current_approval_amount"])
    if loan.get("jobs_reported") is not None:
        loan["jobs_reported"] = float(loan["jobs_reported"])
    loan["loan_number"] = str(loan["loan_number"])
    return loan


def _empty(loan_number: str, reason: str) -> dict[str, Any]:
    """The graceful no-result shape (loan missing, no name/amount, empty pool)."""
    return {
        "loan_number": loan_number,
        "available": False,
        "reason": reason,
        "target": None,
        "params": {},
        "neighbors": [],
        "summary": {"pool_size": 0, "n_neighbors": 0, "n_fraud_neighbors": 0},
        "disclaimer": SIMILARITY_DISCLAIMER,
    }


def _structured_rank(target: dict, pool: list[dict]) -> list[str]:
    """Rank pool loan_numbers by structured proximity to the target (best first).

    Sort key (ascending — smaller is closer): same-NAICS first, then dollar delta,
    then same-ZIP5, then loan_number for a deterministic tie-break.
    """
    t_naics = target.get("naics_code")
    t_zip = target.get("zip5")
    t_amt = target["current_approval_amount"]

    def key(loan: dict) -> tuple:
        amt = loan.get("current_approval_amount") or 0.0
        delta = abs(amt - t_amt) / t_amt if t_amt else 1.0
        same_naics = t_naics is not None and loan.get("naics_code") == t_naics
        same_zip = t_zip is not None and loan.get("zip5") == t_zip
        return (not same_naics, delta, not same_zip, loan["loan_number"])

    return [loan["loan_number"] for loan in sorted(pool, key=key)]


def find_similar(
    con: duckdb.DuckDBPyConnection,
    loan_number: str,
    *,
    k: int = 20,
    min_amount: float = 150_000.0,
    amount_tol: float = 0.25,
    same_state: bool = True,
    max_pool: int = 3000,
    embedder: Embedder | None = None,
    lexical: Embedder | None = None,
) -> dict[str, Any]:
    """Return the ``k`` loans most similar to ``loan_number`` (read-only).

    ``min_amount`` is the dollar threshold below which no candidate is considered (so
    we never embed the millions of small loans). ``amount_tol`` sets the +/- dollar
    band, ``same_state`` blocks to the borrower's state, and ``max_pool`` caps how
    many (closest-by-dollar) candidates get embedded. ``embedder`` (semantic) and
    ``lexical`` are injectable for offline tests; the defaults load lazily.

    Returns a self-describing dict (see the module docstring); every neighbor exposes
    its component scores and an ``is_fraud`` flag. Never raises on bad input — returns
    an ``available: False`` shape with a ``reason`` instead.
    """
    target_row = con.execute(
        f"SELECT {', '.join(_FIELDS)} FROM loans WHERE loan_number = ?",
        [loan_number],
    ).fetchone()
    if target_row is None:
        return _empty(loan_number, "loan_not_found")
    target = _row_to_loan(target_row)

    name = (target.get("borrower_name") or "").strip()
    if not name:
        return _empty(loan_number, "missing_name")
    t_amt = target.get("current_approval_amount")
    if not t_amt or t_amt <= 0:
        return _empty(loan_number, "missing_amount")

    # Lazy defaults: only construct (and only the semantic one loads a model) when used.
    if embedder is None:
        from relief_probe.embeddings import Model2VecEmbedder

        embedder = Model2VecEmbedder()
    if lexical is None:
        from relief_probe.embeddings import HashingEmbedder

        lexical = HashingEmbedder()

    lo = max(min_amount, t_amt * (1.0 - amount_tol))
    hi = t_amt * (1.0 + amount_tol)
    state_clause = "AND borrower_state = ?" if same_state else ""
    params: list[Any] = [loan_number, min_amount, lo, hi]
    if same_state:
        params.append(target.get("borrower_state"))
    params.append(t_amt)  # ORDER BY closeness
    params.append(int(max_pool))

    pool_rows = con.execute(
        f"""
        SELECT {', '.join(_FIELDS)}
        FROM loans
        WHERE loan_number <> ?
          AND current_approval_amount >= ?
          AND current_approval_amount BETWEEN ? AND ?
          AND borrower_name IS NOT NULL AND borrower_name <> ''
          {state_clause}
        ORDER BY abs(current_approval_amount - ?) ASC, loan_number
        LIMIT ?
        """,
        params,
    ).fetchall()
    pool = [_row_to_loan(r) for r in pool_rows]
    if not pool:
        result = _empty(loan_number, "empty_pool")
        result["target"] = target
        result["params"] = {
            "k": k, "min_amount": min_amount, "amount_tol": amount_tol,
            "same_state": same_state, "max_pool": max_pool, "pool_size": 0,
        }
        return result

    # Embed [target] + pool names once per embedder; rows are L2-normalized so a dot
    # product is a cosine similarity.
    names = [name] + [loan["borrower_name"] for loan in pool]
    sem = embedder.embed(names)
    lex = lexical.embed(names)
    sem_sims = (sem[1:] @ sem[0]).astype(float)
    lex_sims = (lex[1:] @ lex[0]).astype(float)

    ln_list = [loan["loan_number"] for loan in pool]
    order = list(range(len(pool)))
    semantic_rank = [
        ln_list[i] for i in sorted(order, key=lambda i: (-sem_sims[i], ln_list[i]))
    ]
    lexical_rank = [
        ln_list[i] for i in sorted(order, key=lambda i: (-lex_sims[i], ln_list[i]))
    ]
    structured_rank = _structured_rank(target, pool)

    rankings = [semantic_rank, lexical_rank, structured_rank]
    fused = reciprocal_rank_fusion(rankings, k=60, weights=_FUSION_WEIGHTS)
    fused_score = _rrf_scores(rankings)

    fraud = labeled_fraud_loans(con)
    t_naics = target.get("naics_code")
    t_zip = target.get("zip5")
    sims_by_ln = {
        ln_list[i]: (float(sem_sims[i]), float(lex_sims[i])) for i in range(len(pool))
    }
    loan_by_ln = {loan["loan_number"]: loan for loan in pool}

    neighbors: list[dict[str, Any]] = []
    for rank, ln in enumerate(fused[:k], start=1):
        loan = loan_by_ln[ln]
        sem_s, lex_s = sims_by_ln[ln]
        amt = loan.get("current_approval_amount") or 0.0
        neighbors.append(
            {
                **{f: loan.get(f) for f in _FIELDS},
                "zip5": loan.get("zip5"),
                "semantic_sim": round(sem_s, 4),
                "lexical_sim": round(lex_s, 4),
                "amount_delta_pct": round(abs(amt - t_amt) / t_amt, 4),
                "same_naics": t_naics is not None and loan.get("naics_code") == t_naics,
                "same_state": loan.get("borrower_state") == target.get("borrower_state"),
                "same_zip5": t_zip is not None and loan.get("zip5") == t_zip,
                "is_fraud": ln in fraud,
                "rank": rank,
                "fused_score": round(fused_score.get(ln, 0.0), 6),
            }
        )

    target["is_fraud"] = loan_number in fraud
    n_fraud = sum(1 for n in neighbors if n["is_fraud"])
    deltas = [n["amount_delta_pct"] for n in neighbors]
    summary = {
        "pool_size": len(pool),
        "n_neighbors": len(neighbors),
        "n_fraud_neighbors": n_fraud,
        "n_same_naics": sum(1 for n in neighbors if n["same_naics"]),
        "n_same_zip5": sum(1 for n in neighbors if n["same_zip5"]),
        "median_amount_delta_pct": round(float(np.median(deltas)), 4) if deltas else None,
    }
    return {
        "loan_number": loan_number,
        "available": True,
        "reason": None,
        "target": target,
        "params": {
            "k": k, "min_amount": min_amount, "amount_tol": amount_tol,
            "same_state": same_state, "max_pool": max_pool, "pool_size": len(pool),
            "semantic_embedder": type(embedder).__name__,
            "lexical_embedder": type(lexical).__name__,
        },
        "neighbors": neighbors,
        "summary": summary,
        "disclaimer": SIMILARITY_DISCLAIMER,
    }


def _rrf_scores(rankings: list[list[str]], *, k: int = 60) -> dict[str, float]:
    """The RRF score per item (for display), matching reciprocal_rank_fusion's math."""
    scores: dict[str, float] = {}
    for ranking, w in zip(rankings, _FUSION_WEIGHTS, strict=True):
        for rank, ln in enumerate(ranking, start=1):
            scores[ln] = scores.get(ln, 0.0) + w / (k + rank)
    return scores
