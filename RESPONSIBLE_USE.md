# Responsible use

> **Disclaimer.** relief-probe is a **research and educational** project. It is **not**
> legal, financial, or investigative advice, and it makes **no accusation of fraud or
> wrongdoing against any person or business.** Scores are statistical *leads for review of
> public data*, never determinations of guilt. **All examples, screenshots, and demos in
> this repository use anonymized or synthetic (fictitious) data** — no individual or business
> is named as fraudulent. Read the rest of this document before using or publishing any output.

relief-probe produces **statistical leads for review, not evidence of fraud.** A high
score means a loan looks anomalous relative to peers or matches a rule pattern in
public data — nothing more.

## What the scores are (and are not)

- **Not a fraud determination.** Anomalies have benign explanations (legitimate
  high-wage businesses, data-entry artifacts, unusual-but-valid industries, sparse
  cohorts). Every flagged loan needs human review against primary records.
- **Built on public data only.** SBA published this loan-level data under FOIA; the
  enforcement labels are public DOJ/SBA-OIG records. No private or PII data beyond
  what is already public is used.
- **Shared-address rings name real people and addresses.** The `duplicate_address_ring`
  signal groups loans by a building-level address and surfaces the borrower names at
  that address — but legitimate shared office buildings, strip malls, co-working spaces,
  and registered-agent / commercial-filing addresses produce identical patterns. A ring
  is a **review lead, not proof of fraud or coordination**; never present co-located
  borrowers as a scheme on the basis of the score alone.

## KYB (know-your-business) external evidence — the most legally-sensitive layer

The KYB layer (Loop 5) verifies a borrower against **external** sources — a business
registration date, a non-registered flag, an address type — to refine a lead. Because it
names real businesses and, sometimes, real people, it carries obligations the rest of the
project does not:

- **FCRA-adjacency for named individuals / sole-proprietors.** Scoring a *registered entity*
  against public corporate-registry facts is one thing; scoring a *named individual* or a
  sole-proprietor (where the business name **is** a person) edges toward consumer-report
  territory. Treat any signal attached to a natural person as especially sensitive, never use a
  KYB output for an adverse decision about a person, and consult counsel before any
  individual-level use. This tool makes no eligibility, credit, or guilt determination.
- **Defamation / false-positive harm from wrong-entity matches.** A KYB result is only as good
  as the entity match behind it — two companies can share a name across states. The provider is
  built precision-first (it returns a low-confidence lead or `None` rather than assert a
  "non-registered" claim it cannot stand behind), but a wrong match could attach a damaging
  registration/address finding to the **wrong** business. Always verify the matched record
  (the stored `raw_ref`) against primary sources before acting on or publishing it. A KYB
  finding is a **review lead, not proof** — and recency/registration is at most an *eligibility*
  tell, not evidence of fraud.
- **OpenCorporates Terms of Service.** The Tier-B provider uses the OpenCorporates API under its
  free-tier terms: **share-alike + attribution** (we store the company URL as `raw_ref` so any
  republished fact can carry its required attribution), **rate limits** (the run is hard-capped
  and cached; do **not** create multiple accounts or otherwise act to bypass the gates or
  quotas), and honor any usage restriction the data carries. The real `--live` run is a manual,
  legally-reviewed step, never an autonomous one.
- **Every output is a lead for review, not proof.** As everywhere else in this project, a KYB
  signal flags a loan for a human to investigate against primary records. It is not a fraud
  determination.

## The labels are positive-unlabeled (PU) and biased

Confirmed/charged fraud is a **tiny (<0.1%) and non-random sample** of estimated
fraud — it over-represents large, egregious, *prosecuted* cases. Therefore:

- We report **recall-on-known-fraud**, never a "fraud rate" or precision as if labels
  were complete. A loan not in `fraud_cases` is **unlabeled, not innocent.**
- Lift@k and recall@k describe how well the ranking surfaces *already-charged* fraud —
  a lower bound on, and biased view of, true performance.

## Named examples (anonymized)

The underlying SBA files name real borrowers, but **this repository names none** as
fraudulent: every example in the docs is described generically, and the dashboard
screenshot uses **synthetic, fictitious company names** (Northwind / Contoso / Proseware /
…) on demo data, not real loans. When *using* the tool on real data, keep it that way —
prefer aggregate metrics or already-charged (public-record) cases, anonymize any row you
share, and never present an individual as fraudulent on the basis of a score.

## Scope

This is a **research / portfolio / educational** artifact for surfacing review leads and
demonstrating methodology. It is **not** an enforcement, compliance, credit, or
eligibility tool; it makes no guilt, eligibility, or creditworthiness determination about
any person or business; and it is not a substitute for professional or legal advice.
