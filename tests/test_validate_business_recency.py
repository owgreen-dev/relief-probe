"""Smoke + pure-helper tests for scripts/validate_business_recency.py.

The script's real artifact is a read-only run against the full warehouse (a manual
post-loop step), so here we only cover the parts that do NOT need it: that the module
imports cleanly (no extra required at import time) and that its LABEL-FREE pure
helpers (`recency_score`, `rank_slice_by_recency`) are monotonic + deterministic and
never fire on the non-recency / missing values. Never touches the real warehouse.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "validate_business_recency.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_business_recency", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_imports_without_extra():
    mod = _load_module()
    assert hasattr(mod, "main")
    assert mod.MIN_AMOUNT >= 150_000.0
    # Out-of-time evaluation must be on a real holdout year.
    assert isinstance(mod.HOLDOUT_YEAR, int)


def test_recency_score_monotonic_and_label_free():
    mod = _load_module()
    startup = mod.recency_score("Startup, Loan Funds will Open Business")
    new = mod.recency_score("New Business or 2 years or less")
    change = mod.recency_score("Change of Ownership")
    # Ordinal, strongest first.
    assert startup > new > change > 0.0
    # Case-insensitive (matches the detector's casefold lookup).
    assert mod.recency_score("STARTUP, LOAN FUNDS WILL OPEN BUSINESS") == startup
    # NEVER fire on the eligible baseline, the missing value, or null/blank.
    assert mod.recency_score("Existing or more than 2 years old") == 0.0
    assert mod.recency_score("Unanswered") == 0.0
    assert mod.recency_score(None) == 0.0
    assert mod.recency_score("") == 0.0
    # Deterministic.
    assert mod.recency_score("Change of Ownership") == change


def test_rank_slice_orders_by_score_then_id():
    mod = _load_module()
    rows = [
        ("existing_b", "Existing or more than 2 years old"),
        ("startup_a", "Startup, Loan Funds will Open Business"),
        ("new_a", "New Business or 2 years or less"),
        ("existing_a", "Unanswered"),
        ("change_a", "Change of Ownership"),
    ]
    ranked = mod.rank_slice_by_recency(rows)
    # Strongest recency first; the two score-0 loans tie and break by loan_number.
    assert ranked == ["startup_a", "new_a", "change_a", "existing_a", "existing_b"]
