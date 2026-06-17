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
