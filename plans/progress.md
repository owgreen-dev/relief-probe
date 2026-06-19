# Ralph Progress Log

Milestone: Loop 5 — agentic-KYB external-evidence layer
Branch: `m7-tier1-and-m8-ai-followups` (deps live only here, not main)
Verify: `uv run --extra vision --extra graph pytest && uvx ruff check .`

## This milestone (Loop 5)

KYB (know-your-business) EXTERNAL-EVIDENCE — the one untried "bring NEW information" avenue
(AI/ML wins at retrieval/external evidence, not row-wise prediction; 4 honest prediction
negatives this project). TWO TIERS:
- TIER A (free, NO API, validatable NOW): a LABEL-FREE business-recency detector over
  `business_age_description` (100% populated) — fires on "Startup, Loan Funds will Open
  Business" (near-explicit Feb-15-2020 eligibility red flag), "New Business or 2 years or
  less", "Change of Ownership"; does NOT fire on "Existing…" or "Unanswered". Motivation:
  Benesch 53% fabricated/backdated; Griffin et al. (J.Finance 2023).
- TIER B (external, RATE-LIMITED ~50/day, real validation is a MANUAL follow-on): an
  OpenCorporates client (precise registration date / non-registered / address type) over the
  TOP-K composite leads, refining Tier A.

AUTONOMY BOUNDARY: this loop FULLY validates Tier A offline but only builds Tier-B MACHINERY
against a deterministic StubProvider — never claim a Tier-B result; the real OpenCorporates
run is a manual, legally-reviewed follow-on. Features KYB-001..005 in plans/prd.json.

CRITICAL: deterministic-first (NO network/token in tests — StubProvider; OpenCorporatesProvider
lazy + token-gated; LLM behind the `agent` extra; external client tested only via injected
transport). Tier-A detector LABEL-FREE (SIGN-012, empty-fraud_cases proof). EXPLORATORY only
(SIGN-010). Never touch real data/ in tests (SIGN-007). Hard cap (MAX_KYB) + cache + bounded
concurrency + telemetry on external calls. "Unanswered" → NO fire. No invented numbers
(SIGN-008). RESPONSIBLE_USE: FCRA-adjacency for named individuals, defamation/FP harm,
OpenCorporates ToS (share-alike + attribution, no account-creation-to-bypass-gates).

## Codebase Patterns

- Detectors: subclass `detectors/base.py::Detector`, `run(con) -> list[Signal]`, READ-ONLY,
  graceful on empty. New → `exploratory_detectors()` only. Template: `graph/detector.py`.
- REUSE (exact): `config.load_env`/`raw_dir`/`data_dir` (add `kyb_cache_dir()` +
  `opencorporates_token()`); `requests` (CORE — no new extra) + `labels/doj.py::_fetch_page`
  backoff (Session + `time.sleep(2**attempt)`, catch `(requests.RequestException, ValueError)`)
  + `ingest/download.py` cache-by-existence; `triage/judge.py::LlmJudge` (key-gate + lazy
  import + bounded `ThreadPoolExecutor` + retry + `n_errors` + hard-cap) + `triage/core.py`
  (MAX_TRIAGE, select_candidates, rerank, telemetry); `scoring.composite_ranking(con,
  limit=k)`; `labels/resolve.py::normalize_name`/`US_STATES`/`score_match` (disambiguate a
  borrower → the right OpenCorporates record); `agent/graph._synthesize_narrative` +
  `similarity/explain.py` (deterministic_summary + key-gated explain_* — the optional agentic
  narrator); `benchmark/core.py::temporal_label_split`/`ranking_metrics`/`positive_rank_stats`;
  `scripts/validate_ring_graph.py` (read-only harness + its importlib-by-path unit test).
- loans has: business_age_description (VARCHAR, 100%), date_approved (DATE, 100%),
  borrower_name/address/city/state/zip, naics_code, current_approval_amount.
- Style: `from __future__ import annotations`, typed, docstrings, ruff 90. ONE feature/commit.
  Mirror tests/test_ring_detector.py + tests/test_similarity.py (tmp_path seed; stub injection;
  importorskip + monkeypatch for key-gated paths).

## Environment (IMPORTANT — do not regress)

- Verify: `uv run --extra vision --extra graph pytest && uvx ruff check .` (no new extra —
  requests is core). `agent`/`embeddings*` extras stay opt-in (importorskip). External KYB
  client tested ONLY via injected transport — NEVER a real network call in tests.
- Runs on the PR branch `m7-tier1-and-m8-ai-followups` (deps not on main).

## Key Files (Loop 5)

- NEW: `src/relief_probe/detectors/business_recency.py`; `src/relief_probe/kyb/{__init__,
  provider,enrich}.py`; `scripts/validate_business_recency.py`
- NEW tests: `tests/test_business_recency.py`, `tests/test_kyb_provider.py`,
  `tests/test_kyb_enrich.py`, `tests/test_validate_business_recency.py`
- MODIFY: `config.py` (kyb_cache_dir + opencorporates_token), `detectors/registry.py`
  (exploratory), `cli.py` (kyb-enrich), `README.md`, `NEXT_STEPS.md`, `RESPONSIBLE_USE.md`

## Learnings (append as you go)

- KYB-001 DONE (2026-06-19): `detectors/business_recency.py` ::BusinessRecencyDetector,
  detector_id='business_recency'. Ordinal label-free score over
  `business_age_description`: "Startup, Loan Funds will Open Business"=3.0 (Feb-15-2020
  eligibility red flag), "New Business or 2 years or less"=2.0, "Change of Ownership"=1.0.
  NEVER fires on "Existing or more than 2 years old", "Unanswered", null, or blank
  (SQL filters NULL/blank; the RECENCY_TELLS dict simply omits the non-firing values, so
  any unrecognized value is silently quiet — no missing-as-suspicious). Match is
  `.strip().casefold()` against dict keys. Evidence carries business_age_description,
  date_approved, matched_tell, reason.
- Registered in `registry.exploratory_detectors()` ONLY (SIGN-010); all_detectors()
  unchanged; get_detector resolves it; registry module docstring updated.
- Test pattern reused from test_ring_detector.py: tmp_path `warehouse.connect`, executemany
  INSERT, one row per business_age_description value + null/blank edge cases. Label-free
  proof = run on empty fraud_cases and assert exact scores {STARTUP:3,NEW:2,CHANGE:1};
  `fraud_cases` table is created by `connect()` (warehouse/db.py) and starts empty.
- Verify run: `uv run --extra vision --extra graph pytest && uvx ruff check .` →
  181 passed, 6 skipped; ruff clean. ~21s.
