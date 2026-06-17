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
