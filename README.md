# relief-probe

**An open-source PPP/SBA pandemic-loan fraud-lead lab — anomaly detection over public loan data, validated against *future* DOJ/OIG fraud prosecutions, with an agentic investigation layer and a document-authenticity (vision) tab.**

Everything here is reproducible by a stranger from public federal files (SBA FOIA loan data; DOJ/SBA-OIG enforcement records). It runs on a laptop against a local DuckDB warehouse — no cluster required.

> A high score is a **statistical lead for review, not evidence of fraud.** Scores reflect anomalies relative to peers and rule patterns on public data. See [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md).

---

## Why this is hard (and the honest framing)

PPP fraud is estimated at ~$200B (SBA-OIG), but **confirmed/charged fraud is a tiny (<0.1%) and biased sample** — it over-represents egregious, *caught* cases (DOJ has charged 3,500+ defendants). So this is a **positive-unlabeled (PU)** problem, and results are reported as **recall-on-known-fraud, not a true fraud rate**. That honesty is the point — and the multi-year prosecution lag (10-year statute, charges still landing 2024–2026) makes the out-of-time validation genuine: the labels strictly post-date every loan.

## Architecture

Layers mirror a real program-integrity shop; each is independently demoable:

```
ingest/      Layer 1 — Warehouse:    resolve + download public SBA CSVs → DuckDB (one row per loan)   ✅
detectors/   Layer 2 — Detection:    self-contained scheme modules → unified signals table             ✅
labels/      Layer 3 — Labels:       scrape DOJ enforcement → entity-resolve to loan_number             ✅
benchmark/   Layer 4 — Validation:   rank loans, measure how charged-fraud concentrates at the top      ✅
vision/      Layer 5 — Documents:    ELA forgery-detection plumbing, demoed on SYNTHETIC splices       ✅
agent/       Layer 6 — Investigation: agentic, tool-grounded loan-investigator + MCP server             ✅
triage/      Layer 7 — LLM cascade:  escalate only the top-k composite leads to a plausibility judge    ✅ (Tier 1)
similarity/  Layer 8 — Similar cases: hybrid (semantic+keyword+$/area) look-alike retrieval for rings  ✅
graph/       Layer 9 — Fraud rings:  multi-relational loan graph (address+entity+similarity) + community ⚗️ (exploratory)
```

Output contract: every detector emits `(loan_number, detector_id, score, evidence_json)` into one `signals` table.

## Headline result (and an honest baseline check)

The warehouse holds the **full ~11.3M-loan** PPP population (every public FOIA slice).
All **325** DOJ-prosecuted labels (entity-resolved from 3,414 enforcement releases) fall
in the public **$150k+ disclosure slice**, so we measure lift on that **965,122-loan**
labelable slice for an apples-to-apples base rate (0.034%); ranking the whole 11.3M only
inflates lift via a 10× larger denominator, so full-population **recall** is reported
separately (7 of 325 in the top 5,000 ≈ 2.1%). Crucially, we compare the detector
machinery against **dumb baselines** — because the right question isn't "what lift do I
get?" but "do my robust-z + FDR + cohort detectors beat a one-line sort?" Lift over base
rate (raw hit counts in parens):

| ranking | lift@100 | lift@500 | lift@1000 | recall@5000 |
| --- | --- | --- | --- | --- |
| **Composite** (3 detectors, percentile-combined) | 89.1× (3) | **23.8× (4)** | 11.9× (4) | 5.2% (17) |
| Trivial: `ORDER BY amount/jobs DESC` (one line) | 29.7× (1) | 11.9× (2) | 14.8× (5) | — |
| Dumb: `ORDER BY loan_amount DESC` | 0× (0) | 0× (0) | 5.9× (2) | — |

Composite lift with **95% bootstrap CIs** (2,000-resample Poisson bootstrap, the honest
error bars on those single-digit hits): **@100 0.0–207.9×**, **@500 5.9–47.5×**,
**@1000 3.0–23.8×**, @5000 5.3–14.8×. The composite now includes a third detector,
`multiple_funded_loans` (entity-resolved duplicate funding), promoted after it showed
genuine *independent* lift (≈18–21× at k=500–1000, near-zero overlap with the $/job
detectors); it lifts the very top (k=100/250) and recall@5000 (14→17 hits) over the
two-detector composite. Note @100's headline rests on 3 loans (CI spans 0), so the
durable wins are the recall bump and the k=250–500 concentration, not the point @100.

What this honestly shows:
- **The k=100 number is noise; the k≥500 signal is real.** The eye-catching 29.7×@100
  rests on a *single* loan — its 95% CI (0–89×) includes zero, so it is not
  distinguishable from chance. But from k=500 outward the CIs clear 1× decisively
  (5.9–47.7× at k=500), so the concentration of prosecuted loans near the top is a
  genuine effect, not an artifact. **Trust the @500–5000 band, not the headline @100.**
- **The core signal is real.** Dollars-per-reported-job decisively beats raw loan
  amount (which finds *nothing* in the top 500). Normalizing by jobs is doing the work.
- **The fancy stats add only a little.** The cohort-z/FDR composite clearly beats a
  one-line `amount/jobs` sort at k=500 (4 hits vs 2) but the trivial sort edges it back
  at k=1000 (5 vs 4). The methodology is sound hygiene, but most of the signal is the
  ratio, not the machinery.
- **Estimates are noisy — and now quantified.** The bootstrap CIs above replace
  hand-waving about "single-digit hit counts" with actual intervals. Labels are a small,
  **prosecution-biased PU sample** → this is **recall-on-known-fraud, not a fraud rate**.
- **No train/test leakage.** The detectors are **unsupervised** — thresholds come from
  program rules and statistics, not from fitting to the 325 labels — so nothing is
  trained on the answers, and the labels are prosecutions dated *years after* the loans.
  (The entity resolver was tuned for match *precision*, not for benchmark lift.) A
  held-out split only becomes necessary once something is *fit* to the labels — see the
  temporal-holdout plan (H7) for the learned-scorer path.

Reproduce: `relief-probe ingest && relief-probe fetch-labels && relief-probe resolve-labels && relief-probe benchmark` (the table is `benchmark`'s baseline comparison).

> Numbers above are on the **$150k+ slice**, where all 325 prosecuted labels live. The full ~11.3M-loan population is ingested; ranking it only inflates lift via a 10× larger denominator (the under-$150k slice added 0 prosecuted labels), so the slice is the honest evaluation universe and full-population recall is reported separately.

## Status

All six layers built and tested; layers 1–4 and 6 verified on real data, the vision layer (5) demonstrated on synthetic splices only (plumbing, not a validated capability). See [NEXT_STEPS.md](NEXT_STEPS.md).

- **Detectors (in the default composite):** `naics_cohort_outlier` (robust cohort z-score, BH-FDR) · `payroll_cap_exceedance` (per-employee program ceiling) and `multiple_funded_loans` (entity-resolved duplicate funding, promoted into the composite after real-data validation — see below).
- **`duplicate_address_ring` — a validated *negative* (kept, but out of the composite).** A co-location / link-analysis detector that groups loans by a normalized building-level address key and flags addresses shared by ≥3 *distinct* borrowers. It was built to answer the fair critique that the two $/job detectors are "two views of the same ratio," and it *is* genuinely orthogonal to them (real-data Jaccard ≈ 0.02 vs payroll, ≈ 0.001 vs naics). **But on the real warehouse it has no validated lift:** prosecuted loans sit in shared-address rings at essentially the base rate at *every* ring-size threshold (lift ≈ 0.6–1.0×), because address clustering is dominated by legitimate co-location (office parks, strip malls, apartments, registered-agent addresses) and the prosecuted labels are large-dollar single-borrower schemes. So it is **excluded from the default composite** (it only diluted the mid-tail) and kept as an opt-in investigation/evidence signal. Reporting an honest negative — an independent signal that *doesn't* move the needle against the labels we have — is the point, not a failure. (False-positive modes are exactly that legitimate co-location, so any ring is a review lead, never proof.)
- **Exploratory detectors — built + tested, NOT yet in the default composite (pending real-data validation).** Research-driven candidates targeting fraud patterns *different* from the dollars-per-job ratio that live in `registry.exploratory_detectors()` (resolvable by id, opt-in via `run_all(con, detectors=...)`) but stayed out of the composite because real-data validation showed no usable lift — exactly the discipline the ring detector taught. (`multiple_funded_loans`, the other Loop-1 candidate, *was* validated and promoted — see the production list above.)
  - **`amount_anomaly`** — per-loan signal that the approved amount looks *fabricated/reverse-engineered* rather than payroll-derived, via two documented sub-signals: (a) **round-number** — the amount is an exact round multiple ($1k / $5k / $10k), graded so rounder scores higher; (b) **cap-maximization ("bunching")** — the implied per-employee loan (`amount / jobs_reported`) sits at or just below the program's per-employee ceiling ($20,833 general; $29,167 for NAICS 72), i.e. the borrower claimed the maximum allowable salary for *every* employee. Motivated by the forensic-finance literature on round-number / cap bunching as fabrication tells (Griffin et al.). Distinct from `payroll_cap_exceedance`, which flags amounts *above* the cap — the bunching band is at/just-below, so the two don't double-count. **Validated weak on real data** (~13% of loans flagged, ~0 lift through k=1000), so it stays exploratory. Honest false-positive mode: some legitimate loans are genuinely round, and genuinely small payroll-light businesses can sit at the cap, so a hit is a review lead, not proof.
  - **`multiple_funded_loans`** *(promoted to the production composite — listed above; kept here for the full description)* — entity-resolution signal: groups loans by a canonical borrower key (normalized name + building-level address) and flags entities holding *more funded loans than the program's one-per-draw rule allows* — ≥2 loans of the same draw type (first-draw `PPP` or second-draw `PPS`), or >2 funded loans total. Scored monotonically in the excess loan count. Motivated by the GAO finding of tens of thousands of recipients with multiple funded loans. This is the precise, entity-resolved version of the duplicate-address idea (the *same resolved borrower* across many loans, not many *distinct* borrowers at one building). Honest false-positive mode: imperfect entity resolution can merge genuinely distinct borrowers that share a normalized name + address, so a hit is a review lead, not proof.
  - **`establishment_overcount`** *(Loop 2 — validated weak, kept exploratory)* — a **density** signal orthogonal to the dollars-per-job ratio: it ignores loan size entirely and flags cells where *far more PPP loans were made than there are real businesses to receive them.* For each `(borrower_zip × NAICS)` cell it compares the count of PPP loans to the establishment count from **Census ZIP Business Patterns (ZBP)** and fires when `ratio = ppp_loan_count / max(establishments, 1)` exceeds a threshold (`min_ratio`, default 4×), scoring every loan in the cell by `log(ratio)`. The NAICS rollup (`naics_digits`, default 6-digit; coarsen to 4/2 for denser ZBP coverage) is a documented constructor choice. Motivated by **Griffin, Kruger & Mahajan (J. Finance 2023)**, who found ~19% of first-draw loans (≈36% for fintech-originated) were "excess" relative to local establishment counts. Needs a new public-data join, so the Census ZBP file is a **manual public download** ingested via `relief-probe ingest-establishments PATH` (see Quickstart). Honest false-positive modes: the ZBP vintage predates 2020–21 business growth; home-based/online/gig businesses legitimately lack a local establishment; thin cells (1–2 establishments) make the ratio jumpy. Cells with **no** matching ZBP row are *skipped*, not flagged (absent ≠ zero), a deliberate false-negative-over-false-positive choice. **Real-data verdict:** validated against the DOJ labels (after a 5-digit-ZIP fix to join ZIP+4 loans), it has weak *standalone* independent lift (≈18×@500, recall ~1.8%@5000; Jaccard <0.01 vs the other detectors) but **does not improve the composite** — the few prosecuted loans it surfaces are already caught at those ranks — so it stays exploratory. A hit is a review lead, not proof.
  - **`lender_concentration`** *(Loop 3 — validated zero-lift, kept exploratory)* — an **unsupervised, peer-relative, lender-level** signal: instead of scoring each loan on its own numbers, it groups loans by `originating_lender` (only lenders with `>= min_loans`, default 100, so the rate is stable), computes each lender's **rate of cap-busting loans** (the label-free program-rule predicate `amount / jobs_reported >= the per-employee ceiling` — $20,833 general; $29,167 for NAICS 72, reusing the `payroll_cap` constants), robust-z's that rate **across all qualifying lenders** (`stats.robust_z`, median/MAD with a `min_mad` floor), and flags **every** loan from a lender whose z `>= min_z` (default 3.0). The score is the lender's z — one anomaly value per book. The point is a structural signal the per-loan detectors miss: it flags even the *individually-clean* loans that came from a book unusually full of cap-busting originations. Motivated by the **GAO** finding that a handful of nonbank/fintech auto-approval lenders originated a disproportionate share of fraud-case loans. **Deliberately label-free (`SIGN-012`): it never reads `fraud_cases` or any label table** — the signal comes from program rules + cross-lender statistics only, because training on the prosecution labels would both leak the answer and inherit the labels' prosecution bias (the benchmark must stay an independent validator, not part of the detector); this is proven by a test that fires on a warehouse with an **empty** `fraud_cases` table. It also avoids raw **volume** (big banks legitimately have huge books) by keying on the *rate*. **Real-data verdict:** validated against the DOJ labels, it showed **zero lift** (0 prosecuted hits in the top 5,000; ~3% of loans flagged across 324 lenders, independent of the other detectors but uncorrelated with the prosecuted labels), so it stays exploratory — the high-cap-busting lenders just aren't where the prosecuted fraud sits. Honest false-positive mode: a high cap-busting rate can reflect a lender's legitimate **industry mix** rather than fraud — a book concentrated in NAICS-72 (food/accommodation, where the per-employee cap is higher and payroll-light businesses cluster) can look cap-heavy for benign reasons — so a hit is a review lead, not proof.
  - **`naics_name_mismatch`** *(semantic — the honest redo of the LLM plausibility idea)* — does the borrower NAME fit its declared NAICS INDUSTRY? The composite ranks on the *numbers* and never reads the text; this embeds each business name and every candidate industry title and scores the declared industry's **mismatch as a normalized gap** below the best-matching industry — a *continuous* feature (unlike the saturated 0-3 LLM judge that gave a null result; see [docs/LLM_RESEARCH.md](docs/LLM_RESEARCH.md)). Ranks against bundled canonical 2-digit NAICS sector titles by default; load finer titles via `ingest-naics`. Three embedders: offline `HashingEmbedder` (lexical proxy, default), torch-free `Model2VecEmbedder` (`embeddings-lite` extra — semantic, no GPU), and `SentenceTransformerEmbedder` (`embeddings` extra, heavy). **Real-data verdict: a validated NEGATIVE.** Ranking 20k sampled $150k+ loans + the labels by mismatch (`scripts/validate_naics_mismatch.py`) showed **no concentration** — semantic mean percentile ~0.49 (≈ random), lift **< 1.0× at every k**. Prosecuted loans aren't industry-mismatched; their declared NAICS looks plausible (the fraud is fabricated dollars, not a wrong industry). Echoes the Tier-1 null. **Kept exploratory, not promoted** — an honest negative, same discipline as the other exploratory detectors.
  - **`fraud_ring_graph`** *(Loop 4 — the multi-relational graph layer; built + tested, real-data validation is a MANUAL post-loop step)* — the **relational** bet: model the $150k+ slice as a graph and ask whether *coordinated ring structure* concentrates prosecuted loans where row-wise scoring couldn't. `graph/build.py::build_loan_graph` links loans by **three** label-free edge types — **address** (shared normalized building-level key, reusing `detectors/_address.normalize_address`), **entity** (same resolved borrower / duplicate funding, reusing `detectors/_entity.entity_key`), and **similarity** (high name + amount-band + same-area look-alikes, reusing the `similarity` blocking + an offline `HashingEmbedder`) — then `graph/features.py` computes label-free structural features (component size, per-edge-type degree, distinct borrowers, and **community size** via NetworkX `greedy_modularity_communities`). The detector fires on every loan in a component spanning **≥2 distinct edge types AND ≥2 distinct borrowers**, scored by a structural quantity monotonic in ring strength (`log1p(distinct_borrowers) + log1p(community_size)`). **WHY a graph:** the two relational *wins* this project (LLM entity-resolution +79 labels; name+amount+area homophily ~3.4×) say fraud here is **coordinated/relational**, not row-wise — so generalize that signal into a graph and let community detection separate real rings from benign clustering. **HONEST address-alone-null callback (do not re-litigate):** the single-edge-type `duplicate_address_ring` (shared address *alone*) was already validated **NULL** — legitimate co-location dominates — so this layer's whole bet is that *combining* edge types + communities does what address alone could not. Built **LABEL-FREE** (SIGN-012 — never reads `fraud_cases`; proven by an empty-`fraud_cases` test), **sparse-by-blocking** (link only within a shared-key group; skip groups above `max_group` so a giant shared key can't form a clique), with NetworkX imported lazily behind the **`graph` extra**. Registered in `registry.exploratory_detectors()` **only** (SIGN-010); `all_detectors()` UNCHANGED — promotion is a manual decision *after* `scripts/validate_ring_graph.py` measures held-out concentration vs the composite on the real warehouse. **An honest NEGATIVE (ring structure no better than the composite/chance) is an acceptable, documented outcome.** Honest false-positive modes: legitimate co-location and shared infrastructure — office parks, strip malls, apartment buildings, registered-agent addresses, and shared lenders — naturally form dense subgraphs, so any ring is a review lead, never proof.
  - **`business_recency`** *(Loop 5 — the Tier-A KYB signal; built + tested LABEL-FREE, real-data validation is a MANUAL post-loop step)* — the **external-evidence** bet's free, no-API half: the loans table already carries `business_age_description` (100% populated) + `date_approved` (100%), so a label-free detector can flag the *business-recency tells* without any API call. It fires with an **ordinal** label-free score on the new/startup/change values — **"Startup, Loan Funds will Open Business"** strongest (a near-explicit eligibility red flag, since PPP required the business to be operating by **Feb 15 2020** — "funds will open the business" says it was not), then **"New Business or 2 years or less"**, then **"Change of Ownership"** — and deliberately does **NOT** fire on "Existing or more than 2 years old", "Unanswered", null, or blank (never score missing-as-suspicious — that manufactures lift from a data-quality artifact). **WHY:** the published fraud evidence here is the strongest of any avenue — Benesch reports **53%** of PPP fraud involved a fabricated/backdated business, and Griffin, Kruger & Mahajan (J. Finance 2023) validate "non-registered business" + "formed right before the loan" as fraud indicators. Built **LABEL-FREE (SIGN-012** — never reads `fraud_cases`; proven by an empty-`fraud_cases` test) and read-only. Registered in `registry.exploratory_detectors()` **only** (SIGN-010); `all_detectors()` UNCHANGED — promotion is a manual decision *after* `scripts/validate_business_recency.py` measures held-out concentration vs the composite on the real warehouse. **An honest NEGATIVE is an explicitly acceptable outcome here:** business recency is an *eligibility* tell, not necessarily a *fraud* tell, so it may not concentrate the prosecuted labels — and the score is a coarse 4-level ordinal (large ties at 0). Honest false-positive mode: plenty of legitimate businesses were genuinely new, changed hands, or self-described as a startup, so a hit is a review lead, not proof. See the **KYB layer** section below for the Tier-B external half.
- **Labels:** DOJ press-release scraper + precision-tuned entity resolution (amount-corroborated) → 325 labels, **hand-validated at ~84–88% precision** (95% CI ≈ [72%, 92%]; all false positives concentrate in the weaker no-state / approximate-amount match tiers, while the exact `name+state+amount` tier is ~93%+ clean — see [docs/LABEL_PRECISION.md](docs/LABEL_PRECISION.md)).
- **Vision:** Error Level Analysis (ELA) document-forgery *plumbing* + Streamlit dashboard (Loan-leads + Document-authenticity tabs). Honest scope: the detector is demonstrated only on **synthetic spliced images** (trivially separable, so it proves the wiring — not real-world forgery detection). Real anchors (IDNet, "Find it again!") are wired but not run; no real-document accuracy is claimed.
- **Agent/MCP:** tool-grounded, deterministic-first loan investigator (`relief-probe investigate`) and an MCP server (`relief-probe serve-mcp`) over the same four read-only warehouse tools.
- **Triage cascade (M7 Tier 1 — built + validated; a measured *negative*, kept opt-in):** a cost-shaped LLM cascade (`relief-probe triage --top-k N [--llm] [--gate]`). Tier 0 (the composite) ranks all ~11.3M loans for free; Tier 1 escalates **only the top-k leads** to a *semantic plausibility* judge — "could this business plausibly justify this loan?" over `borrower_name × NAICS × amount × jobs × payroll_proceed` — and blends a 0–3 implausibility into a transparent re-rank (`composite + 0.5·(implausibility/3)`). World knowledge catches mismatches statistics can't (an "Elite Nail Spa LLC" coded as landscaping at $2.1M for 1 job). Two judges share one interface: a **deterministic `heuristic_judge`** (offline, no key — structured-field tells; the *baseline*) and a concurrent, robust **`LlmJudge`** (Haiku 4.5, strict 0–3 structured output + rubric + few-shot, bounded `--concurrency`, lazily imported behind the `agent` extra). A **hard cap (2,000 loans)** bounds and logs how many ever reach the LLM — the cost ceiling; the LLM **never** runs over the full population. Cost design: ~$2–4 per run (Batch API + caching) vs ~$8–16k to run the LLM over all 11.3M loans (~99.97% saving) — see [docs/M7_PLAN.md](docs/M7_PLAN.md).
  - **Real-data verdict (`--llm --gate` on the 11.3M warehouse / 325 labels): no lift — an honest negative.** The validation gate compares composite-only vs triage-reranked lift@k on the $150k+ slice (re-ranking only the top-k, so k≥top_k is unchanged by design). Re-ranking the top 300 with Haiku **did not concentrate prosecuted loans better** and `regressed` the very top by exactly **one loan** (3→2 hits at k=25/50; identical at k≥100) — a single-loan swing well inside the bootstrap noise (the same H3 lesson that top-k rests on 1–3 loans). The judge's calls look sensible on their face (egregious on $X-million single-job loans), but its plausibility ordering simply isn't aligned with *what got prosecuted* — the composite already nails the top, and some prosecuted loans look "plausible" to the LLM. Caveats both ways: the PU labels can't reward fraud the DOJ never charged (the LLM may surface *different* real fraud), and the blend is coarse. So Tier 1 stays **built and opt-in (the `triage` CLI), not promoted into any default ranking** — the same build→validate→disposition discipline as `duplicate_address_ring` / `lender_concentration`. Reporting an LLM feature that *didn't* beat a transparent baseline is the point, not a failure.

- **Similar-case retrieval (the LLM-for-retrieval win):** `relief-probe similar <loan_number>` (and a "Similar cases" dashboard tab) finds a loan's look-alikes by a hybrid of business-name **semantic** + **keyword** similarity (RRF-fused) plus dollar/area/industry proximity — *blocking-first* on the $150k+ slice so it never embeds the whole warehouse. NOT a predictor; a retrieval/investigation tool that surfaces rings/templates and flags which look-alikes are already prosecuted, with an optional grounded LLM explanation (BYOK). **Validated positive:** `scripts/validate_similar_homophily.py` shows **homophily lift ~3.4×** — a prosecuted loan's nearest look-alikes are ~3.4× more likely to be prosecuted than chance (fraud clusters into rings, and the tool surfaces it). This is the clean counterpoint to the *prediction* attempts (LLM reranker, embedding mismatch) that came back null: AI here earns its keep at **retrieval**, not scoring. A resemblance is a lead for review, not proof.

## KYB layer — external-evidence verification (Loop 5, exploratory)

The session's meta-finding is that AI/ML earns its keep at **retrieval / bringing NEW
information**, not at row-wise prediction over the loans' own fields (prosecuted loans look
plausible individually — four honest prediction negatives this project). The one untried
"new information" avenue is **KYB (know-your-business)**: verifying a borrower against
*external* sources — does it exist, was it registered before the loan, is the address real?
This is the angle with the strongest published fraud evidence (Benesch: **53%** of PPP fraud
involved a fabricated/backdated business; Griffin, Kruger & Mahajan, *J. Finance* 2023:
"non-registered business" + "formed right before the loan" are validated indicators). It is
built in **two tiers**, with a hard autonomy boundary between them:

- **Tier A — free, no API, validatable now (`business_recency` detector, cataloged above).**
  The loans table already carries `business_age_description` (100% populated) + `date_approved`
  (100%), so a **label-free** detector flags the new/startup/change-of-ownership tells with no
  external call. Fully built + unit-tested offline; the real held-out verdict comes from the
  read-only `scripts/validate_business_recency.py` harness, run on the warehouse **outside the
  loop** (no rate limit). An honest NEGATIVE (recency is an *eligibility* tell, not necessarily
  a *fraud* tell) is an acceptable, documented outcome.
- **Tier B — external KYB, rate-limited, a MANUAL legally-reviewed follow-on.** `kyb/` builds
  an `EvidenceProvider` interface returning a precise **registration date**, a
  **non-registered** flag, and an **address type** for the top-K composite leads, refining the
  Tier-A score. Two implementations: a deterministic, fixture-backed **`StubProvider`** (the
  default — ZERO network, used in every test) and a token-gated **`OpenCorporatesProvider`**
  (lazy `import requests`, disambiguates a borrower to the right record via the label resolver's
  name/state matching, caches each response to `raw_dir()/kyb/` for the share-alike/attribution
  obligation, and raises a clear `RuntimeError` if `OPENCORPORATES_TOKEN` is unset). `kyb-enrich`
  fans out over the leads under a **hard cap (`MAX_KYB`)** + bounded concurrency + telemetry
  (mirroring the M7 triage cost-bounding), and stops cleanly preserving cached results if the
  quota is exhausted mid-run.
- **Optional agentic LLM dossier.** `synthesize_dossier` writes a deterministic grounded summary
  by default (no key, no network); a key-gated LLM narration behind the `agent` extra +
  `ANTHROPIC_API_KEY` narrates **only the grounded facts** with "lead, not proof" language. The
  external client and the LLM are both opt-in; nothing here calls the network or an API at import
  time.

`relief-probe kyb-enrich --top-k N [--live] [--llm]` runs the enrichment (default `--stub` =
fully offline). **Disposition: EXPLORATORY only (SIGN-010)** — `business_recency` is in
`registry.exploratory_detectors()`, never the default composite; promotion is a manual decision
after real-data lift against the DOJ labels. **Autonomy boundary (critical):** this layer only
builds + tests the Tier-B *machinery* against the deterministic stub — it makes **no Tier-B
claim**. The real OpenCorporates run (`kyb-enrich --live`, ~50/day, legally reviewed) is an
explicit manual follow-on, never an autonomous step. This is the most legally-sensitive layer in
the project — see [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md) (FCRA-adjacency for named
individuals/sole-props, defamation/false-positive harm, OpenCorporates ToS).

## Quickstart

```bash
uv run --with pytest pytest                         # offline tests (45)
uv run relief-probe ingest --slice 150k_plus        # ~1M big-dollar loans (~430 MB)
uv run relief-probe ingest-establishments PATH      # load a MANUALLY-downloaded Census ZBP CSV (for establishment_overcount)
uv run relief-probe score                           # run detectors → ranked leads
uv run relief-probe fetch-labels                    # scrape DOJ enforcement releases
uv run relief-probe resolve-labels                  # entity-resolve → fraud_cases labels (precise)
uv run --extra agent relief-probe resolve-labels-llm  # + LLM-adjudicated DBA/sole-prop labels (amount-gated)
uv run relief-probe ingest-naics PATH               # load Census NAICS titles (for naics_name_mismatch)
uv run relief-probe benchmark                       # forward PU lift@k + ablation
uv run --extra vision relief-probe vision-demo      # train the ELA doc-forgery detector
uv run relief-probe investigate <loan_number>       # grounded, evidence-cited lead report
uv run --extra embeddings-lite relief-probe similar <loan_number>  # find look-alikes (rings) by name+$+area
uv run relief-probe kyb-enrich --top-k 25            # Loop 5 KYB: enrich top leads (default --stub = offline); --live needs OPENCORPORATES_TOKEN
uv run relief-probe triage --top-k 100 --gate       # M7 Tier 1: re-rank top leads by plausibility (heuristic baseline)
uv run --extra agent relief-probe triage --top-k 100 --llm --gate  # … with the Haiku semantic-plausibility judge
uv run --extra agent relief-probe serve-mcp         # serve the 4 read-only tools over MCP
uv run --extra viz --extra vision streamlit run app/dashboard.py   # dashboard (2 tabs)
```

## Data sources

| source | role | status |
| --- | --- | --- |
| SBA PPP FOIA loan-level data (data.sba.gov) | core loan population | ✅ ingested |
| Census ZIP Business Patterns (ZBP, census.gov) | establishment counts by ZIP × NAICS (for `establishment_overcount`) | manual download → `ingest-establishments` |
| DOJ COVID-fraud prosecution press releases | benchmark labels (PU positives) | ✅ scraped + resolved |
| Synthetic spliced documents (built-in) | vision train/eval (offline) | ✅ |
| IDNet (synthetic ID forgery) · "Find it again!" (receipt tamper) | real vision anchors | wired (opt-in) |

## License

[Apache-2.0](LICENSE).
