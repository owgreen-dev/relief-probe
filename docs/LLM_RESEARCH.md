# How to use LLMs well in relief-probe (research synthesis, June 2026)

Motivated by the M7 Tier-1 **null result** (an LLM-as-judge re-ranking the top composite
leads by pointwise "semantic plausibility" added no lift). Five parallel web-research
agents investigated better ways to bring AI into the pipeline. They converged. This doc
records the findings, the sources, and a prioritized plan so we don't re-research it.

## Why the Tier-1 null was over-determined (not "LLMs don't help")

Every angle independently reached the same diagnosis — three stacked failure modes plus a
measurement problem:

1. **No new information.** The judge re-scored the *same structured fields the composite
   already used* (dollars-per-job, amount, jobs). Re-judging exhausted features can't add
   signal. Confirmed by **FinFRE-RAG** (arXiv 2512.13040, Dec 2025): direct LLM prompting
   on tabular fraud is ≈ random (F1 0.00–0.14) while XGBoost hits 0.68–0.89.
2. **Pointwise absolute scoring saturates.** LLMs cluster scores ("everything is a 3"), so
   a uniform bonus can't discriminate — a textbook artifact, not a data property. **PRP**
   (Qin et al., NAACL 2024, arXiv 2306.17563): pairwise/relative judgments beat pointwise
   by >10% NDCG precisely because they need no calibration.
3. **Additive blend of uncalibrated scores is the named anti-pattern.** Rank fusion (RRF,
   Cormack 2009; Elastic/OpenSearch/Azure docs) beats "adding a score to a z-score" because
   it uses *ranks, not magnitudes*. Our `composite + 0.5·(implausibility/3)` was exactly the
   thing the fusion literature warns against.
4. **precision@k / lift are formally unreliable on prosecution-biased PU labels.** Under the
   PU selection model, **recall@k and average-rank of known positives are estimable;
   precision@k and lift are not** (arXiv 2509.24228, 2025). Our "356× lift@25" is itself
   optimistically biased and rests on 3 loans.

## Where LLMs genuinely add signal: only on information the composite can't see

### A. Text / semantic signal — the highest-confidence, lowest-cost new axis
The composite never looks at the *meaning* of `borrower_name` or the NAICS description.
That's unused, discriminative, and cheap.
- **Name↔NAICS mismatch as a continuous feature.** Embed the business name and the declared
  NAICS code's text description; the **rank/percentile of the declared code's similarity
  against all NAICS codes** is a continuous mismatch score ("the claimed industry is only
  this name's 800th-closest of 1,000 codes"). This is the honest, non-saturated redo of the
  plausibility idea. Production precedent: **Ramp** does NAICS classification this way;
  **ING industry2vec** ships pretrained NAICS-description vectors. Use rank/margin, not raw
  cosine (short strings are anisotropic and saturate — "Semantics at an Angle," 2504.16318).
- **Shell-name genericness.** GPT-2 perplexity and/or embedding→IsolationForest/ECOD over
  normalized names (strip LLC/Inc first) flags templated/auto-generated names. Baseline to
  beat: the regex name-score in the public `Dicklesworthstone/ppp_loan_fraud_analysis` repo
  (which has *no* semantic name↔industry check — a genuine gap).
- **Embedding-space kNN anomaly.** L2-normalize → PCA ~50–100d → FAISS average-kNN-distance
  (k≈10–50). Proven: simple kNN beats deep AD (DN2 2002.10445; TAD-Bench 2501.11960;
  Text-ADBench 2507.12295). Skip autoencoders/DeepSVDD.
- **Cost is a non-issue:** embed all 11.3M names locally (EmbeddingGemma-308M / bge-small,
  256-d, ~1–2h on one GPU) or via API for ~$2–5 total. Do NOT sample.

### B. External evidence — the strongest *fraud-correlation* evidence
An agentic LLM that fetches evidence the loan row doesn't contain. Grounded in the
authoritative PPP-fraud paper, **Griffin, Kruger & Mahajan, J. Finance 2023** (10.1111/
jofi.13209): ~11% of loans carry ≥1 of four indicators — **(1) non-registered business,
(2) multiple businesses at the same residential address,** (3) abnormal comp/employee,
(4) jobs inconsistency; FinTech lenders ~5× more associated, 9 of top-10 suspicious-rate
lenders were FinTechs.
- **Signals checkable from public data:** SoS **registration date vs loan date** (formed
  right before the loan, or after the Feb-15-2020 eligibility cutoff — 53% of PPP fraud
  cases involved a fabricated/backdated business, Benesch survey); **address type**
  (CMRA / residential / virtual-office for a claimed large employer); **web footprint**
  absence (ProPublica: Kabbage sent 378 loans to fictional businesses in no state records).
- **Sources + access:** OpenCorporates API (free tier 50/day, share-alike — fine for an
  open-source project; forces top-k-only); USPS address APIs (CMRA "Enhanced" not yet
  released — Smarty ~$50/mo gives CMRA/RDI now); SAM.gov Exclusions (free); WHOIS-RDAP
  (free domain age). The 50/day cap *enforces* running over top-k leads only.
- **Pattern:** a single Claude **ReAct tool-use agent** (Yao et al. 2210.03629) per lead;
  closest published analog is the **agentic AML adverse-media screener** (arXiv 2602.23373,
  Dec 2025). Keep entity-matching **deterministic** (name+address+state) *before* the LLM
  reasons — agents misattribute facts across similarly-named entities (DeepHalluBench).
- **Cautions (real):** scraping legality (prefer official APIs; never create accounts to
  bypass gates — *Meta v. Bright Data*); FCRA-adjacent if scoring named *individuals* (CFPB
  Aug 2024) — stay clearly on the research/non-decisioning side; defamation/FP harm on real
  named businesses — every output stays a **LEAD, opinion-framed, human-gated**. Maps to
  RESPONSIBLE_USE.md.

### C. Break the label bottleneck (the binding constraint: only 325 PU positives)
- **LLM-adjudicated entity resolution.** Blocking (Splink, MoJ, DuckDB-native) → LLM
  adjudicates only the ambiguous mid-score band → accept only if LLM-match **AND** our
  existing amount-corroboration agrees. Recovers the sole-prop / DBA / misspelled matches
  our precision-first n-gram resolver drops. **GPT-4 zero-shot ER holds ~87 F1 and—crucially—
  generalizes to *unseen* entities where fine-tuned PLMs drop 22–61 F1** (Peeters & Bizer,
  2310.11244). Never threshold on raw LLM confidence (poorly calibrated); use it for routing
  only. Highest-payoff, lowest-regret way to grow the labels.
- **More label *sources* via LLM extraction** (CourtListener/RECAP free API; DOJ releases) —
  **every extracted (name, amount, program) validated by a join back to the public SBA loan
  data** (catches hallucinations, doubles as the precision gate). Honest caveat: these stay
  the *same prosecution-selected population* — adds volume, not coverage.

## What NOT to do (evidence-backed negatives — don't burn time here)
- **LLM-as-judge re-ranker over structured fields** → dead end (FinFRE-RAG; our own null).
  Keep the existing `triage` only for analyst-facing *explanations* of already-ranked leads.
- **TabPFN** → scale mismatch (designed for ≤~100K rows; we have 11.3M).
- **NVIDIA transaction-foundation-model (+42% AP)** → requires per-entity transaction
  *sequences* we don't have (PPP is largely one-shot loans).
- **Synthetic positives** → model collapse erases the rare-class tail (Nature 2024), tabular
  generators destroy behavioral fraud signal (2604.13125), leakage inflates metrics. Skip.
- **Weak supervision with label functions derived from our own composite** → circular;
  contaminates the benchmark. Only use LFs *orthogonal* to the unsupervised score.

## Cross-cutting disciplines
- **Switch the eval headline to recall@k / average-rank** of the 325 positives (PU-honest);
  keep lift only as a flagged-caveat secondary.
- **If we ever keep a reranker:** RRF rank-fusion (not additive), pairwise/listwise (not
  pointwise), as a **tie-breaker on the top ~20–30 only** (deeper k *hurts* — 2406.18740),
  with swap-symmetry. Tune *one* scalar under leave-one-out CV + 1-SE rule.
- **Keep the benchmark sacrosanct:** no LLM/weak/synthetic label that shares heuristics with
  the composite may enter evaluation; every accepted label clears the external SBA-join gate.
- **Prosecution bias is a *coverage* problem, not an estimator problem** (Hammoudeh & Lowd,
  NeurIPS 2020): never-observed fraud is unrecoverable by any method — only diverse,
  independent label sources expand coverage. PU/propensity (SAR-EM) de-bias only *within*
  observed support.

## Prioritized plan (status — June 2026)
1. ✅ **BUILT — Name↔NAICS embedding-mismatch detector** (`detectors/naics_mismatch.py`,
   `embeddings.py`). Continuous normalized-gap feature; offline lexical default +
   `embeddings`-extra semantic model. Registered exploratory; real-data lift validation
   pending the semantic model. (Methodology fix also built: PU-honest metrics + RRF
   primitive in `benchmark/core.py`.)
2. ⏸ **DEFERRED — Agentic KYB evidence enrichment** (user skipped option 🅑). Strongest
   fraud evidence; revisit when ready for the external-API + legal/ethical surface.
3. ✅ **BUILT + VALIDATED — LLM-adjudicated entity resolution** (`labels/llm_resolve.py`,
   `resolve-labels-llm`). Amount-blocked + LLM name adjudication recovered 7 new labels
   (325→332) from a capped 400-candidate real run — DBA / sole-prop / punctuation variants.
4. ⏭ **NEXT — PU-learning scorer** (PU-bagging first; classical ML) consuming features 1–3,
   validated by held-out-positive recall@k, blended not substituted.

## Key sources
FinFRE-RAG 2512.13040 · PRP 2306.17563 · RankGPT 2304.09542 · RRF (Cormack SIGIR 2009) ·
PU-eval 2509.24228 · Griffin/Kruger/Mahajan JoF 2023 (10.1111/jofi.13209) · ReAct
2210.03629 · agentic AML 2602.23373 · Peeters & Bizer LLM-ER 2310.11244 · Splink (MoJ) ·
DN2 2002.10445 · Text-ADBench 2507.12295 · Ramp NAICS-embedding · ING industry2vec ·
Bekker & Davis PU survey 1811.04820 · Hammoudeh & Lowd 2002.10261 · model-collapse
(Nature 2024). Full per-claim URLs are in the agent briefs (session transcript).
