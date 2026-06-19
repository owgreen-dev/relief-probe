"""relief-probe command-line interface."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from relief_probe.ingest.sources import SLICES
from relief_probe.warehouse import connect

app = typer.Typer(
    add_completion=False,
    help="PPP/SBA pandemic-loan fraud-lead lab. A high score is a lead for "
    "review, not evidence of fraud — see RESPONSIBLE_USE.md.",
)
console = Console()

_TABLES = ("loans", "fraud_cases", "signals")


@app.callback()
def _main() -> None:
    """Load .env (if present) so ANTHROPIC_API_KEY etc. are available to commands."""
    from relief_probe.config import load_env

    load_env()


@app.command()
def info() -> None:
    """Show the warehouse location and per-table row counts."""
    from relief_probe.config import warehouse_path

    console.print(f"[bold]warehouse[/] {warehouse_path()}")
    with connect(read_only=True) as con:
        t = Table("table", "rows")
        for name in _TABLES:
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            except Exception:
                n = 0
            t.add_row(name, f"{n:,}")
        console.print(t)


@app.command(name="vision-demo")
def vision_demo(
    n_per_class: int = typer.Option(60, help="Synthetic images per class."),
) -> None:
    """Generate synthetic clean/forged docs, train the ELA detector, report accuracy.

    Self-contained (no downloads). For real data, point training at a folder of
    `authentic/` + `forged/` images (see relief_probe.vision.datasets).
    """
    try:
        from relief_probe.vision import SYNTHETIC_NOTE
        from relief_probe.vision.datasets import make_synthetic
        from relief_probe.vision.model import train
    except ImportError:
        console.print("[yellow]Needs the vision extra:[/] uv sync --extra vision")
        raise typer.Exit(code=1) from None

    from relief_probe.config import data_dir

    console.print(f"[yellow]{SYNTHETIC_NOTE}[/]")
    synth = data_dir() / "vision_synth"
    model_path = data_dir() / "models" / "doc_authenticity.joblib"
    console.print(f"Generating {n_per_class}×2 synthetic documents …")
    make_synthetic(synth, n_per_class=n_per_class)
    console.print("Training ELA classifier …")
    summary = train(synth, out_path=model_path)
    console.print(
        f"[green]Trained[/] on {summary['n_images']} images "
        f"({summary['n_authentic']} authentic / {summary['n_forged']} forged): "
        f"CV accuracy [bold]{summary['cv_accuracy_mean']:.1%}[/] "
        f"± {summary['cv_accuracy_std']:.1%} ({summary['cv_folds']}-fold). "
        f"Model: {summary['out_path']}"
    )
    console.print(
        "[dim]ELA flags recompression/splice artifacts, not 'fraud' — a screening "
        "aid. See RESPONSIBLE_USE.md.[/]"
    )


@app.command(name="vision-score")
def vision_score(
    image: str = typer.Argument(..., help="Path to an image to score."),
) -> None:
    """Score one image's forgery probability with the trained ELA detector."""
    try:
        from PIL import Image

        from relief_probe.vision.model import forgery_probability, load_model
    except ImportError:
        console.print("[yellow]Needs the vision extra:[/] uv sync --extra vision")
        raise typer.Exit(code=1) from None

    from relief_probe.config import data_dir

    model_path = data_dir() / "models" / "doc_authenticity.joblib"
    if not model_path.exists():
        console.print("[yellow]No model.[/] Run `relief-probe vision-demo` first.")
        raise typer.Exit(code=1)
    model = load_model(model_path)
    with Image.open(image) as img:
        p = forgery_probability(model, img)
    console.print(f"P(forged) = [bold]{p:.1%}[/] for {image}")
    console.print(
        "[dim]Note: unless this model was trained on real labeled forgeries, it is "
        "synthetic-trained — this score reflects the synthetic demo task, not "
        "validated real-document forgery detection.[/]"
    )


@app.command()
def benchmark(
    full_population: bool = typer.Option(
        False,
        "--full-population",
        help="Evaluate lift over all ~11.4M loans instead of the labelable $150k+ "
        "slice. Inflates lift (10x bigger haystack, same hits) — slice is the "
        "honest default.",
    ),
) -> None:
    """Forward PU validation: how strongly prosecuted loans rank at the top.

    Runs all detectors, ranks loans by composite score, and reports precision@k /
    lift / recall@k against the resolved `fraud_cases` labels, with a per-detector
    ablation. By default lift is measured on the **$150k+ disclosure slice** where
    the labels live (apples-to-apples base rate); full-population recall is shown
    separately. Needs `ingest`, `fetch-labels`, and `resolve-labels` first.
    """
    from relief_probe.benchmark import run_benchmark

    min_amount = None if full_population else 150_000.0
    with connect() as con:
        n_labels = con.execute("SELECT COUNT(*) FROM fraud_cases").fetchone()[0]
        if n_labels == 0:
            console.print(
                "[yellow]No labels.[/] Run `fetch-labels` then `resolve-labels` first."
            )
            raise typer.Exit(code=1)
        console.print("Scoring + benchmarking …")
        res = run_benchmark(con, min_amount=min_amount)

    console.print(
        f"Slice [bold]{res['slice']}[/] · [bold]{res['population']:,}[/] loans · "
        f"[bold]{res['n_labeled_fraud']}[/] prosecuted (base rate "
        f"{res['base_rate']:.4%}) · {res['n_ranked']:,} flagged & ranked."
    )

    ks = res["ks"]
    ci = res.get("overall_ci") or {}
    t = Table(title="Forward lift@k — composite ranking vs DOJ-prosecuted loans")
    t.add_column("k")
    t.add_column("hits", justify="right")
    t.add_column("precision@k", justify="right")
    t.add_column("lift", justify="right")
    if ci:
        t.add_column("lift 95% CI", justify="right")
    t.add_column("recall", justify="right")
    for k in ks:
        m = res["overall"][k]
        lift = m["lift"]
        row = [
            f"{k:,}", str(m["hits"]), f"{m['precision']:.3%}",
            "—" if lift is None else f"{lift:.1f}x",
        ]
        if ci:
            lo, hi = ci[k]["lift_ci"]
            row.append(f"{lo:.1f}–{hi:.1f}x")
        row.append("—" if m["recall"] is None else f"{m['recall']:.1%}")
        t.add_row(*row)
    console.print(t)
    if ci:
        console.print(
            f"[dim]95% CIs from a {res['n_boot']:,}-resample Poisson bootstrap; a "
            "lower bound near 0 means the point lift rests on one or two loans.[/]"
        )

    # PU-honest summary: rank of the known positives. On a prosecution-biased PU
    # sample, lift@k is not reliably estimable but the rank of known positives is —
    # so this is the number to trust over the lift table above.
    pr = res.get("positive_ranks") or {}
    if pr.get("n_positives"):
        conc = pr["mean_percentile_in_ranking"]
        coverage = pr["n_ranked"] / pr["n_positives"] if pr["n_positives"] else 0.0
        console.print(
            "[bold]PU-honest rank of known fraud[/] (trust over lift, two parts):"
        )
        if conc is not None:
            console.print(
                f"  • [bold]Concentration[/]: the {pr['n_ranked']} flagged positives "
                f"sit at median rank [bold]{pr['median_rank_ranked']:,.0f}[/] of "
                f"{pr['n_in_ranking']:,} flagged — mean percentile "
                f"[bold]{conc:.3f}[/] "
                f"({'better than random' if conc < 0.5 else 'no better than random'}; "
                "~0.5 = random, lower = top-concentrated)."
            )
        console.print(
            f"  • [bold]Coverage[/]: only [bold]{pr['n_ranked']}/{pr['n_positives']}"
            f"[/] positives ({coverage:.0%}) are flagged at all — "
            f"{pr['n_unranked']} never fire any detector (the recall ceiling that "
            "lift@k hides)."
        )

    a = Table(title="Per-detector ablation (lift@k in isolation)")
    a.add_column("detector")
    a.add_column("flagged", justify="right")
    for k in ks:
        a.add_column(f"lift@{k}", justify="right")
    for det, info in res["ablation"].items():
        row = [det, f"{info['n_flagged']:,}"]
        for k in ks:
            lift = info["metrics"][k]["lift"]
            row.append("—" if lift is None else f"{lift:.1f}x")
        a.add_row(*row)
    console.print(a)

    # Composite vs naive whole-population baselines: does the machinery beat a sort?
    b = Table(title="Composite vs naive baselines (lift@k)")
    b.add_column("ranking")
    for k in ks:
        b.add_column(f"lift@{k}", justify="right")

    def _lift_row(label: str, metrics: dict) -> list[str]:
        row = [label]
        for k in ks:
            lift = metrics[k]["lift"]
            row.append("—" if lift is None else f"{lift:.1f}x")
        return row

    b.add_row(*_lift_row("composite", res["overall"]))
    for name, info in res["baselines"].items():
        b.add_row(*_lift_row(name, info["metrics"]))
    console.print(b)

    # Full-population recall, reported separately so the slice never hides labels.
    fp = res["full_population"]
    fp_k = ks[-1]
    fp_m = fp["metrics"][fp_k]
    console.print(
        f"[dim]Full population: of {fp['n_labeled_fraud']} resolved labels across "
        f"all {fp['population']:,} loans, the composite surfaces "
        f"{fp_m['hits']} in the top {fp_k:,} (recall "
        f"{fp_m['recall']:.1%}).[/]"
    )
    console.print(
        "[dim]Recall-on-known-fraud, not a fraud rate: labels are a small, "
        "prosecution-biased PU sample resolved mostly to the $150k+ slice. "
        "See RESPONSIBLE_USE.md.[/]"
    )


@app.command(name="fetch-labels")
def fetch_labels(
    min_year: int = typer.Option(
        2020, help="Stop paging once releases predate Jan 1 of this year."
    ),
    max_pages: int = typer.Option(
        400, help="Safety cap on pages (250 releases each)."
    ),
) -> None:
    """Scrape DOJ press releases for PPP/EIDL loan-fraud and stage them.

    Pages the DOJ JSON API newest-first, keeps loan-fraud releases, and stores them
    in `press_releases`. The entity-resolution step (next) links these to loans.
    """
    import datetime as dt

    from relief_probe.labels import iter_doj_pages, store_releases

    console.print("[bold]Scraping DOJ press releases[/] (newest-first) …")
    added = fetched = 0
    by_program: dict[str, int] = {}
    # Store per page so a mid-run failure keeps progress (idempotent on id).
    with connect() as con:
        for page_idx, rows in enumerate(
            iter_doj_pages(min_date=dt.date(min_year, 1, 1), max_pages=max_pages),
            start=1,
        ):
            fetched += len(rows)
            for r in rows:
                by_program[r["program"]] = by_program.get(r["program"], 0) + 1
            added += store_releases(con, rows)
            if page_idx % 25 == 0 or rows:
                console.print(
                    f"  page {page_idx}: {fetched:,} loan-fraud releases "
                    f"({added:,} new)"
                )
    console.print(
        f"[green]Staged {added:,} new releases[/] ({fetched:,} loan-fraud "
        f"releases seen) — by program: {by_program}."
    )


@app.command(name="resolve-labels")
def resolve_labels(
    threshold: float = typer.Option(
        0.6, help="Minimum match confidence [0-1] to accept a loan as a fraud label."
    ),
) -> None:
    """Entity-resolve staged DOJ releases to loans, building `fraud_cases` labels."""
    from relief_probe.labels.resolve import resolve_all

    with connect() as con:
        n_press = con.execute("SELECT COUNT(*) FROM press_releases").fetchone()[0]
        if n_press == 0:
            console.print(
                "[yellow]No staged releases.[/] Run `relief-probe fetch-labels` first."
            )
            raise typer.Exit(code=1)
        console.print(f"Resolving [bold]{n_press:,}[/] staged releases to loans …")
        summary = resolve_all(
            con,
            threshold=threshold,
            progress=lambda i, n: console.print(f"  scanned {i:,} → {n:,} matches"),
        )
    console.print(
        f"[green]Labeled {summary['loans_labeled']:,} loans[/] from "
        f"{summary['releases_matched']:,}/{summary['releases_scanned']:,} "
        f"loan-fraud releases that resolved to a loan."
    )


@app.command(name="resolve-labels-llm")
def resolve_labels_llm(
    threshold: float = typer.Option(
        0.7, help="Minimum LLM confidence [0-1] to accept a new label."
    ),
    max_releases: int = typer.Option(
        0, help="Cap releases scanned (0 = all)."
    ),
    max_adjudications: int = typer.Option(
        2000, help="Hard cap on candidate loans sent to the LLM (the cost ceiling)."
    ),
    concurrency: int = typer.Option(8, "--concurrency", help="Max concurrent LLM calls."),
) -> None:
    """Add LLM-adjudicated fraud labels (DBA / misspelled / sole-prop names).

    Blocks candidate loans by amount (the external corroboration gate), then has an
    LLM adjudicate whether each release truly charges that borrower — recovering
    matches the exact resolver misses. ADDITIVE: never overwrites exact labels;
    new labels are marked `amount+llm`. Needs the `agent` extra + ANTHROPIC_API_KEY.
    Run `resolve-labels` (the precise pass) first.
    """
    from relief_probe.config import llm_model

    with connect() as con:
        n_press = con.execute("SELECT COUNT(*) FROM press_releases").fetchone()[0]
        if n_press == 0:
            console.print(
                "[yellow]No staged releases.[/] Run `fetch-labels` first."
            )
            raise typer.Exit(code=1)
        before = con.execute(
            "SELECT COUNT(DISTINCT loan_number) FROM fraud_cases"
        ).fetchone()[0]

        from relief_probe.labels.llm_resolve import LlmAdjudicator, resolve_with_llm

        model = llm_model()
        adjudicator = LlmAdjudicator(model=model, max_concurrency=concurrency)
        console.print(
            f"LLM entity resolution ({model}) — amount-blocking then adjudicating "
            f"(cap {max_adjudications:,}) …"
        )
        try:
            summary = resolve_with_llm(
                con,
                adjudicator,
                threshold=threshold,
                max_releases=max_releases or None,
                max_adjudications=max_adjudications,
                progress=lambda m: console.print(f"  {m}"),
            )
        except RuntimeError as exc:  # missing extra / API key
            console.print(f"[yellow]{exc}[/]")
            raise typer.Exit(code=1) from None

    if summary["cap_hit"]:
        console.print(
            f"[yellow]Capped[/] at {summary['max_adjudications']:,} adjudications."
        )
    if summary["n_errors"]:
        console.print(
            f"[yellow]{summary['n_errors']} adjudication(s)[/] failed and fell back "
            "to no-match (precision-safe)."
        )
    console.print(
        f"[green]Added {summary['new_loans_labeled']:,} new labels[/] from "
        f"{summary['candidates_adjudicated']:,} amount-blocked candidates "
        f"({before:,} → {before + summary['new_loans_labeled']:,} distinct labeled "
        "loans). New labels are marked `amount+llm`."
    )


@app.command()
def score(
    top: int = typer.Option(25, help="How many ranked loans to print."),
) -> None:
    """Run all detectors, persist signals, and print the ranked lead list."""
    from relief_probe.detectors.runner import run_all
    from relief_probe.scoring import composite_ranking

    with connect() as con:
        n_loans = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
        if n_loans == 0:
            console.print(
                "[yellow]No loans in the warehouse.[/] Run "
                "`relief-probe ingest` first."
            )
            raise typer.Exit(code=1)

        console.print(f"Running detectors over [bold]{n_loans:,}[/] loans …")
        counts = run_all(con)
        for det_id, n in counts.items():
            console.print(f"  {det_id}: [bold]{n:,}[/] signals")

        ranking = composite_ranking(con, limit=top)

    if ranking.empty:
        console.print("[yellow]No loans flagged.[/]")
        return

    t = Table(title=f"Top {top} leads by composite risk score")
    cols = ("loan_number", "borrower_name", "naics", "st", "amount",
            "jobs", "score", "n", "detectors")
    for col in cols:
        t.add_column(col)
    for r in ranking.itertuples(index=False):
        t.add_row(
            str(r.loan_number),
            (r.borrower_name or "")[:28],
            str(r.naics_code or ""),
            str(r.state or ""),
            f"${r.amount:,.0f}" if r.amount is not None else "",
            f"{r.jobs_reported:g}" if r.jobs_reported is not None else "",
            f"{r.composite_score:.2f}",
            str(r.n_signals),
            ", ".join(r.detectors),
        )
    console.print(t)
    console.print(
        "[dim]A high score is a statistical lead for review, not evidence of "
        "fraud. See RESPONSIBLE_USE.md.[/]"
    )


@app.command()
def investigate(
    loan_number: str = typer.Argument(..., help="The loan_number to investigate."),
    llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Synthesize the narrative with an LLM (claude-haiku-4-5 by default; "
        "set RELIEF_PROBE_LLM_MODEL to override). Needs the `agent` extra + "
        "ANTHROPIC_API_KEY. Default is the deterministic path.",
    ),
) -> None:
    """Investigate one loan: gather read-only evidence and print a grounded report.

    The default path is pure-Python and deterministic. `--llm` rewrites only the
    summary prose from the same tool-fetched facts. A populated report is a
    statistical lead for review, not evidence of fraud — see RESPONSIBLE_USE.md.
    """
    from relief_probe.agent.graph import investigate as run_investigate

    with connect(read_only=True) as con:
        n = con.execute(
            "SELECT COUNT(*) FROM loans WHERE loan_number = ?", [loan_number]
        ).fetchone()[0]
        if n == 0:
            console.print(f"[yellow]No loan[/] {loan_number} in the warehouse.")
            raise typer.Exit(code=1)
        try:
            result = run_investigate(con, loan_number, use_llm=llm)
        except RuntimeError as exc:
            console.print(f"[yellow]{exc}[/]")
            raise typer.Exit(code=1) from None

    report = result["report"]
    telemetry = result["telemetry"]

    _risk_styles = {
        "low": "green",
        "elevated": "yellow",
        "high": "red",
        "critical": "bold red",
    }
    style = _risk_styles.get(report.risk_level, "white")
    console.print(
        f"[bold]Loan {report.loan_number}[/] — risk "
        f"[{style}]{report.risk_level.upper()}[/] "
        f"[dim]({telemetry['path']} path, {telemetry['tool_calls']} tools)[/]"
    )
    console.print(report.summary)

    if report.evidence:
        t = Table(title="Evidence (every row cites its source)")
        t.add_column("claim")
        t.add_column("source")
        t.add_column("detail")
        for item in report.evidence:
            t.add_row(item.claim, item.source, item.detail)
        console.print(t)

    if report.alternative_explanations:
        console.print("[bold]Alternative explanations[/]")
        for alt in report.alternative_explanations:
            console.print(f"  • {alt}")
    if report.recommended_next_steps:
        console.print("[bold]Recommended next steps[/]")
        for step in report.recommended_next_steps:
            console.print(f"  • {step}")

    console.print(f"[dim]{report.disclaimer}[/]")


@app.command()
def triage(
    top_k: int = typer.Option(
        100,
        "--top-k",
        help="How many top composite leads to escalate to the Tier-1 judge. "
        "Hard-capped at 2,000 (the cost ceiling) regardless of this value.",
    ),
    llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Use the Haiku-4.5 semantic plausibility judge (set "
        "RELIEF_PROBE_LLM_MODEL to override). Needs the `agent` extra + "
        "ANTHROPIC_API_KEY. Default is the deterministic heuristic judge.",
    ),
    gate: bool = typer.Option(
        False,
        "--gate/--no-gate",
        help="Also run the validation gate: does Tier-1 re-ranking improve lift@k "
        "over the composite alone on the resolved DOJ labels?",
    ),
    concurrency: int = typer.Option(
        8,
        "--concurrency",
        help="Max concurrent LLM calls (--llm only). Higher is faster until the "
        "API rate limit's backoff erases the gain; lower if you get throttled.",
    ),
) -> None:
    """M7 Tier 1 — escalate the top composite leads to a plausibility judge.

    Tier 0 (the composite) ranks all loans for free; this re-ranks only the top-k
    by "could this business plausibly justify this loan?". The LLM NEVER sees more
    than the hard cap (2,000) — that bound is logged. A high triage score is a
    lead for review, not evidence of fraud — see RESPONSIBLE_USE.md.
    """
    from relief_probe.triage.core import triage as run_triage
    from relief_probe.triage.judge import LlmJudge, heuristic_judge

    if llm:
        from relief_probe.config import llm_model

        model = llm_model()
        judge = LlmJudge(model=model, max_concurrency=concurrency)
    else:
        model = None
        judge = heuristic_judge

    with connect() as con:
        n_signals = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        if n_signals == 0:
            console.print(
                "[yellow]No signals.[/] Run `relief-probe score` (or `benchmark`) "
                "first to populate the composite ranking."
            )
            raise typer.Exit(code=1)
        try:
            result = run_triage(con, top_k=top_k, judge=judge, model=model)
        except RuntimeError as exc:  # missing extra / API key on the --llm path
            console.print(f"[yellow]{exc}[/]")
            raise typer.Exit(code=1) from None

        tel = result["telemetry"]
        judged = tel["n_judged"]
        if tel["cap_hit"]:
            console.print(
                f"[yellow]Capped:[/] requested {tel['requested_top_k']:,} but the "
                f"hard cap is {tel['max_triage']:,} — judging {judged:,}."
            )
        console.print(
            f"Tier-1 judge [bold]{tel['judge']}[/]"
            + (f" ({model})" if model else "")
            + f" over [bold]{judged:,}[/] composite leads …"
        )
        if tel.get("n_errors"):
            console.print(
                f"[yellow]{tel['n_errors']} loan(s)[/] fell back to a neutral "
                "verdict after retries (not truly judged)."
            )

        t = Table(title=f"Top leads re-ranked by Tier-1 plausibility (top {top_k})")
        for col in ("rank", "loan_number", "borrower_name", "naics", "amount",
                    "jobs", "implaus", "verdict", "triage", "composite"):
            t.add_column(col)
        for i, s in enumerate(result["ranked"][:25], start=1):
            c = s.candidate
            t.add_row(
                str(i),
                str(c.loan_number),
                (c.borrower_name or "")[:24],
                str(c.naics_code or ""),
                f"${c.amount:,.0f}" if c.amount is not None else "",
                f"{c.jobs:g}" if c.jobs is not None else "",
                str(s.verdict.implausibility),
                s.verdict.verdict,
                f"{s.triage_score:.2f}",
                f"{c.composite_score:.2f}",
            )
        console.print(t)

        if gate:
            from relief_probe.triage.gate import validation_gate

            n_labels = con.execute("SELECT COUNT(*) FROM fraud_cases").fetchone()[0]
            if n_labels == 0:
                console.print(
                    "[yellow]No labels[/] — skipping the gate. Run `fetch-labels` "
                    "then `resolve-labels` first."
                )
            else:
                console.print("Validation gate (composite vs triage re-rank) …")
                # Reuse the verdicts already computed above — never re-judge (on
                # the LLM path that would double the model cost).
                head = [s.candidate.loan_number for s in result["ranked"]]
                g = validation_gate(
                    con, top_k=top_k, judge=judge, reranked_head=head
                )
                gt = Table(
                    title=f"Gate · slice {g['slice']} · {g['n_labeled_fraud']} "
                    f"labels · judge {g['judge']}"
                )
                gt.add_column("k")
                gt.add_column("composite lift", justify="right")
                gt.add_column("triage lift", justify="right")
                gt.add_column("Δ", justify="right")
                for k in g["ks"]:
                    row = g["per_k"][k]
                    cl = row["composite"]["lift"]
                    tl = row["triage"]["lift"]
                    d = row["lift_delta"]
                    gt.add_row(
                        f"{k:,}",
                        "—" if cl is None else f"{cl:.1f}x",
                        "—" if tl is None else f"{tl:.1f}x",
                        "—" if d is None else f"{d:+.1f}",
                    )
                console.print(gt)
                _gate_styles = {
                    "improved": "green", "neutral": "yellow", "regressed": "red"
                }
                gv = g["verdict"]
                console.print(
                    f"Gate verdict: [{_gate_styles.get(gv, 'white')}]{gv}[/] "
                    f"(total lift Δ {g['total_lift_delta']:+.1f}). "
                    "[dim]Re-ranking only permutes the top-k, so k≥top_k is "
                    "unchanged by design.[/]"
                )

    console.print(
        "[dim]A high triage score is a statistical lead for review, not evidence "
        "of fraud. See RESPONSIBLE_USE.md.[/]"
    )


@app.command()
def similar(
    loan_number: str = typer.Argument(
        ..., help="The loan_number to find look-alikes for."
    ),
    k: int = typer.Option(20, "--k", help="How many similar loans to return."),
    min_amount: float = typer.Option(
        150_000.0,
        "--min-amount",
        help="Dollar threshold; loans below this are never considered (keeps it "
        "cheap — we only embed the in-band pool, never the whole warehouse).",
    ),
    amount_tol: float = typer.Option(
        0.25, "--amount-tol", help="+/- dollar band around the loan's amount."
    ),
    same_state: bool = typer.Option(
        True, "--same-state/--all-states", help="Restrict to the borrower's state."
    ),
    lexical_only: bool = typer.Option(
        False,
        "--lexical-only",
        help="Use only the offline lexical embedder (no model download / "
        "embeddings-lite extra needed).",
    ),
    llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Add a grounded LLM narrative of the cluster. Needs the `agent` extra "
        "+ ANTHROPIC_API_KEY.",
    ),
) -> None:
    """Find loans that resemble this one (hybrid name + dollar/area similarity).

    A retrieval tool for investigation — surfaces rings/templates and which
    look-alikes are already prosecuted. NOT a fraud prediction: a resemblance is a
    lead for review, not evidence of fraud. See RESPONSIBLE_USE.md.
    """
    from relief_probe.similarity.core import SIMILARITY_DISCLAIMER, find_similar

    embedder = lexical = None
    if lexical_only:
        from relief_probe.embeddings import HashingEmbedder

        embedder = lexical = HashingEmbedder()

    with connect(read_only=True) as con:
        try:
            result = find_similar(
                con, loan_number, k=k, min_amount=min_amount,
                amount_tol=amount_tol, same_state=same_state,
                embedder=embedder, lexical=lexical,
            )
        except RuntimeError as exc:  # missing embeddings-lite extra (semantic path)
            console.print(f"[yellow]{exc}[/]")
            console.print("[dim]Tip: pass --lexical-only for an offline run.[/]")
            raise typer.Exit(code=1) from None

        if not result["available"]:
            console.print(
                f"[yellow]No similar cases[/] for {loan_number} "
                f"({result['reason']})."
            )
            raise typer.Exit(code=0)

        t = result["target"]
        s = result["summary"]
        flag = " [red](prosecuted)[/]" if t.get("is_fraud") else ""
        console.print(
            f"[bold]{t.get('borrower_name')}[/]{flag} — "
            f"${t['current_approval_amount']:,.0f}, {t.get('borrower_state')}, "
            f"NAICS {t.get('naics_code')} · pool {s['pool_size']:,} · "
            f"[bold]{s['n_fraud_neighbors']}[/] prosecuted look-alike(s)"
        )

        tbl = Table(title=f"Top {len(result['neighbors'])} similar loans")
        for col in ("rank", "loan_number", "borrower_name", "naics", "st",
                    "amount", "d$%", "sem", "lex", "fraud"):
            tbl.add_column(col)
        for n in result["neighbors"]:
            tbl.add_row(
                str(n["rank"]),
                str(n["loan_number"]),
                (n.get("borrower_name") or "")[:26],
                str(n.get("naics_code") or ""),
                str(n.get("borrower_state") or ""),
                f"${(n.get('current_approval_amount') or 0):,.0f}",
                f"{n['amount_delta_pct']:.0%}",
                f"{n['semantic_sim']:.2f}",
                f"{n['lexical_sim']:.2f}",
                "yes" if n["is_fraud"] else "",
                style="red" if n["is_fraud"] else None,
            )
        console.print(tbl)

        from relief_probe.similarity.explain import deterministic_summary

        console.print(deterministic_summary(result))
        if llm:
            from relief_probe.config import llm_model
            from relief_probe.similarity.explain import explain_cluster

            try:
                console.print(f"[bold]LLM narrative[/] [dim]({llm_model()})[/]")
                console.print(explain_cluster(result, model=llm_model()))
            except RuntimeError as exc:
                console.print(f"[yellow]{exc}[/]")
                raise typer.Exit(code=1) from None

    console.print(f"[dim]{SIMILARITY_DISCLAIMER}[/]")


@app.command(name="learn-score")
def learn_score(
    holdout_year: int = typer.Option(
        2023,
        "--holdout-year",
        help="Train on cases charged <= this year; validate on later ones (the "
        "leakage-free out-of-time split, H7).",
    ),
    bags: int = typer.Option(50, "--bags", help="PU-bagging estimators."),
    model: str = typer.Option(
        "pu-bagging",
        "--model",
        help="Learned model: `pu-bagging` (M10 baseline) or `lgbm` (the Loop 6 "
        "LightGBM nested-validation harness; needs the `ml` extra).",
    ),
) -> None:
    """Learned PU scorer vs the composite on a temporal holdout (needs the `ml` extra).

    Fits a PU-bagging model on prosecutions charged <= the holdout year (using the
    detector scores + structured fields as features) and validates on prosecutions
    charged later — the honest test of whether fitting to labels beats the
    unsupervised composite on FUTURE enforcement. Recall-on-known-fraud, not a fraud
    rate. See RESPONSIBLE_USE.md.

    `--model lgbm` runs the EXPLORATORY LightGBM nested-validation harness instead:
    grouped-k-fold CV tunes (entity-grouped, charges<=holdout_year), the temporal
    holdout is the honest headline, and lgbm/pu-bagging/composite/rrf-fusion are
    compared on the SAME held-out positives. EXPLORATORY only (SIGN-010).
    """
    model = model.lower().replace("_", "-")
    if model not in ("pu-bagging", "lgbm"):
        console.print(
            f"[yellow]Unknown --model {model!r}.[/] Use `pu-bagging` or `lgbm`."
        )
        raise typer.Exit(code=1)

    if model == "lgbm":
        _learn_score_lgbm(holdout_year)
        return

    from relief_probe.scorer.validate import run_holdout_validation

    with connect() as con:
        n_lab = con.execute("SELECT COUNT(*) FROM fraud_cases").fetchone()[0]
        n_sig = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        if n_lab == 0 or n_sig == 0:
            console.print(
                "[yellow]Need labels + signals.[/] Run `fetch-labels`, "
                "`resolve-labels`, and `score` first."
            )
            raise typer.Exit(code=1)
        console.print(
            f"Training PU-bagging ({bags} bags, holdout <= {holdout_year}) …"
        )
        try:
            res = run_holdout_validation(
                con, holdout_year=holdout_year, n_estimators=bags
            )
        except RuntimeError as exc:  # missing ml extra
            console.print(f"[yellow]{exc}[/]")
            raise typer.Exit(code=1) from None

    console.print(
        f"Holdout: train (<= {res['holdout_year']}) "
        f"[bold]{res['n_train_positives']}[/] positives · test (> "
        f"{res['holdout_year']}) [bold]{res['n_test_positives']}[/] · population "
        f"{res['population']:,} · base {res['base_rate']:.4%}"
    )
    ks = res["ks"]
    t = Table(title="Held-out recall@k — learned PU scorer vs composite")
    t.add_column("k")
    t.add_column("learned hits", justify="right")
    t.add_column("learned recall", justify="right")
    t.add_column("composite hits", justify="right")
    t.add_column("composite recall", justify="right")
    for k in ks:
        lm = res["learned"]["metrics"][k]
        cm = res["composite"]["metrics"][k]
        t.add_row(
            f"{k:,}",
            str(lm["hits"]),
            "—" if lm["recall"] is None else f"{lm['recall']:.1%}",
            str(cm["hits"]),
            "—" if cm["recall"] is None else f"{cm['recall']:.1%}",
        )
    console.print(t)
    cr = res["composite"]["ranks"]
    console.print(
        f"[dim]Held-out positives ranked: learned uses ALL {res['population']:,} "
        f"loans; composite only ranks the {cr['n_in_ranking']:,} detector-flagged "
        "(the rest sit in an arbitrary tail) — the learned scorer's edge is ranking "
        "the unflagged majority.[/]"
    )
    console.print(
        "[bold]Top learned features:[/] "
        + ", ".join(f"{n} ({v})" for n, v in res["top_features"][:6])
    )
    styles = {"learned BEATS composite": "green"}
    console.print(
        f"Verdict: [{styles.get(res['verdict'], 'yellow')}]{res['verdict']}[/]  "
        "[dim](out-of-time; recall-on-known-fraud on a PU sample).[/]"
    )


def _learn_score_lgbm(holdout_year: int) -> None:
    """`learn-score --model lgbm`: the EXPLORATORY LightGBM nested-validation run.

    Compares lgbm vs pu-bagging vs composite vs rrf-fusion on the SAME temporal
    holdout (SIGN-013); CV only tunes (entity-grouped, SIGN-016). EXPLORATORY —
    never auto-promoted into the production composite (SIGN-010).
    """
    from relief_probe.scorer.validate import run_nested_lgbm_validation

    with connect() as con:
        n_lab = con.execute("SELECT COUNT(*) FROM fraud_cases").fetchone()[0]
        n_sig = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        if n_lab == 0 or n_sig == 0:
            console.print(
                "[yellow]Need labels + signals.[/] Run `fetch-labels`, "
                "`resolve-labels`, and `score` first."
            )
            raise typer.Exit(code=1)
        console.print(
            f"Tuning + training LightGBM (nested CV, holdout <= {holdout_year}) …"
        )
        try:
            res = run_nested_lgbm_validation(con, holdout_year=holdout_year)
        except RuntimeError as exc:  # missing ml extra / lightgbm absent
            console.print(f"[yellow]{exc}[/]")
            raise typer.Exit(code=1) from None

    console.print(
        f"Holdout: train (<= {res['holdout_year']}) "
        f"[bold]{res['n_train_positives']}[/] positives · test (> "
        f"{res['holdout_year']}) [bold]{res['n_test_positives']}[/] · population "
        f"{res['population']:,} · base {res['base_rate']:.4%}"
    )
    ks = res["ks"]
    rk = res["rankings"]
    cols = ("lgbm", "pu_bagging", "composite", "rrf_fusion")
    t = Table(title="Held-out recall@k — LightGBM vs PU-bagging vs composite vs RRF")
    t.add_column("k")
    for name in cols:
        t.add_column(name, justify="right")
    for k in ks:
        row = [f"{k:,}"]
        for name in cols:
            m = rk[name]["metrics"][k]
            row.append("—" if m["recall"] is None else f"{m['recall']:.1%}")
        t.add_row(*row)
    console.print(t)
    console.print(
        "[bold]Top LightGBM features (gain):[/] "
        + ", ".join(f"{n} ({v})" for n, v in res["feature_importance"][:6])
    )
    styles = {"improved": "green", "regressed": "red"}
    console.print(
        f"Verdict (lgbm vs composite): "
        f"[{styles.get(res['verdict'], 'yellow')}]{res['verdict']}[/]  "
        "[dim](EXPLORATORY, SIGN-010; temporal holdout, recall-on-known-fraud).[/]"
    )


@app.command(name="kyb-enrich")
def kyb_enrich(
    top_k: int = typer.Option(
        25,
        "--top-k",
        help="How many top composite leads to enrich with external KYB evidence. "
        "Hard-capped at 50 (the OpenCorporates free-tier ~50/day cost ceiling) "
        "regardless of this value.",
    ),
    max_concurrency: int = typer.Option(
        4, "--max-concurrency", help="Max concurrent KYB lookups (bounded I/O fan-out)."
    ),
    live: bool = typer.Option(
        False,
        "--live/--stub",
        help="--live hits the OpenCorporates API (needs OPENCORPORATES_TOKEN; "
        "rate-limited ~50/day). Default --stub uses the offline StubProvider — no "
        "network, no token.",
    ),
    llm: bool = typer.Option(
        False,
        "--llm/--no-llm",
        help="Narrate the top lead's KYB dossier with an LLM (grounded facts only). "
        "Needs the `agent` extra + ANTHROPIC_API_KEY. Default is deterministic.",
    ),
) -> None:
    """Tier-B KYB — enrich the top composite leads with external registry evidence.

    Pulls the top-k composite leads, looks each up against an external registry
    (incorporation date / non-registered / address type), and refines the ranking
    with a grounded KYB bonus. The live provider NEVER sees more than the hard cap
    (50). Default --stub is fully offline. Every output is a LEAD for review, not
    evidence of fraud — see RESPONSIBLE_USE.md.
    """
    from relief_probe.kyb.enrich import enrich_top_k, synthesize_dossier
    from relief_probe.kyb.provider import OpenCorporatesProvider, StubProvider

    if live:
        provider: object = OpenCorporatesProvider()
        try:
            # Fail fast with the clear, actionable token message (the enrich loop
            # itself swallows per-lookup errors, so we gate the token up front).
            provider._ensure_token()  # type: ignore[attr-defined]
        except RuntimeError as exc:
            console.print(f"[yellow]{exc}[/]")
            raise typer.Exit(code=1) from None
    else:
        provider = StubProvider()

    model = None
    if llm:
        from relief_probe.config import llm_model

        model = llm_model()

    with connect(read_only=True) as con:
        n_signals = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        if n_signals == 0:
            console.print(
                "[yellow]No signals.[/] Run `relief-probe score` first to populate "
                "the composite ranking."
            )
            raise typer.Exit(code=1)

        result = enrich_top_k(
            con, provider, top_k=top_k, max_concurrency=max_concurrency
        )
        tel = result["telemetry"]
        if tel["cap_hit"]:
            console.print(
                f"[yellow]Capped:[/] requested {tel['requested']:,} but the hard "
                f"cap is {tel['max_kyb']} — enriching {tel['n_leads']}."
            )
        console.print(
            f"KYB enrichment via [bold]{tel['provider']}[/] over "
            f"[bold]{tel['enriched']:,}[/] composite leads "
            f"[dim]({tel['n_cache_hits']} cache hits)[/] …"
        )
        if tel["n_errors"]:
            console.print(
                f"[yellow]{tel['n_errors']} lookup(s)[/] failed and returned no "
                "evidence (telemetered, never aborts the batch)."
            )
        if tel["quota_exhausted"]:
            console.print(
                "[yellow]Quota exhausted[/] mid-run — stopped cleanly; already-"
                "fetched results are preserved."
            )

        enriched = result["enriched"]
        if not enriched:
            console.print("[yellow]No leads to enrich.[/]")
            return

        t = Table(title=f"Top leads refined by KYB evidence (top {top_k})")
        for col in ("rank", "loan_number", "borrower_name", "st", "amount",
                    "composite", "kyb+", "kyb_score", "evidence"):
            t.add_column(col)
        for i, e in enumerate(enriched[:25], start=1):
            t.add_row(
                str(i),
                str(e.loan_number),
                (e.borrower_name or "")[:24],
                str(e.state or ""),
                f"${e.amount:,.0f}" if e.amount is not None else "",
                f"{e.composite_score:.2f}",
                f"+{e.kyb_bonus:.2f}" if e.kyb_bonus else "—",
                f"{e.kyb_score:.2f}",
                _kyb_evidence_cell(e.evidence),
            )
        console.print(t)

        if llm:
            top = enriched[0]
            try:
                console.print(f"[bold]LLM dossier[/] [dim]({model})[/]")
                console.print(synthesize_dossier(top, top.evidence, model=model))
            except RuntimeError as exc:
                console.print(f"[yellow]{exc}[/]")
                raise typer.Exit(code=1) from None

    console.print(
        "[dim]A KYB hit is a statistical lead for review, not evidence of fraud — "
        "a borrower may use a DBA/variant name and a wrong-entity match defames a "
        "real business. See RESPONSIBLE_USE.md.[/]"
    )


def _kyb_evidence_cell(evidence) -> str:
    """One-line registry footprint for the results table (grounded, never proof)."""
    if evidence is None:
        return "—"
    if evidence.is_non_registered:
        return f"not in registry ({evidence.match_confidence:.2f})"
    parts: list[str] = []
    if evidence.registration_date:
        parts.append(f"reg {evidence.registration_date.isoformat()}")
    if evidence.address_type:
        parts.append(str(evidence.address_type))
    parts.append(f"conf {evidence.match_confidence:.2f}")
    return ", ".join(parts)


@app.command(name="serve-mcp")
def serve_mcp() -> None:
    """Serve the four read-only investigator tools over MCP (stdio).

    Exposes `score_loan`, `peer_compare`, `check_fraud_case`, and `investigate`
    to an MCP client (e.g. Claude Desktop). Needs the `agent` extra
    (`uv sync --extra agent`). Every tool is read-only — the server never writes
    to the warehouse, and reports remain leads for review, not evidence of fraud.
    """
    from relief_probe.agent.mcp_server import build_server

    try:
        server = build_server()
    except RuntimeError as exc:
        console.print(f"[yellow]{exc}[/]")
        raise typer.Exit(code=1) from None
    # Startup notice goes to stderr so it never corrupts the stdio JSON-RPC stream.
    Console(stderr=True).print(
        "[green]relief-probe MCP server[/] — 4 read-only tools, stdio. Ctrl-C to stop."
    )
    server.run()


@app.command()
def ingest(
    slice_name: str = typer.Option(
        "150k_plus",
        "--slice",
        help=f"Which PPP files to load. One of: {', '.join(SLICES)}.",
    ),
) -> None:
    """Resolve + download + load public PPP FOIA loan data into the warehouse.

    `150k_plus` (~1M loans) is the fast default; `all` pulls ~11.4M loans (~8 GB).
    """
    from relief_probe.ingest import ingest_ppp

    console.print(f"[bold]Ingesting PPP slice[/] '{slice_name}' …")
    with connect() as con:
        results = ingest_ppp(
            con, slice_name, progress=lambda m: console.print(f"  {m}")
        )
    total = sum(r["rows"] for r in results)
    console.print(
        f"[green]Loaded {total:,} loans[/] from {len(results)} file(s)."
    )


@app.command(name="ingest-establishments")
def ingest_establishments(
    path: str = typer.Argument(
        ..., help="Local path to a Census ZIP Business Patterns (ZBP) CSV."
    ),
) -> None:
    """Load a LOCAL Census ZBP CSV into the `establishments` table.

    Establishment counts by ZIP x NAICS feed the (exploratory) `establishment_overcount`
    detector. This command does NOT download — the real ZBP file is a manual public
    download (see ingest/sources.py: ZBP_LANDING_URL); it takes a local path only.
    """
    from pathlib import Path

    from relief_probe.ingest.establishments import load_zbp_csv

    csv_path = Path(path)
    if not csv_path.exists():
        console.print(f"[yellow]No such file[/] {path}")
        raise typer.Exit(code=1)
    console.print(f"[bold]Loading Census ZBP establishments[/] from {csv_path} …")
    with connect() as con:
        inserted = load_zbp_csv(con, csv_path)
    console.print(
        f"[green]Loaded {inserted:,} establishment rows[/] into `establishments`."
    )


@app.command(name="ingest-naics")
def ingest_naics(
    path: str = typer.Argument(
        ..., help="Local path to a Census NAICS code/title CSV."
    ),
) -> None:
    """Load a LOCAL Census NAICS code/title CSV into the `naics_titles` table.

    Gives the (exploratory) `naics_name_mismatch` detector finer industry titles than
    its bundled 2-digit sector defaults. Does NOT download — the NAICS file is a manual
    public download (census.gov/naics); it takes a local path only.
    """
    from pathlib import Path

    from relief_probe.ingest.naics import load_naics_titles

    csv_path = Path(path)
    if not csv_path.exists():
        console.print(f"[yellow]No such file[/] {path}")
        raise typer.Exit(code=1)
    console.print(f"[bold]Loading NAICS titles[/] from {csv_path} …")
    with connect() as con:
        try:
            inserted = load_naics_titles(con, csv_path)
        except ValueError as exc:
            console.print(f"[yellow]{exc}[/]")
            raise typer.Exit(code=1) from None
    console.print(f"[green]Loaded {inserted:,} NAICS titles[/] into `naics_titles`.")


if __name__ == "__main__":
    app()
