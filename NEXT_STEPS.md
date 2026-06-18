# Next steps

Prioritized backlog for relief-probe. Status as of the initial scaffold.

## Done (M0 + M1)

- **Repo + packaging** (`pyproject.toml`, src layout, ruff config).
- **Warehouse** (`warehouse/db.py`): `loans` (one row per PPP loan, 42 typed columns
  mapped from the 53-col FOIA CSV), `fraud_cases` (PU labels), `signals` (output contract).
- **PPP ingest** (`ingest/`): live CKAN URL resolver (no hardcoded links) →
  streaming download (cached) → `INSERT OR IGNORE` column-mapped load. Slices:
  `150k_plus` (~1M loans), `under_150k`, `all` (~11.5M).
- **CLI**: `relief-probe ingest --slice …`, `relief-probe info`.
- **Tests**: offline loader tests (real header, type/round-trip, idempotency) — 2 passing.
- Verified the live resolver returns real URLs (1 file / 13 files).

## M2 — loan-level detectors ✅ (done)

Two complementary detectors over public loan fields, reusing `stats.py` + `_cohort.py`:
- `naics_cohort_outlier` — loan $ per reported job, robust-scored (log1p median/MAD,
  BH-FDR) within NAICS×state cohorts. The flagship (relative signal). 4,350 signals.
- `payroll_cap_exceedance` — loan $/job above the program's per-employee payroll
  ceiling ($20,833; $29,167 for NAICS 72) by ≥1.5×. Absolute, program-rule signal.
  14,431 signals.

Plus `detectors/runner.py` (run all → persist `signals`), `scoring.py`
(`max(score) + 0.5·(n−1)` composite), and `relief-probe score`. On the real 965k-loan
warehouse the top leads are all $2M–$6.5M loans claiming **1 job** — the textbook
pattern. Both detectors corroborate (n=2) on most.

`duplicate_address_ring` (shared-address / link-analysis ring signal) built in **H6** but
kept OUT of the default composite — independent yet no validated lift (see H6 below).
Still planned (M2.1): `proceeds_anomaly` (payroll-proceed share vs jobs/term),
`lender_concentration`.

### Loop 1 — research-driven detectors ✅ (built, validated, dispositioned)

Two new public-data detectors targeting patterns *different* from dollars-per-job, built
exploratory then validated on the real ~11.3M-loan warehouse against the DOJ labels:
- **`multiple_funded_loans`** (`detectors/multiple_funded_loans.py`) — entity resolution
  (normalized name + building-level address, `detectors/_entity.py::entity_key`) →
  borrowers exceeding the one-per-draw rule (≥2 same-draw loans or >2 funded total).
  Motivation: GAO finding of tens of thousands of multiply-funded recipients.
  **VALIDATED + PROMOTED** to `all_detectors()`: selective (~0.1% of loans), genuine
  independent lift (≈18× @500, ≈21× @1000; Jaccard <0.01 vs the $/job detectors). In the
  composite it lifted the top (k=100/250) and recall@5000 (14→17 hits) without dilution.
- **`amount_anomaly`** (`detectors/amount_anomaly.py`) — per-loan round-number +
  payroll-cap-maximization ("bunching") tells of a fabricated/reverse-engineered amount.
  Motivation: Griffin et al. round-number / cap-bunching forensic literature.
  **VALIDATED WEAK, stays exploratory**: flags ~13% of the slice with ~0 lift through
  k=1000 (like the ring detector). Kept in `exploratory_detectors()` for investigation.

The H6 discipline worked: build candidates, validate on real labels, promote only what
earns it. Next batch is **Loop 2** (Census ZBP overcount), which needs a new public-data
ingest.

### Loop 2 — Census establishment-overcount detector ✅ (built + validated; kept exploratory)

A new public-data join + detector targeting loan **density**, orthogonal to the
dollars-per-job ratio: where far more PPP loans were made in a `(ZIP × NAICS)` cell than
there are real businesses to receive them.
- **`establishments` table + ZBP loader** (`warehouse/db.py` SCHEMA_SQL,
  `ingest/establishments.py::load_zbp_csv`, source in `ingest/sources.py`): establishment
  counts by ZIP × NAICS from **Census ZIP Business Patterns**, joined directly on
  `loans.borrower_zip` (no zip→county crosswalk). Loader is path-based + schema-tolerant
  (`all_varchar` + `TRY_CAST` + `INSERT OR IGNORE`); `normalize_names` lowercases the
  case-varying ZBP headers.
- **`establishment_overcount`** (`detectors/establishment_overcount.py`) — flags every
  loan in a cell where `ppp_loan_count / max(establishments, 1) >= min_ratio` (default
  4×), score `log(ratio)`. NAICS rollup configurable (`naics_digits`, default 6).
  Motivation: **Griffin, Kruger & Mahajan (J. Finance 2023)** — ~19% of first-draw loans
  (≈36% fintech) "excess" vs local establishment counts. Cells with no ZBP row are
  skipped (absent ≠ zero); empty/missing table → `[]`; read-only.
- **CLI** `relief-probe ingest-establishments PATH` (`cli.py`) — one documented command to
  load a manually-downloaded local ZBP CSV; it does NOT download.
- **Validated on the real warehouse (June 2026):** ingested Census **ZBP 2018** detail
  (`zbp18detail`, 2.87M ZIP×NAICS rows) via `ingest-establishments`. Two findings:
  1. **ZIP+4 join bug (fixed):** `loans.borrower_zip` mixes 5-digit and ZIP+4; ZBP keys
     on 5-digit. The detector now truncates to the first 5 digits — coverage went 1.2M →
     5.1M matched loans. (Test: `test_zip_plus_four_loans_match_five_digit_establishments`.)
  2. **Verdict — kept exploratory.** Weak *standalone* independent lift (~18×@500, recall
     ~1.8%@5000; Jaccard <0.01 vs the other detectors), but promoting it into the
     composite changed nothing (4-detector benchmark: +1 hit @2000, identical at every
     other k, recall@5000 unchanged at 5.2%) — the prosecuted loans it catches are
     already caught. So it stays in `registry.exploratory_detectors()` (SIGN-010);
     `all_detectors()` remains the 3 validated detectors. An honest marginal result.

### Loop 3 — lender_concentration detector ✅ (built + validated zero-lift; kept exploratory)

A new **unsupervised, peer-relative, label-free** detector targeting a *lender-level*
pattern the per-loan detectors miss.
- **`lender_concentration`** (`detectors/lender_concentration.py`) — groups loans by
  `originating_lender` (only lenders with `>= min_loans`, default 100), computes each
  lender's **rate** of cap-busting loans (label-free program-rule predicate:
  `amount / jobs_reported >= the per-employee ceiling`, reusing `payroll_cap` constants
  $20,833 / $29,167-for-NAICS-72), robust-z's that rate **across lenders**
  (`stats.robust_z`, `min_mad` floor), and flags **every** loan from a lender whose
  z `>= min_z` (default 3.0). Score = the lender's z (one anomaly value per book). It
  fires even on the individually-clean loans of a structurally-bad book. Motivation:
  **GAO** finding that a handful of nonbank/fintech auto-approval lenders originated a
  disproportionate share of fraud-case loans. Keys on *rate*, not volume (big banks have
  legitimately huge books). Registered in `registry.exploratory_detectors()` only
  (`all_detectors()` UNCHANGED — SIGN-010); read-only.
- **Deliberately LABEL-FREE (SIGN-012):** never reads `fraud_cases` / any label table —
  training on the prosecution labels would leak the answer and inherit prosecution bias,
  so the benchmark must stay an independent validator. Proven by a test that fires on a
  warehouse with an **empty** `fraud_cases` table.
- **EIDL↔PPP jobs-mismatch idea was DROPPED** (do not re-attempt): the public COVID-EIDL
  disclosure release is in **DATA Act / USAspending format** and carries **no
  per-loan jobs or NAICS field**, so the cross-program mismatch detector can't be built
  from public data at the granularity needed to join. `lender_concentration` replaced it.
- **Real-data verdict (validated):** **zero lift** — 0 prosecuted hits in the top 5,000
  at min_z 3 or 5 (flags ~3% of the slice / 324 lenders; Jaccard 0.02 vs payroll, 0.001
  vs multiple_funded — independent but uncorrelated with the labels). The high-cap-busting
  lenders aren't where the prosecuted fraud sits (likely the documented industry-mix FP:
  NAICS-72-heavy books bust the per-employee cap legitimately). **Kept exploratory** (it
  was already registered there — no promotion). Mirrors the H6 / `establishment_overcount`
  discipline: built, validated, honest negative.

## M3 — label construction ✅ (done, the differentiator)

**Scraper ✅ done** (`labels/doj.py`, `relief-probe fetch-labels`): pages the DOJ
press-release JSON API by publication date (newest-first), keeps SBA-loan-fraud
releases (COVID topic OR PPP/EIDL keyword), extracts the scheme amount + program,
and stages them in `press_releases`. Robust to the API's stray old-dated records
(whole-page date stop), retries transient errors, stores incrementally per page
(idempotent on a url hash). Offline-tested (parse/amount/program/idempotency).

**Entity resolution ✅ done + precision-tuned on real data** (`labels/resolve.py`,
`relief-probe resolve-labels`): n-gram name match against a loan-name index, accepted
only with **amount corroboration** (loan's dollar figure in the release, exact or ~);
descriptive words kept (only legal entity types stripped); boilerplate stoplist. Hand-
checking the naive version (which was ~88% false positives) drove each rule. Result:
**325 high-precision labeled loans** from 3,414 staged releases (2020-02→2026-06).
Limitations (documented): misses person-name sole-props, DBA/misspellings (no fuzzy
edit distance yet); precision-first by design (false labels poison the benchmark).
Optional later: SBA-OIG records as a second `source`; person-name + fuzzy matching.

Context: there is NO public per-loan label list (the 2026 "562K referred loans" are
flagged-not-charged and not downloadable), so this self-built, prosecution-biased PU
label set is the only path — hence recall-on-known-fraud framing in M4.

## M4 — PU forward benchmark ✅ (done)

`benchmark/core.py` + `relief-probe benchmark`: rank loans by composite score, report
precision@k / lift / recall@k vs resolved `fraud_cases`, with per-detector ablation.
**Real result** (965,122 loans, 325 prosecuted labels, base 0.034%): **lift@100 29.7×,
@500 23.8×, @1000 14.8×** — comparable to probity's Medicare ~32×@500, with only 2
detectors. Recall low (3.4% @5000) — PU + only 2 detectors + labels on the 150k+ slice.
Ablation: payroll_cap drives the very top; naics_cohort peaks ~35×@250.

Still planned (M4.1): optional learned PU scorer vs the transparent baseline on the
same split (`ml` extra); ingest the `under_150k`/`all` slices to broaden recall.

## M6 — document-authenticity vision tab ✅ (done)

`vision/` (`vision` extra) + `app/dashboard.py` Streamlit tab. **Error Level Analysis**
features (`ela.py`) → scikit-learn classifier (`model.py`), CPU-friendly, no GPU/large
download. `datasets.py` ships a deterministic synthetic clean/spliced generator (so the
pipeline + tests run offline) and resolvers/notes for the real anchors (Find-it-again
direct zip; IDNet CC0 ~400 GB). CLI `vision-demo` / `vision-score`. Synthetic CV
accuracy >0.9; dashboard verified end-to-end (uploaded a spliced doc → ELA heatmap +
P(forged)=100%). Honest gap stated: no public fake-paystub/bank-statement dataset
exists, so financial-doc tamper is shown on synthesized edits, not leaked fakes.

Dashboard (`app/dashboard.py`, `viz`+`vision` extras): two tabs — **Loan leads**
(composite ranking + counts) and **Document authenticity** (upload → ELA + score).
Run: `uv run --extra viz --extra vision streamlit run app/dashboard.py`.

## M5 — agent + MCP (`agent/`) ✅ (done)

Tool-grounded, **deterministic-first** loan investigator (profile, signals, peer
comparison, fraud-case check, composite) → structured, evidence-cited report
(`agent/tools.py`, `agent/report.py`, `agent/graph.py`, `relief-probe
investigate`). The default path is pure Python and fully tested without the
`agent` extra; `--llm` only rewrites the summary prose from the same cited facts.
The same four read-only tools (`score_loan`, `peer_compare`, `check_fraud_case`,
`investigate`) are exposed over MCP (`agent/mcp_server.py`, `relief-probe
serve-mcp`); `mcp`/LLM deps are imported lazily so the core env stays green.

## M7 — cost-aware LLM triage cascade (Tier 1 ✅ built — see docs/M7_PLAN.md)

**Full grounded plan + cost estimate in [docs/M7_PLAN.md](docs/M7_PLAN.md).** Decided:
build **H4 first** (the hand-labeled sample is the Tier-1 judge's calibration set), then
**M7 Tier 1 only** (Haiku 4.5 plausibility scorer + `triage` CLI + validation gate).
Established LLM-cascade pattern (FrugalGPT); Batch API (50% off) + prompt caching +
structured outputs; ~$2–4 per run vs ~$8–16k to run the LLM over all 11.3M loans.

### Tier 1 — semantic plausibility scorer ✅ (built; deterministic-first + key-gated)

New `triage/` package + `relief-probe triage --top-k N [--llm] [--gate]`. The cascade:
Tier 0 (the composite) ranks all loans for free → escalate only the **top-k** leads to a
plausibility judge ("could this business plausibly justify this loan?" over
`borrower_name × NAICS × amount × jobs × payroll_proceed`) → blend the judge's 0–3
implausibility into a transparent re-rank (`composite + 0.5·(implausibility/3)`).
- **Two judges behind one `Judge` shape** (`triage/judge.py`): `heuristic_judge`
  (deterministic, offline, no extra — structured-field tells: $/job vs the per-employee
  cap, single-job mega-loans, round-number amounts; it is the **baseline**, a near-
  restatement of $/job) and `LlmJudge` (Haiku 4.5, structured output via a strict 0–3
  JSON schema + rubric + few-shot, CoT-before-score). `langchain_anthropic` imported
  lazily; missing extra/`ANTHROPIC_API_KEY` → clear error. So the whole pipeline
  (select → judge → re-rank → gate) builds + tests with **no key**.
- **Hard cap (`MAX_TRIAGE = 2000`, `triage/core.py`)** bounds how many loans ever reach
  the LLM regardless of `--top-k`; the cap-hit + judged count are logged every run — cost
  is bounded and visible. NEVER runs the LLM over the full population (Tier 0 does the cut).
- **Robust + concurrent LLM path** (hardened during the first real run): `LlmJudge` judges
  over a bounded `ThreadPoolExecutor` (`--concurrency`, default 8 — 300 loans in ~3.5 min
  vs ~15+ min sequential), coerces malformed structured output (Haiku occasionally leaks
  tool-call markup into the integer field), and retries-then-falls-back per loan so one bad
  cell never aborts a batch (`n_errors` telemetry). The gate **reuses the judged head**
  (`reranked_head`) so it never re-judges / double-spends.
- **Validation gate (`triage/gate.py`, `--gate`)** — same discipline as every detector:
  compares composite-only vs triage-reranked lift@k on the resolved labels / $150k+ slice
  (only k ≤ top_k, since re-ranking the head can't move lift beyond it) and prints
  `improved`/`neutral`/`regressed`. 18 tests; full suite green, ruff clean.

### Tier-1 real-data verdict (June 2026): honest NEGATIVE — built, opt-in, NOT promoted

Ran `triage --top-k 300 --llm --gate` (Haiku 4.5) on the real 11.3M warehouse / 325 labels
(`data/triage_runs/`). **Result: no lift — gate `regressed` by exactly one loan.**

| k | composite lift | triage (Haiku) lift | hits |
| --- | --- | --- | --- |
| 25 | 356.4× | 237.6× | **3 → 2** |
| 50 | 178.2× | 118.8× | **3 → 2** |
| 100 | 89.1× | 89.1× | 3 → 3 |
| 250 | 35.6× | 35.6× | 3 → 3 |

The whole "regression" is **one prosecuted loan** dropping out of the top-25/50 (3→2 hits),
zero change at k≥100 — a single-loan swing, i.e. within the H3 bootstrap noise (top-k rests
on 1–3 loans). Honest read: **the semantic-plausibility re-rank does not concentrate the
prosecuted labels better than the composite, and perturbs the very top within noise.** Why:
the composite already nails the top (3/25 prosecuted), the LLM marks *many* loans
`egregious` so the uniform `+0.5·(implausibility/3)` bonus can't discriminate, and some
prosecuted loans look *plausible* to the LLM (coherent name/industry/scale) so they slip.
The judge's calls themselves look sensible (egregious on $X-million single-job loans, an
"L SQUARE HAIR CO" personal-care shop at $377k/1-job) — the signal just isn't aligned with
*what got prosecuted*. Two caveats both ways: PU labels can't reward fraud the DOJ never
charged (the LLM may flag *different* real fraud), and the blend is coarse. **Disposition:
kept built + opt-in (`triage` CLI), NOT promoted into any default ranking** — mirrors
`duplicate_address_ring` / `establishment_overcount` / `lender_concentration`.

- **Next up:** the productive follow-ups now that the re-ranker is a measured negative —
  (a) **Tier 2** (DOJ press-release corroboration, which *also* improves label quality, H4)
  rather than blind plausibility; (b) reframe Tier 1 as an **explanation/triage-narrowing**
  aid (cheap human-readable "why this looks off" on the top leads) instead of a re-ranker,
  where being label-aligned isn't required; (c) **H7 temporal holdout** before any
  label-aware tuning. No promotion of Tier 1 until something earns lift.

Cheap deterministic triage narrows millions of loans to hundreds, then the LLM runs
**only on that subset** — the right way to use an expensive model at scale.

```
score (all loans) → top-k → LLM plausibility tier (Haiku 4.5) → re-rank/flag
   → deep investigate (Opus 4.8) on the top survivors → enriched report
```

- **Tier 1 — semantic plausibility (the novel signal).** Feed the LLM
  `borrower_name × NAICS × amount × jobs` and ask whether the business could plausibly
  justify the loan. World-knowledge catches mismatches pure stats can't reason about
  ("'Elite Nail Spa LLC', 1 employee, $2.1M, NAICS=landscaping"). Start here.
- **Tier 2 — press-release corroboration.** LLM reads the matched/nearby DOJ text for a
  flagged loan and assesses whether it truly corroborates (also lifts label quality, H4).
- **Tier 3 — narrative synthesis.** Already built (`investigate --llm`).
- **Tier 4 — LLM-assisted entity resolution.** Rule-based blocking → LLM adjudicates the
  ambiguous candidates (person names, DBA, fuzzy). Improves label recall/precision.

**Model cascade for cost:** Haiku 4.5 over top-1000 (volume), Opus 4.8 on the top ~25
(depth). Top-500 × a few cents ≈ a few dollars; NEVER run the LLM over the full
population.

**Constraints (mirror M5):** deterministic-first + key-gated — builds and tests WITHOUT
`ANTHROPIC_API_KEY` (mock/skip the LLM calls via `importorskip`/monkeypatch); LLM behind
the `agent` extra + a `--llm`/`triage` flag. **Hard cap** on how many loans hit the LLM,
logged, so cost is bounded and visible. New CLI `relief-probe triage --top-k N [--llm]`.

**Honest scope:** the "LLM reads the application form" idea needs forms — PPP supporting
docs are NOT public, so this cascade runs on structured fields + press-release TEXT, not
documents. (Forms — synthetic or in a real-work context — would slot into the vision tab
+ an LLM-OCR step later.)

## M8 — AI research follow-ups (built after the Tier-1 null; see docs/LLM_RESEARCH.md)

Five parallel research agents diagnosed *why* the Tier-1 LLM-judge null was
over-determined (re-judged fields the composite already had; pointwise scoring
saturates; additive blend of uncalibrated scores; lift@k is unreliable on PU labels)
and where AI genuinely adds signal (text semantics; external evidence; more labels).
Built the three the user picked (1, 3, 4 — skipped the agentic-KYB agent):

### Phase 1 — PU-honest metrics + RRF primitive ✅
- `benchmark/core.py::positive_rank_stats` + CLI: replaces the misleading lift@k headline
  with a **two-part PU-honest summary** — *concentration* (mean percentile of flagged
  positives within the flagged list; ~0.5 = random) and *coverage* (fraction flagged at
  all). **Real data:** the 28 flagged positives concentrate at mean percentile **0.309**
  (better than random), but **only 28/325 (9%) are flagged at all** — the recall ceiling
  lift@k hid. (arXiv 2509.24228: on PU labels recall/rank are estimable, lift is not.)
- `reciprocal_rank_fusion` (Cormack 2009): the correct rank-fusion primitive (vs the
  additive blend that sank Tier 1), ready for any future reranker.

### Phase 2 — name↔NAICS embedding-mismatch detector ✅ (the honest redo of Tier 1)
- `detectors/naics_mismatch.py` + `embeddings.py`: embeds each borrower name and every
  candidate NAICS industry title, scores the declared industry's **normalized mismatch
  gap** (continuous, tie-robust — not a saturated 0-3). Targets the *text* the composite
  never reads. Registered EXPLORATORY (SIGN-010). Default offline `HashingEmbedder` is a
  lexical proxy (proves the machinery); the real semantic signal needs the `embeddings`
  extra (a local sentence-transformer) — **real-data validation pending that model**.
  Bundled canonical 2-digit NAICS sector titles; finer titles via `ingest-naics PATH`.

### Phase 3 — LLM-adjudicated entity resolution ✅ (grows the labels; validated on real data)
- `labels/llm_resolve.py` + `relief-probe resolve-labels-llm`: **block by amount** (the
  external corroboration gate — find loans whose exact amount appears in a release the
  precise resolver missed) → **LLM adjudicates only the NAME** (DBA / a.k.a. / misspelling
  / person-name sole-prop) → accept on match AND confidence. ADDITIVE + marked
  `amount+llm` (never overwrites exact labels; reversible; a purist benchmark can exclude
  them). Deterministic-first/key-gated + concurrent/robust (mirrors the triage `LlmJudge`).
- **Real-data verdict (validated):** from **400** amount-blocked candidates (cap), Haiku
  recovered **7 new labels in ~2.8 min** (325 → **332** distinct, +2.2%) — exactly the
  fuzzy categories the exact resolver can't reach: *Exotica Beauty Bar → "Exotica Beauty
  LLC"* (DBA), *AWE Watersports LLC → "Thomas Aaron Signorelli"* (person sole-prop), VSoft
  / Alpha Health punctuation+whitespace variants. Every match amount-gated, conf ≥ 0.85.
  A real recall win on the binding constraint from a capped slice; a full sweep would find
  more. (To revert: `DELETE FROM fraud_cases WHERE match_method='amount+llm'`.)

+23 tests across the three phases (suite now 155); ruff clean.

**Next AI follow-ups (deferred, not built):** the agentic-KYB evidence agent (option 🅑 —
SoS registration-date / address-type / footprint, the Griffin et al. indicators); run the
embedding detector with the real semantic model + validate lift; sweep `resolve-labels-llm`
past the 400-cap for more recall; PU-bagging learned scorer consuming these features.

## Hardening / rigor backlog (post-M6, from the objective self-review)

The build is complete and above-median on breadth + engineering + honesty, but the
*analytical* claim is fragile. Objective findings to fix, in priority order:

- **H1 — Baseline comparison (credibility gap #1).** The "lift" rests on single-digit
  hits AND the cohort-z+FDR machinery barely beats a one-line `ORDER BY amount/jobs
  DESC`. Measured on the 150k+ slice (325 labels, base 0.034%):
  - composite (detectors+FDR+z): lift@100 29.7× (1 hit), @500 23.8× (4), @1000 14.8× (5)
  - trivial (sort by $/job):     lift@100 29.7× (1), @500 11.9× (2), @1000 14.8× (5)
  - dumbest (sort by loan $):    lift@100 0×, @500 0×, @1000 5.9× (2)
  So $/job is the real signal (beats raw amount), but the fancy stats add little.
  **Fix:** build baseline rankings into `benchmark` + CLI, and put the honest
  comparison in the README — showing you stress-tested your own method is the senior move.
- **H2 — Ingest `under_150k`/`all` (~8 GB).** ✅ Done — warehouse is now the full
  **11,365,188** loans. Two findings, both honest negatives:
  1. **No new labels.** All **325** distinct prosecuted loans still fall in the $150k+
     slice (0 under $150k) — prosecutions concentrate in large loans, so 10× more
     haystack added zero needles. Hit counts stayed single-digit; H3 (bootstrap CIs)
     is the real fix for noisy lift, not more loans.
  2. **It broke + then fixed the composite.** Re-scoring on the dense under-$150k
     cohorts exposed two bugs (now fixed): (a) near-zero-MAD cohorts produced absurd
     ~38,950-σ z-scores → added a `min_mad` floor in `stats.robust_z`; (b) the
     composite combined *raw* incomparable detector scales (naics z up to ~39k vs
     payroll ratio ≤313), so naics swamped everything → composite now percentile-
     normalises per detector (`CUME_DIST`) before `max + corroboration`. Composite
     lift recovered from **0× → 29.7×@100 / 23.8×@500** on the slice.
  Because lift over the full 11.3M is denominator-inflated (same hits, 10× base-rate
  drop), `benchmark` now defaults to the labelable **$150k+ slice** and reports
  full-population recall separately (`--full-population` to override).
- **H3 — Bootstrap CIs on lift@k.** ✅ Done. `bootstrap_lift_cis` (2,000-resample
  Poisson bootstrap) in `benchmark/core.py`; `benchmark` prints a "lift 95% CI" column.
  Result confirmed the worry: **@100 lift 29.7× has a 0.0–89.1× CI** (rests on one loan,
  includes zero), while **k≥500 CIs clear 1×** (@500 5.9–47.7×) — so the real signal is
  the @500–5000 band, not the headline @100. README updated to say so.
- **H4 — Measure label precision** ✅ Done — hand-adjudicated a stratified 51-row sample:
  **~84–88% precision** (95% CI ≈ [72%, 92%]); FPs concentrate in the weaker `~`/no-state
  tiers, exact `name+state+amount` is ~93%+ clean. Doubles as the M7 judge calibration set.
  See [docs/LABEL_PRECISION.md](docs/LABEL_PRECISION.md).
- **H5 — Vision honesty.** ELA hits ~100% on *engineered* synthetic splices → proves
  plumbing, not document-fraud detection. Either run on real IDNet/Find-it-again, or
  label the tab explicitly as a synthetic plumbing demo. Don't let "100%" stand naked.
- **H6 — One genuinely independent detector** ✅ Done (code + synthetic independence
  test). Added `duplicate_address_ring` (`detectors/duplicate_address_ring.py`): a
  link-analysis / co-location signal that keys each loan to a normalized building-level
  address (`detectors/_address.py::normalize_address`) and flags addresses shared by
  ≥3 **distinct** borrowers, scored monotonically in ring size + total ring dollars.
  Registered in `registry.py`; the generic composite picks it up so a loan tripping the
  ring AND a $/job detector now shows corroboration across **independent** views.
  Orthogonality is proven on synthetic data and **confirmed on real data**
  (`detector_overlap` Jaccard ≈ 0.019 vs payroll, ≈ 0.0015 vs naics).
  **Real-data verdict (run on the full 11.3M warehouse): a validated NEGATIVE.** The
  ring detector flags ~27% of the $150k+ slice (263k loans) and prosecuted loans sit in
  rings at essentially the base rate at *every* ring-size threshold (lift ≈ 0.6–1.0× at
  min_ring 3/5/8/12/20; ≈0 beyond) — address clustering is dominated by legitimate
  co-location and the prosecuted labels are large-dollar single-borrower $/job schemes.
  Including it only diluted the composite mid-tail (lift@2000 11.9×→5.9×). **Decision:
  moved out of the default composite** — `all_detectors()` is back to the two validated
  $/job detectors; the ring detector lives in `registry.exploratory_detectors()` (kept,
  tested, resolvable by id, opt-in via `run_all(con, detectors=...)`) for investigation.
  An honest independent-signal-that-doesn't-move-the-needle result, documented in README.
- **H7 — Temporal holdout for any label-aware step.** Detectors are currently
  unsupervised, so no split is needed *yet* (and splitting 325 labels → single-digit
  top-k hits would be uselessly noisy). But **before** tuning thresholds to lift or
  training the M4.1 PU scorer, add `benchmark --holdout-year Y`: develop on prosecutions
  charged ≤ Y, validate on those charged > Y (charge dates span 2020–2026). A temporal
  holdout is more defensible for fraud than a random split and mirrors deployment;
  mandatory the moment anything is fit to labels. Also: freeze the resolver (tuned for
  match precision, not lift) before any detector tuning.

Also still open: M4.1 learned PU scorer (`ml` extra); real vision data + CNN vs ELA.

### In progress
- **Done this session:** H2 full ingest (11.3M loans) + composite hardening
  (`min_mad` floor, percentile-normalised composite, slice-aware `benchmark`) + **H3
  bootstrap CIs on lift@k**. README headline regenerated on the real full data.
- **Done this session:** H6 — built the genuinely independent `duplicate_address_ring`
  detector, then validated it on real data: it's orthogonal (Jaccard ≈ 0.02) but has
  **no lift** against the prosecuted labels at any threshold, so it was **dropped from
  the default composite** (kept as an exploratory detector). Honest negative result.
- **Done this session:** **M7 Tier 1** built AND validated on real data — `triage/` package
  (heuristic + concurrent/robust Haiku `Judge`s, hard cap, transparent re-rank) +
  `relief-probe triage --top-k N [--llm] [--gate] [--concurrency C]` + validation gate, all
  deterministic-first/key-gated (18 tests; suite green). Real `--llm --gate` run on the
  11.3M warehouse / 325 labels: **honest NEGATIVE** (no lift; gate regressed by one loan) —
  kept built + opt-in, NOT promoted. See the Tier-1 real-data verdict above.
- **Next up:** Tier 2 (press-release corroboration) or reframe Tier 1 as an explanation aid;
  then H7 temporal holdout before any label-aware tuning. No promotion until something
  earns measurable lift.

## Watch-outs

- **PU labels are biased toward caught fraud** — never report a "fraud rate"; report
  recall-on-known-fraud. See RESPONSIBLE_USE.md.
- **Public data names real borrowers** — SBA already published these; still, frame
  every example as a lead, and prefer already-charged (public-record) cases for any
  named example.
- **`all` slice is ~8 GB** — `150k_plus` is the fast default for iteration.
