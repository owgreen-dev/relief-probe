"""Load a Census ZIP Business Patterns (ZBP) CSV into the ``establishments`` table.

ZBP publishes the number of business establishments per ZIP x NAICS cell. We join
it directly on ``loans.borrower_zip`` (no zip->county crosswalk) to measure PPP loan
density vs the real count of businesses that exist in an industry-geography — the
input to the ``establishment_overcount`` detector.

Like the PPP loader we read with ``all_varchar=true`` and ``TRY_CAST`` so blanks or
malformed counts become NULL instead of aborting the load. Census ZBP headers vary
in case across vintages (``ZIP``/``zip``, ``NAICS``/``naics``, ``EST``/``est``), so
we read with ``normalize_names=true`` (DuckDB lowercases headers) and map the
lowercased ``zip`` / ``naics`` / ``est`` columns. Loads are idempotent via
``INSERT OR IGNORE`` on the ``(zip, naics)`` primary key.

The real Census file is a MANUAL public download (see ingest/sources.py); this
loader only ever takes a local path — no network.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

# Raw (normalized) ZBP header -> warehouse column:
#   zip   -> zip
#   naics -> naics
#   est   -> establishments
_ZBP_INSERT = """
INSERT OR IGNORE INTO establishments (zip, naics, establishments)
SELECT
    "zip",
    "naics",
    TRY_CAST("est" AS INTEGER)
FROM {rel}
WHERE "zip" IS NOT NULL AND "naics" IS NOT NULL
"""


def load_zbp_csv(con: duckdb.DuckDBPyConnection, csv_path: Path) -> int:
    """Load one Census ZBP CSV into ``establishments``. Returns rows inserted."""
    rel = (
        f"read_csv('{csv_path}', header=true, all_varchar=true, "
        "normalize_names=true, ignore_errors=true)"
    )
    before = con.execute("SELECT COUNT(*) FROM establishments").fetchone()[0]
    con.execute(_ZBP_INSERT.format(rel=rel))
    after = con.execute("SELECT COUNT(*) FROM establishments").fetchone()[0]
    return after - before
