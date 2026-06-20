"""The synthetic demo warehouse builds, fires every production detector, and is
deterministic — so the hosted demo is reproducible and never ships real data.

All offline: build into a tmp DuckDB (SIGN-007 — never the real warehouse).
"""

from __future__ import annotations

from relief_probe.demo.seed import COHORTS, build_demo_warehouse
from relief_probe.warehouse import connect


def _build(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    counts = build_demo_warehouse(con)
    return con, counts


def test_population_is_all_in_the_150k_slice(tmp_path):
    con, _ = _build(tmp_path)
    n = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    n_slice = con.execute(
        "SELECT COUNT(*) FROM loans WHERE current_approval_amount >= 150000"
    ).fetchone()[0]
    assert n > 200
    assert n_slice == n  # every demo loan is labelable


def test_every_cohort_clears_the_min_size_gate(tmp_path):
    con, _ = _build(tmp_path)
    sizes = dict(
        con.execute(
            "SELECT naics_code || '|' || borrower_state, COUNT(*) "
            "FROM loans GROUP BY 1"
        ).fetchall()
    )
    for state, naics, _label in COHORTS:
        assert sizes[f"{naics}|{state}"] >= 30  # NaicsCohortOutlier min_cohort_size


def test_all_three_production_detectors_fire(tmp_path):
    con, _ = _build(tmp_path)
    fired = {
        r[0]
        for r in con.execute("SELECT DISTINCT detector_id FROM signals").fetchall()
    }
    assert fired == {
        "naics_cohort_outlier",
        "payroll_cap_exceedance",
        "multiple_funded_loans",
    }


def test_labels_resolve_to_real_loans_and_skew_high_dollar_per_job(tmp_path):
    con, _ = _build(tmp_path)
    unresolved = con.execute(
        "SELECT COUNT(*) FROM fraud_cases fc "
        "LEFT JOIN loans l ON l.loan_number = fc.loan_number "
        "WHERE l.loan_number IS NULL"
    ).fetchone()[0]
    assert unresolved == 0
    pop = con.execute(
        "SELECT MEDIAN(current_approval_amount / jobs_reported) FROM loans "
        "WHERE jobs_reported >= 1"
    ).fetchone()[0]
    pros = con.execute(
        "SELECT MEDIAN(l.current_approval_amount / l.jobs_reported) FROM loans l "
        "JOIN (SELECT DISTINCT loan_number FROM fraud_cases) fc "
        "ON fc.loan_number = l.loan_number WHERE l.jobs_reported >= 1"
    ).fetchone()[0]
    assert pros > pop  # prosecuted loans are the high-$/job tail, by construction


def test_similar_cases_surfaces_the_ring(tmp_path):
    from relief_probe.embeddings import HashingEmbedder
    from relief_probe.similarity.core import find_similar

    con, _ = _build(tmp_path)
    query = con.execute(
        "SELECT loan_number FROM loans WHERE borrower_name LIKE 'NORTHWIND%' "
        "ORDER BY loan_number LIMIT 1"
    ).fetchone()[0]
    lex = HashingEmbedder()
    res = find_similar(
        con, str(query), k=10, min_amount=150000, amount_tol=0.30,
        same_state=True, embedder=lex, lexical=lex,
    )
    assert res["available"]
    top_names = [n["borrower_name"] for n in res["neighbors"][:6]]
    assert sum(name.startswith("NORTHWIND") for name in top_names) >= 3
    assert res["summary"]["n_fraud_neighbors"] >= 1


def test_rebuild_is_deterministic(tmp_path):
    con, _ = _build(tmp_path)
    fp1 = con.execute(
        "SELECT loan_number, borrower_name, current_approval_amount "
        "FROM loans ORDER BY loan_number"
    ).fetchall()
    build_demo_warehouse(con)  # rebuild in place
    fp2 = con.execute(
        "SELECT loan_number, borrower_name, current_approval_amount "
        "FROM loans ORDER BY loan_number"
    ).fetchall()
    assert fp1 == fp2
