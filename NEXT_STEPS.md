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

## Remaining / optional

- **M2.1** more detectors (duplicate-address rings, proceeds anomalies, lender
  concentration) to lift recall.
- **M4.1** learned PU scorer vs the transparent baseline (`ml` extra); ingest
  `under_150k`/`all` slices to broaden label coverage.
- Real vision data (IDNet / Find-it-again) + a CNN baseline vs ELA.
- Merge `feature/detectors` → `main`; rewrite README around the M4 headline.

## Watch-outs

- **PU labels are biased toward caught fraud** — never report a "fraud rate"; report
  recall-on-known-fraud. See RESPONSIBLE_USE.md.
- **Public data names real borrowers** — SBA already published these; still, frame
  every example as a lead, and prefer already-charged (public-record) cases for any
  named example.
- **`all` slice is ~8 GB** — `150k_plus` is the fast default for iteration.
