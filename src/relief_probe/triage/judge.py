"""Tier-1 plausibility judges (LLM-as-judge, plus a deterministic baseline).

The judge answers one question per loan — *could this business plausibly justify
this loan?* — and returns a categorical :class:`PlausibilityVerdict`
(``implausibility`` 0-3 with an explicit rubric, a ``verdict`` enum label, and
free-text ``reasons``). A :class:`Judge` is any callable mapping a list of
:class:`~relief_probe.triage.core.LoanCandidate` to an equally-ordered list of
verdicts, so the orchestration and validation gate are judge-agnostic.

Two implementations:

* :func:`heuristic_judge` — deterministic, offline, no ``agent`` extra. Scores
  implausibility from structured-field tells (dollars-per-job vs the program's
  per-employee payroll ceiling, single-job mega-loans, round-number amounts). It
  is, by design, mostly a restatement of the $/job signal the composite already
  ranks on — so it is the **baseline** the LLM must beat, and it lets the whole
  cascade run and be tested with no key.
* :class:`LlmJudge` — Haiku 4.5 with structured output. This is the novel signal:
  world knowledge catches *semantic* mismatches statistics cannot (industry vs
  name, implausible scale for the trade). ``langchain_anthropic`` is imported
  lazily and the API key is required only here.

A verdict is a *lead for review*, never proof of fraud — see ``RESPONSIBLE_USE.md``.
"""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from relief_probe.detectors.payroll_cap import FIRST_DRAW_CAP, FOOD_ACCOMMODATION_CAP

if TYPE_CHECKING:  # avoid a runtime import cycle (core imports judge)
    from relief_probe.triage.core import LoanCandidate

#: The 0-3 implausibility ladder, with the enum label carried on each verdict.
#: A categorical integer scale with explicit definitions calibrates an LLM judge
#: far better than a free-form number (LLM-as-judge best practice).
VERDICT_LABELS: dict[int, str] = {
    0: "plausible",
    1: "minor_concern",
    2: "implausible",
    3: "egregious",
}
VERDICTS: tuple[str, ...] = tuple(VERDICT_LABELS.values())


@dataclass(frozen=True)
class PlausibilityVerdict:
    """One judge's read of a single loan's plausibility.

    ``implausibility`` is the 0-3 score; ``verdict`` is its enum label
    (:data:`VERDICT_LABELS`); ``reasons`` are short grounded justifications.
    """

    implausibility: int
    verdict: str
    reasons: list[str]

    def __post_init__(self) -> None:
        if self.implausibility not in VERDICT_LABELS:
            raise ValueError(
                f"implausibility must be one of {sorted(VERDICT_LABELS)}, "
                f"got {self.implausibility!r}"
            )

    @classmethod
    def of(cls, implausibility: int, reasons: list[str]) -> PlausibilityVerdict:
        """Build a verdict from a score, deriving the enum label from the rubric."""
        score = max(0, min(3, int(implausibility)))
        return cls(score, VERDICT_LABELS[score], reasons)


class Judge(Protocol):
    """A plausibility judge: candidates -> equally-ordered verdicts."""

    def __call__(
        self, candidates: list[LoanCandidate]
    ) -> list[PlausibilityVerdict]: ...


def _amount_per_job(candidate: LoanCandidate) -> float | None:
    """Dollars-per-job, or None when jobs/amount are missing or non-positive."""
    amount, jobs = candidate.amount, candidate.jobs
    if not amount or amount <= 0 or not jobs or jobs < 1:
        return None
    return amount / jobs


def _per_employee_cap(candidate: LoanCandidate) -> float:
    """The program's per-employee payroll ceiling for this loan's industry."""
    naics = (candidate.naics_code or "").strip()
    return FOOD_ACCOMMODATION_CAP if naics.startswith("72") else FIRST_DRAW_CAP


def heuristic_judge(
    candidates: list[LoanCandidate],
) -> list[PlausibilityVerdict]:
    """Deterministic structured-field plausibility baseline (no LLM, no network).

    Accumulates implausibility from a few orthogonal tells a reviewer would note
    on the application's face, capped at the 0-3 rubric:

    * dollars-per-job at/above the per-employee payroll cap (the program rule):
      ``>= 2x`` cap -> +2, ``>= 1x`` -> +1;
    * a single reported job on a large (``>= $500k``) loan -> +1;
    * an exact round-number amount (multiple of $100k) -> +1.

    This is intentionally a near-restatement of the $/job signal — it is the
    baseline, not the novel Tier-1 contribution. The LLM judge adds the semantic
    world-knowledge the structured fields cannot express.
    """
    verdicts: list[PlausibilityVerdict] = []
    for c in candidates:
        score = 0
        reasons: list[str] = []
        per_job = _amount_per_job(c)
        if per_job is not None:
            cap = _per_employee_cap(c)
            ratio = per_job / cap
            if ratio >= 2.0:
                score += 2
                reasons.append(
                    f"${per_job:,.0f}/job is {ratio:.1f}x the ${cap:,.0f} "
                    "per-employee payroll ceiling"
                )
            elif ratio >= 1.0:
                score += 1
                reasons.append(
                    f"${per_job:,.0f}/job exceeds the ${cap:,.0f} per-employee cap"
                )
        if c.jobs == 1 and (c.amount or 0) >= 500_000:
            score += 1
            reasons.append(
                f"${c.amount:,.0f} on a single reported job"
            )
        if c.amount and c.amount >= 100_000 and float(c.amount).is_integer() and (
            int(c.amount) % 100_000 == 0
        ):
            score += 1
            reasons.append(f"exact round-number amount (${c.amount:,.0f})")
        if not reasons:
            reasons.append("no structured-field implausibility tell")
        verdicts.append(PlausibilityVerdict.of(score, reasons))
    return verdicts


# --- LLM judge (Tier 1 proper) ------------------------------------------------

#: JSON schema the model must emit per loan — a strict, structured verdict so the
#: chain-of-thought never leaks into the score and parsing cannot drift.
PLAUSIBILITY_SCHEMA: dict = {
    "title": "PlausibilityVerdict",
    "description": "Whether a business could plausibly justify its PPP loan.",
    "type": "object",
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "Brief step-by-step reasoning BEFORE deciding the score.",
        },
        "implausibility": {
            "type": "integer",
            "enum": [0, 1, 2, 3],
            "description": (
                "0 plausible — name/industry/scale cohere; "
                "1 minor_concern — mild tension, defensible; "
                "2 implausible — the amount/jobs/industry do not add up; "
                "3 egregious — a clear mismatch (e.g. industry vs name, "
                "impossible scale for the trade)."
            ),
        },
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "reasons": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short grounded justifications for the score.",
        },
    },
    "required": ["reasoning", "implausibility", "verdict", "reasons"],
}

_SYSTEM_PROMPT = """\
You are a careful PPP/SBA pandemic-loan fraud-LEAD reviewer. For one loan you are \
given only public structured fields: borrower name, NAICS industry code, approved \
amount, reported jobs, and the payroll portion of proceeds. Judge a single \
question: could a real business of this name and industry plausibly justify a loan \
of this size for this many jobs?

Reason about world knowledge a statistic cannot: does the industry match the name? \
Is the per-job amount possible for that trade? Is the scale realistic for the \
stated headcount? Then assign a categorical implausibility score:

  0 plausible      — name, industry, and scale cohere.
  1 minor_concern  — mild tension but defensible.
  2 implausible    — amount, jobs, and industry do not add up.
  3 egregious      — a clear mismatch (industry contradicts the name, or an
                     impossible scale for the trade).

Rules: reason step by step in `reasoning` BEFORE choosing the score; a high score \
is a LEAD FOR REVIEW, never a fraud finding; never assert fraud; ground every item \
in `reasons` on the given fields only — invent nothing.

Examples:
- "Elite Nail Spa LLC", NAICS 561730 (Landscaping), $2,100,000, 1 job ->
  implausibility 3 (egregious): a nail spa coded as landscaping, $2.1M for one job.
- "Riverside Family Dentistry", NAICS 621210 (Dentists), $310,000, 12 jobs ->
  implausibility 0 (plausible): industry matches the name, scale fits the headcount.
- "JKL Holdings LLC", NAICS 531390 (Real estate services), $480,000, 2 jobs ->
  implausibility 2 (implausible): generic shell-style name, high amount for 2 jobs.
"""


def _candidate_user_prompt(candidate: LoanCandidate) -> str:
    """Render one loan's public fields for the model (facts only, no scores)."""
    amount = f"${candidate.amount:,.0f}" if candidate.amount is not None else "unknown"
    jobs = (
        f"{candidate.jobs:g}" if candidate.jobs is not None else "unknown"
    )
    payroll = (
        f"${candidate.payroll_proceed:,.0f}"
        if candidate.payroll_proceed is not None
        else "unknown"
    )
    return (
        "Judge this loan:\n"
        f"- borrower_name: {candidate.borrower_name or 'unknown'}\n"
        f"- naics_code: {candidate.naics_code or 'unknown'}\n"
        f"- state: {candidate.state or 'unknown'}\n"
        f"- approved_amount: {amount}\n"
        f"- reported_jobs: {jobs}\n"
        f"- payroll_proceed: {payroll}\n"
    )


def _coerce_score(value: object) -> int:
    """Pull a 0-3 implausibility out of a possibly-malformed model field.

    Haiku's structured output occasionally leaks tool-call markup into a field
    (e.g. ``'2</implausibility>...'``); rather than abort the whole batch on one
    bad cell, extract the first digit. :meth:`PlausibilityVerdict.of` clamps the
    result into the 0-3 rubric, so out-of-range digits are handled downstream.
    """
    if isinstance(value, bool):  # bool is an int subclass — guard before int
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d", str(value))
    return int(match.group()) if match else 0


class LlmJudge:
    """Haiku-4.5 semantic plausibility judge with structured output (``--llm``).

    Lazily imports ``langchain_anthropic`` and requires ``ANTHROPIC_API_KEY``; a
    missing extra or key raises a clear, actionable error rather than silently
    hitting the network. Calls the model once per candidate (a shared cached
    system prompt carries the rubric + few-shot). The orchestration enforces the
    hard cap, so the number of model calls is bounded and logged before any run.

    Loans are judged concurrently over a bounded ``max_concurrency`` thread pool
    (each call is independent I/O), which turns a sequential 300/1,000-call run
    from minutes-to-tens-of-minutes into a fraction of that; the cap stays modest
    so we don't trip the API rate limit (whose own backoff would erase the gain).
    Output order matches input order.

    Robust to a flaky cell mid-batch: each loan is retried ``max_retries`` times on
    a transient API error or unparseable response, then falls back to a neutral
    verdict so one bad loan never aborts a run. ``n_errors`` counts the fallbacks
    for telemetry — a non-zero count means some loans were not truly judged. (An
    auth failure surfaces on the first call, before any batch spend.)
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
        self._lock = threading.Lock()  # guards n_errors across worker threads
        self._client = None  # built on first use

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as exc:  # pragma: no cover - only without the extra
            raise RuntimeError(
                "The --llm triage path needs the `agent` extra. Install it with "
                "`uv sync --extra agent`, or run triage without --llm (the "
                "deterministic heuristic judge)."
            ) from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "The --llm triage path needs ANTHROPIC_API_KEY in the environment. "
                "Export it, or run triage without --llm."
            )
        llm = ChatAnthropic(
            model=self.model, temperature=self.temperature, max_tokens=600
        )
        self._client = llm.with_structured_output(PLAUSIBILITY_SCHEMA)
        return self._client

    def _judge_one(self, candidate: LoanCandidate) -> PlausibilityVerdict:
        client = self._ensure_client()
        messages = [
            ("system", _SYSTEM_PROMPT),
            ("human", _candidate_user_prompt(candidate)),
        ]
        last_exc: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                # with_structured_output(json_schema) returns a plain dict.
                raw = client.invoke(messages)
                score = _coerce_score(raw.get("implausibility", 0))
                reasons = [str(x) for x in (raw.get("reasons") or [])]
                return PlausibilityVerdict.of(score, reasons)
            except Exception as exc:  # transient API error or unparseable response
                last_exc = exc
        # Graceful fallback: a single bad loan must not abort the batch.
        with self._lock:
            self.n_errors += 1
        name = type(last_exc).__name__ if last_exc else "unknown"
        return PlausibilityVerdict.of(0, [f"judge_error: {name}"])

    def __call__(
        self, candidates: list[LoanCandidate]
    ) -> list[PlausibilityVerdict]:
        if not candidates:
            return []
        # Build the client once, single-threaded, so workers don't race the lazy
        # init (and an auth/extra error still surfaces before any fan-out).
        self._ensure_client()
        if self.max_concurrency == 1 or len(candidates) == 1:
            return [self._judge_one(c) for c in candidates]
        # ThreadPoolExecutor.map preserves input order regardless of completion.
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            return list(pool.map(self._judge_one, candidates))
