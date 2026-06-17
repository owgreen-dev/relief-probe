# Ralph Progress Log

Milestone: M5 — agent + MCP layer (`ralph/agent-mcp`)
Verify: `uv run pytest && uv run ruff check .`

## Codebase Patterns

- Entity key is `loan_number` (string), never NPI.
- Warehouse: `relief_probe.warehouse.connect(path)` opens+inits a DuckDB file.
  Tables: `loans`, `fraud_cases`, `press_releases`, `signals`. Schema in
  `src/relief_probe/warehouse/db.py`.
- `signals` columns: (loan_number, detector_id, score, evidence_json) — evidence
  is a JSON string; parse with `json.loads`.
- Composite score = `MAX(score) + CORROBORATION_WEIGHT * (n_signals - 1)`;
  `CORROBORATION_WEIGHT` lives in `relief_probe.scoring` (0.5).
- Cohort logic = NAICS x state on `amount/jobs` (mirror
  `detectors/naics_cohort_outlier`), min cohort size 30.
- Style: `from __future__ import annotations`, typed, module docstrings, ruff
  line-length 90 (select E,F,I,UP,B).
- Tests seed a tiny warehouse via `connect(tmp_path / "wh.duckdb")` then INSERT.

## Environment (IMPORTANT — fixed in T-001)

- The `.venv` was created but NOT synced: `relief_probe` was not installed
  editable and `pytest` was absent, so `uv run pytest` fell through to a *different*
  project's venv on PATH (`fraud-github/.venv`) and failed to import `relief_probe`.
- Fix: added a `[dependency-groups] dev` with `pytest`, `pillow`, `scikit-learn`
  to pyproject.toml. Dev groups install by default, so `uv run pytest` now
  self-provisions the test runner + the deps `tests/test_vision` imports at module
  load (PIL). `uv run` ignores the leaked `VIRTUAL_ENV` (warning only) and uses the
  project `.venv`, so verification is robust.
- `uv 0.11.19` does NOT support `[tool.uv] default-extras` — do not use it.
- The `agent` extra (langgraph/langchain-anthropic/mcp) stays OPT-IN; T-002..T-004
  LLM/MCP tests must use `pytest.importorskip` so the core env stays green.

## Key Files

- `src/relief_probe/agent/__init__.py`, `src/relief_probe/agent/tools.py`
- `tests/test_agent_tools.py`
- `pyproject.toml` (dependency-groups dev)

---

## 2026-06-17 - Session Notes

### Task: T-001 - agent/tools.py read-only evidence tools

**What was implemented:**
- `agent/__init__.py` (package docstring) and `agent/tools.py` with six pure-Python
  read-only tools: `loan_profile`, `loan_signals`, `peer_comparison`,
  `fraud_case_check`, `composite_for`, `gather_evidence`.
- All degrade gracefully: `{}` / `[]` / `{'available': False, 'reason': ...}` /
  `{'flagged': False}` for not-found / no-cohort / not-flagged branches.
- `tests/test_agent_tools.py`: seeds loans+signals+fraud_cases, asserts each tool's
  shape and values plus the empty branches (11 tests).
- Fixed the broken test environment (see Environment section).

**Files changed:**
- src/relief_probe/agent/__init__.py (new)
- src/relief_probe/agent/tools.py (new)
- tests/test_agent_tools.py (new)
- pyproject.toml (dev dependency group)

**Learnings:**
- `peer_comparison` computes the cohort median in SQL with `MEDIAN(...)` over
  NAICS x state peers (jobs>=1, amount>0); returns `available: False` with a
  `reason` for the loan-not-found / missing-jobs / cohort-too-small branches.
- `composite_for` reuses `CORROBORATION_WEIGHT` and the scoring formula for a
  single loan rather than reranking the whole table.
- Verification: 33 passed, ruff clean.

---
