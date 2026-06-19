"""Grounded natural-language explanation of a similar-case cluster.

Two paths over the *same* retrieved facts (mirroring
:func:`relief_probe.agent.graph._synthesize_narrative`):

* :func:`deterministic_summary` — pure Python, no LLM, no network, always available.
  One factual sentence from the result's counts. This is the deterministic-first
  default the CLI/dashboard can always show.
* :func:`explain_cluster` — BYOK: an Anthropic model rewrites that summary into a
  short investigator narrative using ONLY the retrieved neighbors. ``langchain_anthropic``
  is imported lazily; a missing ``agent`` extra or ``ANTHROPIC_API_KEY`` raises a clear,
  actionable error. The model never re-ranks or invents — it narrates grounded facts.

A resemblance is a *lead for review*, never proof — see ``RESPONSIBLE_USE.md``.
"""

from __future__ import annotations

import os
from typing import Any


def deterministic_summary(result: dict[str, Any]) -> str:
    """A grounded one-sentence cluster summary from the retrieval facts (no LLM)."""
    if not result.get("available"):
        return (
            f"No similar-case cluster for loan {result.get('loan_number')} "
            f"({result.get('reason', 'unavailable')})."
        )
    s = result["summary"]
    target = result["target"]
    name = target.get("borrower_name") or "this borrower"
    amt = target.get("current_approval_amount")
    amt_str = f"${amt:,.0f}" if amt is not None else "an unknown amount"
    lead = (
        f"{name} ({amt_str}, {target.get('borrower_state') or 'unknown state'}) "
        f"sits in a pool of {s['pool_size']:,} loans of similar size; its top "
        f"{s['n_neighbors']} look-alikes include {s['n_fraud_neighbors']} prosecuted "
        f"case(s) and {s['n_same_naics']} sharing its industry"
    )
    if s.get("n_same_zip5"):
        lead += f", {s['n_same_zip5']} at the same ZIP"
    return lead + ". A resemblance is a lead for review, not proof."


def explain_cluster(result: dict[str, Any], *, model: str) -> str:
    """BYOK: have an Anthropic model narrate the cluster from grounded facts only.

    Imports ``langchain_anthropic`` lazily; raises a clear, actionable
    :class:`RuntimeError` when the ``agent`` extra or ``ANTHROPIC_API_KEY`` is
    absent. Falls back to :func:`deterministic_summary` if the cluster is empty.
    """
    if not result.get("available"):
        return deterministic_summary(result)

    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:  # pragma: no cover - exercised only with extra absent
        raise RuntimeError(
            "The --llm explanation needs the `agent` extra. Install it with "
            "`uv sync --extra agent`, or use the deterministic summary."
        ) from exc

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "The --llm explanation needs ANTHROPIC_API_KEY in the environment. "
            "Export it (or set it in .env), or use the deterministic summary."
        )

    target = result["target"]
    s = result["summary"]
    top = result["neighbors"][:6]
    facts = "\n".join(
        f"  - {n.get('borrower_name')} (${(n.get('current_approval_amount') or 0):,.0f}, "
        f"{n.get('borrower_state')}, NAICS {n.get('naics_code')}; "
        f"name-similarity {n.get('semantic_sim')}; "
        f"{'PROSECUTED' if n.get('is_fraud') else 'unlabeled'})"
        for n in top
    ) or "  - (none)"
    prompt = (
        "You are a careful loan-fraud-lead investigator. Using ONLY the facts below, "
        "write a 2-4 sentence factual description of how this loan relates to its "
        "similar-case cluster. Note shared name/industry/amount patterns and how many "
        "neighbors are already prosecuted. Do NOT invent details, do NOT assert fraud "
        "— a resemblance is a lead for review, not proof.\n\n"
        f"Target loan: {target.get('borrower_name')} "
        f"(${(target.get('current_approval_amount') or 0):,.0f}, "
        f"{target.get('borrower_state')}, NAICS {target.get('naics_code')}; "
        f"{'in fraud_cases' if target.get('is_fraud') else 'unlabeled'})\n"
        f"Pool size: {s['pool_size']}; neighbors shown: {s['n_neighbors']}; "
        f"prosecuted neighbors: {s['n_fraud_neighbors']}; same-industry: "
        f"{s['n_same_naics']}.\n"
        f"Top look-alikes:\n{facts}\n"
    )
    llm = ChatAnthropic(model=model, temperature=0, max_tokens=400)
    narrative = llm.invoke(prompt).content
    if isinstance(narrative, list):  # content blocks -> flatten to text
        narrative = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in narrative
        )
    return narrative.strip() or deterministic_summary(result)
