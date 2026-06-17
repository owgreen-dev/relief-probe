"""Tests for the read-only evidence-gathering tools (Layer 6).

We seed a tiny warehouse — one flagged+labeled outlier inside a real cohort,
plus a clean loan — and assert each tool's shape and values, including the
not-found / not-flagged / cohort-too-small branches.
"""

from __future__ import annotations

import json

from relief_probe.agent import tools
from relief_probe.warehouse import connect

OUTLIER = "OUTLIER-1"
CLEAN = "CLEAN-1"


def _seed(con):
    rows = []
    # 40 normal restaurants in TX at ~$10k per job (jobs=10) -> a real cohort.
    for i in range(40):
        amount = (9000 + i * 75) * 10
        rows.append((f"N{i:03d}", f"Normal Diner {i}", "722511", "TX", amount, 10.0))
    # One loan claiming $200k per job — flagged and labeled.
    rows.append((OUTLIER, "Suspicious Eats LLC", "722511", "TX", 1_000_000.0, 5.0))
    # A clean loan in a thin cohort (its NAICS x state has too few peers).
    rows.append((CLEAN, "Honest Bakery", "311811", "WY", 50_000.0, 8.0))
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code, "
        "borrower_state, current_approval_amount, jobs_reported) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.execute(
        "UPDATE loans SET loan_status = 'Paid in Full', forgiveness_amount = 1000000, "
        "date_approved = DATE '2020-05-01' WHERE loan_number = ?",
        [OUTLIER],
    )
    # Two corroborating signals on the outlier.
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
            (
                OUTLIER,
                "payroll_cap_exceedance",
                3.2,
                json.dumps({"x_cap": 6.8}),
            ),
        ],
    )
    # A resolved fraud case linked to the outlier.
    con.execute(
        "INSERT INTO fraud_cases (case_id, loan_number, defendant_name, "
        "business_name, alleged_amount, source, source_url, match_method, "
        "match_confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "case-1",
            OUTLIER,
            "John Doe",
            "Suspicious Eats LLC",
            900_000.0,
            "doj",
            "https://justice.gov/x",
            "name_state",
            0.92,
        ],
    )


def test_loan_profile_returns_key_fields(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    profile = tools.loan_profile(con, OUTLIER)
    assert profile["borrower_name"] == "Suspicious Eats LLC"
    assert profile["naics_code"] == "722511"
    assert profile["borrower_state"] == "TX"
    assert profile["current_approval_amount"] == 1_000_000.0
    assert profile["jobs_reported"] == 5.0
    assert profile["loan_status"] == "Paid in Full"
    assert profile["forgiveness_amount"] == 1_000_000.0
    assert profile["date_approved"] == "2020-05-01"


def test_loan_profile_not_found_returns_empty(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    assert tools.loan_profile(con, "NOPE") == {}


def test_loan_signals_parsed_and_sorted(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    sigs = tools.loan_signals(con, OUTLIER)
    assert [s["detector_id"] for s in sigs] == [
        "naics_cohort_outlier",
        "payroll_cap_exceedance",
    ]
    assert sigs[0]["score"] == 7.5
    assert sigs[0]["evidence"]["x_cohort_median"] == 18.2
    assert tools.loan_signals(con, CLEAN) == []


def test_peer_comparison_against_real_cohort(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    pc = tools.peer_comparison(con, OUTLIER)
    assert pc["available"] is True
    assert pc["cohort"] == "722511 | TX"
    assert pc["cohort_size"] == 41
    assert pc["amount_per_job"] == 200_000.0
    assert pc["x_cohort_median"] > 5


def test_peer_comparison_cohort_too_small(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    pc = tools.peer_comparison(con, CLEAN)
    assert pc["available"] is False
    assert pc["reason"] == "cohort_too_small"


def test_peer_comparison_missing_jobs(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    con.execute(
        "INSERT INTO loans (loan_number, naics_code, borrower_state, "
        "current_approval_amount, jobs_reported) VALUES "
        "('NOJOBS', '722511', 'TX', 50000, NULL)"
    )
    pc = tools.peer_comparison(con, "NOJOBS")
    assert pc["available"] is False
    assert pc["reason"] == "missing_jobs_or_amount"


def test_peer_comparison_loan_not_found(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    pc = tools.peer_comparison(con, "NOPE")
    assert pc == {"available": False, "reason": "loan_not_found"}


def test_fraud_case_check_labeled_and_unlabeled(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    fc = tools.fraud_case_check(con, OUTLIER)
    assert fc["labeled"] is True
    assert len(fc["cases"]) == 1
    case = fc["cases"][0]
    assert case["match_method"] == "name_state"
    assert case["match_confidence"] == 0.92
    assert case["source_url"] == "https://justice.gov/x"

    clean = tools.fraud_case_check(con, CLEAN)
    assert clean == {"labeled": False, "cases": []}


def test_composite_for_flagged_and_unflagged(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    comp = tools.composite_for(con, OUTLIER)
    assert comp["flagged"] is True
    assert comp["n_signals"] == 2
    # max(score) + 0.5 * (n - 1) = 7.5 + 0.5 = 8.0
    assert comp["composite_score"] == 8.0
    assert set(comp["detectors"]) == {
        "naics_cohort_outlier",
        "payroll_cap_exceedance",
    }
    assert tools.composite_for(con, CLEAN) == {"flagged": False}


def test_gather_evidence_bundles_all_tools(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    ev = tools.gather_evidence(con, OUTLIER)
    assert ev["loan_number"] == OUTLIER
    assert set(ev) == {
        "loan_number",
        "profile",
        "signals",
        "peer_comparison",
        "fraud_case",
        "composite",
    }
    assert ev["profile"]["borrower_name"] == "Suspicious Eats LLC"
    assert len(ev["signals"]) == 2
    assert ev["peer_comparison"]["available"] is True
    assert ev["fraud_case"]["labeled"] is True
    assert ev["composite"]["flagged"] is True
