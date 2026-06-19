# Example output — `relief-probe investigate`

What the tool actually produces, for readers who won't run it themselves. This is the
**deterministic** investigator (no API key, no network): given a loan number, it gathers
evidence from the warehouse with read-only tools and assembles a grounded, source-cited report.
A high score is a **statistical lead for review, not evidence of fraud.**

> **Synthetic data.** The loan below is fabricated, with a fictitious sample-company name
> (`NORTHWIND HOSPITALITY GROUP LLC`) and a placeholder enforcement URL — no real business or
> person. It's a $3.5M loan reporting **2 jobs** (≈ $1.75M per employee, 60× the program's
> per-employee cap) — the kind of egregious anomaly the detectors surface.

```console
$ relief-probe investigate LEAD-1

Loan LEAD-1 — risk CRITICAL (deterministic path, 5 tools)
Loan LEAD-1 — NORTHWIND HOSPITALITY GROUP LLC (722511 in IL) — is a critical-risk lead. 1 detector
fired (composite score 60.0). It is linked to a resolved public enforcement (fraud_cases) record.

                               Evidence (every row cites its source)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ claim                                 ┃ source           ┃ detail                                ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Composite risk score 60.0 across 1    │ composite_for    │ payroll_cap_exceedance                │
│ detector(s).                          │                  │                                       │
│ Detector payroll_cap_exceedance fired │ loan_signals     │ amount=3500000.0,                     │
│ (score 60.0).                         │                  │ amount_per_job=1750000.0,             │
│                                       │                  │ borrower_name=NORTHWIND HOSPITALITY   │
│                                       │                  │ GROUP LLC, jobs_reported=2.0,         │
│                                       │                  │ naics_code=722511,                    │
│                                       │                  │ per_employee_cap=29166.67, state=IL,  │
│                                       │                  │ x_cap=60.0                            │
│ Linked to a resolved public           │ fraud_case_check │ NORTHWIND HOSPITALITY GROUP LLC via   │
│ enforcement case (doj).               │                  │ name+state+amount (confidence 1.0) —  │
│                                       │                  │ https://example.gov/synthetic-demo-…  │
└───────────────────────────────────────┴──────────────────┴───────────────────────────────────────┘

Alternative explanations
  • A legitimately high-wage or capital-intensive business can post a high dollars-per-job ratio
    without any wrongdoing.
  • Data-entry artifacts (mis-keyed jobs or amounts, NAICS miscoding) can manufacture an apparent
    outlier.
  • EIDL-refinance or other valid program mechanics can trip pattern rules without indicating fraud.

Recommended next steps
  • Review the loan against primary SBA records and the borrower's filings.
  • Cross-reference the linked enforcement record to confirm it is the same entity (matches can be
    approximate).
  • Prioritize for human analyst review; do not treat the score as a determination.

This report is a statistical lead for review, not evidence of fraud. A high score means a loan
looks anomalous relative to peers or matches a rule pattern in public data — nothing more.
Anomalies have benign explanations; every flagged loan needs human review against primary
records. A loan not in fraud_cases is unlabeled, not innocent. See RESPONSIBLE_USE.md.
```

## What you're looking at

- **Every claim cites its source tool** (`composite_for`, `loan_signals`, `fraud_case_check`) —
  the report is assembled from read-only warehouse lookups, not free-form generation, so nothing
  is asserted that isn't traceable to a row.
- **The detector evidence is fully unpacked** — here `payroll_cap_exceedance` shows the exact
  arithmetic (`amount_per_job=1,750,000` vs `per_employee_cap=29,166.67` → `x_cap=60`), so a
  reviewer can audit *why* it fired.
- **Alternative (benign) explanations are always listed** — the report leads with how the flag
  could be wrong, by design.
- **An LLM narration is optional** (`--llm`, behind the `agent` extra + an API key): it narrates
  *only* these grounded facts in "lead, not proof" language. The default path shown here needs
  neither.
