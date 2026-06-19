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
- KYB-002 DONE (2026-06-19): `kyb/__init__.py` + `kyb/provider.py`. `KybEvidence`
  frozen dataclass (registration_date: dt.date|None, is_non_registered, address_type,
  matched_name, match_confidence, source, raw_ref). `EvidenceProvider` runtime_checkable
  Protocol: `fetch(name, state, *, amount=None) -> KybEvidence | None`.
  - `StubProvider(fixtures)`: keyed by `normalize_name`, ZERO network; unknown -> None.
  - `OpenCorporatesProvider`: `requests` imported LAZILY inside `_fetch_raw` (module
    import is net-free); `_ensure_token()` raises clear RuntimeError mentioning
    OPENCORPORATES_TOKEN (token via ctor `token=` or `config.opencorporates_token()`);
    backoff mirrors doj.py (`time.sleep(2**attempt)`, catch
    `(requests.RequestException, ValueError)`). Tested ONLY via injected `session=`
    (a fake whose `.get` returns canned payloads) — NEVER a real call.
  - Cache: `config.kyb_cache_dir()` = `raw_dir()/kyb`; one `<key>.json` per query
    (`_cache_key` = normalize_name + state, slugified). Cache-by-existence; corrupt
    JSON -> `_load_cache` returns None -> re-fetch (try/except ValueError,OSError),
    never crashes, never treated as empty-registry.
  - Disambiguation (`_select`): precision-first — only registry hits whose
    `normalize_name` == query are candidates; loan state breaks ties via
    `resolve.score_match` (built a `_company_text` blob with the jurisdiction state
    abbr+full-name so the state regex fires). Best returned with its conf; below
    ACCEPT_CONFIDENCE(0.6) it's a low-confidence LEAD, not dropped. ZERO companies ->
    is_non_registered=True (conf NON_REGISTERED_CONFIDENCE=0.5). Companies exist but
    NONE name-match -> None (NOT a false "non-registered" claim — defamation/FP harm).
  - score_match math: "X Trading Co" -> CORP suffix CO stripped -> 2 tokens, base 0.4
    + state 0.25 = 0.65 (>=0.6); no state match -> 0.4 (<0.6). config.py gained
    `kyb_cache_dir()` + `opencorporates_token()`. Tests set RELIEF_PROBE_DATA_DIR to
    tmp (autouse fixture) so the cache writes under tmp_path, never real data/.
- Verify run KYB-002: 189 passed, 6 skipped; ruff clean. ~16s.
- KYB-003 DONE (2026-06-19): `kyb/enrich.py`. `MAX_KYB=50` (free-tier ~50/day cost
  cap, not a benchmark number), `DEFAULT_MAX_CONCURRENCY=4`, `KYB_WEIGHT=0.5`,
  `PPP_ELIGIBILITY_DATE=2020-02-15`.
  - `enrich_top_k(con, provider, *, top_k, max_concurrency=4, cache=None) ->
    {'enriched':[EnrichedLead...], 'telemetry':{...}}`. Pulls
    `composite_ranking(con, limit=k)` (slim `_Lead` rows: loan_number, name, state,
    amount, composite_score), clamps k to MAX_KYB, fans out over a bounded
    ThreadPoolExecutor mirroring LlmJudge (seq when max_concurrency==1 or 1 lead).
    Telemetry: requested, max_kyb, cap_hit (requested>MAX_KYB), n_leads, enriched,
    n_cache_hits, n_errors, quota_exhausted, provider.
  - Quota: provider raises `QuotaExhaustedError` (NEW in provider.py, exported) ->
    worker sets a `threading.Event` stop flag + quota_exhausted=True and returns
    None; workers that haven't started yet see stop and skip cleanly (return None).
    Already-fetched results preserved. Deterministic only at max_concurrency=1
    (the quota test uses it: raise_after=4 -> enriched==4, calls==5).
  - Cache: optional `cache` dict (loan_number -> KybEvidence|None) consulted before
    fetch, written after each SUCCESS. Errors are NOT cached (a re-run may retry).
    Pass the same dict across runs -> second run is offline, n_cache_hits==n_leads,
    provider.fetch not re-invoked. None-unknown IS cached (counts as a hit).
  - Errors: any provider exception (except QuotaExhaustedError) -> n_errors++ and
    the lead still appears with evidence=None (one flaky lookup never aborts batch).
  - `evidence_refinement(evidence) -> (bonus, reason)`, confidence-scaled, GROUNDED:
    is_non_registered -> KYB_WEIGHT*conf; registration_date > Feb-15-2020 ->
    KYB_WEIGHT*conf (eligibility tell); non-commercial address_type (residential/PO
    box/mailbox substrings) -> 0.5*KYB_WEIGHT*conf; None/before-date -> 0.0 (never
    manufacture a signal from absence). `kyb_score = composite_score + bonus`;
    enriched sorted by kyb_score desc (refines the ranking).
  - `synthesize_dossier(lead, evidence, *, model=None)`: model=None -> deterministic
    grounded summary (`_deterministic_dossier`, no key/net, ends "lead for review,
    not proof"); model set -> lazy `langchain_anthropic`, clear RuntimeError on
    missing `agent` extra OR ANTHROPIC_API_KEY (mirrors similarity/explain).
  - tests/test_kyb_enrich.py: seed n loans + 1 signal each (distinct score ->
    clean composite ranking); `_CountingStub` wraps StubProvider, counts calls,
    optional raise_after for quota. Covers refinement units, end-to-end+cap_hit,
    empty warehouse, quota stop-clean, cache-hit-no-refetch, error-telemetered,
    dossier deterministic (no key) + LLM gate (importorskip-conditional).
  - LINT GOTCHAS (fixed): ruff UP031 bans `%`-format even inside f-string
    conditionals -> use nested f-string `{f'${x:,.0f}' if ...}`; keep reason-append
    f-strings wrapped to <=90 cols.
- Verify run KYB-003: 202 passed, 6 skipped; ruff clean. ~19-55s.
- KYB-004 DONE (2026-06-19): CLI `kyb-enrich` + `scripts/validate_business_recency.py`
  + `tests/test_validate_business_recency.py`.
  - `cli.py::kyb_enrich`: `--top-k`(25), `--max-concurrency`(4), `--live/--stub`
    (default --stub = offline StubProvider), `--llm/--no-llm`. --live builds
    `OpenCorporatesProvider()` then calls `provider._ensure_token()` UP FRONT to
    fail fast with the clear OPENCORPORATES_TOKEN RuntimeError (caught -> yellow ->
    Exit(1)) — necessary because `enrich_top_k`'s worker swallows per-lookup
    exceptions into n_errors, so the token gate would never bubble otherwise.
    Uses `connect(read_only=True)`; guards empty `signals` (yellow + Exit(1) like
    triage). Prints telemetry line (provider, enriched, cache hits, n_errors,
    quota_exhausted, cap_hit) + a results Table (rank/loan/name/st/amount/composite/
    kyb+/kyb_score/evidence via `_kyb_evidence_cell`) + leads-not-proof disclaimer.
    --llm narrates the TOP lead's dossier via `synthesize_dossier(top, top.evidence,
    model=llm_model())`, RuntimeError -> yellow -> Exit(1).
  - `scripts/validate_business_recency.py`: READ-ONLY (connect(read_only=True), never
    writes). MIN_AMOUNT=150_000.0, HOLDOUT_YEAR=2023, KS=(50,100,250,500,1000).
    Pure `recency_score(age)` SHARES `RECENCY_TELLS` with the detector (no drift):
    startup=3>new=2>change=1, everything else (existing/unanswered/null/blank)=0.0.
    `rank_slice_by_recency(rows)` sorts (-score, loan_number). main() ranks the
    $150k+ slice, evaluates ONLY on `temporal_label_split(con, HOLDOUT_YEAR)` test
    set ∩ slice, prints CONTRAST (mean percentile + recall@k) vs base rate AND vs
    `composite_ranking(con)` on the SAME held-out labels (skips composite if signals
    empty). `_report` is copied verbatim from validate_ring_graph.py. Honest caveats
    in docstring: coarse 4-level ordinal (huge ties at 0) + recency is an
    ELIGIBILITY tell not a fraud tell (honest NEGATIVE is valid). Benesch/Griffin as
    MOTIVATION only (SIGN-008, no invented numbers).
  - test: importlib `spec_from_file_location` load (proves no-extra import); asserts
    MIN_AMOUNT>=150k, HOLDOUT_YEAR is int; recency_score monotonic + case-insensitive
    + label-free (0 on existing/unanswered/None/blank) + deterministic; rank orders
    by score then id. Never touches the real warehouse (pure helpers only, main() not
    called).
- Verify run KYB-004: 205 passed, 6 skipped; ruff clean. ~22s.
