"""M7 Tier 1 — cost-aware LLM triage cascade (semantic plausibility).

Tier 0 (the deterministic composite, already built) ranks all ~11.3M loans for
free; this layer escalates **only the top-k composite leads** to a semantic
plausibility judge — "could this business plausibly justify this loan?" over
``borrower_name x NAICS x amount x jobs x proceeds``. World knowledge catches
mismatches pure statistics cannot ("'Elite Nail Spa LLC', 1 employee, $2.1M,
NAICS=landscaping").

Two judges share one :class:`~relief_probe.triage.judge.Judge` shape so the whole
pipeline (select -> judge -> re-rank -> validation gate) is exercised offline:

* :func:`~relief_probe.triage.judge.heuristic_judge` — deterministic, no LLM, no
  network, no ``agent`` extra. The default path and the tested gate; also the
  baseline the LLM must beat.
* :class:`~relief_probe.triage.judge.LlmJudge` — Haiku 4.5 with structured output
  (``--llm``). ``langchain_anthropic`` is imported lazily; a missing extra or
  ``ANTHROPIC_API_KEY`` raises a clear error. A **hard cap** bounds how many loans
  ever reach the model, so cost stays bounded and logged.

**Cardinal rule:** never run the LLM over the full population — Tier 0 does the
11.3M -> top-k cut for free. A high triage score is a *statistical lead for
review*, never proof of fraud — see ``RESPONSIBLE_USE.md``.
"""

from __future__ import annotations
