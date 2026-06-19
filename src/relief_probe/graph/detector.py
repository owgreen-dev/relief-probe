"""EXPLORATORY multi-relational fraud-ring detector (Loop 4).

:class:`MultiRelationalRingDetector` is the per-loan surface of the graph layer.
It builds the multi-relational loan graph
(:func:`relief_probe.graph.build.build_loan_graph`), computes LABEL-FREE
structural features (:func:`relief_probe.graph.features.graph_structural_features`),
and fires on every loan that sits in a connected component which is *both*:

* spun by **>= ``min_edge_types`` distinct edge relations** (address / entity /
  similarity) — the whole bet of Loop 4 is that COMBINING relations separates
  real rings from benign clustering; the single-relation ``duplicate_address_ring``
  (shared address ALONE) was already validated NULL because legitimate
  co-location (office parks, strip malls, registered agents) dominates; and
* woven from **>= ``min_borrowers`` distinct borrowers** — one borrower funding
  many loans is duplicate funding, not a ring.

The ``score`` is a label-free structural quantity (``log1p(distinct_borrowers) +
log1p(community_size)``), monotonic in ring strength and comparable WITHIN this
detector. Read-only and LABEL-FREE — it never reads ``fraud_cases``.

DISPOSITION: EXPLORATORY only (SIGN-010). It lives in
:func:`relief_probe.detectors.registry.exploratory_detectors` and is NEVER in
``all_detectors()`` / the production composite; promotion is a MANUAL human
decision after real-data validation against the prosecuted-fraud labels
(``scripts/validate_ring_graph.py``).
"""

from __future__ import annotations

import math
from typing import Any

import duckdb

from relief_probe.detectors.base import Detector, Signal
from relief_probe.embeddings import Embedder
from relief_probe.graph.build import build_loan_graph
from relief_probe.graph.features import graph_structural_features


class MultiRelationalRingDetector(Detector):
    detector_id = "fraud_ring_graph"
    summary = (
        "Loans embedded in a multi-relational ring — a connected component "
        "spanning >=2 edge relations (address + entity + similarity) and >=2 "
        "distinct borrowers (the address-alone-null redo: combining relations)."
    )

    def __init__(
        self,
        *,
        min_edge_types: int = 2,
        min_borrowers: int = 2,
        embedder: Embedder | None = None,
        build_kwargs: dict[str, Any] | None = None,
    ) -> None:
        # A ring must span at least this many distinct edge relations...
        self.min_edge_types = min_edge_types
        # ...and weave together at least this many distinct borrowers.
        self.min_borrowers = min_borrowers
        # Injectable embedder for the similarity relation (offline default).
        self.embedder = embedder
        # Extra kwargs forwarded to build_loan_graph (min_amount, max_group, ...).
        self.build_kwargs = dict(build_kwargs or {})

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        import networkx as nx

        graph = build_loan_graph(con, embedder=self.embedder, **self.build_kwargs)
        feats = graph_structural_features(graph)

        signals: list[Signal] = []
        for comp in nx.connected_components(graph):
            sub = graph.subgraph(comp)
            types_in_comp: set[str] = set()
            for u, v in sub.edges():
                types_in_comp |= graph[u][v]["types"]
            if len(types_in_comp) < self.min_edge_types:
                continue

            # distinct_borrowers is a component-level quantity (same for all nodes).
            any_node = next(iter(comp))
            distinct = feats[any_node]["distinct_borrowers"]
            if distinct < self.min_borrowers:
                continue

            community_size = max(feats[n]["community_size"] for n in comp)
            score = round(math.log1p(distinct) + math.log1p(community_size), 4)
            edge_types_sorted = sorted(types_in_comp)
            for ln in comp:
                f = feats[ln]
                signals.append(
                    Signal(
                        loan_number=ln,
                        detector_id=self.detector_id,
                        score=score,
                        evidence={
                            "component_size": f["component_size"],
                            "distinct_borrowers": distinct,
                            "edge_types": edge_types_sorted,
                            "n_address_edges": f["n_address_edges"],
                            "n_entity_edges": f["n_entity_edges"],
                            "n_similarity_edges": f["n_similarity_edges"],
                            "community_size": f["community_size"],
                        },
                    )
                )
        return signals
