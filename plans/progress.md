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

- H6-001 done: `detectors/_address.py::normalize_address(address, city, state, zip)`
  → canonical building-level key (UPPER, punctuation→space, USPS suffix abbrev,
  unit/suite/#/APT stripped). Key = `street | city | state | zip5`. Returns None for
  blank/sparse street so unkeyable loans are EXCLUDED from ring grouping. Tests in
  tests/test_address.py (equivalence classes, distinct buildings, None cases, purity).
- Ruff line-length is 90 — long test tuples must wrap; collapsed a 96-char `for` into
  a multi-line `variants` tuple to satisfy E501.
- H6-002 done: `detectors/duplicate_address_ring.py::DuplicateAddressRingDetector`
  (`detector_id='duplicate_address_ring'`, `__init__(min_ring_size=3)`). run() reads
  all loans, keys each via `normalize_address`, buckets by key, and a RING = key with
  >= min_ring_size DISTINCT borrower_names (a set of names, so one borrower with many
  loans is NOT a ring; None keys excluded). Every loan in a ring emits a Signal with
  the SAME score = `log1p(ring_size) + log1p(total_ring_amount)` (monotonic in both,
  comparable within-detector). Evidence: normalized_address, ring_size (distinct
  borrowers), n_loans, total_ring_amount, borrower_names_sample (capped at 10, sorted).
  Read-only. Tests in tests/test_ring_detector.py (ring fires across formats, solo
  borrower no-fire, below-threshold no-fire, unkeyable excluded, score monotonicity).
  Used pandas-free pure-Python bucketing (collections.defaultdict) — simplest here.
- H6-003 done: registered `DuplicateAddressRingDetector()` in
  `detectors/registry.py::all_detectors()` (3rd entry) and refreshed its module
  docstring "Live" list. NO runner/scoring/benchmark changes — they iterate
  `all_detectors()` generically, so the 3rd detector "just works". Proved it in
  tests/test_ring_detector.py with a seeded warehouse (`_INSERT_FULL` adds
  naics_code/jobs_reported so a loan can trip both a $/job detector AND the ring):
  a 3-borrower ring whose ringleader also claims $200k/job trips ALL THREE
  detectors → composite n_signals==3 with both $/job + ring detector_ids; the
  other ring members trip the ring alone (n_signals==1), so the ringleader's
  corroboration bonus ranks it strictly higher. Tests: registration present,
  run_all keys 'duplicate_address_ring'==3, composite corroboration.
  NOTE: cohort outlier needs min_cohort_size>=30 — seed plants 40 normal peers
  (722511|TX) so the planted outlier has a real cohort (mirrors test_detectors.py).
- H6-004 done: synthetic INDEPENDENCE proof + overlap helper.
  * Added `benchmark/core.py::detector_overlap(a, b)` (pure set math →
    n_a/n_b/intersection/union/jaccard; jaccard=0.0 when union empty) and
    `detector_flagged_loans(con, detector_id)` (distinct loan_numbers from the
    `signals` table). Both exported from `benchmark/__init__.py`.
  * tests/test_ring_detector.py: `_seed_normal_dollar_ring` plants 3 distinct
    borrowers at one building whose $/job ($10k, jobs=10) sits MID-cohort
    (722511|TX, 40 normal peers ~$9k-$11.9k/job) and far below the NAICS-72
    payroll cap. Asserts ring detector flags them while naics_cohort_outlier and
    payroll_cap_exceedance do NOT — orthogonal signal. Plus a pure unit test of
    detector_overlap and a seeded test showing ring∩naics Jaccard == 0.0.
  * Key: to be invisible to $/job detectors the ring loans must (a) land inside
    their NAICSxstate cohort's median (robust_z~0 → not BH-flagged) and (b) be
    below payroll_cap min_ratio (1.5x of $29,167 for NAICS 72). $10k/job clears
    both. Real-data overlap/lift numbers remain a MANUAL post-loop step (no
    invented numbers per SIGN-008).
- H6-005 done (docs only, qualitative — zero invented numbers per SIGN-008):
  * README: added a `duplicate_address_ring` entry to the Status detector catalog +
    a dedicated "Independent corroboration" bullet framing it as the orthogonal
    link-analysis signal that answers the "corroboration is two views of the same
    $/job ratio" critique; states FP modes (shared offices, strip malls, registered-
    agent addresses) honestly.
  * NEXT_STEPS.md: marked H6 ✅ done with what shipped; added an explicit MANUAL
    post-loop note to run `relief-probe score` + `benchmark` on the real warehouse
    and record real overlap (detector_overlap Jaccard) + lift numbers there. Updated
    the M2.1 planned list (duplicate_identity → shipped as duplicate_address_ring)
    and the In-progress/Next-up section (next = H4 label precision).
  * RESPONSIBLE_USE.md: added a "Shared-address rings name real people and addresses"
    bullet — co-located borrowers are a review lead, not proof of fraud/coordination.
  * All H6 features (H6-001..H6-005) now pass; verify = 73 tests + ruff clean.
