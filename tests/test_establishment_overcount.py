"""Tests for the establishment_overcount detector on seeded tmp_path warehouses.

Both a tiny ``loans`` table and a tiny ``establishments`` table are seeded in a
tmp_path warehouse (SIGN-007: never touch the real warehouse). A (ZIP x NAICS) cell
with many PPP loans but few establishments fires on every loan in the cell; a cell
with ample establishments stays quiet; an empty establishments table yields no
signals; loans with a null/blank ZIP or NAICS are skipped; evidence reports the
overcount ratio. No network, no real Census download.
"""

from __future__ import annotations

import math

from relief_probe.detectors.establishment_overcount import (
    EstablishmentOvercountDetector,
)
from relief_probe.warehouse import connect


def _insert_loans(con, rows):
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_zip, naics_code) VALUES (?, ?, ?)",
        rows,
    )


def _insert_est(con, rows):
    con.executemany(
        "INSERT INTO establishments (zip, naics, establishments) VALUES (?, ?, ?)",
        rows,
    )


def test_overcount_cell_fires_on_all_its_loans(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    # Cell (29150, 325510): 6 loans but only 1 establishment -> ratio 6 >= 4 -> fires.
    _insert_loans(
        con,
        [(f"OC-{i}", "29150", "325510") for i in range(6)],
    )
    _insert_est(con, [("29150", "325510", 1)])

    sigs = EstablishmentOvercountDetector().run(con)
    assert {s.loan_number for s in sigs} == {f"OC-{i}" for i in range(6)}
    ev = sigs[0].evidence
    assert ev["zip"] == "29150"
    assert ev["naics_cell"] == "325510"
    assert ev["ppp_loan_count"] == 6
    assert ev["establishment_count"] == 1
    assert ev["ratio"] == 6.0
    # Score is log(ratio), monotonic in the overcount, identical across the cell.
    assert sigs[0].score == round(math.log(6.0), 4)
    assert len({s.score for s in sigs}) == 1


def test_cell_with_ample_establishments_stays_quiet(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    # Cell (75001, 541110): 2 loans, 50 establishments -> ratio 0.04 < 4 -> quiet.
    _insert_loans(
        con,
        [("Q-1", "75001", "541110"), ("Q-2", "75001", "541110")],
    )
    _insert_est(con, [("75001", "541110", 50)])

    assert EstablishmentOvercountDetector().run(con) == []


def test_empty_establishments_table_yields_no_signals(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _insert_loans(con, [(f"E-{i}", "10001", "722511") for i in range(10)])
    # establishments left empty -> nothing to compare against -> no signals.
    assert EstablishmentOvercountDetector().run(con) == []


def test_cell_without_establishment_row_is_skipped(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    # Loans in a cell that has NO ZBP row are skipped (absent != zero establishments),
    # while a different cell with a row still fires.
    _insert_loans(
        con,
        [(f"NOROW-{i}", "60601", "111111") for i in range(8)]
        + [(f"HIT-{i}", "29150", "325510") for i in range(5)],
    )
    _insert_est(con, [("29150", "325510", 1)])

    sigs = EstablishmentOvercountDetector().run(con)
    assert {s.loan_number for s in sigs} == {f"HIT-{i}" for i in range(5)}


def test_null_or_blank_zip_or_naics_skipped(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _insert_loans(
        con,
        [
            ("N-1", None, "325510"),  # null zip -> skipped
            ("N-2", "", "325510"),  # blank zip -> skipped
            ("N-3", "29150", None),  # null naics -> skipped
            ("N-4", "29150", ""),  # blank naics -> skipped
        ],
    )
    _insert_est(con, [("29150", "325510", 1)])
    assert EstablishmentOvercountDetector().run(con) == []


def test_naics_truncation_forms_coarser_cells(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    # Two different 6-digit codes under the same 4-digit rollup 3255. At naics_digits=4
    # they share one cell (4 loans vs 1 establishment -> fires); at 6-digit each cell
    # is too thin against its own ZBP row.
    _insert_loans(
        con,
        [
            ("T-1", "29150", "325510"),
            ("T-2", "29150", "325510"),
            ("T-3", "29150", "325520"),
            ("T-4", "29150", "325520"),
        ],
    )
    _insert_est(con, [("29150", "3255", 1)])

    sigs = EstablishmentOvercountDetector(naics_digits=4).run(con)
    assert {s.loan_number for s in sigs} == {"T-1", "T-2", "T-3", "T-4"}
    assert sigs[0].evidence["naics_cell"] == "3255"
    assert sigs[0].evidence["ratio"] == 4.0


def test_min_ratio_is_configurable(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    # 3 loans vs 1 establishment -> ratio 3. Quiet at default (>=4), fires at min_ratio=2.
    _insert_loans(con, [(f"R-{i}", "29150", "325510") for i in range(3)])
    _insert_est(con, [("29150", "325510", 1)])

    assert EstablishmentOvercountDetector().run(con) == []
    sigs = EstablishmentOvercountDetector(min_ratio=2.0).run(con)
    assert len(sigs) == 3
    assert sigs[0].evidence["ratio"] == 3.0
