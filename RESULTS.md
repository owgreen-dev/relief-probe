# Results — the one-page version

*A shareable summary of what relief-probe found. The full per-method verdicts are in
[docs/RESULTS.md](docs/RESULTS.md); the loop-by-loop engineering log is in
[docs/NEXT_STEPS.md](docs/NEXT_STEPS.md).*

**relief-probe** is a public-records benchmark: it ranks fraud leads across **11.4M public
PPP loans** using only public federal files, then measures whether those rankings actually
concentrate **real, future DOJ prosecutions** (the charges post-date every loan — an
out-of-time test). It tests interpretable anomaly detectors, positive-unlabeled learning,
graph expansion, similarity retrieval, and LLM-assisted label recovery — and reports the
**negatives, not just the wins**.

## Does the ranking find prosecuted fraud? — Yes, in the band you can trust

On the labelable **$150k+ slice (965,122 loans; base rate 0.034%)**, the default composite
ranking lifts prosecuted loans **23.8× at k=500** over base rate.

| ranking | lift@100 | lift@500 | lift@1000 | recall@5000 |
| --- | --- | --- | --- | --- |
| **Composite** (3 detectors, percentile-combined) | 89.1× (3) | **23.8× (4)** | 11.9× (4) | 5.2% (17) |
| Trivial: `ORDER BY amount/jobs DESC` (one line) | 29.7× (1) | 11.9× (2) | 14.8× (5) | — |
| Dumb: `ORDER BY loan_amount DESC` | 0× (0) | 0× (0) | 5.9× (2) | — |

**The honest caveat:** the eye-catching **@100 number rests on ~3 loans and its 95%
bootstrap CI spans zero** — it's noise. The k≥500 signal is real (the @500 CI clears 1×
decisively, **5.9–47.5×**). And the composite barely beats a one-line `ORDER BY amount/jobs`
sort — so the **ratio is the signal, not the machinery**. That self-critique is the point.

## The "add AI" experiment, scored honestly

Three retrieval wins, five prediction negatives, one *qualified* prediction win:

| Bucket | Outcome |
| --- | --- |
| **Prediction / cold-ranking** (re-score a loan's own fields) | LLM plausibility judge ❌ · name↔NAICS embedding mismatch ❌ · PU-bagging scorer ❌ *(overfit, caught by the holdout)* · **LightGBM scorer ⚠️ qualified win** *(~2× composite recall@5000 — 11.6% vs 5.5% — on the >2023 holdout, CI-backed; but partly learns lender/geo enforcement patterns; exploratory)* · graph ring cold-rank ❌ · business-recency ~❌ |
| **Retrieval / expansion** (bring new info, exploit relationships) | LLM entity resolution ✅ **+79 labels (+24%)** · similar-case homophily ✅ **3.4×** · graph lead-expansion ✅ |

The repeated finding: **AI/ML here wins at *retrieval* — finding look-alikes, recovering
labels, expanding from a known lead — and mostly *not* at *prediction* over a loan's own
fields**, because prosecuted loans look plausible individually.

## Two rankers, one default

- **Composite — the default.** Three unsupervised detectors, percentile-combined.
  Transparent (every lead explains itself: dollars-per-job, payroll-cap, duplicate funding),
  label-free, and unbiased toward enforcement patterns. The ranking you'd defend to an auditor.
- **LightGBM learned scorer — opt-in, exploratory.** ~2× the composite's recall on the
  >2023 holdout (CI-backed) — but not per-loan explainable, and partly learning *where
  enforcement looks* (lender/geo). A power tool with an asterisk; **never the default**.

## Why the numbers mean what they say

- **No leakage.** Production detectors are unsupervised (program rules + statistics, not fit
  to labels); labels are prosecutions dated *years after* the loans. The one learned scorer
  was validated on a temporal holdout (train ≤2023, test >2023).
- **PU + biased labels** → this is **recall-on-known-fraud, not a fraud rate.** Confirmed
  fraud is a tiny (<0.1%), prosecution-biased sample. See [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md).

---

*Every score is a statistical lead for review of public data — never a determination of
guilt. All examples and screenshots use anonymized or synthetic data.
[Try the synthetic demo](README.md#usage) · [Full results](docs/RESULTS.md) ·
[Responsible use](RESPONSIBLE_USE.md)*
