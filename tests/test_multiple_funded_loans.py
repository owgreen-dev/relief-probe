"""Tests for the multiple_funded_loans detector on seeded tmp_path warehouses.

A borrower with two same-draw loans fires; a legit one-PPP-plus-one-PPS borrower
stays quiet; three+ loans for one resolved entity fire; distinct borrowers each
holding a single loan stay quiet. Entity resolution merges formatting variants of
one name+building, distinguishing this from duplicate_address_ring (many distinct
borrowers at one address).
"""

from __future__ import annotations

from relief_probe.detectors.multiple_funded_loans import MultipleFundedLoansDetector
from relief_probe.warehouse import connect


def _insert(con, rows):
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, borrower_address, "
        "borrower_city, borrower_state, borrower_zip, processing_method, "
        "current_approval_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def test_two_same_draw_loans_fire(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _insert(
        con,
        [
            # Same resolved entity, two FIRST-draw loans → not allowed.
            ("SD-1", "Acme LLC", "1 Main St", "Austin", "TX", "78701", "PPP", 100_000.0),
            ("SD-2", "ACME", "1 Main Street", "Austin", "TX", "78701", "PPP", 120_000.0),
        ],
    )
    sigs = MultipleFundedLoansDetector().run(con)
    by_loan = {s.loan_number: s for s in sigs}

    assert {"SD-1", "SD-2"} <= set(by_loan)
    ev = by_loan["SD-1"].evidence
    assert ev["n_loans"] == 2
    assert ev["excess_loans"] == 1
    assert ev["per_draw_counts"] == {"PPP": 2}
    assert sorted(ev["loan_numbers"]) == ["SD-1", "SD-2"]
    assert by_loan["SD-1"].score == by_loan["SD-2"].score == 1.0


def test_one_first_one_second_draw_is_legit(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _insert(
        con,
        [
            ("LG-1", "Beta Co", "2 Oak Ave", "Reno", "NV", "89501", "PPP", 50_000.0),
            ("LG-2", "Beta Co", "2 Oak Ave", "Reno", "NV", "89501", "PPS", 55_000.0),
        ],
    )
    sigs = MultipleFundedLoansDetector().run(con)
    assert sigs == []


def test_three_plus_loans_fire_and_score_monotonic(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _insert(
        con,
        [
            ("M-1", "Gamma Inc", "3 Elm Rd", "Mesa", "AZ", "85201", "PPP", 10_000.0),
            ("M-2", "Gamma Inc", "3 Elm Rd", "Mesa", "AZ", "85201", "PPP", 20_000.0),
            ("M-3", "Gamma Inc", "3 Elm Rd", "Mesa", "AZ", "85201", "PPS", 30_000.0),
        ],
    )
    sigs = MultipleFundedLoansDetector().run(con)
    by_loan = {s.loan_number: s for s in sigs}

    assert {"M-1", "M-2", "M-3"} <= set(by_loan)
    ev = by_loan["M-1"].evidence
    assert ev["n_loans"] == 3
    # Two PPP (one excess) and three total (one over the allowance of two): excess 1.
    assert ev["excess_loans"] == 1
    assert ev["per_draw_counts"] == {"PPP": 2, "PPS": 1}
    assert ev["total_amount"] == 60_000.0
    # A fourth loan of the same draw raises the excess (monotonic in extra loans).
    _insert(
        con,
        [("M-4", "Gamma Inc", "3 Elm Rd", "Mesa", "AZ", "85201", "PPP", 40_000.0)],
    )
    sigs2 = {s.loan_number: s for s in MultipleFundedLoansDetector().run(con)}
    assert sigs2["M-1"].score > by_loan["M-1"].score


def test_distinct_single_loan_borrowers_stay_quiet(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _insert(
        con,
        [
            ("D-1", "One LLC", "10 A St", "Provo", "UT", "84601", "PPP", 70_000.0),
            ("D-2", "Two LLC", "20 B St", "Provo", "UT", "84601", "PPP", 80_000.0),
            ("D-3", "Three LLC", "30 C St", "Provo", "UT", "84601", "PPS", 90_000.0),
        ],
    )
    sigs = MultipleFundedLoansDetector().run(con)
    assert sigs == []


def test_unkeyable_entity_is_skipped(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _insert(
        con,
        [
            # Blank address → entity_key is None → excluded even though two loans.
            ("U-1", "Ghost LLC", "", "X", "TX", "73301", "PPP", 10_000.0),
            ("U-2", "Ghost LLC", "", "X", "TX", "73301", "PPP", 20_000.0),
        ],
    )
    sigs = MultipleFundedLoansDetector().run(con)
    assert sigs == []


def test_distinct_from_address_ring(tmp_path):
    """Many distinct borrowers at one address is a RING, not multiple-funded.

    This detector requires the SAME resolved entity, so co-located but distinct
    borrowers (the ring signature) must not fire here.
    """
    con = connect(tmp_path / "wh.duckdb")
    _insert(
        con,
        [
            ("R-1", "Alpha LLC", "500 Shared Blvd", "Tulsa", "OK", "74101", "PPP", 1.0),
            ("R-2", "Bravo LLC", "500 Shared Blvd", "Tulsa", "OK", "74101", "PPP", 1.0),
            ("R-3", "Delta LLC", "500 Shared Blvd", "Tulsa", "OK", "74101", "PPP", 1.0),
        ],
    )
    sigs = MultipleFundedLoansDetector().run(con)
    assert sigs == []
