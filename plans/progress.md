# Ralph Progress Log

Milestone: H6 — independent duplicate-address ring detector (`ralph/h6-ring-detector`)
Verify: `uv run --extra vision pytest && uvx ruff check .`

## This milestone (H6)

Goal: add ONE genuinely independent detector (a duplicate-address ring / link-analysis
signal) so corroboration across detectors stops being two views of the same
dollars-per-job ratio. Features H6-001..H6-005 in plans/prd.json. Pure code + TDD on
SEEDED tmp_path warehouses — never touch the real data/ warehouse, never invent numbers.

## Codebase Patterns

- Entity key is `loan_number` (string), never NPI.
- Warehouse: `relief_probe.warehouse.connect(path)` opens+inits a DuckDB file.
  Tables: `loans`, `fraud_cases`, `press_releases`, `signals`. Schema in
  `src/relief_probe/warehouse/db.py`. Loans have borrower_address/city/state/zip.
- Detectors: subclass `detectors/base.py::Detector`, set `detector_id` + `summary`,
  implement `run(con) -> list[Signal]` (READ-ONLY; never write the warehouse).
  Register in `detectors/registry.py::all_detectors()`.
- `signals` columns: (loan_number, detector_id, score, evidence_json) — evidence
  is a JSON string; parse with `json.loads`. Score is comparable WITHIN a detector.
- Composite (in `relief_probe.scoring`) = `MAX(percentile(score)) + CORROBORATION_WEIGHT
  * (n_signals - 1)`; scores are percentile-normalised PER detector (CUME_DIST) before
  combining so different detector scales are comparable; `CORROBORATION_WEIGHT` = 0.5.
- Robust stats: `stats.robust_z(x, min_mad=...)` and `detectors/_cohort.cohort_robust_z`
  (the cohort detectors floor MAD via `min_mad` to avoid absurd z on dense cohorts).
- Style: `from __future__ import annotations`, typed, module docstrings, ruff
  line-length 90 (select E,F,I,UP,B). Commit ONE feature per iteration.
- Tests seed a tiny warehouse via `connect(tmp_path / "wh.duckdb")` then INSERT, and
  assert on detector output / composite_ranking. Mirror tests/test_detectors.py.

## Environment (IMPORTANT — do not regress)

- The `.venv` must be synced or `uv run pytest` can fall through to a DIFFERENT
  project's venv on PATH (`fraud-github/.venv`) and fail to import `relief_probe`.
  Fixed via `[dependency-groups] dev` (pytest, pillow, scikit-learn) in pyproject.toml
  — dev groups install by default, so `uv run pytest` self-provisions.
- `uvx ruff check .` is the lint command (`uv run ruff` is NOT installed in the venv).
- `uv 0.11.19` does NOT support `[tool.uv] default-extras`.
- The `agent` extra (langgraph/langchain-anthropic/mcp) stays OPT-IN; any LLM/MCP
  tests must `pytest.importorskip` so the core env stays green.

## Key Files (H6)

- NEW: `src/relief_probe/detectors/_address.py` (normalize_address)
- NEW: `src/relief_probe/detectors/duplicate_address_ring.py`
- `src/relief_probe/detectors/registry.py` (register the detector)
- NEW: `tests/test_address.py`, `tests/test_ring_detector.py`
- `README.md` detector catalog, `NEXT_STEPS.md`, `RESPONSIBLE_USE.md` (qualitative)

## Learnings (append as you go)

- (none yet for H6)
