# M7 — Cost-aware LLM triage cascade (plan)

Grounded in the established **LLM cascade** pattern (FrugalGPT, arXiv 2305.05176) and
LLM-as-judge best practices, using Anthropic's Batch API + prompt caching + structured
outputs. Planned June 2026.

## The pattern

Run the cheapest stage first; escalate to a more expensive one only on the survivors.
Production cascades report 45–85% cost cuts at ~95% of frontier quality; the canonical
shape is **rule-based → semantic → LLM**. relief-probe already has the rule-based tier
(the deterministic composite), so the cascade is:

| Tier | Model | Scope | Cost basis |
| --- | --- | --- | --- |
| 0 — deterministic composite (built) | none | ALL 11.4M loans | free |
| 1 — semantic plausibility | Haiku 4.5 (`claude-haiku-4-5`, $1/$5 per MTok) | top ~1,000 composite leads | Batch API |
| 2 — DOJ press-release corroboration (later) | Haiku 4.5 | loans near a DOJ release | Batch API |
| 3 — deep investigation (mostly built: `investigate --llm`) | Opus 4.8 (`claude-opus-4-8`, $5/$25 per MTok) | top ~25 survivors | sync |

**Cardinal rule:** never run the LLM over the full population — Tier 0 does the
11.4M → 1,000 cut for free.

## Tier-1 plausibility scorer (LLM-as-judge)

"Could this business plausibly justify this loan?" over `borrower_name × NAICS ×
amount × jobs × proceeds`. World knowledge catches mismatches statistics can't
("'Elite Nail Spa LLC', 1 employee, $2.1M, NAICS=landscaping").

Design per LLM-as-judge best practices:
- **Structured output** — force `{implausibility: 0–3, verdict: enum, reasons: [str]}`
  via `output_config.format` (json_schema) / `messages.parse()` (Pydantic) / strict tool.
- **Categorical integer scale with explicit definitions** (beats free-form numeric).
- **Chain-of-thought BEFORE the score**; concrete rubric; **few-shot examples**.
- **Calibrate against the H4 hand-labeled set** (see Sequencing).

## Cost estimate (real pricing)

Batch API = 50% off all tokens (async, usually <1h — triage is offline). Prompt caching
serves a shared system prompt at 0.1× reads (1.25× write once) — but the **minimum
cacheable prefix is 4,096 tokens**, so the rubric+few-shot system prompt must be padded
past 4k to cache.

| Stage | Volume | Est. cost (Batch + cache) |
| --- | --- | --- |
| Tier 1 — Haiku over top-1,000 | 1,000 loans | ~$1–2 |
| Tier 3 — Opus deep-dive over top-25 | 25 loans | ~$1–2 |
| **Total per triage run** | | **~$2–4** |

Naively running Haiku over all 11.4M loans ≈ **$8–16k**. The cascade gets the same
top-end coverage for single-digit dollars (~99.97% saving). That contrast is the M7 story.

## Non-negotiables (mirror M5 + project discipline)

1. **Deterministic-first / key-gated** — builds + tests with NO `ANTHROPIC_API_KEY`
   (mock / `pytest.importorskip`); LLM behind the `agent` extra + a `--llm` flag.
2. **Hard cap** on loans that hit the LLM, logged → cost bounded + visible. New CLI:
   `relief-probe triage --top-k N [--llm]`.
3. **Validation gate** — measure whether Tier-1 re-ranking actually improves lift@k over
   the composite alone on the 325 labels / $150k+ slice. If it doesn't, report the honest
   negative (same discipline as every detector).
4. **Honest scope** — no public PPP application documents, so this runs on structured
   fields + DOJ press-release text, not forms.

## Sequencing (decided)

- **H4 first** — build the ~50-row hand-labeled label-precision sample. It doubles as the
  Tier-1 judge's calibration/validation set (the LLM-as-judge literature requires a
  human-annotated calibration set), and turns "325 high-precision labels" into a measured
  number.
- **Then M7 Tier 1 only** — Haiku plausibility scorer + `triage` CLI + the validation
  gate. Tiers 2 (corroboration) and 3 (Opus deep-dive end-to-end) are follow-ups once
  Tier 1 shows real lift.

## Result (Tier 1, built + validated June 2026) — honest negative

Built as planned: `triage/` package, `relief-probe triage --top-k N [--llm] [--gate]
[--concurrency C]`, two judges behind one interface (deterministic `heuristic_judge`
baseline + concurrent/robust `LlmJudge` on Haiku 4.5 with strict 0–3 structured output),
hard cap (2,000), and the validation gate. Deterministic-first/key-gated, 18 tests.

Real `--llm --gate` run on the full 11.4M warehouse / 325 labels (300 leads judged in
~3.5 min at `--concurrency 10`, 0 fallbacks; logs in `data/triage_runs/`):

| k | composite lift | triage (Haiku) lift | hits |
| --- | --- | --- | --- |
| 25 | 356.4× | 237.6× | 3 → 2 |
| 50 | 178.2× | 118.8× | 3 → 2 |
| 100 | 89.1× | 89.1× | 3 → 3 |
| 250 | 35.6× | 35.6× | 3 → 3 |

**No lift; gate `regressed` by exactly one loan** (3→2 hits at k=25/50, unchanged at
k≥100) — a single-loan swing inside the H3 bootstrap noise. The semantic-plausibility
re-rank does not concentrate the *prosecuted* labels better than the composite: the
composite already nails the top, the LLM marks many loans `egregious` (so the uniform
blend can't discriminate), and some prosecuted loans look *plausible* to it. The judge's
per-loan calls are sensible; they just don't align with what got charged. Caveats both
ways: PU labels can't reward fraud the DOJ never charged, and the blend is coarse. **Kept
built + opt-in, NOT promoted** — same discipline as every exploratory detector.

Productive follow-ups (none promote Tier 1 as-is): **Tier 2** press-release corroboration
(also lifts label quality, H4); reframe Tier 1 as an **explanation/triage-narrowing** aid
(label-alignment not required); **H7** temporal holdout before any label-aware tuning.

## Sources

- FrugalGPT — arXiv 2305.05176
- Dynamic model routing & cascading survey — arXiv 2603.04445
- LLM-as-judge best practices — montecarlo.ai, deepeval, Arize
- Anthropic Batch API / prompt caching / structured outputs — claude-api skill reference
