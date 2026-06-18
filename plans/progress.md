# Ralph Progress Log

Milestone: Loop 2 — Census establishment-overcount detector
(`ralph/loop2-establishment-overcount`)
Verify: `uv run --extra vision pytest && uvx ruff check .`

## This milestone (Loop 2)

Build the **establishment_overcount** detector (Griffin/Kruger: PPP loans frequently
exceed the number of business establishments that exist in an industry-geography). Needs
a NEW public-data join: Census **ZIP Business Patterns (ZBP)** — establishment counts by
ZIP x NAICS — joined directly on `loans.borrower_zip` (no zip->county crosswalk).
Features L2-001..L2-005 in plans/prd.json.

CRITICAL: the detector registers in `registry.exploratory_detectors()`, NOT
`all_detectors()` (SIGN-010). NO network/downloads (SIGN-011) — loaders are path-based,
tested against synthetic CSV fixtures in tmp_path. Never touch the real data/ warehouse
(SIGN-007). No invented numbers (SIGN-008). The real Census download + ingest + lift
validation + promotion is a MANUAL post-loop step.

## Codebase Patterns

- Warehouse: `warehouse.connect(path)` opens+inits DuckDB; schema in
  `warehouse/db.py::SCHEMA_SQL` (CREATE TABLE IF NOT EXISTS — add `establishments` there).
  Tables: loans, fraud_cases, press_releases, signals (+ new: establishments).
- Loaders (ingest/loader.py): read CSV with `all_varchar=true` + `TRY_CAST` (blanks ->
  NULL), idempotent `INSERT OR IGNORE`. Sources registered in ingest/sources.py (don't
  hardcode fragile URLs in the loader; take a local path).
- Detectors: subclass `detectors/base.py::Detector`, `run(con) -> list[Signal]`,
  READ-ONLY, return [] gracefully when their input table is empty/missing. New detectors
  -> `exploratory_detectors()`. Composite = `MAX(percentile(score)) + 0.5*(n-1)`.
- Production composite now = naics_cohort_outlier + payroll_cap_exceedance +
  multiple_funded_loans (Loop 1 promoted multiple_funded after real-data validation).
- `run_all(con, detectors=None)` defaults to all_detectors(); pass an explicit list for
  exploratory detectors.
- loans geo fields: borrower_zip, project_zip, project_county_name, naics_code (6-digit).
- Style: `from __future__ import annotations`, typed, docstrings, ruff line-length 90.
  Commit ONE feature per iteration. CLI uses typer; test with typer.testing.CliRunner.

## Environment (IMPORTANT — do not regress)

- `uv run pytest` self-provisions via `[dependency-groups] dev`.
- `uvx ruff check .` is the lint command (`uv run ruff` is NOT installed).
- The `agent` extra stays OPT-IN; LLM/MCP tests must `pytest.importorskip`.

## Key Files (Loop 2)

- `src/relief_probe/warehouse/db.py` (add `establishments` table)
- NEW: `src/relief_probe/ingest/establishments.py` (or extend ingest/loader.py)
- `src/relief_probe/ingest/sources.py` (register the Census ZBP source)
- NEW: `src/relief_probe/detectors/establishment_overcount.py`
- `src/relief_probe/detectors/registry.py` (add to exploratory_detectors)
- `src/relief_probe/cli.py` (add `ingest-establishments PATH`)
- NEW tests: `tests/test_establishments_loader.py`, `tests/test_establishment_overcount.py`
- `README.md`, `NEXT_STEPS.md`, `docs/SCHEMA.md` (qualitative, no numbers)

## Learnings (append as you go)

- L2-001 (establishments table + ZBP loader): added `establishments` table to
  warehouse/db.py SCHEMA_SQL with composite PK (zip, naics) for idempotency. New
  loader ingest/establishments.py::load_zbp_csv mirrors load_ppp_csv (all_varchar +
  TRY_CAST + INSERT OR IGNORE). Key trick: ZBP headers vary in case across vintages,
  so read with `normalize_names=true` (DuckDB lowercases headers) and reference
  quoted lowercase "zip"/"naics"/"est". Source documented in ingest/sources.py
  (ZBP_LANDING_URL + note) — no hardcoded fragile URL. Tests in
  tests/test_establishments_loader.py against a synthetic CSV (mixed-case headers +
  blank est -> NULL). 95 tests pass.
