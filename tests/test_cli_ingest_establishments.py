"""Offline CLI test for `relief-probe ingest-establishments`.

No network, no real warehouse: we write a tiny synthetic ZBP-style CSV to tmp_path,
point the CLI's `connect()` at a tmp warehouse, and assert the command reports the
loaded row count.
"""

from __future__ import annotations

from typer.testing import CliRunner

from relief_probe import cli
from relief_probe.warehouse import connect

ZBP_CSV = (
    "ZIP,NAICS,EST\n"
    "29150,325510,4\n"
    "75001,541110,120\n"
)


def test_ingest_establishments_reports_loaded_count(tmp_path, monkeypatch):
    csv = tmp_path / "zbp.csv"
    csv.write_text(ZBP_CSV)

    # Keep the command off the real warehouse: connect() -> tmp warehouse.
    monkeypatch.setattr(cli, "connect", lambda: connect(tmp_path / "wh.duckdb"))

    result = CliRunner().invoke(cli.app, ["ingest-establishments", str(csv)])
    assert result.exit_code == 0, result.output
    normalized = " ".join(result.output.split())
    assert "Loaded 2 establishment rows" in normalized

    # The rows actually landed in the tmp warehouse.
    con = connect(tmp_path / "wh.duckdb")
    assert con.execute("SELECT COUNT(*) FROM establishments").fetchone()[0] == 2


def test_ingest_establishments_missing_file_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "connect", lambda: connect(tmp_path / "wh.duckdb"))
    result = CliRunner().invoke(
        cli.app, ["ingest-establishments", str(tmp_path / "nope.csv")]
    )
    assert result.exit_code == 1
    assert "No such file" in " ".join(result.output.split())
