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

Still planned (M2.1): `proceeds_anomaly` (payroll-proceed share vs jobs/term),
`duplicate_identity` (shared address/borrower ring signal), `lender_concentration`.

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

## M7 — cost-aware LLM triage cascade (planned; run after H2 lands)

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
- **H2 — Ingest `under_150k`/`all` (~8 GB).** 10× more loans → more labels → hit counts
  in the dozens, so lift stops resting on one loan. Highest-leverage rigor fix.
  (After ingest: re-run `score` + `resolve-labels` + `benchmark` on the full set.)
- **H3 — Bootstrap CIs on lift@k.** Stop quoting point estimates on n=1–8 hits.
- **H4 — Measure label precision** on a ~50-row hand-labeled sample → report a number,
  not "high-precision."
- **H5 — Vision honesty.** ELA hits ~100% on *engineered* synthetic splices → proves
  plumbing, not document-fraud detection. Either run on real IDNet/Find-it-again, or
  label the tab explicitly as a synthetic plumbing demo. Don't let "100%" stand naked.
- **H6 — One genuinely independent detector** (duplicate-address rings / lender
  concentration) so "corroboration" isn't two views of the same $/job ratio.
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
- **Loop (branch `ralph/hardening`):** H1 baseline machinery + H5 vision honesty.
- **Background:** H2 full-slice ingest.
- **Finalize (manual, on real full data):** regenerate the README baseline numbers.

## Watch-outs

- **PU labels are biased toward caught fraud** — never report a "fraud rate"; report
  recall-on-known-fraud. See RESPONSIBLE_USE.md.
- **Public data names real borrowers** — SBA already published these; still, frame
  every example as a lead, and prefer already-charged (public-record) cases for any
  named example.
- **`all` slice is ~8 GB** — `150k_plus` is the fast default for iteration.
