# Responsible use

> **Disclaimer.** relief-probe is a **research and educational** project. It is **not**
> legal, financial, or investigative advice, and it makes **no accusation of fraud or
> wrongdoing against any person or business.** Scores and pattern comparisons are
> statistical observations on *public data*, never determinations of guilt, risk, or
> legal jeopardy. **All examples, screenshots, and demos in this repository use
> anonymized or synthetic (fictitious) data** — no individual or business is named as
> fraudulent. The "Prosecution pattern" tab compares a loan against the statistical
> pattern of publicly charged cases; it is not a risk assessment and not legal advice.
> Read the rest of this document — especially the prosecution pattern section — before
> using or publishing any output.

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

## Prosecution pattern comparison — use by borrowers, attorneys, and researchers

The "Prosecution pattern" dashboard tab lets a user look up a loan number and see
where it sits relative to the statistical pattern of the 325 DOJ-prosecuted PPP loans
in the public enforcement record. This use case carries specific obligations and
limitations that differ from the investigator-facing layers of this project.

### What this tool does

It compares a loan's public fields (loan amount, jobs reported, NAICS code, state,
lender, borrower address type) against:

- The full $150k+ loan population (965,122 loans) — to show where the loan sits in
  the overall distribution.
- The 325 entity-resolved DOJ-prosecuted loans — to show what signals, if any, the
  loan shares with cases that were actually charged.
- The prosecuted population in the same NAICS and state — to show industry-specific
  context.

It does NOT produce a risk score, a probability of prosecution, or any determination
of guilt, innocence, eligibility, or legal jeopardy.

### What this tool does NOT do — read carefully before using

- **It is not legal advice.** Nothing in this output should be interpreted as legal
  advice or as an assessment of legal risk. If you received a PPP loan and have
  concerns about your legal exposure, consult a licensed attorney.
- **It is not a risk determination.** A loan that shares signals with prosecuted cases
  is not at elevated legal risk on that basis alone. The 325 prosecuted loans are a
  tiny, prosecution-biased sample — they over-represent large, egregious, easy-to-prove
  cases. DOJ prosecution decisions depend on factors entirely outside this data:
  evidence quality, witness availability, prosecutorial capacity, district priorities,
  and the specific facts of each case.
- **Low signal overlap does not mean legal safety.** A loan with zero shared signals
  is not thereby "clean" or legally protected. This tool compares against *detected
  and prosecuted* fraud only — not against the full universe of fraud that was never
  caught or charged.
- **High signal overlap does not mean legal jeopardy.** Many loans with anomalous
  $/job ratios or unusual lender patterns are perfectly legitimate — legitimate
  high-wage businesses, data-entry artifacts in the SBA records, unusual-but-valid
  industries. An anomaly is a statistical observation, not an accusation.
- **It is not a FCRA consumer report.** This tool uses only public data published
  by the SBA under FOIA. It does not access private credit data, banking records, or
  any non-public information. However, sole-proprietors and individual borrowers should
  be aware that the loan file contains their personal name, and any output referencing
  their loan references a natural person. Do not use this tool to make adverse decisions
  about individuals.
- **Entity resolution may be imperfect.** The 325 prosecuted labels were resolved from
  DOJ press releases with ~84-88% precision. A small number of labels may be incorrect
  matches. A match to the `fraud_cases` table reflects the entity resolver's best
  judgment, not a court finding.

### Who this is appropriate for

- **Borrowers** who want to understand how their loan compares statistically to the
  prosecuted population — for their own peace of mind or to inform a conversation with
  their attorney. The output is educational context, not legal advice.
- **Attorneys** representing PPP borrowers in FCA investigations, qui tam suits, or
  DOJ inquiries, who want a structured comparison of their client's loan against the
  prosecution pattern. The output is a research aid, not an expert opinion.
- **Journalists and researchers** studying the PPP enforcement record who want to
  contextualize a specific loan against the broader statistical pattern. The output
  should be characterized as statistical comparison against public data, not a fraud
  finding.

### Who this is NOT appropriate for

- **Do not use this to make adverse decisions about third parties.** If you are a
  lender, servicer, employer, or other decision-maker, do not use this tool's output
  to deny credit, employment, or services to a borrower. This is not a compliance
  or eligibility tool.
- **Do not publish individual outputs without legal review.** Publishing a named
  individual's comparison output — even accurately — could constitute defamation if
  the framing implies guilt. Always characterize output as statistical comparison
  against public data, not as a fraud finding.
- **Do not use as a substitute for SBA or DOJ official records.** If you need to know
  whether a specific loan is under investigation or whether a specific person has been
  charged, consult official SBA, DOJ, or court records directly.

### The prosecution selection bias — the most important limitation

The 325 prosecuted loans are not a random sample of PPP fraud. They are the cases that
were (a) large enough to attract DOJ attention, (b) egregious enough to be provable,
(c) in districts with capacity and appetite to prosecute, and (d) resolved to a loan
number via entity resolution with sufficient corroboration. True PPP fraud is estimated
at ~$200B (SBA-OIG); confirmed charged fraud totals a few billion. The gap between
those numbers reflects investigator capacity and prosecutorial selection, not the actual
distribution of fraud. A loan that does not resemble the prosecuted pattern may still
be fraud; a loan that resembles it closely is more likely simply anomalous in ways that
happen to correlate with the prosecution record.

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
