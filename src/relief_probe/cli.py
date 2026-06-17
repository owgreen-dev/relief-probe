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
        from relief_probe.vision.datasets import make_synthetic
        from relief_probe.vision.model import train
    except ImportError:
        console.print("[yellow]Needs the vision extra:[/] uv sync --extra vision")
        raise typer.Exit(code=1) from None

    from relief_probe.config import data_dir

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


@app.command()
def benchmark() -> None:
    """Forward PU validation: how strongly prosecuted loans rank at the top.

    Runs all detectors, ranks loans by composite score, and reports precision@k /
    lift / recall@k against the resolved `fraud_cases` labels, with a per-detector
    ablation. Needs `ingest`, `fetch-labels`, and `resolve-labels` first.
    """
    from relief_probe.benchmark import run_benchmark

    with connect() as con:
        n_labels = con.execute("SELECT COUNT(*) FROM fraud_cases").fetchone()[0]
        if n_labels == 0:
            console.print(
                "[yellow]No labels.[/] Run `fetch-labels` then `resolve-labels` first."
            )
            raise typer.Exit(code=1)
        console.print("Scoring + benchmarking …")
        res = run_benchmark(con)

    console.print(
        f"Population [bold]{res['population']:,}[/] loans · "
        f"[bold]{res['n_labeled_fraud']}[/] prosecuted (base rate "
        f"{res['base_rate']:.4%}) · {res['n_ranked']:,} flagged & ranked."
    )

    ks = res["ks"]
    t = Table(title="Forward lift@k — composite ranking vs DOJ-prosecuted loans")
    t.add_column("k")
    t.add_column("hits", justify="right")
    t.add_column("precision@k", justify="right")
    t.add_column("lift", justify="right")
    t.add_column("recall", justify="right")
    for k in ks:
        m = res["overall"][k]
        lift = m["lift"]
        t.add_row(
            f"{k:,}", str(m["hits"]), f"{m['precision']:.3%}",
            "—" if lift is None else f"{lift:.1f}x",
            "—" if m["recall"] is None else f"{m['recall']:.1%}",
        )
    console.print(t)

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

    console.print(
        "[dim]Recall-on-known-fraud, not a fraud rate: labels are a small, "
        "prosecution-biased PU sample resolved to the $150k+ slice. "
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
        help="Synthesize the narrative with claude-opus-4-8 (needs the `agent` "
        "extra + ANTHROPIC_API_KEY). Default is the deterministic path.",
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
def ingest(
    slice_name: str = typer.Option(
        "150k_plus",
        "--slice",
        help=f"Which PPP files to load. One of: {', '.join(SLICES)}.",
    ),
) -> None:
    """Resolve + download + load public PPP FOIA loan data into the warehouse.

    `150k_plus` (~1M loans) is the fast default; `all` pulls ~11.5M loans (~8 GB).
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


if __name__ == "__main__":
    app()
