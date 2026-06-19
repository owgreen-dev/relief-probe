"""Build a multi-relational loan graph (NetworkX, the ``graph`` extra).

:func:`build_loan_graph` reads the ``$150k+`` loan slice (read-only) and links
loans by three LABEL-FREE, SPARSE-by-blocking relations:

* **address** — loans sharing a normalized building-level key
  (:func:`relief_probe.detectors._address.normalize_address`): the same physical
  building (a co-location lead, independent of dollars-per-job).
* **entity** — loans sharing a resolved-borrower key
  (:func:`relief_probe.detectors._entity.entity_key`): the same borrower filing
  more than once (duplicate funding).
* **similarity** — loans that are high name + amount-band + same-area look-alikes
  (reusing the similarity engine's blocking idea + an offline embedder): different
  filings that *resemble* one another the way re-used shell templates do.

Why a graph (the relational thesis)
-----------------------------------
The row-wise detectors ask "is this loan implausible?"; a graph asks "is this loan
*embedded in a coordinated structure*?". Three prediction attempts on this project
were honest NEGATIVES because individual loans look plausible, while both project
wins were relational. The single-edge-type ``duplicate_address_ring`` was already
NULL (legitimate co-location dominates), so the bet is that COMBINING edge types
plus community detection separates real rings from benign clustering.

Sparsity discipline (so ~965k nodes stays tractable)
----------------------------------------------------
NetworkX is pure-python, so edges must stay sparse. We GROUP loans by a shared key
in Python and link only *within* a group; any group larger than ``max_group`` is
SKIPPED so a giant shared key (a registered-agent address, a mega-lender, a common
amount band) cannot detonate into a million-edge clique.

LABEL-FREE & read-only
----------------------
The builder never reads ``fraud_cases`` (or any label table) and never writes to
the warehouse — it works unchanged on a warehouse with an empty ``fraud_cases``
table. NetworkX is imported lazily; a missing ``graph`` extra raises a clear,
actionable :class:`RuntimeError` (mirroring the embeddings/agent gating).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

import duckdb

from relief_probe.detectors._address import normalize_address
from relief_probe.detectors._entity import entity_key
from relief_probe.embeddings import Embedder
from relief_probe.labels.resolve import normalize_name

if TYPE_CHECKING:  # pragma: no cover - typing only
    import networkx as nx

#: The three relation kinds an edge can carry.
EDGE_TYPES = ("address", "entity", "similarity")

#: Loan columns the builder reads (read-only).
_FIELDS = (
    "loan_number",
    "borrower_name",
    "borrower_address",
    "borrower_city",
    "borrower_state",
    "borrower_zip",
    "naics_code",
    "current_approval_amount",
)


def _require_networkx() -> Any:
    """Import NetworkX lazily; a missing ``graph`` extra → a clear RuntimeError."""
    try:
        import networkx as nx
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise RuntimeError(
            "The fraud-ring graph layer needs the `graph` extra (NetworkX). "
            "Install it with `uv sync --extra graph` (pure-python, no torch)."
        ) from exc
    return nx


def _add_edge(graph: Any, u: str, v: str, etype: str) -> None:
    """Add (or augment) an undirected edge, accumulating its relation ``types``.

    A pair of loans can be linked by more than one relation (e.g. same address AND
    look-alike names); we collapse them onto one edge but keep the full set of
    ``types`` so a component's edge-type span is recoverable. ``type`` carries a
    single representative value (in :data:`EDGE_TYPES`) for callers that want one.
    """
    if graph.has_edge(u, v):
        graph[u][v]["types"].add(etype)
        return
    graph.add_edge(u, v, type=etype, types={etype})


def _link_clique(graph: Any, members: list[str], etype: str, max_group: int) -> None:
    """Link every pair in ``members`` with an ``etype`` edge — unless oversized.

    Groups above ``max_group`` are SKIPPED entirely (no edges) so a giant shared
    key cannot form a huge clique. Used for the address/entity relations, where
    sharing the key *is* the link.
    """
    n = len(members)
    if n < 2 or n > max_group:
        return
    for i in range(n):
        for j in range(i + 1, n):
            _add_edge(graph, members[i], members[j], etype)


def _link_similarity(
    graph: Any,
    members: list[dict[str, Any]],
    *,
    embedder: Embedder,
    threshold: float,
    max_group: int,
) -> None:
    """Link look-alike pairs within one (state, amount-band) block.

    Embeds the borrower names once (L2-normalized rows → a dot product is a cosine
    similarity) and links pairs at/above ``threshold``. Unlike the address/entity
    relations this is NOT a clique — only genuinely similar names connect. Blocks
    above ``max_group`` are skipped to keep the pairwise step bounded.
    """
    n = len(members)
    if n < 2 or n > max_group:
        return
    names = [m["borrower_name"] or "" for m in members]
    vecs = embedder.embed(names)
    sims = vecs @ vecs.T
    for i in range(n):
        for j in range(i + 1, n):
            if float(sims[i, j]) >= threshold:
                _add_edge(graph, members[i]["loan_number"], members[j]["loan_number"],
                          "similarity")


def build_loan_graph(
    con: duckdb.DuckDBPyConnection,
    *,
    min_amount: float = 150_000.0,
    edge_types: tuple[str, ...] = EDGE_TYPES,
    max_group: int = 50,
    sim_threshold: float = 0.85,
    amount_band: float = 50_000.0,
    embedder: Embedder | None = None,
) -> nx.Graph:
    """Build the multi-relational loan graph over the ``min_amount``+ slice.

    Nodes are ``loan_number`` strings (with ``borrower_name`` / ``norm_name`` /
    ``borrower_state`` / ``amount`` attributes for downstream features). Edges
    carry a ``type`` (in :data:`EDGE_TYPES`) and a ``types`` set. ``edge_types``
    selects which relations to build; ``max_group`` caps every shared-key group;
    ``sim_threshold`` / ``amount_band`` tune the similarity relation; ``embedder``
    is injectable for tests (defaults to the offline :class:`HashingEmbedder`).

    Read-only and LABEL-FREE — never queries ``fraud_cases`` and never writes.
    """
    nx = _require_networkx()
    rows = con.execute(
        f"SELECT {', '.join(_FIELDS)} FROM loans "
        "WHERE current_approval_amount >= ?",
        [min_amount],
    ).fetchall()

    graph: nx.Graph = nx.Graph()
    loans: list[dict[str, Any]] = []
    for row in rows:
        loan = dict(zip(_FIELDS, row, strict=True))
        ln = str(loan["loan_number"])
        amount = (
            float(loan["current_approval_amount"])
            if loan["current_approval_amount"] is not None
            else None
        )
        graph.add_node(
            ln,
            borrower_name=loan["borrower_name"],
            norm_name=normalize_name(loan["borrower_name"]),
            borrower_state=loan["borrower_state"],
            amount=amount,
        )
        loan["loan_number"] = ln
        loan["amount"] = amount
        loans.append(loan)

    if "address" in edge_types:
        groups: dict[str, list[str]] = defaultdict(list)
        for loan in loans:
            key = normalize_address(
                loan["borrower_address"], loan["borrower_city"],
                loan["borrower_state"], loan["borrower_zip"],
            )
            if key is not None:
                groups[key].append(loan["loan_number"])
        for members in groups.values():
            _link_clique(graph, members, "address", max_group)

    if "entity" in edge_types:
        groups = defaultdict(list)
        for loan in loans:
            key = entity_key(
                loan["borrower_name"], loan["borrower_address"],
                loan["borrower_city"], loan["borrower_state"], loan["borrower_zip"],
            )
            if key is not None:
                groups[key].append(loan["loan_number"])
        for members in groups.values():
            _link_clique(graph, members, "entity", max_group)

    if "similarity" in edge_types:
        if embedder is None:
            from relief_probe.embeddings import HashingEmbedder

            embedder = HashingEmbedder()
        blocks: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for loan in loans:
            state = loan["borrower_state"]
            name = (loan["borrower_name"] or "").strip()
            amount = loan["amount"]
            if not state or not name or not amount:
                continue
            band = round(amount / amount_band)
            blocks[(state, band)].append(loan)
        for members in blocks.values():
            _link_similarity(
                graph, members, embedder=embedder,
                threshold=sim_threshold, max_group=max_group,
            )

    return graph
