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

### Loop 1 — research-driven exploratory detectors ✅ (built + tested, NOT promoted)

Two new public-data detectors targeting patterns *different* from dollars-per-job, both
registered in `registry.exploratory_detectors()` (NOT `all_detectors()` — the default
composite is unchanged):
- **`amount_anomaly`** (`detectors/amount_anomaly.py`) — per-loan round-number +
  payroll-cap-maximization ("bunching") tells of a fabricated/reverse-engineered amount.
  Reuses the per-employee cap constants from `payroll_cap.py`; the bunching band is
  at/just-below the cap, distinct from `payroll_cap_exceedance` (above the cap).
  Motivation: Griffin et al. round-number / cap-bunching forensic literature.
- **`multiple_funded_loans`** (`detectors/multiple_funded_loans.py`) — entity resolution
  (normalized name + building-level address, `detectors/_entity.py::entity_key`) →
  borrowers exceeding the one-per-draw rule (≥2 same-draw loans or >2 funded total).
  Motivation: GAO finding of tens of thousands of multiply-funded recipients.

**MANUAL post-loop step (not done here):** score both on the real ~11.3M-loan warehouse
via `run_all(con, detectors=[*all_detectors(), *exploratory_detectors()])`, measure lift
vs the DOJ labels (and detector_overlap vs the existing detectors), and **promote into
`all_detectors()` only any that show real, independent lift** — mirroring the H6
discipline (the ring detector looked good on synthetic data but had zero real lift). No
lift numbers are claimed until that human validation runs.

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
- **H4 — Measure label precision** on a ~50-row hand-labeled sample → report a number,
  not "high-precision."
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
- **Next up (highest leverage):** H4 (measure label precision on a hand-labeled
  sample). Then H7 temporal holdout before any label-aware tuning.

## Watch-outs

- **PU labels are biased toward caught fraud** — never report a "fraud rate"; report
  recall-on-known-fraud. See RESPONSIBLE_USE.md.
- **Public data names real borrowers** — SBA already published these; still, frame
  every example as a lead, and prefer already-charged (public-record) cases for any
  named example.
- **`all` slice is ~8 GB** — `150k_plus` is the fast default for iteration.
