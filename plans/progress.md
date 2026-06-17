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

### Task: T-002 - agent/report.py InvestigatorReport + deterministic build_report

**What was implemented:**
- `agent/report.py` with frozen dataclasses `EvidenceItem(claim, source, detail)`
  and `InvestigatorReport(loan_number, risk_level, summary, evidence,
  alternative_explanations, recommended_next_steps, disclaimer)`.
- `DISCLAIMER` constant (lead-not-evidence wording mirroring RESPONSIBLE_USE.md).
- `build_report(evidence)` is pure/deterministic and consumes the dict from
  `tools.gather_evidence` verbatim. It fabricates nothing — every row cites its
  source tool (`composite_for`, `loan_signals`, `peer_comparison`,
  `fraud_case_check`).
- `tests/test_agent_report.py`: flagged+labeled -> critical with cited evidence;
  flagged+unlabeled -> high; unflagged -> low + empty evidence; disclaimer always
  present; frozen-instance check (7 tests).

**Files changed:**
- src/relief_probe/agent/report.py (new)
- tests/test_agent_report.py (new)

**Learnings:**
- Risk ladder is coarse triage (detector scores aren't calibrated): `critical`
  if labeled (fraud_cases match) regardless of score; else `low` when not
  flagged; else `high` when composite_score >= 6.0 OR n_signals >= 3; else
  `elevated`. Thresholds `_HIGH_COMPOSITE=6.0`, `_HIGH_N_SIGNALS=3` are documented
  in the module.
- `build_report` reads defensively (`evidence.get(...) or {}`) so a partial
  evidence dict never raises. One evidence row per fired detector, ordered by the
  upstream tool's score-DESC sort.
- Verification: 38 passed, ruff clean.

---

### Task: T-003 - agent/graph.py investigate() + `relief-probe investigate` CLI

**What was implemented:**
- `agent/graph.py` with `investigate(con, loan_number, *, use_llm=False) ->
  {report, telemetry}`. Default path is pure Python: `gather_evidence ->
  build_report`. Telemetry = `{path, tool_calls, use_llm}` (+ `model` on LLM path).
- `tool_calls` counts the evidence keys minus `loan_number` (= 5: profile,
  signals, peer_comparison, fraud_case, composite).
- LLM path (`use_llm=True`): imports `langchain_anthropic` LAZILY inside
  `_synthesize_narrative`; gathers the SAME deterministic evidence + builds the
  SAME grounded report, then asks `claude-opus-4-8` (temp 0) to rewrite ONLY the
  summary prose from the cited facts via `dataclasses.replace`. Risk level,
  evidence rows, disclaimer stay deterministic — model can reword, never re-rank
  or invent. Raises clear RuntimeError if the `agent` extra OR ANTHROPIC_API_KEY
  is missing.
- CLI `relief-probe investigate <loan_number> [--llm/--no-llm]`: opens warehouse
  read-only, exits cleanly if loan absent, prints risk + summary + evidence table
  (rich) + alternatives + next steps + disclaimer. Catches RuntimeError (missing
  extra/key) and prints a yellow hint instead of a traceback.
- `tests/test_agent_graph.py`: deterministic investigate on seeded outlier ->
  critical + cited evidence + telemetry (tool_calls==5); clean loan -> low; LLM
  test uses `pytest.importorskip('langchain_anthropic')` so core env skips it.

**Files changed:**
- src/relief_probe/agent/graph.py (new)
- src/relief_probe/cli.py (investigate command)
- tests/test_agent_graph.py (new)

**Learnings:**
- LLM grounding strategy: don't let the model produce the report structure — let
  it reword the deterministic summary only. The grounded EvidenceItems and risk
  band are computed in Python, so the LLM cannot fabricate or re-rank.
- `ChatAnthropic.invoke().content` can be a str OR a list of content blocks;
  flatten defensively before stripping.
- CLI uses `connect(read_only=True)` — investigate never writes.
- Verification: 41 passed, ruff clean.

---
