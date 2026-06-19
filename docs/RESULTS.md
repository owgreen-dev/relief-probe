# Results — what worked, what didn't

The clean, human-readable version of the method verdicts. (The blow-by-blow engineering log
with every parameter and caveat is [NEXT_STEPS.md](NEXT_STEPS.md); this is the summary written
for a reader.)

All numbers are on the labelable **$150k+ slice** (965,122 loans), validated out-of-time
against charges that post-date the loans. The entity-resolved DOJ label set is **404** loans
(325 exact-match + 79 LLM-recovered; 368 fall in the slice). The **composite lift benchmark
below is measured on the 325 exact-match labels** (base rate 0.034%) and was not re-run on the
larger set; the **3.4× homophily** result uses the full 404. Because confirmed fraud is a tiny,
prosecution-biased sample, everything is **recall-on-known-fraud, not a fraud rate** — a loan
not in the label set is *unlabeled, not innocent*.

## The headline: does the ranking find prosecuted fraud?

**Yes, and the signal is honest about its limits.** The composite ranking (three unsupervised
detectors, percentile-combined) lifts prosecuted loans **23.8× at k=500** over the base rate.
Two caveats the project insists on:

- **The very top is noise; the k≥500 band is real.** The eye-catching lift@100 rests on ~3
  loans and its 95% bootstrap CI spans zero. From k=500 outward the CI clears 1× decisively
  (5.9–47.5×), so the concentration is a genuine effect, not an artifact.
- **The ratio does the work, not the machinery.** Dollars-per-reported-job decisively beats
  ranking by raw loan amount (which finds *nothing* in the top 500). The cohort-z / FDR
  statistics only edge a one-line `ORDER BY amount/jobs` sort. Sound hygiene — but most of the
  signal is the ratio.
- **No leakage.** The production detectors are unsupervised (program rules + statistics, never
  fit to the labels); the labels are prosecutions dated years after the loans.

## The thesis: AI/ML wins at *retrieval*, not *prediction*

The recurring finding across every "add AI/ML" experiment: it earns its keep at **retrieval and
expansion** — finding look-alikes, recovering labels, expanding from a known lead — and *not* at
**prediction** over a loan's own fields, because prosecuted loans look plausible individually.

### Three wins (retrieval / expansion)

- **LLM entity resolution — +79 labels (+24%).** The exact-match label resolver misses fuzzy
  cases (legal-suffix and spelling variants; sole-props where the release names the owner, not
  the LLC). An amount-gated LLM adjudication step recovers them, growing the benchmark from 325
  to 404 distinct labeled loans — hand-validated at ~91–99% precision on the recovered tier. The
  LLM brings *new information* (reading the press release) the structured matcher can't.

- **Similar-case retrieval — 3.4× homophily.** A prosecuted loan's nearest look-alikes (by a
  hybrid of business-name semantics, dollar band, and area) are ~3.4× more likely to be
  prosecuted than chance. Fraud clusters into rings/templates, and the retrieval surfaces it.
  This is an investigation tool (find the rest of a ring from one known case), **not** a
  predictor.

- **Graph lead-expansion.** The multi-relational loan graph (shared address + entity +
  similarity edges) is a genuine expansion tool — from a known seed it surfaces structurally
  connected loans. Consistent with the homophily win. (But see the negative: it does *not* work
  as a cold ranker.)

### Five negatives (prediction / cold-ranking)

Reported because reporting them is the point — an honest negative is a result, not a failure.

- **LLM plausibility judge — no lift.** Escalating the top-k composite leads to an LLM
  "could this business plausibly justify this loan?" judge (which re-ranks them) did not
  concentrate prosecuted loans better, and regressed the very top by one loan. Its calls look sensible, but plausibility
  simply isn't aligned with *what got prosecuted* — the composite already nails the top, and
  many prosecuted loans look plausible.

- **Name↔industry embedding mismatch — no lift.** Scoring whether a borrower's name fits its
  declared NAICS industry showed essentially random concentration (lift < 1× at every k).
  Prosecuted loans aren't industry-mismatched; the fraud is fabricated *dollars*, not a wrong
  industry.

- **Learned PU scorer — no lift (overfit, caught by the holdout).** A PU-bagging scorer over the
  detector + structured features showed no improvement on a temporal holdout (train ≤2023, test
  >2023) — and the holdout is what caught it overfitting `forgiveness_ratio`. Kept exploratory.

- **Graph ring cold-ranking — no lift.** Ranking loans purely by ring/community structure did
  not beat the composite or chance. Combined with the expansion win above: graph structure is a
  *retrieval* tool, not a *prediction* one.

- **Business-recency (KYB Tier-A) — weak/no independent lift.** Flagging "startup / new business
  / change of ownership" tells didn't concentrate the prosecuted labels — recency is an
  *eligibility* tell, not necessarily a *fraud* tell.

## Detectors built, validated, and *not* promoted

Same discipline at the detector level: several were built, validated against the labels, and
honestly kept out of the production composite because they showed no usable lift —
`duplicate_address_ring` (null; legitimate co-location dominates), `establishment_overcount`
(weak; doesn't improve the composite), `lender_concentration` (zero lift), `amount_anomaly`
(weak). They remain as opt-in exploratory signals. The composite stays small on purpose.
