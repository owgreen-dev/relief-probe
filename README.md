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
ingest/      Layer 1 — Warehouse:    resolve + download public SBA CSVs → DuckDB (one row per loan)
detectors/   Layer 2 — Detection:    self-contained scheme modules → unified signals table
benchmark/   Layer 3 — Validation:   rank loans, measure how charged-fraud concentrates at the top (PU forward validation)
agent/       Layer 4 — Investigation: agentic, tool-grounded loan-investigator + MCP server
vision/      Layer 5 — Documents:    supporting-document authenticity (ID-forgery + amount-tamper) tab
```

Output contract: every detector emits `(loan_number, detector_id, score, evidence_json)` into one `signals` table.

## Status

Early build. **Done:** warehouse schema (`loans` / `fraud_cases` / `signals`), PPP FOIA ingest (resolve → download → column-mapped load), CLI (`info`, `ingest`), offline loader tests.

**Next (see [NEXT_STEPS.md](NEXT_STEPS.md)):** loan-level detectors → DOJ-label scraper + entity resolution → PU forward benchmark → agent + MCP → document-authenticity vision tab (anchors: IDNet, "Find it again!").

## Quickstart

```bash
uv run --with pytest pytest          # offline tests
uv run relief-probe ingest --slice 150k_plus   # ~1M big-dollar loans (fast)
uv run relief-probe ingest --slice all         # ~11.5M loans (~8 GB)
uv run relief-probe info                        # warehouse + row counts
```

## Data sources

| source | role | status |
| --- | --- | --- |
| SBA PPP FOIA loan-level data (data.sba.gov) | core loan population | ✅ ingested |
| DOJ COVID-fraud prosecutions / SBA-OIG | benchmark labels (PU positives) | planned |
| IDNet (synthetic ID forgery) · "Find it again!" (receipt tamper) | document-authenticity vision tab | planned |

## License

[Apache-2.0](LICENSE).
