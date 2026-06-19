"""Forward positive-unlabeled validation — the centerpiece metric.

Rank loans by composite detector score, then measure how strongly DOJ-prosecuted
loans (entity-resolved into ``fraud_cases``) concentrate at the top of the ranking:
precision@k, lift over the base rate, recall@k, and a per-detector ablation showing
which detectors carry the signal.

This is out-of-time validation — the labels are prosecutions that post-date every
loan, so there is no leakage. Honest caveats (see RESPONSIBLE_USE.md): confirmed fraud
is a tiny (<0.1%), prosecution-biased sample of true fraud, so these are
**recall-on-known-fraud**, NOT a fraud rate — a lower bound conflated with what
enforcement happened to catch and resolve to a loan. A measured weak signal still
beats an unmeasured claim.
"""

from __future__ import annotations

import duckdb
import numpy as np

from relief_probe.detectors.runner import run_all
from relief_probe.scoring import composite_ranking

DEFAULT_KS: tuple[int, ...] = (100, 250, 500, 1000, 2000, 5000)
DEFAULT_N_BOOT = 2000


def labeled_fraud_loans(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Distinct loan_numbers that resolved to a prosecution (the PU positives)."""
    return {
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT loan_number FROM fraud_cases "
            "WHERE loan_number IS NOT NULL"
        ).fetchall()
    }


def temporal_label_split(
    con: duckdb.DuckDBPyConnection, holdout_year: int
) -> tuple[set[str], set[str]]:
    """Split prosecuted loans into (train, test) by enforcement ``charge_date``.

    ``train`` = loans whose case was charged in or before ``holdout_year``;
    ``test`` = loans charged strictly after it. A loan with multiple cases is
    placed by its EARLIEST charge date (when it first became known fraud). This is
    the out-of-time split mandated before fitting anything to labels (H7): a model
    trained on the train positives is validated on the test positives it never saw,
    which mirrors deployment (predict future enforcement) and can't leak.

    Loans with no ``charge_date`` are dropped from both sets (can't be placed in
    time).
    """
    rows = con.execute(
        """
        SELECT loan_number, MIN(charge_date) AS first_charge
        FROM fraud_cases
        WHERE loan_number IS NOT NULL AND charge_date IS NOT NULL
        GROUP BY loan_number
        """
    ).fetchall()
    train: set[str] = set()
    test: set[str] = set()
    for loan_number, first_charge in rows:
        (train if first_charge.year <= holdout_year else test).add(str(loan_number))
    return train, test


def detector_flagged_loans(
    con: duckdb.DuckDBPyConnection, detector_id: str
) -> set[str]:
    """Distinct loan_numbers a given detector flagged (from the ``signals`` table)."""
    return {
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT loan_number FROM signals WHERE detector_id = ?",
            [detector_id],
        ).fetchall()
    }


def detector_overlap(a: set[str], b: set[str]) -> dict:
    """Set overlap between two detectors' flagged loan sets.

    Used to argue that corroboration (a loan flagged by two detectors) is across
    genuinely *independent* views rather than two restatements of the same signal:
    a LOW Jaccard means the detectors mostly flag different loans, so the loans they
    agree on are corroborated by orthogonal evidence. Pure set math — no I/O.

    Returns counts plus the Jaccard index ``|a ∩ b| / |a ∪ b|`` (0.0 when both are
    empty).
    """
    intersection = a & b
    union = a | b
    return {
        "n_a": len(a),
        "n_b": len(b),
        "intersection": len(intersection),
        "union": len(union),
        "jaccard": round(len(intersection) / len(union), 6) if union else 0.0,
    }


def baseline_rankings(con: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """Whole-population baseline rankings to contrast against the composite detector.

    Unlike the composite (which ranks only flagged loans), these naive sorts rank the
    ENTIRE population — that contrast is the point: a reader sees whether the detector
    machinery beats a one-line SQL sort.

    - ``amount_per_job``: dollars-per-job descending (jobs >= 1, amount > 0), the
      crudest "too much money for too few jobs" heuristic.
    - ``raw_amount``: current approval amount descending (biggest loans first).
    """
    amount_per_job = [
        str(r[0])
        for r in con.execute(
            "SELECT loan_number FROM loans "
            "WHERE jobs_reported >= 1 AND current_approval_amount > 0 "
            "ORDER BY current_approval_amount / jobs_reported DESC, loan_number"
        ).fetchall()
    ]
    raw_amount = [
        str(r[0])
        for r in con.execute(
            "SELECT loan_number FROM loans "
            "WHERE current_approval_amount IS NOT NULL "
            "ORDER BY current_approval_amount DESC, loan_number"
        ).fetchall()
    ]
    return {"amount_per_job": amount_per_job, "raw_amount": raw_amount}


def ranking_metrics(
    ranked: list[str],
    positives: set[str],
    base_rate: float,
    ks: tuple[int, ...] = DEFAULT_KS,
) -> dict:
    """precision@k / lift / recall@k for a ranked loan list (denominator = k)."""
    out: dict[int, dict] = {}
    n_pos = len(positives)
    for k in ks:
        hits = sum(1 for ln in ranked[:k] if ln in positives)
        precision = hits / k if k else 0.0
        out[k] = {
            "hits": hits,
            "precision": round(precision, 5),
            "lift": round(precision / base_rate, 2) if base_rate else None,
            "recall": round(hits / n_pos, 4) if n_pos else None,
        }
    return out


def positive_rank_stats(
    ranked: list[str], positives: set[str], population: int
) -> dict:
    """Average-rank / median-rank of the known positives — the PU-honest summary.

    On a prosecution-biased positive-unlabeled (PU) sample, precision@k and lift@k
    are NOT reliably estimable, but **recall@k and the rank of known positives
    ARE** (they need only the labeled positives, never the unknown true negatives).
    See arXiv 2509.24228. So this is the metric to trust over lift.

    Ranks are 1-based within the flagged, score-descending ``ranked`` list. The
    summary deliberately separates two things the headline lift conflates:

    * **Concentration** — among the positives a detector *did* flag, how near the
      top of the flagged list do they sit? ``mean_percentile_in_ranking`` is the
      mean of ``rank / len(ranked)`` over flagged positives; **~0.5 is random, <0.5
      means they concentrate at the top.** This is the honest "is the ordering
      good" number, comparable to random.
    * **Coverage** — what fraction of positives are flagged at all
      (``n_ranked / n_positives``). Selective detectors can have excellent
      concentration and poor coverage; lift@k hides the latter.

    ``mean_percentile_population_conservative`` folds the unflagged positives in at
    the worst rank (``population``) — a coverage-penalized number, NOT comparable to
    random (it can exceed 0.5 simply because most positives went unflagged).
    """
    pos_ranks = [i + 1 for i, ln in enumerate(ranked) if ln in positives]
    n_pos = len(positives)
    n_ranked = len(pos_ranks)
    n_in_ranking = len(ranked)
    unranked = n_pos - n_ranked
    conservative = pos_ranks + [population] * unranked
    return {
        "n_positives": n_pos,
        "n_ranked": n_ranked,
        "n_unranked": unranked,
        "n_in_ranking": n_in_ranking,
        "mean_rank_ranked": (
            round(float(np.mean(pos_ranks)), 1) if n_ranked else None
        ),
        "median_rank_ranked": (
            round(float(np.median(pos_ranks)), 1) if n_ranked else None
        ),
        # Concentration of flagged positives within the flagged list (~0.5 random).
        "mean_percentile_in_ranking": (
            round(float(np.mean(pos_ranks)) / n_in_ranking, 4)
            if n_ranked and n_in_ranking
            else None
        ),
        # Coverage-penalized over the whole population (NOT vs random).
        "mean_percentile_population_conservative": (
            round(float(np.mean(conservative)) / population, 4)
            if n_pos and population
            else None
        ),
    }


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    *,
    k: int = 60,
    weights: list[float] | None = None,
) -> list[str]:
    """Fuse several ranked loan lists by Reciprocal Rank Fusion (Cormack 2009).

    ``score(d) = Σ_r w_r / (k + rank_r(d))`` over each ranking ``r`` the item
    appears in (rank 1-based). RRF combines *ranks, not scores*, so an uncalibrated
    list (e.g. a saturated LLM score) can neither swamp nor be swamped by a
    z-score list — the failure mode that sank the additive triage blend (see
    docs/LLM_RESEARCH.md). Returns loan_numbers in fused order, highest first.

    The default ``k=60`` is Cormack's and needs no tuning. ``weights`` (one per
    ranking, default all 1.0) encodes "trust ranking A more than B" with a single
    ratio — tune at most one scalar, on a held-out split, if at all.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("weights must align one-to-one with rankings")
    fused: dict[str, float] = {}
    for ranking, w in zip(rankings, weights, strict=True):
        for rank, loan in enumerate(ranking, start=1):
            fused[loan] = fused.get(loan, 0.0) + w / (k + rank)
    # Sort by fused score desc; ties broken by loan_number for determinism.
    return [ln for ln, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))]


def bootstrap_lift_cis(
    ranked: list[str],
    positives: set[str],
    base_rate: float,
    ks: tuple[int, ...] = DEFAULT_KS,
    *,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict:
    """Percentile bootstrap confidence intervals for hits@k and lift@k.

    The point estimates rest on single-digit hit counts, so a bare "29.7x" hides
    enormous sampling noise (at k=100 it is literally one loan). We quantify that
    with a **Poisson bootstrap**: under the nonparametric bootstrap each loan's
    resample multiplicity is ~Binomial(N, 1/N) ≈ Poisson(1). Since only flagged
    loans (score > 0) can occupy the top k, resampling the whole population reduces
    to drawing a Poisson(1) multiplicity for each loan in ``ranked`` (the flagged,
    score-descending list) and recomputing how many of the first k resampled slots
    are positives. The base rate is held at its observed value — its own
    uncertainty is second order next to the top-k hit variance.

    Returns, per k, ``hits_ci`` and ``lift_ci`` as ``[lo, hi]`` at the central
    ``1 - alpha`` level. A CI whose lower bound is 0 hits is the honest signal that
    the point estimate is not distinguishable from "found nothing".
    """
    y = np.array([1.0 if ln in positives else 0.0 for ln in ranked])
    n = y.size
    rng = np.random.default_rng(seed)
    lo_q, hi_q = 100 * alpha / 2, 100 * (1 - alpha / 2)

    hits = {k: np.zeros(n_boot) for k in ks}
    if n:
        for b in range(n_boot):
            m = rng.poisson(1.0, size=n)
            cum = np.cumsum(m)
            ypos = y * m  # positive copies contributed by each ranked loan
            for k in ks:
                idx = int(np.searchsorted(cum, k, side="left"))
                if idx >= n:  # fewer than k resampled slots exist — count them all
                    hits[k][b] = ypos.sum()
                    continue
                taken = float(ypos[:idx].sum())
                already = cum[idx - 1] if idx else 0
                taken += y[idx] * (k - already)  # partial fill from the boundary loan
                hits[k][b] = taken

    out: dict[int, dict] = {}
    for k in ks:
        hs = hits[k]
        lift = (hs / k) / base_rate if base_rate else np.zeros_like(hs)
        out[k] = {
            "hits_ci": [
                round(float(np.percentile(hs, lo_q)), 1),
                round(float(np.percentile(hs, hi_q)), 1),
            ],
            "lift_ci": [
                round(float(np.percentile(lift, lo_q)), 2),
                round(float(np.percentile(lift, hi_q)), 2),
            ],
        }
    return out


def _slice_universe(
    con: duckdb.DuckDBPyConnection, min_amount: float | None
) -> set[str] | None:
    """Loan_numbers in the evaluation slice, or None for the whole population.

    The resolved labels live almost entirely in the public $150k+ disclosure slice,
    so ranking the full ~11.4M-loan population mechanically deflates the base rate
    and inflates lift (the same handful of hits over a 10x bigger haystack). The
    default benchmark therefore restricts evaluation to the *labelable* slice for an
    apples-to-apples lift; full-population recall is reported separately.
    """
    if min_amount is None:
        return None
    return {
        str(r[0])
        for r in con.execute(
            "SELECT loan_number FROM loans WHERE current_approval_amount >= ?",
            [min_amount],
        ).fetchall()
    }


def _restrict(ranked: list[str], universe: set[str] | None) -> list[str]:
    """Keep only loans in ``universe`` (order-preserving); identity if None."""
    if universe is None:
        return ranked
    return [ln for ln in ranked if ln in universe]


def run_benchmark(
    con: duckdb.DuckDBPyConnection,
    *,
    ks: tuple[int, ...] = DEFAULT_KS,
    rescore: bool = True,
    min_amount: float | None = 150_000.0,
    n_boot: int = DEFAULT_N_BOOT,
) -> dict:
    """Rank loans by composite score, validate against resolved fraud_cases labels.

    ``min_amount`` restricts evaluation to the labelable slice (default: the $150k+
    disclosure slice). Pass ``None`` to evaluate the whole population. ``n_boot``
    sets the bootstrap resamples for the lift@k confidence intervals (0 to skip).
    """
    if rescore:
        run_all(con)

    universe = _slice_universe(con, min_amount)
    all_positives = labeled_fraud_loans(con)
    if universe is None:
        population = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
        positives = all_positives
    else:
        population = len(universe)
        positives = all_positives & universe
    base_rate = (len(positives) / population) if population else 0.0

    ranking = composite_ranking(con)
    full_ranked = [str(x) for x in ranking["loan_number"].tolist()]
    ranked = _restrict(full_ranked, universe)
    overall = ranking_metrics(ranked, positives, base_rate, ks)
    overall_ci = (
        bootstrap_lift_cis(ranked, positives, base_rate, ks, n_boot=n_boot)
        if n_boot
        else {}
    )

    # Per-detector ablation: rank by each detector's own score in isolation.
    ablation: dict[str, dict] = {}
    for (det,) in con.execute(
        "SELECT DISTINCT detector_id FROM signals"
    ).fetchall():
        det_ranked = _restrict(
            [
                str(r[0])
                for r in con.execute(
                    "SELECT loan_number FROM signals WHERE detector_id = ? "
                    "ORDER BY score DESC",
                    [det],
                ).fetchall()
            ],
            universe,
        )
        ablation[det] = {
            "n_flagged": len(det_ranked),
            "metrics": ranking_metrics(det_ranked, positives, base_rate, ks),
        }

    # Naive whole-population baselines, scored against the SAME positives/base_rate/ks.
    baselines: dict[str, dict] = {}
    for name, ranked_baseline in baseline_rankings(con).items():
        baselines[name] = {
            "metrics": ranking_metrics(
                _restrict(ranked_baseline, universe), positives, base_rate, ks
            )
        }

    # Full-population recall (denominator = ALL resolved labels), reported separately
    # so the slice restriction never hides labels that surface outside the slice.
    full_population = {
        "population": con.execute("SELECT COUNT(*) FROM loans").fetchone()[0],
        "n_labeled_fraud": len(all_positives),
        "metrics": ranking_metrics(full_ranked, all_positives, 0.0, ks),
    }

    return {
        "ks": list(ks),
        "slice": "all" if min_amount is None else f">=${int(min_amount):,}",
        "population": population,
        "n_labeled_fraud": len(positives),
        "base_rate": round(base_rate, 6),
        "n_ranked": len(ranked),
        "overall": overall,
        "overall_ci": overall_ci,
        # PU-honest summary: rank of known positives (trust over lift@k).
        "positive_ranks": positive_rank_stats(ranked, positives, population),
        "n_boot": n_boot,
        "ablation": ablation,
        "baselines": baselines,
        "full_population": full_population,
    }
