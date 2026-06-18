"""Offline tests for the Census ZBP (establishments) loader and schema.

No network: we write a tiny synthetic ZBP-style CSV to tmp_path and assert the
loader maps + types it into the ``establishments`` table and that re-loading is
idempotent. NO real Census download.
"""

from __future__ import annotations

from relief_probe.ingest.establishments import load_zbp_csv
from relief_probe.warehouse import connect

# Mixed-case headers + a blank/malformed est value (-> NULL) to prove tolerance.
ZBP_CSV = (
    "ZIP,NAICS,EST\n"
    "29150,325510,4\n"
    "75001,541110,120\n"
    "10001,722511,\n"
)


def _write_csv(path, text=ZBP_CSV):
    path.write_text(text)
    return path


def test_zbp_loader_maps_and_types(tmp_path):
    csv = _write_csv(tmp_path / "zbp.csv")
    con = connect(tmp_path / "wh.duckdb")
    inserted = load_zbp_csv(con, csv)
    assert inserted == 3

    rows = con.execute(
        "SELECT zip, naics, establishments FROM establishments ORDER BY zip"
    ).fetchall()
    assert rows == [
        ("10001", "722511", None),  # blank est -> NULL, not a load failure
        ("29150", "325510", 4),
        ("75001", "541110", 120),
    ]
    # Types: establishments is an INTEGER column.
    est = con.execute(
        "SELECT establishments FROM establishments WHERE zip = '75001'"
    ).fetchone()[0]
    assert isinstance(est, int)


def test_zbp_loader_is_idempotent(tmp_path):
    csv = _write_csv(tmp_path / "zbp.csv")
    con = connect(tmp_path / "wh.duckdb")
    assert load_zbp_csv(con, csv) == 3
    # Re-loading the same file inserts nothing (INSERT OR IGNORE on (zip, naics)).
    assert load_zbp_csv(con, csv) == 0
    assert con.execute("SELECT COUNT(*) FROM establishments").fetchone()[0] == 3
