# Architecture Decision Records

The "why did you choose X" answers, written down. Each record is a real decision this
project made, the alternative it rejected, and the cost it paid for the choice —
because the interesting part of a decision is what it *cost*, not what it bought.

These are deliberately opinionated. Where a choice trades a bigger headline number for
a more honest one, that's the choice — and the record says so.

| # | Decision | One-line why |
| --- | --- | --- |
| [0001](#adr-0001-a-single-file-duckdb-warehouse-not-spark-or-a-cloud-db) | Single-file DuckDB warehouse | 11.4M loans, one row per loan, reproducible by a stranger on a laptop |
| [0002](#adr-0002-positive-unlabeled-evaluation-not-binary-classification) | Positive-unlabeled evaluation | The 11.4M unprosecuted loans are *unlabeled, not innocent* |
| [0003](#adr-0003-out-of-time-validation-never-a-random-split) | Out-of-time validation | A random split leaks future-charge patterns and inflates the result |
| [0004](#adr-0004-a-transparent-unsupervised-composite-is-the-default-ranker) | Transparent composite is the default | The learned model wins on recall but partly learns *who gets prosecuted* |
| [0005](#adr-0005-build--validate--dispose-negatives-are-published-results) | Build → validate → dispose | An honest negative is a result; the composite stays small on purpose |
| [0006](#adr-0006-aiml-is-deployed-for-retrieval-not-row-wise-prediction) | AI for retrieval, not prediction | Prosecuted loans look plausible one at a time; fraud clusters into rings |

---

## ADR-0001: A single-file DuckDB warehouse, not Spark or a cloud DB

**Status:** Accepted · **Interview question:** *"11 million rows — why not Spark / BigQuery / Postgres?"*

**Context.** The core population is the SBA PPP FOIA release: **11.4M loans, one row per
loan**, with no time dimension on the entity (every loan was originated in 2020–2021 — the
"forward" axis lives entirely in the labels, which are DOJ charges that post-date the loans).
The whole pitch is *reproducible by a stranger from public files, on a laptop, no cluster.*
A distributed engine or a hosted warehouse would contradict that pitch and add setup the
reviewer has to pay before seeing a single number.

**Decision.** One embedded, columnar **DuckDB** file (`data/relief_probe.duckdb`). Detectors
push down aggregation into SQL (the README figure bins 11.4M rows into a small grid *in the
database* — it never pulls the rows into Python). Every analytic connection opens
`read_only=True` so a benchmark or figure run can never mutate the warehouse.

**Consequences.**
- ✅ `git clone` → `make warehouse` → `make benchmark` on a laptop; no accounts, no cluster, no per-query cost. The ~430 MB warehouse rebuilds from public sources.
- ✅ Columnar scans over 11.4M rows are sub-second; the log–log density grid and lift curves compute interactively.
- ⚠️ Single-writer, single-node. This is a **research/benchmark** warehouse, not an OLTP serving layer — DuckDB would be the wrong choice for concurrent production writes, and this repo isn't that.
- ⚠️ The file isn't committed (it's reproducible, and it's 2 GB); the reproducibility guarantee is the *pipeline*, not the artifact.

---

## ADR-0002: Positive-unlabeled evaluation, not binary classification

**Status:** Accepted · **Interview question:** *"What's your false-positive rate?"* (a trap — see below)

**Context.** Confirmed fraud is a tiny (<0.1%), **prosecution-biased** sample: 404
entity-resolved DOJ labels against ~965k labelable loans. The 11.4M loans without a label are
**not** confirmed-legitimate — the vast majority have simply never been investigated. Treating
"not yet charged" as a negative is the naive-supervised trap: it scores *unlabeled* as
*innocent*, trains a model to reproduce prosecution-selection bias, and reports a "fraud rate"
that is really an artifact of who got caught.

**Decision.** Frame the whole evaluation as **positive-unlabeled (PU)**. Report
**recall-on-known-fraud**, never a fraud rate or a false-positive rate. The metrics that survive
PU honestly are (a) lift@k / recall@k of the known positives, and (b) the **rank of the known
positives** in the ranking (mean percentile, median rank) — which is estimable under PU when a
raw precision is not. Every headline is captioned "recall-on-known-fraud, not a fraud rate."

**Consequences.**
- ✅ No overclaiming: a lead is "this loan resembles the pattern of *charged* cases," never "this loan is fraudulent." That framing carries straight into [RESPONSIBLE_USE.md](../RESPONSIBLE_USE.md).
- ✅ The right answer to "what's your false-positive rate?" is *"the question isn't well-posed under PU, and here's why"* — which is a stronger answer than a fabricated number.
- ⚠️ The numbers are smaller and need a paragraph of explanation. A binary framing would produce a shinier (and dishonest) headline.
- ⚠️ The label set is biased toward egregious, *caught* cases, so even the recall is a recall against a biased positive sample — stated explicitly rather than hidden.

---

## ADR-0003: Out-of-time validation, never a random split

**Status:** Accepted · **Interview question:** *"How do you know it's not overfitting / leaking?"*

**Context.** The labels are DOJ prosecutions that land *years after* the loan (2024–2026 charges
under the 10-year statute on 2020–2021 loans). A random 80/20 train/test split would put a
defendant's 2025 charge in the training set and score a co-conspirator's loan in test — leaking
future-charge and ring-membership patterns and inflating every metric. Two cited prior efforts
(PLODI, on a random split; Dicklesworthstone, predicting its *own* rule-flags) show exactly how
good a leaked or circular evaluation can look.

**Decision.** The only headline is a **temporal holdout**: train on charges **≤ 2023**, evaluate
on the **> 2023** holdout. For the learned scorer, this is the *outer* loop of a **nested**
design — the *inner* loop is entity-grouped k-fold CV (grouped by resolved borrower so one entity
never spans folds), used only to tune hyperparameters and early stopping. Two leakage guards are
load-bearing: **no post-hoc features** (`forgiveness_ratio` and friends are dropped — that's what
an earlier PU-bagging scorer overfit) and **no label-derived features** (the model trains on
labels; the features never touch them).

**Consequences.**
- ✅ The temporal holdout is what *caught* the PU-bagging scorer overfitting `forgiveness_ratio` — the validation earned its keep by killing a method.
- ✅ Directly answers the leakage question and distinguishes this work from the random-split prior art.
- ⚠️ Smaller positive counts per period (train ≤2023 ≈ 204 positives; test >2023 ≈ 164) → wider confidence intervals. Reported with 95% bootstrap CIs rather than hidden.
- ⚠️ The production detectors are **unsupervised** anyway (program rules + statistics, never fit to the labels), so for the default ranker "leakage" is moot by construction — the temporal discipline exists for the *learned* experiments.

---

## ADR-0004: A transparent, unsupervised composite is the default ranker

**Status:** Accepted · **Interview question:** *"Your LightGBM doubles recall — why isn't it the product?"*

**Context.** Two rankers exist for two jobs. The **composite** is three unsupervised detectors
(dollars-per-reported-job, payroll-cap, duplicate funding), percentile-combined — transparent,
label-free, every lead explains itself. A regularized **LightGBM** over the full feature union
does measurably better on held-out recall (**11.6% vs 5.5% recall@5000** on the >2023 holdout,
CI-backed — roughly 2×). The obvious move is to ship the model as the default.

**Decision.** Ship the **composite** as the default; keep the LightGBM **exploratory** and never
auto-promote it into production (`SIGN-010`). The reason is *what the model learns*: its top
features by gain are **`originating_lender`, `term`, and `state`** — it is substantially learning
*which lenders' and geographies' loans get prosecuted* (a real fintech-lender signal **and** DOJ
prosecution-selection bias), not purely "is this loan fraudulent." That is genuinely useful for
**lead-ranking** but is **not a guilt signal**, and it is not a ranking you could defend to an
auditor without a bias asterisk.

**Consequences.**
- ✅ The default ranking is one you can explain line-by-line and defend as unbiased toward enforcement patterns — the right default for a tool that names real defendants.
- ✅ The model isn't thrown away: it's documented as a CI-backed *qualified win*, and an RRF fusion (LightGBM + composite) is reported for anyone who wants the recall with eyes open.
- ⚠️ The default leaves measured lift on the table — by choice. The honest framing ("prediction *can* beat the composite, with a prosecution-bias caveat") is worth more than the extra recall.
- ⚠️ Two rankers means every headline number must name *which* ranker produced it. The docs do.

---

## ADR-0005: Build → validate → dispose; negatives are published results

**Status:** Accepted · **Interview question:** *"What did you try that didn't work?"*

**Context.** It's easy to build a dozen clever detectors and an "add AI" layer and quietly keep
only the ones that look good. That produces a pile of methods with no discipline and a README
that hides its failures — the opposite of credible.

**Decision.** Every method follows **build → validate on real labels → honest disposition**, and
disposition is a first-class outcome recorded in the docs. Methods are **kept or killed on
held-out evidence**, and the negatives are written down as results, not deleted. Concretely,
several detectors were built, validated, and **not promoted** — `duplicate_address_ring` (null;
legitimate co-location dominates), `establishment_overcount` (weak), `lender_concentration`
(zero lift), `amount_anomaly` (weak) — and five "add AI" bets came back negative (LLM plausibility
judge, name↔NAICS embedding mismatch, PU-bagging scorer, graph cold-ranking, business-recency).

**Consequences.**
- ✅ The production composite stays **small on purpose** — three detectors that earned their place, not twelve that didn't.
- ✅ The null results *are* the differentiator: they're the evidence the discipline is real, and they make the wins believable.
- ✅ Reproducible by construction — every verdict regenerates read-only from the warehouse via `make validate` (the `scripts/validate_*.py` harnesses), so no number is hand-typed and left to rot.
- ⚠️ Slower than "ship what looks good." Each method costs a validation harness before it's allowed an opinion.

---

## ADR-0006: AI/ML is deployed for retrieval, not row-wise prediction

**Status:** Accepted · **Interview question:** *"Where does the LLM / the graph / the embeddings actually help?"*

**Context.** The reflexive "add AI" move is to have a model score each loan's own fields and
predict fraud. But prosecuted PPP loans look **plausible individually** — the fraud is fabricated
*dollars* and *jobs*, not a wrong industry or an implausible story — so row-wise prediction over a
loan's own fields mostly fails (see the five negatives in ADR-0005). What *is* true is that fraud
clusters into **rings and templates**.

**Decision.** Point the AI/ML tooling at **retrieval and expansion**, where relationships and
outside information pay off, not at cold prediction:
- **LLM entity resolution** reads the DOJ press release and recovers fuzzy labels the exact matcher misses — **+79 labels (+24%)**, growing the benchmark from 325 to 404. The LLM brings *new information* a structured join can't.
- **Similar-case retrieval** (hybrid name-semantics + dollar band + area) finds a prosecuted loan's look-alikes at **3.4× homophily** — an investigation tool for expanding a known ring, not a predictor.
- **Graph lead-expansion** over shared-address / entity / similarity edges surfaces structurally connected loans from a known seed.

**Consequences.**
- ✅ Each AI component is deployed exactly where it demonstrably wins, and the wins are on real labels, out-of-time.
- ✅ The thesis is falsifiable and was *refined* by evidence, not asserted: the LightGBM twist (ADR-0004) shows prediction *can* add lift with the full metadata union — so the claim is "retrieval clearly wins; prediction adds honest-but-biased lift," not "prediction never works."
- ⚠️ The vocabulary matters: "similar cases" is framed as *find the rest of the ring*, never *this neighbor is guilty*. A resemblance is a lead for review, not proof.

---

*These records are pointers, not the full story — the per-method numbers and caveats live in
[RESULTS.md](RESULTS.md) (reader version) and [NEXT_STEPS.md](NEXT_STEPS.md) (engineering log),
and the legal framing in [RESPONSIBLE_USE.md](../RESPONSIBLE_USE.md).*
