"""Expose the read-only investigator tools over the Model Context Protocol.

Four thin tools wrap the deterministic :mod:`relief_probe.agent.tools` /
:mod:`relief_probe.agent.graph` functions over a *read-only* warehouse
connection, so an MCP client (Claude Desktop, etc.) can score a loan, compare it
to its NAICS x state peers, check it against resolved DOJ/OIG enforcement
records, and run the full deterministic investigation — without ever writing to
the warehouse.

``mcp`` is imported lazily inside :func:`build_server`, so importing this module
never requires the ``agent`` extra. The deterministic tools underneath are pure
Python and carry the same lead-not-evidence disclaimer as the CLI: a populated
report is a statistical lead for review, never proof of fraud (RESPONSIBLE_USE.md).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from relief_probe.agent.graph import investigate as run_investigate
from relief_probe.agent.tools import composite_for, fraud_case_check, peer_comparison
from relief_probe.warehouse import connect

#: The four read-only tool names exposed over MCP — a stable public surface.
TOOL_NAMES = ("score_loan", "peer_compare", "check_fraud_case", "investigate")


def build_server(db_path: Path | str | None = None) -> Any:
    """Build (but do not run) the MCP server with the four read-only tools.

    ``mcp`` is imported here, not at module load, so the core env (without the
    ``agent`` extra) can still import this module. Raises a clear
    :class:`RuntimeError` if the extra is missing. ``db_path`` overrides the
    configured warehouse path (handy for tests); each tool call opens its own
    read-only connection, so the server never writes.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only with extra absent
        raise RuntimeError(
            "The MCP server needs the `agent` extra. Install it with "
            "`uv sync --extra agent`."
        ) from exc

    server = FastMCP("relief-probe")

    def _con() -> Any:
        return connect(db_path, read_only=True)

    @server.tool(name="score_loan")
    def score_loan(loan_number: str) -> dict[str, Any]:
        """Composite risk score + corroborating detectors for one loan.

        Returns ``{'flagged': False}`` if no detector fired. A high score is a
        statistical lead for review, not evidence of fraud.
        """
        with _con() as con:
            return composite_for(con, loan_number)

    @server.tool(name="peer_compare")
    def peer_compare(loan_number: str) -> dict[str, Any]:
        """Dollars-per-job versus the loan's NAICS x state cohort.

        Returns ``{'available': False, 'reason': ...}`` when jobs/amount are
        missing or the cohort is too small to compare against.
        """
        with _con() as con:
            return peer_comparison(con, loan_number)

    @server.tool(name="check_fraud_case")
    def check_fraud_case(loan_number: str) -> dict[str, Any]:
        """Whether the loan links to any resolved DOJ/OIG enforcement record.

        An unmatched loan is unlabeled, not innocent (see RESPONSIBLE_USE.md).
        """
        with _con() as con:
            return fraud_case_check(con, loan_number)

    @server.tool(name="investigate")
    def investigate(loan_number: str) -> dict[str, Any]:
        """Full deterministic investigation: grounded report + telemetry.

        Returns ``{'report': {...}, 'telemetry': {...}}``. Every evidence row
        cites its source tool; the report carries a disclaimer that it is a
        statistical lead for review, not proof of fraud.
        """
        with _con() as con:
            result = run_investigate(con, loan_number, use_llm=False)
        return {
            "report": dataclasses.asdict(result["report"]),
            "telemetry": result["telemetry"],
        }

    return server


def main(db_path: Path | str | None = None) -> None:  # pragma: no cover - stdio loop
    """Run the MCP server over stdio (blocking)."""
    build_server(db_path).run()
