"""Label-free structural features over the multi-relational loan graph.

:func:`graph_structural_features` turns the :class:`networkx.Graph` from
:func:`relief_probe.graph.build.build_loan_graph` into a per-loan dict of
LABEL-FREE structural descriptors — quantities that depend only on the graph's
shape (who is connected to whom, by which relation), never on ``fraud_cases`` or
any label table.

Per loan it reports:

* ``component_size`` — size of the loan's connected component (the whole cluster
  it is reachable within).
* ``degree`` — number of distinct neighbours.
* ``n_address_edges`` / ``n_entity_edges`` / ``n_similarity_edges`` — degree split
  by edge relation (an edge linked by more than one relation counts under each).
* ``distinct_borrowers`` — distinct normalized borrower names in the component
  (a single borrower funding many loans is *not* a ring; many distinct borrowers
  woven together is).
* ``community_size`` — size of the loan's community via greedy modularity, run
  ONLY on components at/above ``min_community_size`` to stay cheap; smaller
  components are their own community (``community_size == component_size``).

NetworkX is imported lazily so the package imports without the ``graph`` extra;
a missing extra raises a clear, actionable :class:`RuntimeError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import networkx as nx


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


def graph_structural_features(
    graph: nx.Graph, *, min_community_size: int = 5
) -> dict[str, dict[str, Any]]:
    """Compute LABEL-FREE structural features for every loan node.

    Returns a dict mapping each ``loan_number`` to its feature dict (see the
    module docstring for the keys). Community detection runs only on components
    of at least ``min_community_size`` nodes; below that a component is treated
    as a single community. Reads node attributes only (``norm_name``) — never any
    label table.
    """
    nx = _require_networkx()
    features: dict[str, dict[str, Any]] = {}

    for comp in nx.connected_components(graph):
        comp_size = len(comp)
        distinct = {graph.nodes[n].get("norm_name") for n in comp}
        distinct.discard(None)
        distinct.discard("")
        n_distinct = len(distinct)

        community_of: dict[str, int] = {}
        if comp_size >= min_community_size:
            sub = graph.subgraph(comp)
            for community in nx.community.greedy_modularity_communities(sub):
                size = len(community)
                for n in community:
                    community_of[n] = size

        for n in comp:
            n_addr = n_ent = n_sim = 0
            for nbr in graph[n]:
                types = graph[n][nbr]["types"]
                if "address" in types:
                    n_addr += 1
                if "entity" in types:
                    n_ent += 1
                if "similarity" in types:
                    n_sim += 1
            features[n] = {
                "component_size": comp_size,
                "degree": graph.degree(n),
                "n_address_edges": n_addr,
                "n_entity_edges": n_ent,
                "n_similarity_edges": n_sim,
                "distinct_borrowers": n_distinct,
                "community_size": community_of.get(n, comp_size),
            }

    return features
