"""Load a Census NAICS code/title CSV into the ``naics_titles`` table.

Feeds the ``naics_name_mismatch`` detector with finer-grained industry titles than
the bundled 2-digit sector defaults (so a business name can be ranked against
6-digit industries, not just broad sectors). The Census publishes the NAICS index /
structure files at https://www.census.gov/naics/ — a MANUAL public download; this
loader only ever takes a local path (no network), mirroring the ZBP loader.

Read with ``all_varchar`` + ``normalize_names`` so header-case variation across NAICS
files doesn't matter. We map the first column whose normalized name looks like a code
(``naics``/``code``/``naics_code``) and the first that looks like a title
(``title``/``description``/``naics_title``). Idempotent via ``INSERT OR IGNORE`` on
the ``naics_code`` primary key.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

#: Candidate normalized header names for the code and title columns, in priority order.
_CODE_HEADERS = ("naics_code", "naics", "code", "seq_no")
_TITLE_HEADERS = ("title", "description", "naics_title", "naics_description")


def _pick(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lowered = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand]
    return None


def load_naics_titles(con: duckdb.DuckDBPyConnection, csv_path: Path) -> int:
    """Load one Census NAICS code/title CSV into ``naics_titles``. Returns rows added."""
    rel = (
        f"read_csv('{csv_path}', header=true, all_varchar=true, "
        "normalize_names=true, ignore_errors=true)"
    )
    columns = [
        r[0]
        for r in con.execute(f"SELECT * FROM {rel} LIMIT 0").description  # type: ignore[union-attr]
    ]
    code_col = _pick(columns, _CODE_HEADERS)
    title_col = _pick(columns, _TITLE_HEADERS)
    if code_col is None or title_col is None:
        raise ValueError(
            f"Could not find code/title columns in {csv_path.name}; "
            f"saw headers {columns}. Expected one of {_CODE_HEADERS} and "
            f"{_TITLE_HEADERS}."
        )

    before = con.execute("SELECT COUNT(*) FROM naics_titles").fetchone()[0]
    con.execute(
        f"""
        INSERT OR IGNORE INTO naics_titles (naics_code, title)
        SELECT TRIM("{code_col}"), TRIM("{title_col}")
        FROM {rel}
        WHERE "{code_col}" IS NOT NULL AND "{title_col}" IS NOT NULL
        """
    )
    after = con.execute("SELECT COUNT(*) FROM naics_titles").fetchone()[0]
    return after - before
