# Ralph Progress Log

Milestone: Loop 3 — lender_concentration detector (`ralph/loop3-lender-concentration`)
Verify: `uv run --extra vision pytest && uvx ruff check .`

## This milestone (Loop 3)

Build the **lender_concentration** detector (GAO: a few nonbank/fintech auto-approval
lenders originated a disproportionate share of fraud-case loans). DESIGN: UNSUPERVISED +
LABEL-FREE (SIGN-012) — never read fraud_cases. Peer-relative across lenders: per
originating_lender (>= min_loans), compute the RATE of program-rule-suspicious loans
(label-free: amount_per_job >= per-employee cap), robust-z that rate across lenders,
flag every loan from an extreme-tail lender. Signal = "this loan came from a lender
whose whole book is unusually full of cap-busting loans" (catches even individually-clean
loans the per-loan detectors miss). Features L3-001..L3-003 in plans/prd.json.

CRITICAL: register in `registry.exploratory_detectors()`, NOT `all_detectors()` (SIGN-010);
promotion is manual after real-data validation. Never touch the real data/ warehouse
(SIGN-007). No invented numbers (SIGN-008). Label-free (SIGN-012).

## Codebase Patterns

- Detectors: subclass `detectors/base.py::Detector`, `run(con) -> list[Signal]`,
  READ-ONLY, graceful on empty input. New detectors -> `exploratory_detectors()`.
- REUSE: `stats.robust_z(x, min_mad=...)` (median/MAD z), `payroll_cap.py` per-employee
  cap constants ($20,833 general; $29,167 for NAICS prefix '72'). loans has
  `originating_lender`, `servicing_lender_name`, `jobs_reported`, `current_approval_amount`,
  `naics_code`.
- Production composite = naics_cohort_outlier + payroll_cap_exceedance +
  multiple_funded_loans. exploratory_detectors() = duplicate_address_ring, amount_anomaly,
  establishment_overcount (all validated weak/negative), + (this loop) lender_concentration.
- Composite = `MAX(percentile(score)) + 0.5*(n-1)`; `run_all(con, detectors=None)` defaults
  to all_detectors(), pass an explicit list for exploratory.
- Tests seed a tmp_path warehouse via `connect(tmp_path/...)`; for this loop, LEAVE
  fraud_cases EMPTY to prove the detector is label-free.
- Style: `from __future__ import annotations`, typed, docstrings, ruff line-length 90.
  Commit ONE feature per iteration. Mirror tests/test_detectors.py.

## Environment (IMPORTANT — do not regress)

- `uv run pytest` self-provisions via `[dependency-groups] dev`.
- `uvx ruff check .` is the lint command (`uv run ruff` is NOT installed).
- The `agent` extra stays OPT-IN; LLM/MCP tests must `pytest.importorskip`.

## Key Files (Loop 3)

- NEW: `src/relief_probe/detectors/lender_concentration.py`
- `src/relief_probe/detectors/registry.py` (add to exploratory_detectors)
- NEW: `tests/test_lender_concentration.py`
- `README.md`, `NEXT_STEPS.md` (qualitative, no numbers; record EIDL-dropped note)

## Learnings (append as you go)

- L3-001 DONE: `detectors/lender_concentration.py` + `tests/test_lender_concentration.py`
  (6 tests). Design: group usable loans (originating_lender non-null, jobs>=1,
  amount>0) by lender; per-lender suspicious_rate = (# loans with amount_per_job >=
  per-NAICS cap) / loan_count; only lenders with >= min_loans (default 100);
  `stats.robust_z` the rates across lenders with `min_mad` floor; flag EVERY loan of a
  lender with z >= min_z (default 3.0). Score = lender's robust-z (one value per book).
  Reused `payroll_cap.FIRST_DRAW_CAP`/`FOOD_ACCOMMODATION_CAP` for the label-free cap.
- robust_z GOTCHA: raw MAD==0 -> NaN regardless of min_mad. If clean peers all share
  rate 0 the cross-lender MAD degenerates and nothing fires. Test seeds peers with
  *varied* small rates (0, 0.1, 0.2) so MAD>0 and BADBANK (0.9) lands in the tail.
- Label-free proof: tests leave `fraud_cases` EMPTY (connect() creates it) and assert
  the detector still fires (SIGN-012). Detector never queries any label table.
- ruff: `zip(...)` needs `strict=True` (B905). Full verify: 115 passed, ruff clean.
