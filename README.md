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

## Headline result

Score the **965,122** loans in the public $150k+ slice with two transparent detectors, rank by a simple composite, and validate against **325** DOJ-prosecuted loans entity-resolved from 3,414 enforcement press releases (base rate 0.034%):

| top-k | precision@k | **lift** | recall |
| --- | --- | --- | --- |
| 100 | 1.00% | **29.7×** | 0.3% |
| 500 | 0.80% | **23.8×** | 1.2% |
| 1000 | 0.50% | **14.8×** | 1.5% |

Read honestly: ~24–30× enrichment at the top is real signal (comparable to a Medicare-FWA equivalent), but absolute recall is tiny and labels are a **prosecution-biased PU sample** — so these are **recall-on-known-fraud, a lower bound**, not a fraud rate. Reproduce: `relief-probe ingest && relief-probe fetch-labels && relief-probe resolve-labels && relief-probe benchmark`.

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
