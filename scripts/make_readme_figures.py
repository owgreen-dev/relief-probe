"""Generate the README's two-panel "scope + honest result" figure.

Left panel — *needle in a haystack*: all ~11.4M public PPP loans as a log–log
density field (loan amount x dollars-per-job), with the prosecuted loans overlaid
and the $150k disclosure threshold marked. It shows the scale, and it's honest: the
prosecuted needles are a faint sprinkle that lean into the high-$/job tail
*alongside* many legitimate high-wage firms.

Right panel — *does it work?*: lift-over-base-rate vs ranking depth (top-k) for the
composite ranking, the one-line ``amount/jobs`` baseline, and random (1x), with the
95% Poisson-bootstrap CI band on the composite. This is the README's headline table
as a curve, including the honest beats (the composite barely beats the one-liner;
the CI clears 1x only at k>=500).

Read-only by construction (SIGN-007): opens the warehouse with ``read_only=True``,
never runs the CLI, never writes. Every number comes straight from the warehouse and
the validated ``benchmark`` functions — nothing is invented (SIGN-008).

Run:
    uv run --extra figures python scripts/make_readme_figures.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np

from relief_probe.benchmark.core import (
    _slice_universe,
    baseline_rankings,
    bootstrap_lift_cis,
    ranking_metrics,
)
from relief_probe.config import REPO_ROOT
from relief_probe.scoring import composite_ranking
from relief_probe.warehouse import connect

DISCLOSURE_THRESHOLD = 150_000  # $; borrower-level detail is public only above this.
OUT_PATH = REPO_ROOT / "docs" / "images" / "scope-and-lift.png"

# Clean light/publication palette.
C_COMPOSITE = "#1f4e79"   # deep blue — the composite ranking
C_BASELINE = "#7f8c8d"    # grey — the one-line baseline
C_PROSECUTED = "#e8743b"  # warm orange — the prosecuted needles / annotations
C_BAND = "#9ecae1"        # light blue — the CI band


# --- Data prep (pure; take a connection so they're testable on the demo DB) --------

# The headline benchmark (and the README lift table) is measured on the 325
# exact-match prosecution labels; the 79 LLM-recovered ones (``match_method`` with
# 'llm') grew the retrieval/homophily set but the composite lift was not re-run on
# them. We use the same exact-match set here so the figure's lift@500 matches the
# README's 23.8× rather than introducing a second, slightly-different number.
_EXACT_MATCH = (
    "SELECT DISTINCT loan_number FROM fraud_cases "
    "WHERE loan_number IS NOT NULL AND match_method NOT ILIKE '%llm%'"
)


def exact_match_positives(con: duckdb.DuckDBPyConnection) -> set[str]:
    """The 325 exact-match prosecution labels (the README's headline label set)."""
    return {str(r[0]) for r in con.execute(_EXACT_MATCH).fetchall()}


def density_grid(con: duckdb.DuckDBPyConnection, *, bin: float = 0.05) -> dict:
    """DuckDB-side 2D histogram of log10($/job) x log10(amount) over ALL loans.

    Aggregated in SQL so we never pull 11M rows into Python — the result is a small
    grid of (lx, ly, n) cells. Returns the binned arrays plus the population count.
    """
    rows = con.execute(
        f"""
        SELECT
            round(log10(current_approval_amount / jobs_reported) / {bin}) * {bin} AS lx,
            round(log10(current_approval_amount) / {bin}) * {bin} AS ly,
            count(*) AS n
        FROM loans
        WHERE jobs_reported >= 1 AND current_approval_amount > 0
        GROUP BY 1, 2
        """
    ).fetchall()
    n_total = con.execute(
        "SELECT count(*) FROM loans "
        "WHERE jobs_reported >= 1 AND current_approval_amount > 0"
    ).fetchone()[0]
    lx = np.array([r[0] for r in rows], dtype=float)
    ly = np.array([r[1] for r in rows], dtype=float)
    n = np.array([r[2] for r in rows], dtype=float)
    return {"lx": lx, "ly": ly, "n": n, "n_total": int(n_total), "bin": bin}


def prosecuted_points(con: duckdb.DuckDBPyConnection) -> dict:
    """The prosecuted loans as (log10($/job), log10(amount)) points for the overlay."""
    rows = con.execute(
        f"""
        SELECT log10(l.current_approval_amount / l.jobs_reported) AS lx,
               log10(l.current_approval_amount)                   AS ly
        FROM loans l
        JOIN ({_EXACT_MATCH}) f USING (loan_number)
        WHERE l.jobs_reported >= 1 AND l.current_approval_amount > 0
        """
    ).fetchall()
    return {
        "lx": np.array([r[0] for r in rows], dtype=float),
        "ly": np.array([r[1] for r in rows], dtype=float),
        "n": len(rows),
    }


def lift_curve(
    con: duckdb.DuckDBPyConnection,
    *,
    min_amount: float = float(DISCLOSURE_THRESHOLD),
    n_points: int = 60,
) -> dict:
    """Lift@k vs k for composite vs the amount/jobs baseline, with a composite CI band.

    Restricts both rankings to the labelable $150k+ slice (where labels live) for an
    apples-to-apples lift, exactly as the default benchmark does. Reuses the validated
    benchmark functions — no new analytics.
    """
    positives = exact_match_positives(con)
    universe = _slice_universe(con, min_amount)
    in_slice = positives & universe
    base_rate = len(in_slice) / len(universe) if universe else 0.0

    comp = [str(x) for x in composite_ranking(con, limit=None)["loan_number"].tolist()]
    comp = [ln for ln in comp if ln in universe]
    base = [ln for ln in baseline_rankings(con)["amount_per_job"] if ln in universe]

    # Log-spaced k grid over the trustworthy band (k>=100; below that lift is a
    # 1-3 loan coin-flip, per the README). Union in the canonical README ks so the
    # 500 annotation point exists. Capped at how deep the composite ranking goes.
    k_max = min(len(comp), 10_000)
    canonical = tuple(k for k in (100, 250, 500, 1000, 2000, 5000) if k <= k_max)
    ks = tuple(sorted(
        set(np.unique(np.geomspace(100, max(k_max, 100), n_points).astype(int)).tolist())
        | set(canonical)
    ))

    comp_m = ranking_metrics(comp, positives, base_rate, ks=ks)
    base_m = ranking_metrics(base, positives, base_rate, ks=ks)
    cis = bootstrap_lift_cis(comp, positives, base_rate, ks=ks, n_boot=2000, seed=0)

    return {
        "ks": np.array(ks, dtype=float),
        "comp_lift": np.array([comp_m[k]["lift"] or 0.0 for k in ks]),
        "base_lift": np.array([base_m[k]["lift"] or 0.0 for k in ks]),
        "ci_lo": np.array([cis[k]["lift_ci"][0] for k in ks]),
        "ci_hi": np.array([cis[k]["lift_ci"][1] for k in ks]),
        "base_rate": base_rate,
        "n_positives": len(in_slice),
        "n_universe": len(universe),
        "lift_at_500": comp_m.get(500, {}).get("lift"),
    }


# --- Rendering ---------------------------------------------------------------------

def render(density: dict, prosecuted: dict, curve: dict, out_path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm

    plt.rcParams.update({
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    })
    # 2x2 grid: both plot panels live in column 0 so they share an identical
    # width and left/right edge; the colorbar gets its own thin column on the
    # top row only, and the matching cell under the bottom panel is left empty.
    # This keeps the two panels aligned instead of the colorbar shrinking the
    # top one (which left them the same left edge but different right edges).
    fig = plt.figure(figsize=(11, 13))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.0, 0.028], wspace=0.02)
    axL = fig.add_subplot(gs[0, 0])
    cax = fig.add_subplot(gs[0, 1])
    axR = fig.add_subplot(gs[1, 0])

    # --- Panel 1: the 11.4M density + prosecuted needles ---
    # Render the SQL-binned grid directly with pcolormesh (no re-binning → crisp).
    b = density["bin"]
    lx, ly, n = density["lx"], density["ly"], density["n"]
    ix = np.round((lx - lx.min()) / b).astype(int)
    iy = np.round((ly - ly.min()) / b).astype(int)
    grid = np.full((iy.max() + 1, ix.max() + 1), np.nan)
    grid[iy, ix] = n
    x_edges = lx.min() + (np.arange(grid.shape[1] + 1) - 0.5) * b
    y_edges = ly.min() + (np.arange(grid.shape[0] + 1) - 0.5) * b
    mesh = axL.pcolormesh(
        x_edges, y_edges, np.ma.masked_invalid(grid),
        cmap="Blues", norm=LogNorm(vmin=1, vmax=np.nanmax(grid)),
    )
    cb = fig.colorbar(mesh, cax=cax)
    cb.set_label("loans per cell (log)")

    axL.scatter(
        prosecuted["lx"], prosecuted["ly"], s=14, c=C_PROSECUTED,
        edgecolors="#5a2d12", linewidths=0.4, alpha=0.85, zorder=5,
        label=f"prosecuted loans ({prosecuted['n']})",
    )
    y_thresh = np.log10(DISCLOSURE_THRESHOLD)
    axL.axhline(y_thresh, color="#333", ls="--", lw=1.0, zorder=4)
    axL.text(
        x_edges[0] + 0.1, y_thresh + 0.08, "$150k disclosure threshold",
        fontsize=8.5, color="#333", va="bottom",
    )
    axL.set_title("11.4 million public PPP loans")
    axL.set_xlabel("dollars per reported job  (log₁₀)")
    axL.set_ylabel("loan amount  (log₁₀ $)")
    axL.legend(loc="lower right", framealpha=0.92, fontsize=9)

    # --- Panel 2: lift vs depth, with CI band ---
    ks = curve["ks"]
    axR.fill_between(ks, curve["ci_lo"], curve["ci_hi"], color=C_BAND, alpha=0.45,
                     label="95% bootstrap CI")
    axR.plot(ks, curve["comp_lift"], color=C_COMPOSITE, lw=2.2, label="composite")
    axR.plot(ks, curve["base_lift"], color=C_BASELINE, lw=1.8, ls="--",
             label="one-line $/job sort")
    axR.axhline(1.0, color="#888", ls=":", lw=1.2, label="random (1×)")

    lift500 = curve.get("lift_at_500")
    if lift500:
        axR.annotate(
            f"{lift500:.1f}× @ k=500",
            xy=(500, lift500), xytext=(900, lift500 + 8),
            fontsize=9.5, color=C_COMPOSITE,
            arrowprops=dict(arrowstyle="->", color=C_COMPOSITE, lw=1.0),
        )
    axR.set_xscale("log")
    axR.set_title("Does the ranking concentrate prosecuted fraud?")
    axR.set_xlabel("top-k ranked loans  (log)")
    axR.set_ylabel("lift over base rate")
    axR.set_ylim(bottom=0)
    axR.legend(loc="upper right", framealpha=0.9, fontsize=9)
    axR.text(
        0.015, 0.02,
        f"$150k+ slice: {curve['n_universe']:,} loans, "
        f"{curve['n_positives']} prosecuted (base rate {curve['base_rate']:.3%})",
        transform=axR.transAxes, fontsize=8, color="#555", va="bottom",
    )

    # Explicit margins (not tight_layout, which would repack the gridspec columns
    # and re-break the panel alignment). align_ylabels keeps the two y-axis labels
    # on a common vertical despite different tick-label widths (7 vs 200).
    fig.subplots_adjust(left=0.085, right=0.91, top=0.955, bottom=0.055, hspace=0.16)
    fig.align_ylabels([axL, axR])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return out_path


def main() -> None:
    con = connect(read_only=True)
    try:
        density = density_grid(con)
        prosecuted = prosecuted_points(con)
        curve = lift_curve(con)
    finally:
        con.close()
    out = render(density, prosecuted, curve, OUT_PATH)
    print(f"wrote {out}")
    print(
        f"  population={density['n_total']:,}  prosecuted={prosecuted['n']}  "
        f"lift@500={curve['lift_at_500']}  base_rate={curve['base_rate']:.4%}"
    )


if __name__ == "__main__":
    main()
