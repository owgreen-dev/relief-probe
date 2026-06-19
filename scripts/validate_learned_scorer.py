"""Validate the EXPLORATORY LightGBM learned scorer against the composite —
the rigorous retry of the row-wise PREDICTION bet (read-only).

relief-probe's repeated finding is that AI/ML wins at RETRIEVAL, not row-wise
PREDICTION: M10's PU-bagging scorer came back NULL (it overfit ``forgiveness_ratio``,
caught by the temporal holdout). This script gives prediction one more HONEST shot —
a regularized LightGBM over a COMPOSITE of every signal we built (the detectors that
worked AND the ones that didn't standalone, + graph structural features + a
PLODI-style geo-normalized pay-ratio percentile + categoricals) — on the theory that
gradient-boosted trees find interactions a linear composite + bagged trees miss.

Methodology (the whole point — see SIGN-013/SIGN-016):
  INNER  grouped k-fold CV grouped by ``entity_key`` over the charges<=HOLDOUT_YEAR
         train positives — tuning + early-stopping ONLY, never the reported number.
  OUTER  the temporal holdout ``temporal_label_split(con, HOLDOUT_YEAR)``: train on
         charges<=HOLDOUT_YEAR, REPORT lift@k/recall@k/rank-stats on the >HOLDOUT_YEAR
         positives over the $150k+ slice minus the training positives. lgbm vs
         pu_bagging vs composite vs an RRF fusion (lgbm+composite) on the SAME holdout,
         plus LightGBM gain feature importances.

Prior art (MOTIVATION, not our results):
  PLODI (https://s-chadalavada.github.io/plodi/) — supervised XGBoost on prosecution
  labels + a geo/industry-normalized pay ratio, but a RANDOM 80/20 split (no
  temporal-leakage guard; we improve on that).
  Dicklesworthstone (https://github.com/Dicklesworthstone/ppp_loan_fraud_analysis) —
  a rule engine + secondary XGBoost that predicts its OWN rule-flags (circular). We
  train on REAL prosecution labels only.

Honest scope (mirrors scripts/validate_ring_graph.py): EXPLORATORY only (SIGN-010) —
this is NEVER auto-promoted into all_detectors()/the production composite; promotion
is a manual human decision after real-data lift. The FEATURES stay label-free
(SIGN-015); only the MODEL sees labels (that's expected for a learned scorer; the
temporal holdout is what keeps it honest). An honest NEGATIVE — LightGBM does not beat
the composite on the temporal holdout — is a VALID, documented outcome that CONFIRMS
the retrieval>prediction thesis more rigorously, not a failure. The reported headline
is ALWAYS the temporal holdout, never a random-split / in-fold number (SIGN-013).
Read the CONTRAST (does lgbm beat the composite? does RRF fusion ADD even if lgbm
doesn't win alone?), not absolute lift. Tuning + scoring the full slice is heavy; this
is a manual post-loop step, not part of the headless loop.

Read-only: opens the warehouse with ``connect(read_only=True)`` and never writes.

Run: `uv run --extra ml --extra graph python scripts/validate_learned_scorer.py`.
"""

from __future__ import annotations

from relief_probe.scorer.validate import run_nested_lgbm_validation
from relief_probe.warehouse import connect

#: Train on prosecutions charged <= this year; REPORT on those charged after it.
HOLDOUT_YEAR = 2023
#: Slice floor (matches the learned-scorer feature builder default).
MIN_AMOUNT = 150_000.0
#: Ranking order shown in the comparison table.
RANKINGS = ("lgbm", "pu_bagging", "composite", "rrf_fusion")


def _fmt_pct(value: float | None) -> str:
    """Render a recall/lift value, or an em dash when k exceeds the positives."""
    return "—" if value is None else f"{value:.1%}"


def _report_ranking(name: str, ranking: dict, ks: list[int]) -> None:
    """Print recall@k / lift@k (+ 95% bootstrap CI) + rank concentration."""
    metrics = ranking["metrics"]
    ranks = ranking["ranks"]
    cis = ranking.get("cis", {})
    pct = ranks["mean_percentile_in_ranking"]
    print(f"=== {name} ===")
    print(
        f"  positives mean percentile {pct} "
        "(0.5 = random, lower = top-concentrated)"
    )
    for k in ks:
        m = metrics[k]
        lift = "—" if m["lift"] is None else f"{m['lift']}x"
        ci = cis.get(k, {}).get("lift_ci")
        ci_s = f"  [95% CI {ci[0]}–{ci[1]}x]" if ci else ""
        print(
            f"    @{k:,}: {m['hits']} hits · recall {_fmt_pct(m['recall'])} · "
            f"lift {lift}{ci_s}"
        )
    print()


def main() -> None:
    with connect(read_only=True) as con:
        n_lab = con.execute("SELECT COUNT(*) FROM fraud_cases").fetchone()[0]
        n_sig = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        if not n_lab or not n_sig:
            print(
                "Need labels + signals in the warehouse. Run `fetch-labels`, "
                "`resolve-labels`, and `score` first. Verdict: N/A."
            )
            return

        print(
            f"Tuning + training the LightGBM learned scorer (nested CV, holdout "
            f"<= {HOLDOUT_YEAR}) over the ${int(MIN_AMOUNT):,}+ slice…\n"
        )
        res = run_nested_lgbm_validation(
            con, holdout_year=HOLDOUT_YEAR, min_amount=MIN_AMOUNT
        )

    print(
        f"Holdout: train (<= {res['holdout_year']}) "
        f"{res['n_train_positives']} positives · test (> {res['holdout_year']}) "
        f"{res['n_test_positives']} · population {res['population']:,} · "
        f"base rate {res['base_rate']:.4%}\n"
    )
    ks = res["ks"]
    for name in RANKINGS:
        _report_ranking(name, res["rankings"][name], ks)

    print("=== top LightGBM features (gain) ===")
    for fname, gain in res["feature_importance"]:
        print(f"    {fname}: {gain}")
    print()

    print(
        f"Verdict (lgbm vs composite on summed recall@k): {res['verdict']}  "
        "(EXPLORATORY, SIGN-010 — never auto-promoted; the temporal holdout is the "
        "only honest headline, SIGN-013). An honest 'regressed'/'neutral' CONFIRMS "
        "the retrieval>prediction thesis."
    )


if __name__ == "__main__":
    main()
