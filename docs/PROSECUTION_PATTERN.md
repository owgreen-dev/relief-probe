# How the Prosecution Pattern Comparison Works

> **This is not legal advice and not a risk determination.** See
> [RESPONSIBLE_USE.md](../RESPONSIBLE_USE.md) — especially the prosecution pattern
> section — before drawing any conclusions from this tool's output.

## What this compares

The prosecution pattern comparison takes a loan number and produces four outputs:

1. **Population position** — where this loan sits in the full $150k+ PPP population
   (965,122 loans) on the key dimensions: loan amount, dollars per reported job,
   and lender type.

2. **Signal overlap** — which, if any, of the three production detectors fired on
   this loan, and how the resulting scores compare to the distribution of scores
   among the 325 prosecuted loans vs the broader population.

3. **Industry context** — how the loan compares to other loans in the same NAICS
   code and state, and what the prosecution rate looks like in that NAICS+state cell.

4. **Prosecution pattern summary** — aggregate statistics about the 325 prosecuted
   loans (dollar distribution, NAICS concentration, state concentration, lender
   patterns) shown alongside where this loan falls in those distributions.

## What it does NOT compare

- It does not compare against active investigations, referrals, or cases under seal.
  The DOJ enforcement record used here reflects only publicly announced charges.
- It does not access any non-public SBA records, OIG referral lists, or law
  enforcement databases.
- It does not incorporate factors that drive actual prosecution decisions: evidence
  quality, witness cooperation, district prosecutorial priorities, statute of
  limitations strategy, or the specific facts alleged in any case.

## The 325 labels — what they represent

The 325 prosecuted loans in this dataset were identified by:

1. Scraping all DOJ press releases tagged "COVID-Related Fraud" or containing
   PPP/EIDL loan keywords (3,414 releases total).
2. Entity-resolving defendant business names to loan numbers in the SBA FOIA file
   using normalized name + state + amount corroboration.
3. Hand-validating a sample, yielding estimated precision of ~84-88% (95% CI:
   72-92%).

These 325 represent a **tiny, prosecution-biased slice** of estimated PPP fraud:
- SBA-OIG estimates total PPP fraud at ~$200B
- DOJ has charged ~3,500 defendants totaling a few billion dollars
- The 325 that resolved to loan numbers are a subset of those charges
- The prosecution record over-represents large, egregious, single-borrower schemes

A loan that shares signals with the prosecuted population is statistically similar
to *caught and charged* fraud — not to the full fraud distribution.

## The three signals used

**naics_cohort_outlier** — is the loan's dollars-per-reported-job far above other
loans in the same NAICS industry code and state? Scored as a robust z-score
(median/MAD) in log space, with Benjamini-Hochberg false discovery rate control.
High z = the loan's implied payroll per employee is anomalously high for its industry
and geography.

**payroll_cap_exceedance** — does the loan's implied per-employee amount exceed the
PPP program's legal ceiling ($20,833 for most industries; $29,167 for NAICS 72)?
This is an absolute program-rule check, independent of peer comparison.

**multiple_funded_loans** — does the entity (normalized name + building address)
hold more funded loans than the one-per-draw rule allows? Flags entities with ≥2
same-draw loans or >3 funded loans total.

None of these signals is proof of fraud. Each has documented benign explanations:
legitimate high-wage industries, data-entry artifacts, franchise re-filings. See
[RESPONSIBLE_USE.md](../RESPONSIBLE_USE.md).

## How to read the output

**If no signals fired:** The detectors found no anomaly in this loan relative to
its peers or program rules. This does not mean the loan was legitimate — it means
it does not match the specific patterns these detectors look for. Many fraud schemes
leave no anomalous statistical footprint in aggregate loan-level data.

**If one or more signals fired:** The loan shares a statistical pattern with a
subset of the population — including some prosecuted loans. This does not mean
the loan is fraudulent or that prosecution is likely. It means the loan looks
anomalous on these dimensions. Anomalies have benign explanations. Any concern
should be directed to a licensed attorney who can assess the specific facts.

**If the loan appears in fraud_cases:** The entity resolver matched this loan to
a publicly announced DOJ prosecution with ~84-88% precision. If accurate, this
means a defendant associated with this loan has been publicly charged — information
that is already part of the public court record. The resolver can make mistakes;
verify against the DOJ press release linked in the evidence.

## What to do if you have concerns

If you received a PPP loan and have questions about your legal exposure:
- Consult a licensed attorney with experience in False Claims Act or federal fraud
  matters. This is not optional — this tool cannot assess your legal situation.
- Review your loan application and supporting documentation against the SBA's
  original program rules (sba.gov).
- If you believe you made an error in good faith, proactive voluntary disclosure
  through counsel is an option that attorneys in this space regularly advise.

This tool cannot tell you whether you will be investigated or charged. No public
data tool can. Only an attorney with access to the specific facts of your situation
can give you meaningful guidance.

## Data sources and reproducibility

All data used in this comparison is public:
- **SBA PPP FOIA loan data** (data.sba.gov) — the full loan-level file released
  under FOIA litigation.
- **DOJ enforcement press releases** (justice.gov/opa) — public announcements of
  charges and convictions.
- **Entity resolution** — see [docs/LABEL_PRECISION.md](LABEL_PRECISION.md) for
  the full methodology and precision estimates.

The full pipeline is reproducible from public sources. See the
[README](../README.md) quickstart.
