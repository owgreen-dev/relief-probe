"""Orchestrate real ingestion: resolve -> download -> load into the warehouse."""

from __future__ import annotations

from collections.abc import Callable

import duckdb

from relief_probe.config import raw_dir
from relief_probe.ingest.download import download_file, resolve_ppp_resources
from relief_probe.ingest.loader import load_ppp_csv
from relief_probe.ingest.sources import DEFAULT_SLICE


def ingest_ppp(
    con: duckdb.DuckDBPyConnection,
    slice_name: str = DEFAULT_SLICE,
    *,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Resolve, download, and load the PPP CSVs in ``slice_name``.

    Returns one result dict per file: ``{name, url, rows}``. Downloads are cached
    in ``data/raw/`` and skipped if already present (SBA files are static FOIA
    snapshots). ``progress`` is an optional callback for per-file status lines.
    """
    resources = resolve_ppp_resources(slice_name)
    results: list[dict] = []
    for res in resources:
        dest = raw_dir() / res["name"]
        if not dest.exists():
            if progress:
                progress(f"downloading {res['name']}")
            download_file(res["url"], dest)
        if progress:
            progress(f"loading {res['name']}")
        rows = load_ppp_csv(con, dest)
        results.append({"name": res["name"], "url": res["url"], "rows": rows})
    return results
