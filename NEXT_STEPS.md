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

## M3 — label construction (the differentiator)

DOJ COVID-fraud press-release scraper + SBA-OIG records → `fraud_cases`, then
**entity-resolve** (name + state + amount, fuzzy) back to `loan_number`. This is the
hard, high-value 60%. Record `match_method` / `match_confidence`; keep unmatched
cases too. Frame as PU positives.

## M4 — PU forward benchmark (`benchmark/`)

Rank loans by composite score; measure how `fraud_cases`-matched loans concentrate
at the top (lift@k, recall@k). Report **recall-on-known-fraud**, with the
caught-≠-all caveat up front. Optional learned scorer (PU-learning) vs the
transparent baseline on the same split (`ml` extra).

## M5 — agent + MCP (`agent/`)

Tool-grounded loan investigator (profile, signals, peer comparison, fraud-case
check) → structured, evidence-cited report. Expose the same tools over MCP.

## M6 — document-authenticity vision tab (`vision/`)

`vision` extra. Anchors: **IDNet** (ID forgery — face morph / portrait swap / text
alter) and **"Find it again!"** (receipt amount-tamper). State the gap up front:
no public fake-paystub/bank-statement dataset exists — synthesize and say so.

## Watch-outs

- **PU labels are biased toward caught fraud** — never report a "fraud rate"; report
  recall-on-known-fraud. See RESPONSIBLE_USE.md.
- **Public data names real borrowers** — SBA already published these; still, frame
  every example as a lead, and prefer already-charged (public-record) cases for any
  named example.
- **`all` slice is ~8 GB** — `150k_plus` is the fast default for iteration.
