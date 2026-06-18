# relief-probe

**An open-source PPP/SBA pandemic-loan fraud-lead lab — anomaly detection over public loan data, validated against *future* DOJ/OIG fraud prosecutions, with an agentic investigation layer and a document-authenticity (vision) tab.**

Everything here is reproducible by a stranger from public federal files (SBA FOIA loan data; DOJ/SBA-OIG enforcement records). It runs on a laptop against a local DuckDB warehouse — no cluster required.

> A high score is a **statistical lead for review, not evidence of fraud.** Scores reflect anomalies relative to peers and rule patterns on public data. See [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md).

---

## Why this is hard (and the honest framing)

PPP fraud is estimated at ~$200B (SBA-OIG), but **confirmed/charged fraud is a tiny (<0.1%) and biased sample** — it over-represents egregious, *caught* cases (DOJ has charged 3,500+ defendants). So this is a **positive-unlabeled (PU)** problem, and results are reported as **recall-on-known-fraud, not a true fraud rate**. That honesty is the point — and the multi-year prosecution lag (10-year statute, charges still landing 2024–2026) makes the out-of-time validation genuine: the labels strictly post-date every loan.

## Architecture

Layers mirror a real program-integrity shop; each is independently demoable:

```
ingest/      Layer 1 — Warehouse:    resolve + download public SBA CSVs → DuckDB (one row per loan)   ✅
detectors/   Layer 2 — Detection:    self-contained scheme modules → unified signals table             ✅
labels/      Layer 3 — Labels:       scrape DOJ enforcement → entity-resolve to loan_number             ✅
benchmark/   Layer 4 — Validation:   rank loans, measure how charged-fraud concentrates at the top      ✅
vision/      Layer 5 — Documents:    ELA forgery-detection plumbing, demoed on SYNTHETIC splices       ✅
agent/       Layer 6 — Investigation: agentic, tool-grounded loan-investigator + MCP server             ✅
```

Output contract: every detector emits `(loan_number, detector_id, score, evidence_json)` into one `signals` table.

## Headline result (and an honest baseline check)

The warehouse holds the **full ~11.3M-loan** PPP population (every public FOIA slice).
All **325** DOJ-prosecuted labels (entity-resolved from 3,414 enforcement releases) fall
in the public **$150k+ disclosure slice**, so we measure lift on that **965,122-loan**
labelable slice for an apples-to-apples base rate (0.034%); ranking the whole 11.3M only
inflates lift via a 10× larger denominator, so full-population **recall** is reported
separately (7 of 325 in the top 5,000 ≈ 2.1%). Crucially, we compare the detector
machinery against **dumb baselines** — because the right question isn't "what lift do I
get?" but "do my robust-z + FDR + cohort detectors beat a one-line sort?" Lift over base
rate (raw hit counts in parens):

| ranking | lift@100 | lift@500 | lift@1000 |
| --- | --- | --- | --- |
| **Composite** (detectors + cohort-z + BH-FDR, percentile-combined) | 29.7× (1) | **23.8× (4)** | 11.9× (4) |
| Trivial: `ORDER BY amount/jobs DESC` (one line) | 29.7× (1) | 11.9× (2) | 14.8× (5) |
| Dumb: `ORDER BY loan_amount DESC` | 0× (0) | 0× (0) | 5.9× (2) |

What this honestly shows:
- **The core signal is real.** Dollars-per-reported-job decisively beats raw loan
  amount (which finds *nothing* in the top 500). Normalizing by jobs is doing the work.
- **The fancy stats add only a little.** The cohort-z/FDR composite clearly beats a
  one-line `amount/jobs` sort at k=500 (4 hits vs 2) but the trivial sort edges it back
  at k=1000 (5 vs 4). The methodology is sound hygiene, but most of the signal is the
  ratio, not the machinery.
- **Estimates are noisy.** These rest on **single-digit hit counts**, so treat the lift
  as a rough lower bound, not a precise figure. Labels are a small, **prosecution-biased
  PU sample** → this is **recall-on-known-fraud, not a fraud rate**.
- **No train/test leakage.** The detectors are **unsupervised** — thresholds come from
  program rules and statistics, not from fitting to the 325 labels — so nothing is
  trained on the answers, and the labels are prosecutions dated *years after* the loans.
  (The entity resolver was tuned for match *precision*, not for benchmark lift.) A
  held-out split only becomes necessary once something is *fit* to the labels — see the
  temporal-holdout plan (H7) for the learned-scorer path.

Reproduce: `relief-probe ingest && relief-probe fetch-labels && relief-probe resolve-labels && relief-probe benchmark` (the table is `benchmark`'s baseline comparison).

> Numbers above are on the **$150k+ slice**. A full-population refresh (all ~11.5M loans → more labels → larger, less noisy hit counts) is in progress; this section will be updated with those figures.

## Status

All six layers built and tested; layers 1–4 and 6 verified on real data, the vision layer (5) demonstrated on synthetic splices only (plumbing, not a validated capability). See [NEXT_STEPS.md](NEXT_STEPS.md).

- **Detectors:** `naics_cohort_outlier` (robust cohort z-score, BH-FDR) · `payroll_cap_exceedance` (per-employee program ceiling).
- **Labels:** DOJ press-release scraper + precision-tuned entity resolution (amount-corroborated) → 325 high-precision labels.
- **Vision:** Error Level Analysis (ELA) document-forgery *plumbing* + Streamlit dashboard (Loan-leads + Document-authenticity tabs). Honest scope: the detector is demonstrated only on **synthetic spliced images** (trivially separable, so it proves the wiring — not real-world forgery detection). Real anchors (IDNet, "Find it again!") are wired but not run; no real-document accuracy is claimed.
- **Agent/MCP:** tool-grounded, deterministic-first loan investigator (`relief-probe investigate`) and an MCP server (`relief-probe serve-mcp`) over the same four read-only warehouse tools.

## Quickstart

```bash
uv run --with pytest pytest                         # offline tests (45)
uv run relief-probe ingest --slice 150k_plus        # ~1M big-dollar loans (~430 MB)
uv run relief-probe score                           # run detectors → ranked leads
uv run relief-probe fetch-labels                    # scrape DOJ enforcement releases
uv run relief-probe resolve-labels                  # entity-resolve → fraud_cases labels
uv run relief-probe benchmark                       # forward PU lift@k + ablation
uv run --extra vision relief-probe vision-demo      # train the ELA doc-forgery detector
uv run relief-probe investigate <loan_number>       # grounded, evidence-cited lead report
uv run --extra agent relief-probe serve-mcp         # serve the 4 read-only tools over MCP
uv run --extra viz --extra vision streamlit run app/dashboard.py   # dashboard (2 tabs)
```

## Data sources

| source | role | status |
| --- | --- | --- |
| SBA PPP FOIA loan-level data (data.sba.gov) | core loan population | ✅ ingested |
| DOJ COVID-fraud prosecution press releases | benchmark labels (PU positives) | ✅ scraped + resolved |
| Synthetic spliced documents (built-in) | vision train/eval (offline) | ✅ |
| IDNet (synthetic ID forgery) · "Find it again!" (receipt tamper) | real vision anchors | wired (opt-in) |

## License

[Apache-2.0](LICENSE).
