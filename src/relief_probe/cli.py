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
