"""Tests for the duplicate-address ring detector (H6-002).

All warehouses are seeded synthetically in a tmp_path DuckDB file — we never
touch the real data/ warehouse, and we plant no benchmark numbers.
"""

from __future__ import annotations

from relief_probe.benchmark import detector_flagged_loans, detector_overlap
from relief_probe.detectors.duplicate_address_ring import DuplicateAddressRingDetector
from relief_probe.detectors.naics_cohort_outlier import NaicsCohortOutlierDetector
from relief_probe.detectors.payroll_cap import PayrollCapExceedanceDetector
from relief_probe.detectors.registry import all_detectors
from relief_probe.detectors.runner import run_all
from relief_probe.scoring import composite_ranking
from relief_probe.warehouse import connect

_INSERT = (
    "INSERT INTO loans (loan_number, borrower_name, borrower_address, "
    "borrower_city, borrower_state, borrower_zip, current_approval_amount) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

# Richer insert for integration tests that also need NAICS/jobs (so a loan can
# trip a $/job detector AND the ring detector and thereby corroborate).
_INSERT_FULL = (
    "INSERT INTO loans (loan_number, borrower_name, naics_code, "
    "borrower_address, borrower_city, borrower_state, borrower_zip, "
    "current_approval_amount, jobs_reported) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
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


# --- H6-003: registration + generic integration (runner / composite) ----------

RINGLEADER = "RING-1"


def _seed_ring_plus_dollar_outlier(con):
    """A NAICS cohort, plus a 3-borrower ring whose ringleader is ALSO a $/job
    outlier — so it trips the ring detector AND both dollars-per-job detectors."""
    rows = []
    # 40 normal TX restaurants at ~$9k-$12k per job (jobs=10), no shared address
    # (so they neither form a ring nor trip the $/job detectors).
    for i in range(40):
        amount = (9000 + i * 75) * 10
        rows.append(
            (f"N{i:03d}", f"Normal Diner {i}", "722511", None, None, "TX",
             None, amount, 10)
        )
    # A ring of three DISTINCT borrowers at one building (varied formatting).
    # The ringleader claims $200k/job — also a cohort outlier and over the cap.
    rows += [
        (RINGLEADER, "Shell A LLC", "722511", "100 Shell Street",
         "Austin", "TX", "78701", 1_000_000, 5),
        ("RING-2", "Shell B LLC", "722511", "100 SHELL ST",
         "Austin", "TX", "78701", 110_000, 10),
        ("RING-3", "Shell C LLC", "722511", "100 Shell St., Suite 5",
         "Austin", "TX", "78701", 100_000, 10),
    ]
    con.executemany(_INSERT_FULL, rows)


def test_detector_is_registered():
    ids = {d.detector_id for d in all_detectors()}
    assert "duplicate_address_ring" in ids


def test_run_all_includes_ring_detector_count(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_ring_plus_dollar_outlier(con)
    counts = run_all(con)
    # The runner iterates all_detectors() generically — no special-casing.
    assert counts["duplicate_address_ring"] == 3
    assert counts["naics_cohort_outlier"] >= 1
    assert counts["payroll_cap_exceedance"] >= 1


def test_composite_corroborates_ring_and_dollar_signals(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_ring_plus_dollar_outlier(con)
    run_all(con)

    ranking = composite_ranking(con)
    by_loan = {row["loan_number"]: row for _, row in ranking.iterrows()}

    # The ringleader is implicated by THREE independent detectors — the composite
    # picks this up generically (max percentile + corroboration bonus).
    leader = by_loan[RINGLEADER]
    assert leader["n_signals"] == 3
    assert set(leader["detectors"]) == {
        "duplicate_address_ring",
        "naics_cohort_outlier",
        "payroll_cap_exceedance",
    }

    # The other ring members are flagged by the ring detector alone (n_signals=1),
    # so the ringleader's corroboration bonus ranks it strictly higher.
    assert by_loan["RING-2"]["n_signals"] == 1
    assert by_loan["RING-2"]["detectors"] == ["duplicate_address_ring"]
    assert leader["composite_score"] > by_loan["RING-2"]["composite_score"]


# --- H6-004: independence — orthogonal to the $/job signal ---------------------

RING_LOANS = {"OR-1", "OR-2", "OR-3"}


def _seed_normal_dollar_ring(con):
    """A ring whose loans have PERFECTLY NORMAL dollars-per-job.

    40 normal TX restaurants (no shared address) establish a 722511|TX cohort with
    a real median. The three ring borrowers sit at one building but their $/job
    ($10k, jobs=10) lands squarely inside that cohort's spread AND far below the
    NAICS-72 payroll cap — so the $/job detectors cannot see them. Only the
    co-location (ring) signal fires, which is the whole point of H6.
    """
    rows = []
    for i in range(40):
        # per-job spans ~$9,000-$11,925 — the ring's $10,000/job is mid-cohort.
        amount = (9000 + i * 75) * 10
        rows.append(
            (f"P{i:03d}", f"Normal Diner {i}", "722511", None, None, "TX",
             None, amount, 10)
        )
    # Three DISTINCT borrowers at one building, varied formatting, normal $/job.
    rows += [
        ("OR-1", "Quiet A LLC", "722511", "7 Calm Street",
         "Austin", "TX", "78701", 100_000, 10),
        ("OR-2", "Quiet B LLC", "722511", "7 CALM ST",
         "Austin", "TX", "78701", 100_000, 10),
        ("OR-3", "Quiet C LLC", "722511", "7 Calm St., Suite 3",
         "Austin", "TX", "78701", 100_000, 10),
    ]
    con.executemany(_INSERT_FULL, rows)


def test_ring_with_normal_dollars_per_job_is_invisible_to_dollar_detectors(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_normal_dollar_ring(con)

    ring = {s.loan_number for s in DuplicateAddressRingDetector().run(con)}
    naics = {s.loan_number for s in NaicsCohortOutlierDetector().run(con)}
    payroll = {s.loan_number for s in PayrollCapExceedanceDetector().run(con)}

    # The ring detector sees the co-location the $/job detectors cannot.
    assert RING_LOANS <= ring
    # Neither dollars-per-job detector flags the (normally-sized) ring loans.
    assert RING_LOANS.isdisjoint(naics)
    assert RING_LOANS.isdisjoint(payroll)


def test_detector_overlap_is_pure_set_math():
    a = {"L1", "L2", "L3"}
    b = {"L3", "L4"}
    ov = detector_overlap(a, b)
    assert ov["n_a"] == 3
    assert ov["n_b"] == 2
    assert ov["intersection"] == 1  # only L3
    assert ov["union"] == 4
    assert ov["jaccard"] == round(1 / 4, 6)

    # Disjoint sets → no overlap; empty sets → defined as 0.0 (no division).
    assert detector_overlap({"X"}, {"Y"})["jaccard"] == 0.0
    assert detector_overlap(set(), set())["jaccard"] == 0.0


def test_overlap_of_ring_and_dollar_detectors_is_low(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_normal_dollar_ring(con)
    run_all(con)

    ring = detector_flagged_loans(con, "duplicate_address_ring")
    naics = detector_flagged_loans(con, "naics_cohort_outlier")
    # The detectors flag disjoint loan sets here — corroboration, when it happens,
    # is across independent views (Jaccard 0 on this synthetic seed).
    assert detector_overlap(ring, naics)["jaccard"] == 0.0
