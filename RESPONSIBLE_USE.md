# Responsible use

relief-probe produces **statistical leads for review, not evidence of fraud.** A high
score means a loan looks anomalous relative to peers or matches a rule pattern in
public data — nothing more. Read this before using or publishing any output.

## What the scores are (and are not)

- **Not a fraud determination.** Anomalies have benign explanations (legitimate
  high-wage businesses, data-entry artifacts, unusual-but-valid industries, sparse
  cohorts). Every flagged loan needs human review against primary records.
- **Built on public data only.** SBA published this loan-level data under FOIA; the
  enforcement labels are public DOJ/SBA-OIG records. No private or PII data beyond
  what is already public is used.

## The labels are positive-unlabeled (PU) and biased

Confirmed/charged fraud is a **tiny (<0.1%) and non-random sample** of estimated
fraud — it over-represents large, egregious, *prosecuted* cases. Therefore:

- We report **recall-on-known-fraud**, never a "fraud rate" or precision as if labels
  were complete. A loan not in `fraud_cases` is **unlabeled, not innocent.**
- Lift@k and recall@k describe how well the ranking surfaces *already-charged* fraud —
  a lower bound on, and biased view of, true performance.

## Named examples

Public files name real borrowers. For any named example, prefer borrowers who have
**already been criminally charged** (a matter of public record) or anonymize the row.
Aggregate metrics are fine. Do not present any individual as fraudulent on the basis
of a score.

## Scope

This is a research/portfolio artifact for surfacing review leads and demonstrating
methodology. It is not an enforcement tool and makes no eligibility or guilt
determinations.
