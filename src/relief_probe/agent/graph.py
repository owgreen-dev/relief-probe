"""Loan investigator: gather evidence, then assemble a grounded report.

:func:`investigate` has two paths over the *same* read-only tools:

* **Deterministic** (``use_llm=False``, the default and the tested gate) —
  pure Python: :func:`relief_probe.agent.tools.gather_evidence` →
  :func:`relief_probe.agent.report.build_report`. No LLM, no network, and no
  ``langgraph`` / ``langchain`` import at module load, so it runs in the core
  env with the ``agent`` extra absent.

* **LLM-synthesized** (``use_llm=True``) — gathers the *same* deterministic
  evidence, builds the *same* grounded report, then asks an Anthropic model
  (``claude-haiku-4-5`` by default; override with ``RELIEF_PROBE_LLM_MODEL``) to
  rewrite only the prose summary using **only** the tool-fetched facts. Risk
  level, evidence rows, and the disclaimer stay deterministic, so the model can
  reword but never re-rank or invent. ``langchain_anthropic`` is imported
  lazily; a missing extra or ``ANTHROPIC_API_KEY`` raises a clear error.

A populated report is a *statistical lead for review*, never proof of fraud —
the :data:`~relief_probe.agent.report.DISCLAIMER` rides on every report.
"""

from __future__ import annotations

import os
from typing import Any

import duckdb

from relief_probe.agent.report import InvestigatorReport, build_report
from relief_probe.agent.tools import gather_evidence
from relief_probe.config import llm_model


def investigate(
    con: duckdb.DuckDBPyConnection,
    loan_number: str,
    *,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Investigate one loan and return ``{report, telemetry}``.

    The deterministic default gathers evidence and builds a grounded report
    with zero LLM involvement. ``use_llm=True`` additionally has the model
    synthesize the summary narrative from the same facts (see module docstring).

    Telemetry records the ``path`` taken and the number of evidence ``tool_calls``
    that fed the report.
    """
    evidence = gather_evidence(con, loan_number)
    # gather_evidence consults six read-only tools (profile, signals, peer,
    # fraud_case, composite) bundled under stable keys.
    tool_calls = sum(1 for k in evidence if k != "loan_number")
    report = build_report(evidence)

    telemetry: dict[str, Any] = {
        "path": "deterministic",
        "tool_calls": tool_calls,
        "use_llm": use_llm,
    }

    if use_llm:
        model = llm_model()
        report = _synthesize_narrative(report, evidence, model=model)
        telemetry["path"] = "llm"
        telemetry["model"] = model

    return {"report": report, "telemetry": telemetry}


def _synthesize_narrative(
    report: InvestigatorReport, evidence: dict[str, Any], *, model: str
) -> InvestigatorReport:
    """Rewrite the report's summary with the LLM, grounded on evidence only.

    Imports ``langchain_anthropic`` lazily so module import never requires the
    ``agent`` extra. Raises a clear, actionable error when the extra or the API
    key is missing. The model receives *only* the gathered facts and the
    deterministic risk level; everything else on the report is preserved.
    """
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:  # pragma: no cover - exercised only with extra absent
        raise RuntimeError(
            "The LLM path needs the `agent` extra. Install it with "
            "`uv sync --extra agent`, or use the deterministic path "
            "(use_llm=False)."
        ) from exc

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "The LLM path needs ANTHROPIC_API_KEY in the environment. "
            "Export it, or use the deterministic path (use_llm=False)."
        )

    facts = "\n".join(f"- {item.claim}" for item in report.evidence) or "- (none)"
    prompt = (
        "You are a careful loan-fraud-lead investigator. Using ONLY the facts "
        "below, write a 2-3 sentence factual summary of this lead. Do not invent "
        "details, do not speculate beyond the facts, and do not assert fraud — a "
        f"high score is a lead for review, not proof.\n\n"
        f"Loan: {report.loan_number}\n"
        f"Deterministic risk level: {report.risk_level}\n"
        f"Gathered facts:\n{facts}\n"
    )
    # Haiku 4.5 (default) supports temperature; the narrative is short, so cap tokens.
    llm = ChatAnthropic(model=model, temperature=0, max_tokens=400)
    narrative = llm.invoke(prompt).content
    if isinstance(narrative, list):  # content blocks -> flatten to text
        narrative = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in narrative
        )

    import dataclasses

    return dataclasses.replace(report, summary=narrative.strip() or report.summary)
