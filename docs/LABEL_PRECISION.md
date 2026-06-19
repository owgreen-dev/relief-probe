# H4 — Label precision (hand-adjudicated)

Turns "325 high-precision labels" into a measured number, and doubles as the
calibration/validation set for the M7 Tier-1 LLM judge (see docs/M7_PLAN.md).

## Method (reproducible)

Stratified sample of **51 of the 325** distinct labeled loans, proportional to the
resolver's `match_method` mix, seed-fixed (`numpy default_rng(0)`). For each, the loan's
actual warehouse fields (`borrower_name`, state, `current_approval_amount`) were compared
against the matched DOJ release (`business_name`, `alleged_amount`, title, body) and
hand-adjudicated as true/false/ambiguous: is this loan genuinely the entity the release
charges?

## Result

**Precision ≈ 84–88%** (point 84.3% counting the 2 ambiguous as FP; 88.2% if both are
TP). Wilson 95% CI ≈ **[72%, 92%]**. So roughly **1 in 8 labels is a false positive** —
"high-precision" holds directionally, but with a real, now-quantified error rate.

### Per-tier precision (the actionable finding)

| match tier | clean in sample | FP source |
| --- | --- | --- |
| `name+state+amount` (exact) | 26/28 ≈ **93%+** | (2 flagged are ambiguous, not clear FPs) |
| `name+state+amount~` (approx amount) | 10/13 ≈ 77% | generic / geographic name collisions |
| `name+amount` (no state) | 5/6 ≈ 83% | non-charging "roundup" / general releases |
| `name+amount~` (no state, approx) | 2/4 ≈ 50% | same-named business matched to the wrong state |

**Every false positive is in the weaker (`~` / no-state) tiers.** The exact
`name+state+amount` tier is clean.

### Representative false positives

*(Borrower/defendant names anonymized below; the failure modes are the point.)*

- **A generic placeholder borrower-name** (a "SELF EMPLOYED"-style field value) matched a
  release about an individual's small loan. Name collision on a non-name.
- **A business whose name contains a city word** matched that city appearing as the
  *dateline* of a release charging unrelated, separately-named defendants — coincidental.
- **A same-named behavioral-health business in two states** both matched one release about a
  single out-of-state defendant (no-state tier). At most one could be real; both are
  wrong-state.
- **A civic/non-profit-sounding name** matched a general announcement release (alleged
  amount mis-parsed into the billions), not a charging document for that entity.

### A confirmed *true*-positive pattern (not an FP)

Many loans share `alleged_amount = $14.7M` — these are **one real 22-defendant PPP ring**
(a single release naming many distinctly-named co-defendant businesses). Correctly fanned
out to each charged business. (Side note: a few labels carry a mis-extracted
`alleged_amount` — e.g. one company's real ~$1M settlement stored as ~$1.8B — but the
*label itself* is correct; only that metadata field is noisy.)

## Implications

- **The benchmark's lift is, if anything, a slight under-estimate** — ~12–16% of the 325
  positives are false, so some "misses" near the top are actually mislabeled non-fraud;
  true concentration of real fraud is marginally higher than measured.
- **For the M7 judge calibration set, use the exact `name+state+amount` tier** (~93%+
  clean) as ground truth; treat `~`/no-state labels as provisional.
- **Resolver-tightening opportunity (optional):** dropping the `name+amount~` (and
  possibly no-state `name+amount`) tiers would push precision toward ~90%+ at some recall
  cost — a precision/recall lever to revisit before any label-aware tuning (H7).

## Reproduce

Stratified sample + evidence join is a few lines of DuckDB over `fraud_cases` ⋈ `loans`
⋈ `press_releases` (seed 0); adjudication is manual. No code shipped — this is an
analysis artifact.

---

# `amount+llm` tier precision (the LLM-recovered labels)

The M8 Phase-3 LLM entity-resolution pass added **79** labels marked
`match_method='amount+llm'` (blocked by an exact dollar match, then an LLM adjudicated the
NAME — recovering DBA / sole-prop / misspelled matches the exact resolver misses). This
audits all 79 against their DOJ release text (`scripts/validate_amount_llm_precision.py`
joins each to its loan fields + the matched release body).

## Method

All 79 audited (no sampling — small enough). Each adjudicated true / ambiguous / false by
checking, against the matched release: does the exact loan amount appear, and does the
release genuinely charge the entity behind this loan (vs the dollar amount colliding with an
unrelated figure and the LLM over-matching the name)?

## Result

**Precision ≈ 91% (strict, ambiguous = FP) to ≈ 99% (lenient, ambiguous = TP); point
~94–96%** (72 TP, 6 ambiguous, 1 FP). Wilson 95% CI ≈ **[83%, 99%]**. This is **comparable
to or better than the exact tier (84–88%)** — the exact-dollar gate is a strong precision
anchor, and the LLM only adjudicates the name on top of it.

*(Examples are anonymized/paraphrased; the category is the point.)*

- **72 true positives** — exact business name + exact loan amount in the release, incl. the
  fuzzy categories the exact resolver can't reach: legal-suffix/spelling variants (a
  number-word → its spelled-out form; a typo'd `COSTRUCTION` → `Construction`) and
  person-name sole-props (the release names the *owner* individually rather than the LLC).
- **6 ambiguous** — the exact amount matches and the release names the *defendant/scheme*
  but not the business by name (e.g. a restaurant, a watersports LLC, an engineering firm).
  Mostly legitimate sole-prop recoveries (release names the individual), but the business
  link is unconfirmed from the release alone.
- **1 false positive** — one loan ($983,000): the amount collided with a *wages* figure in
  an unrelated case naming a different entity; lower confidence (0.78).

### Per-tier (the actionable finding)

| LLM confidence | count | precision |
| --- | --- | --- |
| ≥ 0.95 | 68 | essentially clean (all audited TP) |
| < 0.90 | 7 | holds the 1 FP + most ambiguous |

**The 1 FP and most ambiguous concentrate at confidence < 0.90.** Raising the
`resolve-labels-llm --threshold` from 0.7 to ~0.95 would yield a near-spotless tier at the
cost of ~11 labels — the precision/recall lever (revisit before any label-aware tuning, H7).

## Implication

The Phase-3 win holds: the +79 labels are **high precision** (≥91%), so growing the set
325 → 404 was a real, low-noise recall gain (1 FP in 79 ≈ negligible next to the exact
tier's ~12–16%). Kept as-is (tagged + reversible); the conf≥0.95 lever is available if a
spotless benchmark is wanted.

## Reproduce

`uv run python scripts/validate_amount_llm_precision.py` — joins every `amount+llm` label to
its loan + release text and prints the evidence per label; adjudication is manual.
