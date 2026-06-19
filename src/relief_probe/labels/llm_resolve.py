"""LLM-adjudicated entity resolution — recover the labels the exact resolver misses.

The precision-first resolver (``labels/resolve.py``) matches a release to a loan only
when a normalized borrower-name n-gram hits the loan index AND the loan's dollar
amount appears in the release. That misses, by construction, the cases where the
*name* doesn't match on tokens: DBA / "a/k/a" aliases, misspellings, and
sole-proprietor loans filed under a person's name (GPT-4-class models uniquely
generalize to unseen entity aliases — see docs/LLM_RESEARCH.md).

Design (keeps the benchmark sacrosanct)
---------------------------------------
1. **Block by amount, not name.** For each loan-fraud release, extract its dollar
   figures and find loans whose ``current_approval_amount`` equals one of them. The
   amount is the *external corroboration gate* — the same precision anchor the exact
   resolver uses — so an accepted LLM label always rests on a real, checkable signal,
   never on the model's say-so alone. Common/round amounts that match too many loans
   are skipped (the LLM can't disambiguate thousands of candidates).
2. **LLM adjudicates only the NAME.** Given the release text and one candidate loan
   (whose amount already matches), the model decides whether the release truly charges
   the business/person behind *this* loan, reasoning over DBA/alias/misspelling/person
   names. Structured, conservative, chain-of-thought-before-verdict.
3. **Accept** when the model matches AND its confidence clears a threshold. Labels are
   marked ``match_method='amount+llm'`` so they are always distinguishable from the
   exact labels and a purist benchmark can exclude them. They are **additive**: a loan
   already labeled by the exact resolver is never overwritten.

Deterministic-first / key-gated (mirrors the M5 agent + M7 triage paths): the blocking
and acceptance logic is pure Python and fully tested with a stub adjudicator; the
real :class:`LlmAdjudicator` (Claude, structured output, bounded concurrency, hard
cap) is lazily imported behind the ``agent`` extra and needs ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

import duckdb
import pandas as pd

from relief_probe.labels.resolve import normalize_name

#: Dollar figures in release prose, e.g. "$1,452,000" or "$2.1 million" (we handle the
#: comma form; "million" prose is left to the exact resolver's alleged_amount).
_DOLLARS = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{4,})(?:\.\d{2})?")

#: Don't block on amounts below this — small/round figures collide with many loans.
DEFAULT_MIN_AMOUNT = 5_000.0
#: Skip a dollar figure that matches more than this many loans (too ambiguous to
#: adjudicate; the amount isn't discriminating).
DEFAULT_MAX_CANDIDATES_PER_AMOUNT = 25
#: Absolute ceiling on candidate loans sent to the LLM in one run — the cost bound.
MAX_ADJUDICATIONS = 2_000


@dataclass(frozen=True)
class AdjudicationRequest:
    """One (release, candidate loan) pair whose amounts already match."""

    loan_number: str
    borrower_name: str
    state: str | None
    amount: float
    matched_dollar: int
    release_title: str
    release_body: str
    release_url: str
    alleged_amount: float | None
    source: str
    published_date: object


@dataclass(frozen=True)
class AdjudicationVerdict:
    """The adjudicator's read of one request."""

    is_match: bool
    confidence: float
    matched_name: str
    rationale: str


class Adjudicator(Protocol):
    """Maps requests to equally-ordered verdicts (a stub or the real LLM)."""

    def __call__(
        self, requests: list[AdjudicationRequest]
    ) -> list[AdjudicationVerdict]: ...


def extract_dollar_amounts(text: str, *, min_amount: float) -> set[int]:
    """Whole-dollar figures (>= ``min_amount``) mentioned in the release text."""
    out: set[int] = set()
    for m in _DOLLARS.finditer(text or ""):
        whole = int(m.group(1).replace(",", ""))
        if whole >= min_amount:
            out.add(whole)
    return out


def build_amount_index(
    con: duckdb.DuckDBPyConnection,
) -> dict[int, list[tuple[str, str, str, float]]]:
    """round(amount) -> list of (loan_number, name, state, amount) over all loans."""
    index: dict[int, list[tuple[str, str, str, float]]] = {}
    for loan_number, name, state, amount in con.execute(
        "SELECT loan_number, borrower_name, borrower_state, current_approval_amount "
        "FROM loans WHERE current_approval_amount IS NOT NULL "
        "AND borrower_name IS NOT NULL"
    ).fetchall():
        index.setdefault(int(round(amount)), []).append(
            (str(loan_number), name, state, float(amount))
        )
    return index


def block_candidates(
    release: dict,
    amount_index: dict[int, list[tuple[str, str, str, float]]],
    *,
    already_labeled: set[str],
    min_amount: float = DEFAULT_MIN_AMOUNT,
    max_candidates_per_amount: int = DEFAULT_MAX_CANDIDATES_PER_AMOUNT,
) -> list[AdjudicationRequest]:
    """Amount-blocked candidate loans for one release (excludes already-labeled loans).

    For each dollar figure in the release, look up loans with that exact amount; skip
    figures matching too many loans (ambiguous). Returns one request per surviving
    candidate loan.
    """
    text = (release.get("title") or "") + " . " + (release.get("body") or "")
    requests: list[AdjudicationRequest] = []
    for dollar in extract_dollar_amounts(text, min_amount=min_amount):
        hits = amount_index.get(dollar)
        if not hits or len(hits) > max_candidates_per_amount:
            continue  # no match, or too ambiguous to adjudicate
        for loan_number, name, state, amount in hits:
            if loan_number in already_labeled:
                continue
            requests.append(
                AdjudicationRequest(
                    loan_number=loan_number,
                    borrower_name=name,
                    state=state,
                    amount=amount,
                    matched_dollar=dollar,
                    release_title=release.get("title") or "",
                    release_body=release.get("body") or "",
                    release_url=release.get("url") or "",
                    alleged_amount=release.get("alleged_amount"),
                    source=release.get("source") or "doj",
                    published_date=release.get("published_date"),
                )
            )
    return requests


_FRAUD_COLS = (
    "case_id", "loan_number", "defendant_name", "business_name", "alleged_amount",
    "charge_date", "source", "source_url", "match_method", "match_confidence",
)


def _accepted_row(req: AdjudicationRequest, verdict: AdjudicationVerdict) -> dict:
    return {
        "case_id": hashlib.sha256(
            f"{req.release_url}|{req.loan_number}|llm".encode()
        ).hexdigest()[:16],
        "loan_number": req.loan_number,
        "defendant_name": verdict.matched_name or None,
        "business_name": normalize_name(req.borrower_name),
        "alleged_amount": req.alleged_amount,
        "charge_date": req.published_date,
        "source": req.source,
        "source_url": req.release_url,
        "match_method": "amount+llm",
        "match_confidence": round(float(verdict.confidence), 3),
    }


def resolve_with_llm(
    con: duckdb.DuckDBPyConnection,
    adjudicator: Adjudicator,
    *,
    threshold: float = 0.7,
    programs: tuple[str, ...] = ("ppp", "eidl", "both"),
    max_releases: int | None = None,
    min_amount: float = DEFAULT_MIN_AMOUNT,
    max_candidates_per_amount: int = DEFAULT_MAX_CANDIDATES_PER_AMOUNT,
    max_adjudications: int = MAX_ADJUDICATIONS,
    progress=None,
) -> dict:
    """Add LLM-adjudicated labels (additive; never overwrites exact-resolver labels).

    Returns a summary including how many candidates were adjudicated, whether the hard
    cap bit, and how many NEW loans were labeled.
    """
    already_labeled = {
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT loan_number FROM fraud_cases WHERE loan_number IS NOT NULL"
        ).fetchall()
    }
    amount_index = build_amount_index(con)

    placeholders = ", ".join("?" for _ in programs)
    releases = con.execute(
        f"SELECT url, title, body, published_date, alleged_amount, source "
        f"FROM press_releases WHERE program IN ({placeholders})",
        list(programs),
    ).fetch_df()
    if max_releases is not None:
        releases = releases.head(max_releases)

    requests: list[AdjudicationRequest] = []
    cap_hit = False
    for rec in releases.itertuples(index=False):
        pub = None if pd.isna(rec.published_date) else rec.published_date.date()
        release = {
            "url": rec.url,
            "title": rec.title,
            "body": rec.body,
            "published_date": pub,
            "alleged_amount": rec.alleged_amount,
            "source": rec.source,
        }
        # Dedupe within this run too (a loan can match several releases).
        seen = already_labeled | {r.loan_number for r in requests}
        requests.extend(
            block_candidates(
                release,
                amount_index,
                already_labeled=seen,
                min_amount=min_amount,
                max_candidates_per_amount=max_candidates_per_amount,
            )
        )
        if len(requests) >= max_adjudications:
            cap_hit = True
            requests = requests[:max_adjudications]
            break

    if progress:
        progress(f"{len(requests)} amount-blocked candidates → adjudicating")

    verdicts = adjudicator(requests) if requests else []

    new_rows: dict[str, dict] = {}  # loan_number -> best accepted row
    for req, verdict in zip(requests, verdicts, strict=True):
        if not verdict.is_match or verdict.confidence < threshold:
            continue
        row = _accepted_row(req, verdict)
        prev = new_rows.get(req.loan_number)
        if prev is None or row["match_confidence"] > prev["match_confidence"]:
            new_rows[req.loan_number] = row

    if new_rows:
        data = [tuple(r[c] for c in _FRAUD_COLS) for r in new_rows.values()]
        con.executemany(
            f"INSERT INTO fraud_cases ({', '.join(_FRAUD_COLS)}) "
            f"VALUES ({', '.join('?' for _ in _FRAUD_COLS)})",
            data,
        )

    return {
        "releases_scanned": len(releases),
        "candidates_adjudicated": len(requests),
        "cap_hit": cap_hit,
        "max_adjudications": max_adjudications,
        "new_loans_labeled": len(new_rows),
        "n_errors": getattr(adjudicator, "n_errors", 0),
    }


# --- the real LLM adjudicator (key-gated, concurrent, robust) ------------------

ADJUDICATION_SCHEMA: dict = {
    "title": "AdjudicationVerdict",
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "Brief reasoning BEFORE the verdict.",
        },
        "is_match": {
            "type": "boolean",
            "description": "True ONLY if the release charges the entity behind the loan.",
        },
        "confidence": {
            "type": "number",
            "description": "0.0-1.0 confidence in is_match.",
        },
        "matched_name": {
            "type": "string",
            "description": "The defendant/business name in the release this loan maps.",
        },
    },
    "required": ["reasoning", "is_match", "confidence", "matched_name"],
}

_SYSTEM_PROMPT = """\
You adjudicate whether a DOJ/SBA fraud press release charges the SPECIFIC business or \
person behind one PPP/EIDL loan. The loan's exact dollar amount already appears in the \
release, so the amount is NOT the question — the NAME is. Decide whether the borrower \
name on this loan is the entity the release charges, allowing for: DBA / "doing \
business as" / "a/k/a" aliases, misspellings and abbreviations, and SOLE PROPRIETORS \
(the release names a person; the loan may be under that person or their trade name).

Be conservative and precision-first: a false match silently corrupts a fraud-label \
benchmark. Only set is_match=true if the release's named entity is plausibly THIS \
borrower (not merely another business that happens to share the same loan amount). \
Reason step by step in `reasoning` BEFORE the verdict. A match is a LEAD-quality label, \
not a legal finding.
"""


def _request_prompt(req: AdjudicationRequest) -> str:
    body = (req.release_body or "")[:2500]
    return (
        f"LOAN:\n- borrower_name: {req.borrower_name}\n"
        f"- state: {req.state or 'unknown'}\n"
        f"- amount: ${req.amount:,.0f} (this figure appears in the release)\n\n"
        f"RELEASE TITLE: {req.release_title}\n"
        f"RELEASE BODY (truncated): {body}\n"
    )


class LlmAdjudicator:
    """Claude name-adjudicator with structured output — concurrent + robust.

    Lazily imports ``langchain_anthropic`` and needs ``ANTHROPIC_API_KEY``; mirrors
    :class:`relief_probe.triage.judge.LlmJudge` (bounded concurrency, per-item
    retry-then-conservative-fallback, ``n_errors`` telemetry). On exhausted retries a
    request falls back to ``is_match=False`` — the precision-safe default, so a flaky
    call never injects a bad label.
    """

    def __init__(
        self,
        *,
        model: str,
        temperature: float = 0.0,
        max_retries: int = 2,
        max_concurrency: int = 8,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.max_concurrency = max(1, int(max_concurrency))
        self.n_errors = 0
        self._lock = threading.Lock()
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # pragma: no cover - only without the extra
            raise RuntimeError(
                "LLM entity resolution needs the `agent` extra. Install it with "
                "`uv sync --extra agent`."
            ) from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "LLM entity resolution needs ANTHROPIC_API_KEY in the environment."
            )
        llm = ChatAnthropic(
            model=self.model, temperature=self.temperature, max_tokens=600
        )
        self._client = llm.with_structured_output(ADJUDICATION_SCHEMA)
        return self._client

    def _adjudicate_one(self, req: AdjudicationRequest) -> AdjudicationVerdict:
        client = self._ensure_client()
        messages = [("system", _SYSTEM_PROMPT), ("human", _request_prompt(req))]
        last_exc: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                raw = client.invoke(messages)
                return AdjudicationVerdict(
                    is_match=bool(raw.get("is_match", False)),
                    confidence=float(raw.get("confidence", 0.0) or 0.0),
                    matched_name=str(raw.get("matched_name", "") or ""),
                    rationale=str(raw.get("reasoning", "") or ""),
                )
            except Exception as exc:  # transient API error or unparseable response
                last_exc = exc
        with self._lock:
            self.n_errors += 1
        name = type(last_exc).__name__ if last_exc else "unknown"
        # Precision-safe fallback: never inject a label on a failed call.
        return AdjudicationVerdict(False, 0.0, "", f"adjudicator_error: {name}")

    def __call__(
        self, requests: list[AdjudicationRequest]
    ) -> list[AdjudicationVerdict]:
        if not requests:
            return []
        self._ensure_client()
        if self.max_concurrency == 1 or len(requests) == 1:
            return [self._adjudicate_one(r) for r in requests]
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            return list(pool.map(self._adjudicate_one, requests))
