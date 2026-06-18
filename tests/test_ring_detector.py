"""Tests for the duplicate-address ring detector (H6-002).

All warehouses are seeded synthetically in a tmp_path DuckDB file — we never
touch the real data/ warehouse, and we plant no benchmark numbers.
"""

from __future__ import annotations

from relief_probe.detectors.duplicate_address_ring import DuplicateAddressRingDetector
from relief_probe.warehouse import connect

_INSERT = (
    "INSERT INTO loans (loan_number, borrower_name, borrower_address, "
    "borrower_city, borrower_state, borrower_zip, current_approval_amount) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def test_ring_of_distinct_borrowers_at_one_building_all_fire(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    # Three DISTINCT borrowers at one building, written in three different
    # formats that must normalize to the same key.
    rows = [
        ("R1", "Alpha LLC", "123 Main Street", "Austin", "TX", "78701", 100_000),
        ("R2", "Beta LLC", "123 MAIN ST", "Austin", "TX", "78701", 120_000),
        ("R3", "Gamma LLC", "123 Main St., Suite 200", "Austin", "TX", "78701", 90_000),
    ]
    con.executemany(_INSERT, rows)

    sigs = DuplicateAddressRingDetector(min_ring_size=3).run(con)
    flagged = {s.loan_number for s in sigs}
    assert flagged == {"R1", "R2", "R3"}

    ev = sigs[0].evidence
    assert ev["ring_size"] == 3  # distinct borrowers
    assert ev["n_loans"] == 3
    assert ev["total_ring_amount"] == 310_000
    assert set(ev["borrower_names_sample"]) == {"Alpha LLC", "Beta LLC", "Gamma LLC"}


def test_single_borrower_many_loans_is_not_a_ring(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    # ONE borrower holding several loans at one address — not a ring.
    rows = [
        ("S1", "Solo LLC", "500 Oak Avenue", "Dallas", "TX", "75201", 50_000),
        ("S2", "Solo LLC", "500 Oak Ave", "Dallas", "TX", "75201", 60_000),
        ("S3", "Solo LLC", "500 Oak Avenue, Unit 4", "Dallas", "TX", "75201", 70_000),
        ("S4", "Solo LLC", "500 OAK AVE", "Dallas", "TX", "75201", 80_000),
    ]
    con.executemany(_INSERT, rows)

    sigs = DuplicateAddressRingDetector(min_ring_size=3).run(con)
    assert sigs == []


def test_ring_one_below_threshold_does_not_fire(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    rows = [
        ("T1", "One LLC", "9 Pine Rd", "Reno", "NV", "89501", 40_000),
        ("T2", "Two LLC", "9 Pine Road", "Reno", "NV", "89501", 40_000),
    ]
    con.executemany(_INSERT, rows)

    sigs = DuplicateAddressRingDetector(min_ring_size=3).run(con)
    assert sigs == []


def test_unkeyable_addresses_are_excluded(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    rows = [
        ("U1", "A LLC", None, "Austin", "TX", "78701", 10_000),
        ("U2", "B LLC", "   ", "Austin", "TX", "78701", 10_000),
        ("U3", "C LLC", "", "Austin", "TX", "78701", 10_000),
    ]
    con.executemany(_INSERT, rows)

    sigs = DuplicateAddressRingDetector(min_ring_size=3).run(con)
    assert sigs == []


def test_score_is_monotonic_in_ring_size_and_dollars(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    # Small ring at one building.
    small = [
        ("A1", "A1 LLC", "1 First St", "Erie", "PA", "16501", 10_000),
        ("A2", "A2 LLC", "1 First St", "Erie", "PA", "16501", 10_000),
        ("A3", "A3 LLC", "1 First St", "Erie", "PA", "16501", 10_000),
    ]
    # Bigger, richer ring at a different building.
    big = [
        (f"B{i}", f"B{i} LLC", "2 Second St", "Erie", "PA", "16502", 500_000)
        for i in range(6)
    ]
    con.executemany(_INSERT, small + big)

    sigs = DuplicateAddressRingDetector(min_ring_size=3).run(con)
    by_loan = {s.loan_number: s.score for s in sigs}
    assert by_loan["B0"] > by_loan["A1"]
