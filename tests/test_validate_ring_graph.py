"""Smoke + pure-helper tests for scripts/validate_ring_graph.py.

The script's real artifact is a read-only run against the full warehouse (a manual
post-loop step), so here we only cover the parts that do NOT need it: that the
module imports cleanly (no `graph` extra required at import time) and that its
LABEL-FREE pure helpers (`ring_score`, `rank_loans_by_structure`) behave.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_ring_graph.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_ring_graph", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_imports_without_graph_extra():
    mod = _load_module()
    assert hasattr(mod, "main")
    assert mod.MIN_AMOUNT >= 150_000.0
    # Out-of-time evaluation must be on a real holdout year.
    assert isinstance(mod.HOLDOUT_YEAR, int)


def test_ring_score_monotonic_in_structure():
    mod = _load_module()
    weak = {"distinct_borrowers": 1, "community_size": 1}
    strong = {"distinct_borrowers": 6, "community_size": 6}
    assert mod.ring_score(strong) > mod.ring_score(weak)
    # Matches the detector's label-free score formula.
    assert mod.ring_score(strong) == math.log1p(6) + math.log1p(6)


def test_rank_loans_by_structure_orders_by_score_then_id():
    mod = _load_module()
    features = {
        "isolated": {"distinct_borrowers": 1, "community_size": 1},
        "ring_a": {"distinct_borrowers": 4, "community_size": 4},
        "ring_b": {"distinct_borrowers": 4, "community_size": 4},
        "weak": {"distinct_borrowers": 2, "community_size": 2},
    }
    ranked = mod.rank_loans_by_structure(features)
    # Strongest ring loans first; ties broken deterministically by loan_number.
    assert ranked == ["ring_a", "ring_b", "weak", "isolated"]
