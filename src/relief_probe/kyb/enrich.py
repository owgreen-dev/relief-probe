"""Enrich the top-k composite leads with external KYB evidence (Tier-B, Loop 5).

This is the cost-bounded orchestration that fans a small shortlist of the
composite ranking out to an :class:`~relief_probe.kyb.provider.EvidenceProvider`
and folds the registry footprint back into a transparent refined score. It mirrors
the M7 triage cascade (``triage/core.py`` + ``triage/judge.py``) exactly:

* a **hard cap** (:data:`MAX_KYB`) bounds how many borrowers can ever reach the
  external API — the cost ceiling, aligned with the OpenCorporates free tier
  (~50 lookups/day) and logged on every run;
* a **bounded** ``ThreadPoolExecutor`` (each lookup is independent I/O);
* a within-run **cache** so a re-run is offline and the rate limit is spent at
  most once per borrower;
* **telemetry** (requested / enriched / cap_hit / cache hits / errors / quota)
  so the cost and any truncation are always visible;
* **graceful quota exhaustion**: a provider that raises
  :class:`~relief_probe.kyb.provider.QuotaExhaustedError` mid-run stops the
  fan-out cleanly and preserves everything already fetched (no crash, no loss).

The refinement is deliberately small and grounded (see :func:`evidence_refinement`):
a registry date *after* the Feb-15-2020 PPP eligibility cut-off, a confident
"not in the registry" result, or a non-commercial registered address each nudge a
lead up. Every output is a LEAD for review, never proof — see ``RESPONSIBLE_USE.md``.

DETERMINISTIC-FIRST: the default path needs no network and no key. The optional
agentic dossier narrator (:func:`synthesize_dossier` with a ``model``) is key-gated
behind the ``agent`` extra and narrates ONLY the grounded facts.
"""

from __future__ import annotations

import datetime as dt
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import duckdb

from relief_probe.kyb.provider import (
    EvidenceProvider,
    KybEvidence,
    QuotaExhaustedError,
)
from relief_probe.scoring import composite_ranking

#: Absolute ceiling on external KYB lookups in a single run, regardless of
#: ``top_k``. Aligned with the OpenCorporates free tier (~50 lookups/day without
#: an account — see RESPONSIBLE_USE.md); the live provider NEVER sees more than
#: this many borrowers per run. This is a cost bound, not a benchmark number.
MAX_KYB = 50

#: Default fan-out width — modest so we don't trip the API rate limit (whose own
#: backoff would erase the gain), mirroring the triage judge.
DEFAULT_MAX_CONCURRENCY = 4

#: PPP required a business to have been in operation on this date to be eligible.
#: A registry incorporation date *after* it is a near-explicit eligibility tell.
PPP_ELIGIBILITY_DATE = dt.date(2020, 2, 15)

#: Bonus weight for a maximally-confident KYB tell, matching the triage weight so
#: the external evidence nudges the shortlist without swamping the composite.
KYB_WEIGHT = 0.5

#: Substrings in a registered-address type that read as non-commercial — a weak,
#: grounded tell (a registered business at a residence / PO box / mailbox).
_NONCOMMERCIAL_ADDRESS_HINTS = ("residential", "po box", "p.o. box", "mailbox")


@dataclass(frozen=True)
class EnrichedLead:
    """A composite lead after KYB enrichment — the lead, its evidence, and a
    transparent refined score (``composite_score + the grounded KYB bonus``)."""

    loan_number: str
    borrower_name: str | None
    state: str | None
    amount: float | None
    composite_score: float
    evidence: KybEvidence | None
    kyb_bonus: float
    kyb_score: float
    kyb_reason: str


def evidence_refinement(evidence: KybEvidence | None) -> tuple[float, str]:
    """Map external evidence to a small KYB risk bonus + a grounded reason.

    Confidence-scaled so a low-confidence (possible wrong-entity) match nudges the
    score less. Returns ``(0.0, ...)`` when there is no usable evidence — we never
    manufacture a signal from absence. A bonus is a LEAD weight, never proof.
    """
    if evidence is None:
        return 0.0, "no external registry evidence"

    conf = max(0.0, min(1.0, float(evidence.match_confidence)))

    if evidence.is_non_registered:
        return (
            round(KYB_WEIGHT * conf, 4),
            "not found in the registry — a validated 'non-registered business' "
            "fraud indicator (confidence "
            f"{conf:.2f}; a borrower may use a DBA/variant, so this is a lead).",
        )

    bonus = 0.0
    reasons: list[str] = []
    reg = evidence.registration_date
    if reg is not None and reg > PPP_ELIGIBILITY_DATE:
        bonus += KYB_WEIGHT * conf
        reasons.append(
            f"registered {reg.isoformat()}, AFTER the Feb-15-2020 PPP eligibility "
            "date (the business was not yet operating when the program required)"
        )
    addr = (evidence.address_type or "").lower()
    if addr and any(hint in addr for hint in _NONCOMMERCIAL_ADDRESS_HINTS):
        bonus += 0.5 * KYB_WEIGHT * conf
        reasons.append(
            f"registered address type is non-commercial ({evidence.address_type})"
        )

    if not reasons:
        return 0.0, "registry footprint consistent with eligibility (no KYB tell)"
    return round(bonus, 4), "; ".join(reasons) + "."


@dataclass(frozen=True)
class _Lead:
    """The composite fields enrichment needs (a slim view of one ranked loan)."""

    loan_number: str
    borrower_name: str | None
    state: str | None
    amount: float | None
    composite_score: float


def _select_leads(con: duckdb.DuckDBPyConnection, k: int) -> list[_Lead]:
    """Top-``k`` composite leads as slim :class:`_Lead` rows (empty-safe)."""
    if k <= 0:
        return []
    ranking = composite_ranking(con, limit=k)
    if ranking.empty:
        return []
    leads: list[_Lead] = []
    for r in ranking.itertuples(index=False):
        leads.append(
            _Lead(
                loan_number=str(r.loan_number),
                borrower_name=r.borrower_name,
                state=r.state,
                amount=float(r.amount) if r.amount is not None else None,
                composite_score=float(r.composite_score),
            )
        )
    return leads


def _build_enriched(lead: _Lead, evidence: KybEvidence | None) -> EnrichedLead:
    bonus, reason = evidence_refinement(evidence)
    return EnrichedLead(
        loan_number=lead.loan_number,
        borrower_name=lead.borrower_name,
        state=lead.state,
        amount=lead.amount,
        composite_score=lead.composite_score,
        evidence=evidence,
        kyb_bonus=bonus,
        kyb_score=round(lead.composite_score + bonus, 6),
        kyb_reason=reason,
    )


def enrich_top_k(
    con: duckdb.DuckDBPyConnection,
    provider: EvidenceProvider,
    *,
    top_k: int,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    cache: dict[str, KybEvidence | None] | None = None,
) -> dict:
    """Enrich the top-``top_k`` composite leads with external KYB evidence.

    Pulls the composite ranking, clamps to :data:`MAX_KYB`, fans out to
    ``provider.fetch`` over a bounded thread pool, refines each lead's score with
    :func:`evidence_refinement`, and returns ``{"enriched": [EnrichedLead...],
    "telemetry": {...}}`` sorted highest refined-score first.

    ``cache`` (loan_number -> evidence) is consulted before any fetch and updated
    after each successful one; pass the SAME dict across runs to make a re-run
    offline (the within-run cost is spent at most once per borrower). A provider
    that raises :class:`QuotaExhaustedError` stops the fan-out cleanly:
    ``telemetry["quota_exhausted"]`` is True and everything already fetched is
    preserved.
    """
    requested = int(top_k)
    k = max(0, min(requested, MAX_KYB))
    cache = cache if cache is not None else {}
    leads = _select_leads(con, k)

    lock = threading.Lock()
    stop = threading.Event()
    counters = {"n_cache_hits": 0, "n_errors": 0, "quota_exhausted": False}

    def work(lead: _Lead) -> EnrichedLead | None:
        if stop.is_set():
            return None  # quota already exhausted: skip cleanly, no spend
        if lead.loan_number in cache:
            with lock:
                counters["n_cache_hits"] += 1
            return _build_enriched(lead, cache[lead.loan_number])
        try:
            evidence = provider.fetch(
                lead.borrower_name or "", lead.state, amount=lead.amount
            )
        except QuotaExhaustedError:
            stop.set()
            with lock:
                counters["quota_exhausted"] = True
            return None
        except Exception:  # one flaky lookup must not abort the batch (telemetered)
            with lock:
                counters["n_errors"] += 1
            return _build_enriched(lead, None)  # not cached: a re-run may retry
        cache[lead.loan_number] = evidence
        return _build_enriched(lead, evidence)

    if not leads:
        results: list[EnrichedLead | None] = []
    elif max(1, int(max_concurrency)) == 1 or len(leads) == 1:
        results = [work(lead) for lead in leads]
    else:
        with ThreadPoolExecutor(max_workers=max(1, int(max_concurrency))) as pool:
            results = list(pool.map(work, leads))

    enriched = [r for r in results if r is not None]
    # Refine the ranking by the grounded KYB bonus (stable: ties keep composite order).
    enriched.sort(key=lambda e: e.kyb_score, reverse=True)

    telemetry = {
        "requested": requested,
        "max_kyb": MAX_KYB,
        "cap_hit": requested > MAX_KYB,
        "n_leads": len(leads),
        "enriched": len(enriched),
        "n_cache_hits": counters["n_cache_hits"],
        "n_errors": counters["n_errors"],
        "quota_exhausted": counters["quota_exhausted"],
        "provider": getattr(provider, "source", provider.__class__.__name__),
    }
    return {"enriched": enriched, "telemetry": telemetry}


# --- optional agentic dossier narrator ---------------------------------------

_LEAD_NOT_PROOF = "This is a lead for review, not proof of fraud."


def _evidence_facts(evidence: KybEvidence | None) -> list[str]:
    """Grounded one-line facts from the evidence (no interpretation)."""
    if evidence is None:
        return ["No external registry evidence was found for this borrower."]
    facts: list[str] = []
    if evidence.is_non_registered:
        facts.append(
            f"The {evidence.source} registry was searched and returned no matching "
            f"company (confidence {evidence.match_confidence:.2f})."
        )
    else:
        if evidence.matched_name:
            facts.append(
                f"Matched registry record: {evidence.matched_name} "
                f"(confidence {evidence.match_confidence:.2f}, source {evidence.source})."
            )
        if evidence.registration_date:
            facts.append(
                f"Registry incorporation date: {evidence.registration_date.isoformat()}."
            )
        if evidence.address_type:
            facts.append(f"Registered address type: {evidence.address_type}.")
    if evidence.raw_ref:
        facts.append(f"Source record: {evidence.raw_ref}.")
    return facts


def _deterministic_dossier(lead: EnrichedLead, evidence: KybEvidence | None) -> str:
    """A grounded, no-LLM dossier summary (the deterministic-first default)."""
    amt = f"${lead.amount:,.0f}" if lead.amount is not None else "an unknown amount"
    name = lead.borrower_name or "this borrower"
    delta = (
        f"adds +{lead.kyb_bonus:.3f}" if lead.kyb_bonus else "adds no bonus"
    )
    head = (
        f"{name} ({amt}, {lead.state or 'unknown state'}) ranks at composite score "
        f"{lead.composite_score:.3f}; KYB enrichment {delta} "
        f"(refined score {lead.kyb_score:.3f}). {lead.kyb_reason}"
    )
    facts = " ".join(_evidence_facts(evidence))
    return f"{head} {facts} {_LEAD_NOT_PROOF}".strip()


def synthesize_dossier(
    lead: EnrichedLead, evidence: KybEvidence | None, *, model: str | None = None
) -> str:
    """Narrate a one-lead KYB dossier from grounded facts only.

    With ``model=None`` (the default) returns a deterministic, no-key, no-network
    summary (:func:`_deterministic_dossier`). With a ``model`` it has an Anthropic
    model rewrite that summary into a short investigator narrative using ONLY the
    grounded facts — ``langchain_anthropic`` is imported lazily and a missing
    ``agent`` extra or ``ANTHROPIC_API_KEY`` raises a clear, actionable error
    (mirrors :func:`relief_probe.similarity.explain.explain_cluster`). The model
    never invents and never asserts fraud.
    """
    summary = _deterministic_dossier(lead, evidence)
    if model is None:
        return summary

    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:  # pragma: no cover - exercised only with extra absent
        raise RuntimeError(
            "The KYB dossier --llm narration needs the `agent` extra. Install it "
            "with `uv sync --extra agent`, or use the deterministic summary "
            "(model=None)."
        ) from exc

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "The KYB dossier --llm narration needs ANTHROPIC_API_KEY in the "
            "environment. Export it (or set it in .env), or use the deterministic "
            "summary (model=None)."
        )

    facts = "\n".join(f"  - {f}" for f in _evidence_facts(evidence))
    prompt = (
        "You are a careful loan-fraud-lead investigator. Using ONLY the facts "
        "below, write a 2-4 sentence factual KYB (know-your-business) dossier note "
        "for this PPP loan lead. Note the external-registry footprint (does the "
        "business exist, when was it registered, what address). Do NOT invent "
        "details and do NOT assert fraud — a high score is a lead for review, not "
        "proof.\n\n"
        f"Borrower: {lead.borrower_name or 'unknown'} "
        f"({f'${lead.amount:,.0f}' if lead.amount is not None else 'unknown amount'}, "
        f"{lead.state or 'unknown state'})\n"
        f"Composite score: {lead.composite_score:.3f}; refined KYB score: "
        f"{lead.kyb_score:.3f}.\n"
        f"KYB assessment: {lead.kyb_reason}\n"
        f"External evidence facts:\n{facts or '  - (none)'}\n"
    )
    llm = ChatAnthropic(model=model, temperature=0, max_tokens=400)
    narrative = llm.invoke(prompt).content
    if isinstance(narrative, list):  # content blocks -> flatten to text
        narrative = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in narrative
        )
    return narrative.strip() or summary
