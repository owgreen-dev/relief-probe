"""Tests for the investigator graph (Layer 6).

The deterministic path is the gate: it must produce a populated, grounded report
plus telemetry over a seeded warehouse with the `agent` extra absent. The LLM
path test is guarded by ``pytest.importorskip`` so the core env skips it cleanly.
"""

from __future__ import annotations

import json

import pytest

from relief_probe.agent.graph import investigate
from relief_probe.agent.report import DISCLAIMER, InvestigatorReport
from relief_probe.warehouse import connect

OUTLIER = "OUTLIER-1"
CLEAN = "CLEAN-1"


def _seed(con):
    rows = []
    # 40 normal TX restaurants at ~$10k/job -> a real cohort for peer compare.
    for i in range(40):
        amount = (9000 + i * 75) * 10
        rows.append((f"N{i:03d}", f"Normal Diner {i}", "722511", "TX", amount, 10.0))
    rows.append((OUTLIER, "Suspicious Eats LLC", "722511", "TX", 1_000_000.0, 5.0))
    rows.append((CLEAN, "Honest Bakery", "311811", "WY", 50_000.0, 8.0))
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code, "
        "borrower_state, current_approval_amount, jobs_reported) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.executemany(
        "INSERT INTO signals (loan_number, detector_id, score, evidence_json) "
        "VALUES (?, ?, ?, ?)",
        [
            (
                OUTLIER,
                "naics_cohort_outlier",
                7.5,
                json.dumps({"x_cohort_median": 18.2, "cohort": "722511 | TX"}),
            ),
            (OUTLIER, "payroll_cap_exceedance", 3.2, json.dumps({"x_cap": 6.8})),
        ],
    )
    con.execute(
        "INSERT INTO fraud_cases (case_id, loan_number, defendant_name, "
        "business_name, source, source_url, match_method, match_confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "case-1",
            OUTLIER,
            "John Doe",
            "Suspicious Eats LLC",
            "doj",
            "https://justice.gov/x",
            "name_state",
            0.92,
        ],
    )


def test_deterministic_investigate_populates_report_and_telemetry(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    result = investigate(con, OUTLIER)

    report = result["report"]
    assert isinstance(report, InvestigatorReport)
    assert report.loan_number == OUTLIER
    # Flagged + labeled -> top band, with cited evidence and the disclaimer.
    assert report.risk_level == "critical"
    assert report.evidence
    assert all(item.source for item in report.evidence)
    assert report.disclaimer == DISCLAIMER

    telemetry = result["telemetry"]
    assert telemetry["path"] == "deterministic"
    assert telemetry["use_llm"] is False
    # gather_evidence consults five tools beyond the loan_number key.
    assert telemetry["tool_calls"] == 5


def test_deterministic_investigate_on_clean_loan_is_low_risk(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    result = investigate(con, CLEAN)
    assert result["report"].risk_level == "low"
    assert result["report"].evidence == []
    assert result["telemetry"]["path"] == "deterministic"


def test_llm_path_imports_lazily():
    """LLM deps load lazily — module import must succeed without the extra."""
    pytest.importorskip("langchain_anthropic")
    # If the extra IS present, the call must still raise without an API key,
    # never silently hit the network. We only assert it does not import-fail.
    import relief_probe.agent.graph as graph

    assert hasattr(graph, "investigate")
    assert graph.LLM_MODEL == "claude-opus-4-8"
