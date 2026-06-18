"""Tests for the lender_concentration detector on seeded tmp_path warehouses.

The warehouse is built with ``warehouse.connect(tmp_path/...)`` and seeded with
synthetic loans only (SIGN-007: never touch the real warehouse). Crucially the
``fraud_cases`` table is left EMPTY: the detector must fire purely on the
program-rule (cap-busting) RATE per lender, proving it is label-free (SIGN-012).

Scenarios:
* a lender whose book is mostly cap-busting fires on ALL its loans (even the clean
  ones), while clean peer lenders stay quiet;
* lenders below ``min_loans`` are skipped;
* the detector ignores ``originating_lender``-null / unusable loans gracefully.
"""

from __future__ import annotations

from relief_probe.detectors.lender_concentration import LenderConcentrationDetector
from relief_probe.warehouse import connect

# A cap-busting loan: $/job above the $20,833 first-draw ceiling. 1 job, $30k.
BUST = (30000.0, 1.0)
# A clean loan: well under the ceiling. 1 job, $5k.
CLEAN = (5000.0, 1.0)


def _insert(con, rows):
    con.executemany(
        """
        INSERT INTO loans
            (loan_number, originating_lender, naics_code,
             current_approval_amount, jobs_reported)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def _lender_book(lender, n_loans, n_bust, *, naics="541110"):
    """n_loans loans for `lender`, the first n_bust cap-busting and the rest clean."""
    rows = []
    for i in range(n_loans):
        amount, jobs = BUST if i < n_bust else CLEAN
        rows.append((f"{lender}-{i}", lender, naics, amount, jobs))
    return rows


def _seed_peers(con, *, min_loans=10):
    """One extreme lender (BADBANK, ~90% cap-busting) amid mildly-varied clean peers.

    The clean peers carry small, *varied* cap-busting rates (0, 0.1, 0.2) so the
    cross-lender MAD is non-degenerate and BADBANK lands far in the upper tail.
    """
    _insert(con, _lender_book("BADBANK", min_loans, int(min_loans * 0.9)))
    _insert(con, _lender_book("CLEAN1", min_loans, 0))
    _insert(con, _lender_book("CLEAN2", min_loans, 1))
    _insert(con, _lender_book("CLEAN3", min_loans, 1))
    _insert(con, _lender_book("CLEAN4", min_loans, 2))
    _insert(con, _lender_book("CLEAN5", min_loans, 0))
    _insert(con, _lender_book("CLEAN6", min_loans, 1))


def test_structurally_bad_lender_fires_on_all_its_loans(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_peers(con, min_loans=10)

    sigs = LenderConcentrationDetector(min_loans=10).run(con)

    # Every BADBANK loan fires — including its one individually-clean loan.
    flagged = {s.loan_number for s in sigs}
    assert flagged == {f"BADBANK-{i}" for i in range(10)}
    # The whole book shares one score (the lender's robust-z) and it cleared min_z.
    assert len({s.score for s in sigs}) == 1
    assert sigs[0].score >= 3.0
    ev = sigs[0].evidence
    assert ev["originating_lender"] == "BADBANK"
    assert ev["lender_loan_count"] == 10
    assert ev["lender_suspicious_count"] == 9
    assert ev["lender_suspicious_rate"] == 0.9


def test_clean_peer_lenders_stay_quiet(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_peers(con, min_loans=10)

    sigs = LenderConcentrationDetector(min_loans=10).run(con)
    lenders_flagged = {s.evidence["originating_lender"] for s in sigs}
    assert lenders_flagged == {"BADBANK"}  # no CLEANn lender fires


def test_lenders_below_min_loans_are_skipped(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    # TINY has an all-cap-busting book but only 3 loans — below min_loans -> skipped.
    _insert(con, _lender_book("TINY", 3, 3))
    # Give some qualifying peers so the population isn't empty.
    _insert(con, _lender_book("CLEAN1", 10, 0))
    _insert(con, _lender_book("CLEAN2", 10, 1))
    _insert(con, _lender_book("CLEAN3", 10, 2))

    sigs = LenderConcentrationDetector(min_loans=10).run(con)
    assert all(s.evidence["originating_lender"] != "TINY" for s in sigs)


def test_label_free_runs_with_empty_fraud_cases(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_peers(con, min_loans=10)
    # fraud_cases is created by connect() and left empty — the detector must still
    # produce signals, proving its signal does not come from labels.
    assert con.execute("SELECT count(*) FROM fraud_cases").fetchone()[0] == 0

    sigs = LenderConcentrationDetector(min_loans=10).run(con)
    assert sigs  # fired without any labels present


def test_null_lender_and_unusable_loans_skipped(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _insert(
        con,
        [
            ("N-1", None, "541110", 30000.0, 1.0),  # null lender -> skipped
            ("N-2", "", "541110", 30000.0, 1.0),  # blank lender -> skipped
            ("N-3", "BADBANK", "541110", 30000.0, 0.0),  # jobs 0 -> skipped
            ("N-4", "BADBANK", "541110", -5.0, 1.0),  # non-positive amount -> skipped
        ],
    )
    # No qualifying lenders -> no crash, no signals.
    assert LenderConcentrationDetector(min_loans=10).run(con) == []


def test_empty_warehouse_returns_no_signals(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    assert LenderConcentrationDetector().run(con) == []
