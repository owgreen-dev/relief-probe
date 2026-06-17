"""Tests for the deterministic investigator report builder (Layer 6).

We feed :func:`build_report` evidence dicts shaped exactly like
:func:`relief_probe.agent.tools.gather_evidence` output and assert the derived
risk level, grounded evidence rows, and the ever-present disclaimer.
"""

from __future__ import annotations

from relief_probe.agent.report import (
    DISCLAIMER,
    VALID_RISK_LEVELS,
    EvidenceItem,
    InvestigatorReport,
    build_report,
)


def _flagged_labeled_evidence():
    return {
        "loan_number": "OUTLIER-1",
        "profile": {
            "borrower_name": "Suspicious Eats LLC",
            "naics_code": "722511",
            "borrower_state": "TX",
            "current_approval_amount": 1_000_000.0,
            "jobs_reported": 5.0,
        },
        "signals": [
            {
                "detector_id": "naics_cohort_outlier",
                "score": 7.5,
                "evidence": {"x_cohort_median": 18.2, "cohort": "722511 | TX"},
            },
            {
                "detector_id": "payroll_cap_exceedance",
                "score": 3.2,
                "evidence": {"x_cap": 6.8},
            },
        ],
        "peer_comparison": {
            "available": True,
            "cohort": "722511 | TX",
            "cohort_size": 41,
            "amount_per_job": 200_000.0,
            "cohort_median_amount_per_job": 9750.0,
            "x_cohort_median": 20.51,
        },
        "fraud_case": {
            "labeled": True,
            "cases": [
                {
                    "case_id": "case-1",
                    "defendant_name": "John Doe",
                    "business_name": "Suspicious Eats LLC",
                    "source": "doj",
                    "source_url": "https://justice.gov/x",
                    "match_method": "name_state",
                    "match_confidence": 0.92,
                }
            ],
        },
        "composite": {
            "flagged": True,
            "composite_score": 8.0,
            "n_signals": 2,
            "detectors": ["naics_cohort_outlier", "payroll_cap_exceedance"],
        },
    }


def _clean_evidence():
    return {
        "loan_number": "CLEAN-1",
        "profile": {
            "borrower_name": "Honest Bakery",
            "naics_code": "311811",
            "borrower_state": "WY",
            "current_approval_amount": 50_000.0,
            "jobs_reported": 8.0,
        },
        "signals": [],
        "peer_comparison": {"available": False, "reason": "cohort_too_small"},
        "fraud_case": {"labeled": False, "cases": []},
        "composite": {"flagged": False},
    }


def test_flagged_labeled_loan_is_critical_with_cited_evidence():
    report = build_report(_flagged_labeled_evidence())
    assert isinstance(report, InvestigatorReport)
    assert report.loan_number == "OUTLIER-1"
    # A labeled (enforcement-linked) loan is the top band regardless of score.
    assert report.risk_level == "critical"
    assert report.risk_level in VALID_RISK_LEVELS

    sources = {item.source for item in report.evidence}
    # Composite + both detectors + peer comparison + fraud-case match all cited.
    assert sources == {
        "composite_for",
        "loan_signals",
        "peer_comparison",
        "fraud_case_check",
    }
    # One evidence row per fired detector.
    detector_rows = [e for e in report.evidence if e.source == "loan_signals"]
    assert len(detector_rows) == 2
    # Every row is grounded.
    assert all(isinstance(e, EvidenceItem) and e.claim for e in report.evidence)
    assert "Suspicious Eats" in report.summary
    assert report.alternative_explanations
    assert report.recommended_next_steps


def test_flagged_unlabeled_loan_is_high_or_elevated():
    ev = _flagged_labeled_evidence()
    ev["fraud_case"] = {"labeled": False, "cases": []}
    report = build_report(ev)
    # Composite 8.0 >= high threshold, but not labeled -> "high", not critical.
    assert report.risk_level == "high"
    assert not any(e.source == "fraud_case_check" for e in report.evidence)


def test_unflagged_loan_is_low_risk():
    report = build_report(_clean_evidence())
    assert report.risk_level == "low"
    assert report.evidence == []
    assert "No detectors fired" in report.summary
    # Alternatives and next steps are always offered.
    assert report.alternative_explanations
    assert report.recommended_next_steps


def test_disclaimer_always_present():
    for ev in (_flagged_labeled_evidence(), _clean_evidence()):
        report = build_report(ev)
        assert report.disclaimer == DISCLAIMER
        assert "not evidence of fraud" in report.disclaimer


def test_report_is_frozen_and_grounded():
    report = build_report(_flagged_labeled_evidence())
    # Frozen dataclass — immutable.
    import dataclasses

    try:
        report.risk_level = "low"  # type: ignore[misc]
        raise AssertionError("expected FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass
