"""Deterministic investigator report assembled from gathered evidence.

:func:`build_report` consumes the dict produced by
:func:`relief_probe.agent.tools.gather_evidence` and turns it into a structured,
fully grounded :class:`InvestigatorReport`. It is pure Python and deterministic:
no LLM, no warehouse access, and — critically — it invents nothing. Every
:class:`EvidenceItem` cites the tool/query that produced it, and the narrative is
derived only from facts present in the passed evidence.

A high composite score or a peer-cohort outlier is a *statistical lead for
review*, never proof of fraud; the :data:`DISCLAIMER` rides on every report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Carried on every report. See RESPONSIBLE_USE.md.
DISCLAIMER = (
    "This report is a statistical lead for review, not evidence of fraud. A high "
    "score means a loan looks anomalous relative to peers or matches a rule "
    "pattern in public data — nothing more. Anomalies have benign explanations; "
    "every flagged loan needs human review against primary records. A loan not in "
    "fraud_cases is unlabeled, not innocent. See RESPONSIBLE_USE.md."
)

#: Composite-score / signal-count thresholds for the (unlabeled) risk ladder.
#: Detector scores are not calibrated, so these are deliberately coarse triage
#: bands, not probabilities.
_HIGH_COMPOSITE = 6.0
_HIGH_N_SIGNALS = 3

VALID_RISK_LEVELS = ("low", "elevated", "high", "critical")


@dataclass(frozen=True)
class EvidenceItem:
    """A single grounded finding.

    ``source`` names the tool or query that produced the fact (e.g.
    ``"loan_signals"``), so the claim can always be traced back to evidence.
    """

    claim: str
    source: str
    detail: str = ""


@dataclass(frozen=True)
class InvestigatorReport:
    """A structured, grounded review lead for one loan."""

    loan_number: str
    risk_level: str
    summary: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    alternative_explanations: list[str] = field(default_factory=list)
    recommended_next_steps: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER


def _risk_level(composite: dict[str, Any], labeled: bool) -> str:
    """Derive a coarse risk band from composite score / n_signals / labeled."""
    if labeled:
        return "critical"
    if not composite.get("flagged"):
        return "low"
    score = composite.get("composite_score") or 0.0
    n_signals = composite.get("n_signals") or 0
    if score >= _HIGH_COMPOSITE or n_signals >= _HIGH_N_SIGNALS:
        return "high"
    return "elevated"


def _summary(
    loan_number: str,
    risk_level: str,
    profile: dict[str, Any],
    composite: dict[str, Any],
    labeled: bool,
) -> str:
    """Write a factual one-paragraph summary grounded in the evidence."""
    who = profile.get("borrower_name") or "This borrower"
    where = profile.get("borrower_state")
    naics = profile.get("naics_code")
    locus = f" ({naics} in {where})" if naics and where else ""
    parts = [f"Loan {loan_number} — {who}{locus} — is a {risk_level}-risk lead."]
    if composite.get("flagged"):
        n = composite.get("n_signals", 0)
        score = composite.get("composite_score")
        plural = "detector" if n == 1 else "detectors"
        parts.append(
            f"{n} {plural} fired (composite score {score})."
        )
    else:
        parts.append("No detectors fired on this loan.")
    if labeled:
        parts.append(
            "It is linked to a resolved public enforcement (fraud_cases) record."
        )
    return " ".join(parts)


def build_report(evidence: dict[str, Any]) -> InvestigatorReport:
    """Build a deterministic :class:`InvestigatorReport` from gathered evidence.

    ``evidence`` is the dict returned by
    :func:`relief_probe.agent.tools.gather_evidence`. The report uses *only*
    these facts — nothing is fabricated — and every evidence row cites its
    source tool.
    """
    loan_number = evidence.get("loan_number", "")
    profile = evidence.get("profile") or {}
    signals = evidence.get("signals") or []
    peer = evidence.get("peer_comparison") or {}
    fraud_case = evidence.get("fraud_case") or {}
    composite = evidence.get("composite") or {}

    labeled = bool(fraud_case.get("labeled"))
    risk_level = _risk_level(composite, labeled)

    items: list[EvidenceItem] = []

    if composite.get("flagged"):
        items.append(
            EvidenceItem(
                claim=(
                    f"Composite risk score {composite.get('composite_score')} "
                    f"across {composite.get('n_signals')} detector(s)."
                ),
                source="composite_for",
                detail=", ".join(composite.get("detectors", [])),
            )
        )

    # One row per detector that fired, strongest first (tools sorts by score).
    for sig in signals:
        items.append(
            EvidenceItem(
                claim=(
                    f"Detector {sig.get('detector_id')} fired "
                    f"(score {sig.get('score')})."
                ),
                source="loan_signals",
                detail=_format_evidence(sig.get("evidence")),
            )
        )

    if peer.get("available"):
        items.append(
            EvidenceItem(
                claim=(
                    f"Dollars-per-job ${peer.get('amount_per_job'):,.0f} is "
                    f"{peer.get('x_cohort_median')}x the cohort median for "
                    f"{peer.get('cohort')}."
                ),
                source="peer_comparison",
                detail=(
                    f"cohort median "
                    f"${peer.get('cohort_median_amount_per_job'):,.0f}/job over "
                    f"{peer.get('cohort_size')} peers"
                ),
            )
        )

    for case in fraud_case.get("cases", []):
        items.append(
            EvidenceItem(
                claim=(
                    "Linked to a resolved public enforcement case "
                    f"({case.get('source')})."
                ),
                source="fraud_case_check",
                detail=(
                    f"{case.get('defendant_name') or case.get('business_name')} "
                    f"via {case.get('match_method')} "
                    f"(confidence {case.get('match_confidence')}) — "
                    f"{case.get('source_url')}"
                ),
            )
        )

    summary = _summary(loan_number, risk_level, profile, composite, labeled)
    return InvestigatorReport(
        loan_number=loan_number,
        risk_level=risk_level,
        summary=summary,
        evidence=items,
        alternative_explanations=_alternative_explanations(peer, composite),
        recommended_next_steps=_recommended_next_steps(risk_level, labeled, peer),
    )


def _format_evidence(evidence: Any) -> str:
    """Render a detector's parsed evidence dict as a compact ``k=v`` string."""
    if not isinstance(evidence, dict) or not evidence:
        return ""
    return ", ".join(f"{k}={v}" for k, v in evidence.items())


def _alternative_explanations(
    peer: dict[str, Any], composite: dict[str, Any]
) -> list[str]:
    """Benign readings of the same evidence — always offered, never optional."""
    explanations = [
        "A legitimately high-wage or capital-intensive business can post a high "
        "dollars-per-job ratio without any wrongdoing.",
        "Data-entry artifacts (mis-keyed jobs or amounts, NAICS miscoding) can "
        "manufacture an apparent outlier.",
    ]
    if peer.get("available"):
        explanations.append(
            "An unusual-but-valid business model within the cohort, or a sparse "
            "cohort, can make a normal loan look extreme."
        )
    if composite.get("flagged"):
        explanations.append(
            "EIDL-refinance or other valid program mechanics can trip pattern "
            "rules without indicating fraud."
        )
    return explanations


def _recommended_next_steps(
    risk_level: str, labeled: bool, peer: dict[str, Any]
) -> list[str]:
    """Concrete review actions scaled to the lead's strength."""
    steps = [
        "Review the loan against primary SBA records and the borrower's filings.",
    ]
    if peer.get("available"):
        steps.append(
            "Confirm the reported job count and approval amount against payroll "
            "documentation."
        )
    if labeled:
        steps.append(
            "Cross-reference the linked enforcement record to confirm it is the "
            "same entity (matches can be approximate)."
        )
    if risk_level in ("high", "critical"):
        steps.append(
            "Prioritize for human analyst review; do not treat the score as a "
            "determination."
        )
    return steps
