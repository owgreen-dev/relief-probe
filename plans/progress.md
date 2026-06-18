# Ralph Progress Log

Milestone: Loop 1 — research-driven detectors: amount_anomaly + multiple_funded_loans
(`ralph/loop1-amount-entity-detectors`)
Verify: `uv run --extra vision pytest && uvx ruff check .`

## This milestone (Loop 1)

Build TWO new public-data detectors that target patterns DIFFERENT from dollars-per-job:
- **amount_anomaly** — round-number + payroll-cap-maximization ("bunching") tells of a
  fabricated/reverse-engineered loan amount. No external data.
- **multiple_funded_loans** — entity resolution (normalize_name + normalize_address) →
  borrowers with more funded loans than the 1-per-draw rule allows (PPP/PPS). GAO-validated.

CRITICAL: both register in `registry.exploratory_detectors()`, NOT `all_detectors()`.
They are NOT in the default composite. Promotion is a manual decision after real-data
validation (the H6 lesson). Features L1-001..L1-005 in plans/prd.json. Pure code + TDD on
SEEDED tmp_path warehouses — never the real data/ warehouse, never invent numbers.

## Codebase Patterns

- Entity key is `loan_number` (string), never NPI.
- Warehouse: `relief_probe.warehouse.connect(path)` opens+inits a DuckDB file.
  Tables: `loans`, `fraud_cases`, `press_releases`, `signals`. Loans have
  borrower_name/address/city/state/zip, naics_code, jobs_reported,
  current_approval_amount, processing_method ('PPP'=first draw, 'PPS'=second draw).
- Detectors: subclass `detectors/base.py::Detector`, set `detector_id` + `summary`,
  implement `run(con) -> list[Signal]` (READ-ONLY). New detectors → `exploratory_detectors()`.
- REUSE: `detectors/_address.py::normalize_address`, `labels/resolve.py::normalize_name`,
  `payroll_cap.py` per-employee cap constants ($20,833 general; $29,167 for NAICS '72*').
- Composite (in `relief_probe.scoring`) = `MAX(percentile(score)) + 0.5*(n_signals-1)`;
  percentile-normalised per detector (CUME_DIST). Robust stats: `stats.robust_z(x, min_mad=)`.
- `run_all(con, detectors=None)` defaults to `all_detectors()`; pass an explicit list to
  include exploratory detectors for ad-hoc scoring.
- Style: `from __future__ import annotations`, typed, docstrings, ruff line-length 90
  (E,F,I,UP,B). Commit ONE feature per iteration. Mirror tests/test_detectors.py.

## Environment (IMPORTANT — do not regress)

- `uv run pytest` self-provisions via `[dependency-groups] dev`; if a different project's
  venv leaks onto PATH it can mis-resolve — `uv run` uses the project `.venv`.
- `uvx ruff check .` is the lint command (`uv run ruff` is NOT installed).
- The `agent` extra stays OPT-IN; LLM/MCP tests must `pytest.importorskip`.

## Key Files (Loop 1)

- NEW: `src/relief_probe/detectors/_entity.py` (entity_key)
- NEW: `src/relief_probe/detectors/amount_anomaly.py`
- NEW: `src/relief_probe/detectors/multiple_funded_loans.py`
- `src/relief_probe/detectors/registry.py` (add both to exploratory_detectors)
- NEW: `tests/test_entity.py`, `tests/test_amount_anomaly.py`,
  `tests/test_multiple_funded_loans.py`
- `README.md` detector catalog, `NEXT_STEPS.md` (qualitative, no numbers)

## Learnings (append as you go)

- **L1-001 done** (`_entity.py` + `tests/test_entity.py`). `entity_key` = `normalize_name`
  (from `labels/resolve.py`, strips corporate suffixes INC/LLC/THE/AND/OF...) + ` @ ` +
  `normalize_address` (building-level, strips suites). Returns None if either side is
  blank — note "LLC Inc" normalizes to "" (all-suffix name), so it's unkeyable too.
  Key format: `"<NORM NAME> @ <NORM ADDR>"`. Pure function, no warehouse. 79 tests pass.
- **L1-002 done** (`amount_anomaly.py` + `tests/test_amount_anomaly.py`). Per-loan,
  no joins. Two sub-signals, each intensity in [0,1], score = their sum (monotonic):
  (a) round-number — `cents % (divisor*100) == 0` for $10k/$5k/$1k, weighted 1.0/0.66/
  0.33 (work in integer cents to dodge float modulo); (b) cap-maximization — implied
  per-employee (`amount/jobs`, jobs>=1) within `cap_band` (default 5%) BELOW the cap,
  graded 0 at band-floor → 1 at cap. Caps reused from `detectors/payroll_cap.py`
  (`FIRST_DRAW_CAP` 20833.33, `FOOD_ACCOMMODATION_CAP` 29166.67; NAICS prefix '72').
  Strictly-above-cap deliberately scores 0 here (that's payroll_cap_exceedance) — the
  two don't double-count the band. Evidence lists `signals_fired`, divisor, implied
  per-employee + cap. 83 tests pass. NOT yet registered (that's L1-004).
- **L1-003 done** (`multiple_funded_loans.py` + `tests/test_multiple_funded_loans.py`).
  Groups loans by `entity_key` (L1-001), skipping unkeyable (None) entities. Per-entity
  counts loans per `processing_method` (None kept as its own bucket). Excess over the
  one-per-draw allowance: `excess = max(sum_draw(max(0,count-1)), n_loans-2)` — so 2
  same-draw loans OR any 3rd funded loan → excess>=1; legit 1 PPP + 1 PPS → excess 0
  (quiet). Every loan in a flagged entity emits a Signal; score = float(excess),
  monotonic in extra-loan count (total_amount in evidence for triage, NOT scoring).
  Evidence: entity_key, n_loans, excess_loans, per_draw_counts, total_amount,
  loan_numbers (capped 25). DISTINCT from duplicate_address_ring: that finds many
  distinct borrowers at one building; this finds ONE resolved borrower across many
  loans (test_distinct_from_address_ring asserts co-located distinct names stay quiet).
  89 tests pass. NOT yet registered (that's L1-004).
