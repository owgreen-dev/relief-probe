"""The README-figure data prep produces sane shapes on the synthetic demo warehouse.

Tests the pure data-prep functions only (no matplotlib render) — fast, offline, and
SIGN-007-safe (builds a throwaway demo DB, never touches the real warehouse).
"""

from __future__ import annotations

import importlib.util

import numpy as np

from relief_probe.config import REPO_ROOT
from relief_probe.demo.seed import build_demo_warehouse
from relief_probe.warehouse import connect

# The figure generator lives in scripts/, so load it by path.
_spec = importlib.util.spec_from_file_location(
    "make_readme_figures", REPO_ROOT / "scripts" / "make_readme_figures.py"
)
figs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(figs)


def _demo_con(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    build_demo_warehouse(con)
    return con


def test_density_grid_sums_to_population(tmp_path):
    con = _demo_con(tmp_path)
    d = figs.density_grid(con)
    assert d["n_total"] > 0
    assert d["lx"].size == d["ly"].size == d["n"].size > 0
    # Every loan lands in exactly one cell, so the cell counts sum to the population.
    assert int(d["n"].sum()) == d["n_total"]


def test_prosecuted_points_present_and_finite(tmp_path):
    con = _demo_con(tmp_path)
    p = figs.prosecuted_points(con)
    assert p["n"] > 0
    assert p["lx"].size == p["ly"].size == p["n"]
    assert np.isfinite(p["lx"]).all() and np.isfinite(p["ly"]).all()


def test_lift_curve_is_well_formed_and_beats_random(tmp_path):
    con = _demo_con(tmp_path)
    c = figs.lift_curve(con)
    ks = c["ks"]
    assert ks.size == c["comp_lift"].size == c["ci_lo"].size == c["ci_hi"].size > 0
    assert c["base_rate"] > 0 and c["n_positives"] > 0
    # CI band is well-ordered, and the composite concentrates positives (> random 1×).
    assert (c["ci_hi"] >= c["ci_lo"]).all()
    assert c["comp_lift"].max() > 1.0
