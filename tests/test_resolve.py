"""Tests for entity resolution (press_releases -> loans -> fraud_cases)."""

from __future__ import annotations

from relief_probe.labels.resolve import (
    extract_candidates,
    normalize_name,
    resolve_all,
    score_match,
)
from relief_probe.warehouse import connect


def test_normalize_name_collapses_corporate_forms():
    assert normalize_name("PREMIER CARE STAFFING, INC.") == "PREMIER CARE STAFFING"
    assert normalize_name("Premier Care Staffing LLC") == "PREMIER CARE STAFFING"
    assert normalize_name("  the  ABC  Co.  ") == "ABC"


def test_extract_candidates_finds_multiword_orgs():
    cands = extract_candidates(
        "Premier Care Staffing of Houston obtained a loan. The Small Business "
        "Administration was defrauded."
    )
    assert "PREMIER CARE STAFFING HOUSTON" in cands or "PREMIER CARE STAFFING" in cands


def test_score_match_rewards_corroboration():
    text = "Acme Trucking of New York got a $1,234,567 loan."
    high, method = score_match(
        name_tokens=2, loan_state="NY", loan_amount=1_234_567.0,
        alleged_amount=1_234_567.0, text=text,
    )
    low, _ = score_match(
        name_tokens=2, loan_state="CA", loan_amount=999.0,
        alleged_amount=None, text=text,
    )
    assert high > low
    assert "state" in method and "amount" in method


def _seed(con):
    loans = [
        ("L1", "PREMIER CARE STAFFING, INC.", "NY", 6_512_000.0),
        ("L2", "ABC TRUCKING LLC", "TX", 50_000.0),
        ("L3", "UNRELATED BAKERY LLC", "OH", 80_000.0),
    ]
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, borrower_state, "
        "current_approval_amount) VALUES (?, ?, ?, ?)",
        loans,
    )
    releases = [
        # Matches L1 by name + state (New York) + verbatim amount.
        (
            "id1", "doj", "https://justice.gov/pr/1",
            "Owner of Premier Care Staffing Charged",
            "2023-05-01", "ppp", 6_512_000.0,
            "The owner of Premier Care Staffing of New York fraudulently obtained a "
            "$6,512,000 Paycheck Protection Program loan.",
        ),
        # A real loan-fraud release that names no loan in our file -> no match.
        (
            "id2", "doj", "https://justice.gov/pr/2",
            "Man Sentenced for PPP Fraud",
            "2023-06-01", "ppp", 100000.0,
            "A man was sentenced for a Paycheck Protection Program loan scheme "
            "involving Nonexistent Ghost Company of Florida.",
        ),
    ]
    con.executemany(
        "INSERT INTO press_releases (id, source, url, title, published_date, "
        "program, alleged_amount, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        releases,
    )


def test_resolve_all_labels_the_right_loan(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    summary = resolve_all(con)
    assert summary["releases_scanned"] == 2
    assert summary["releases_matched"] == 1
    assert summary["loans_labeled"] == 1

    rows = con.execute(
        "SELECT loan_number, match_method, match_confidence FROM fraud_cases"
    ).fetchall()
    assert len(rows) == 1
    loan_number, method, conf = rows[0]
    assert loan_number == "L1"
    assert "state" in method and "amount" in method
    assert conf >= 0.9  # name + state + amount


def test_resolve_all_is_idempotent(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    resolve_all(con)
    resolve_all(con)  # rerun replaces, doesn't duplicate
    assert con.execute("SELECT COUNT(*) FROM fraud_cases").fetchone()[0] == 1
