"""Runtime configuration and canonical filesystem paths.

Everything the project writes lives under ``data/`` and is reproducible from
public sources, so the whole tree is gitignored. Paths can be overridden with
the ``RELIEF_PROBE_DATA_DIR`` environment variable (useful for tests and CI).
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent.parent


def data_dir() -> Path:
    """Root of the local data tree (raw downloads, DuckDB warehouse)."""
    env = os.environ.get("RELIEF_PROBE_DATA_DIR")
    base = Path(env).expanduser().resolve() if env else REPO_ROOT / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def raw_dir() -> Path:
    p = data_dir() / "raw"
    p.mkdir(parents=True, exist_ok=True)
    return p


def warehouse_path() -> Path:
    """Path to the DuckDB warehouse file."""
    return data_dir() / "relief_probe.duckdb"
