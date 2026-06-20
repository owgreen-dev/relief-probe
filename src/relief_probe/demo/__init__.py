"""Synthetic demo data for the hosted dashboard.

The real warehouse is gitignored and must never be deployed (SIGN-007), so the
public demo builds its own fully-synthetic warehouse on first launch. Enable it
by setting ``RELIEF_PROBE_DEMO=1`` (the dashboard does this check itself). See
:mod:`relief_probe.demo.seed` — every name, loan, and label is fictitious.
"""

from __future__ import annotations

from pathlib import Path

from relief_probe.config import warehouse_path
from relief_probe.demo.seed import build_demo_warehouse
from relief_probe.warehouse import connect

__all__ = ["build_demo_warehouse", "ensure_demo_warehouse"]


def ensure_demo_warehouse() -> Path:
    """Build the synthetic demo warehouse if one isn't already present.

    Safe to call repeatedly: if the warehouse already has loans (a prior demo
    build *or* a real warehouse), it is left untouched — we never clobber data.
    Returns the warehouse path.
    """
    path = warehouse_path()
    if path.exists():
        try:
            con = connect(path, read_only=True)
            try:
                has_loans = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0] > 0
            finally:
                con.close()
            if has_loans:
                return path
        except Exception:
            # Unreadable / schema-less file — fall through and (re)build.
            pass

    con = connect(path)  # read-write → schema bootstrap
    try:
        build_demo_warehouse(con)
    finally:
        con.close()
    return path
