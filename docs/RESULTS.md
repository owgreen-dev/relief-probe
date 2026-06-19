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
expansion** — finding look-alikes, recovering labels, expanding from a known lead — and mostly
*not* at **prediction** over a loan's own fields, because prosecuted loans look plausible
individually. (One honest refinement — see **[The twist](#the-twist-prediction-can-beat-the-composite--with-the-full-feature-union-and-a-caveat)** below: a LightGBM over the *full metadata union* does modestly beat the composite, but partly by learning enforcement patterns.)

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

- **Learned PU scorer (PU-bagging) — no lift (overfit, caught by the holdout).** A PU-bagging
  scorer over the detector + structured features showed no improvement on the temporal holdout
  (train ≤2023, test >2023) — and the holdout is what caught it overfitting `forgiveness_ratio`.
  Kept exploratory. *(But a richer **LightGBM** retry did beat the composite — see The twist below.)*

- **Graph ring cold-ranking — no lift.** Ranking loans purely by ring/community structure did
  not beat the composite or chance. Combined with the expansion win above: graph structure is a
  *retrieval* tool, not a *prediction* one.

- **Business-recency (KYB Tier-A) — weak/no independent lift.** Flagging "startup / new business
  / change of ownership" tells didn't concentrate the prosecuted labels — recency is an
  *eligibility* tell, not necessarily a *fraud* tell.

## The twist: prediction *can* beat the composite — with the full feature union (and a caveat)

Loop 6 gave prediction one more rigorous shot: a **regularized LightGBM** over a *composite of
every signal* — all detectors (the ones that worked and the ones that didn't standalone), graph
structural features, a geo-normalized pay-ratio percentile, and categoricals (lender, state,
NAICS, term) — with **nested validation** (entity-grouped k-fold CV tunes; the **>2023 temporal
holdout is the only headline**). On the holdout (train ≤2023 = 204 positives; test >2023 = 164;
population 964,918; base rate 0.017%):

| ranking | mean percentile | recall@2000 (lift, 95% CI) | recall@5000 (lift, 95% CI) |
| --- | --- | --- | --- |
| **LightGBM** | **0.287** | **8.5% (14)** — 41× **[CI 23.5–67.7×]** | **11.6% (19)** — 22× **[CI 12.9–33.0×]** |
| Composite (production) | 0.431 (~random) | 2.4% (4) — 12× [2.9–23.5×] | 5.5% (9) — 11× [4.7–17.7×] |
| PU-bagging (M10) | 0.246 | 1.2% (2) — 6× [0–14.7×] | 5.5% (9) — 11× [4.7–18.8×] |

LightGBM roughly **doubles the composite's recall** in the durable k=500–5000 band, with 95%
bootstrap CIs that clear 1× decisively and sit above the composite's. (The very top, @100/@250, is
**noise for everyone** — CIs span 0, 1–3 loans.) An RRF fusion of LightGBM + composite is similarly
strong (recall@5000 10.4%, CI 10.6–30.6×).

**The caveat that travels with it:** the top features by gain are **`originating_lender`, `term`,
and `state`** — so the model is substantially learning *which lenders' and geographies' loans get
prosecuted* (a real GAO fintech-lender signal **and** DOJ prosecution-selection bias), not purely
"is this loan fraudulent." That's genuinely useful for **lead-ranking**, but it is **not** a guilt
signal — and it stays **exploratory** (never auto-promoted into the production composite, SIGN-010).

So the thesis refines rather than breaks: retrieval is still where AI clearly wins, but row-wise
prediction *does* add real, temporally-honest lift — once you hand a GBM the full metadata union —
with an honest prosecution-bias asterisk. The pure $/job composite was leaving signal on the table.

## Detectors built, validated, and *not* promoted

Same discipline at the detector level: several were built, validated against the labels, and
honestly kept out of the production composite because they showed no usable lift —
`duplicate_address_ring` (null; legitimate co-location dominates), `establishment_overcount`
(weak; doesn't improve the composite), `lender_concentration` (zero lift), `amount_anomaly`
(weak). They remain as opt-in exploratory signals. The composite stays small on purpose.

## The LightGBM learned scorer — prediction's rigorous retry (exploratory; verdict post-loop)

M10's learned PU scorer was a clean negative — but "PU-bagging over a thin feature set" is only
one way to bet on prediction. Loop 6 gives prediction **one more honest, well-designed shot**
before v1: a **LightGBM** model over a **composite of every signal the project built** — the
detectors that worked *and* the ones that didn't standalone, plus graph structural features, a
PLODI-style geo-normalized pay-ratio percentile, and categoricals. The bet: gradient-boosted
trees find **interactions** a linear composite + bagged trees miss.

**Cited as motivation, not as our results — and how we differ:**

- **PLODI** ([s-chadalavada.github.io/plodi](https://s-chadalavada.github.io/plodi/)) — a
  supervised XGBoost on prosecution labels (752 loans / 108 cases) + a geo/industry-normalized
  pay ratio got a real signal, but on a **random 80/20 split**. *We report on a temporal holdout*
  (train ≤2023, evaluate >2023) — the split that caught M10's overfit; a random split leaks
  future-charge patterns and inflates the result.
- **Dicklesworthstone**
  ([github.com/Dicklesworthstone/ppp_loan_fraud_analysis](https://github.com/Dicklesworthstone/ppp_loan_fraud_analysis))
  — a rule engine + a secondary XGBoost that predicts its **own rule-flags** (circular; ROC-AUC
  0.873 on self-labels). *We train on real DOJ prosecution labels only* — never a self-label
  target — so the model is measured against an independent ground truth.

**The methodology (the honest part):** *nested* validation. The **inner** loop is grouped k-fold
CV (grouped by resolved borrower so one entity never spans folds) over the ≤2023 train period,
used **only** to tune hyperparameters + early stopping. The **outer** loop is the **temporal
holdout** — train on charges ≤2023, report lift@k / recall@k / rank-stats on the >2023 holdout.
The harness compares **LightGBM vs PU-bagging vs the composite vs an RRF-fusion (LightGBM +
composite)** on that same holdout — the fusion asks whether LightGBM *adds* even if it doesn't
win alone — and reports LightGBM gain feature-importances. Two leakage guards are the whole
point: **no post-hoc features** (`forgiveness_ratio` and friends are dropped — that's what M10
overfit) and **no label-derived features** (the similarity layer contributes only its
unsupervised scores; the model trains on labels, the features never touch them).

**Disposition — exploratory; the verdict is generated post-loop.** The scorer is **not** promoted
into the production composite (the same discipline as every other method here). The real
temporal-holdout lift/recall is a **heavy compute run done outside the loop**
(`scripts/validate_learned_scorer.py`, read-only) and is reported there, not invented here. Going
in, the prior is a likely **honest negative** — and an honest negative would *confirm* the
retrieval>prediction thesis more rigorously, not refute it. (**Deferred to a follow-up loop:**
label augmentation — deeper multi-defendant LLM extraction and homophily soft-PU — so this loop
isolates the model/validation change against the existing 404 labels.)
