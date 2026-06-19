"""Smoke + pure-helper tests for scripts/validate_learned_scorer.py.

The script's real artifact is a heavy read-only run against the full warehouse (a
manual post-loop step), so here we only cover the parts that do NOT need it: that the
module imports cleanly with no heavy work at module load, that its read-only contract
+ holdout config are intact, and that its pure formatting helpers are deterministic.
Never touches the real warehouse.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "validate_learned_scorer.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_learned_scorer", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_imports_with_no_heavy_run():
    mod = _load_module()
    assert hasattr(mod, "main")
    # The reported headline is the temporal holdout (SIGN-013).
    assert isinstance(mod.HOLDOUT_YEAR, int)
    assert mod.MIN_AMOUNT >= 150_000.0
    # All four rankings the harness compares are reported.
    assert set(mod.RANKINGS) == {"lgbm", "pu_bagging", "composite", "rrf_fusion"}


def test_fmt_pct_is_deterministic():
    mod = _load_module()
    assert mod._fmt_pct(None) == "—"
    assert mod._fmt_pct(0.25) == "25.0%"
    assert mod._fmt_pct(0.0) == "0.0%"
    # Pure: same input → same output.
    assert mod._fmt_pct(0.5) == mod._fmt_pct(0.5)


def test_script_is_read_only():
    # The script must open the warehouse read-only (never writes — it's a validator).
    text = _SCRIPT.read_text()
    assert "read_only=True" in text
    assert "connect(read_only=True)" in text
