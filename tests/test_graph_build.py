"""Tests for the multi-relational loan-graph builder (offline, deterministic).

A one-hot stub embedder (cosine 1.0 for identical names, 0.0 otherwise) makes the
similarity relation exact and assertable without a real model. The seed plants a
ring (shared address + identical names + close amounts → multiple edge types), two
isolated loans, and an oversized shared-address group that must be SKIPPED (capped,
not exploded into a clique). All on an EMPTY ``fraud_cases`` table — the builder is
label-free.
"""

from __future__ import annotations

import numpy as np
import pytest

from relief_probe.graph.build import EDGE_TYPES, build_loan_graph
from relief_probe.warehouse import connect

pytest.importorskip("networkx")

_RING = "Sunrise Janitorial LLC"


class _StubEmbedder:
    """One-hot vocabulary embedder: cosine 1.0 with self, 0.0 with anything else."""

    def __init__(self, vocab: list[str]) -> None:
        self.index = {t: i for i, t in enumerate(vocab)}
        self.dim = max(len(vocab), 1)

    def embed(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            if t in self.index:
                out[r, self.index[t]] = 1.0
        return out


class _SpyCon:
    """Wraps a connection and records every SQL string the builder executes."""

    def __init__(self, con) -> None:
        self._con = con
        self.sql: list[str] = []

    def execute(self, query, *args, **kwargs):
        self.sql.append(query)
        return self._con.execute(query, *args, **kwargs)


def _seed(con) -> None:
    # cols: loan_number, name, address, city, state, zip, naics, amount
    rows = [
        # The ring: same building + identical names + close amounts (one band).
        ("RING1", _RING, "100 Main St", "SF", "CA", "94105", "561720", 200_000.0),
        ("RING2", _RING, "100 Main St", "SF", "CA", "94105-1234", "561720", 202_000.0),
        ("RING3", _RING, "100 Main St", "SF", "CA", "94105", "561720", 198_000.0),
        # Isolated loans: distinct names + distinct buildings.
        ("ISO1", "Acme Bakery LLC", "500 Oak Ave", "LA", "CA", "90001", "722511",
         300_000.0),
        ("ISO2", "Bobs Plumbing Inc", "900 Pine Rd", "Austin", "TX", "78701", "238220",
         175_000.0),
        # Oversized shared-address group (5 > max_group=3): distinct names so no
        # similarity/entity edges either → must be SKIPPED (not a clique).
        ("BIG1", "Alpha Co", "999 Mega Blvd", "NY", "NY", "10001", "111111", 160_000.0),
        ("BIG2", "Bravo Co", "999 Mega Blvd", "NY", "NY", "10001", "222222", 160_000.0),
        ("BIG3", "Delta Co", "999 Mega Blvd", "NY", "NY", "10001", "333333", 160_000.0),
        ("BIG4", "Echo Co", "999 Mega Blvd", "NY", "NY", "10001", "444444", 160_000.0),
        ("BIG5", "Foxtrot Co", "999 Mega Blvd", "NY", "NY", "10001", "555555", 160_000.0),
        # Below the $150k slice threshold → excluded entirely.
        ("SUB", _RING, "100 Main St", "SF", "CA", "94105", "561720", 90_000.0),
    ]
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, borrower_address, "
        "borrower_city, borrower_state, borrower_zip, naics_code, "
        "current_approval_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def _build(con, **kw):
    return build_loan_graph(
        con, max_group=3, embedder=_StubEmbedder([_RING]), **kw
    )


def test_ring_is_one_component_spanning_multiple_edge_types(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    graph = _build(con)

    import networkx as nx

    # The three ring loans are one connected component.
    comp = next(c for c in nx.connected_components(graph) if "RING1" in c)
    assert comp == {"RING1", "RING2", "RING3"}

    # That component spans >= 2 distinct edge types (address + similarity at least).
    types_in_comp: set[str] = set()
    for u, v in graph.subgraph(comp).edges():
        types_in_comp |= graph[u][v]["types"]
    assert {"address", "similarity"} <= types_in_comp
    assert len(types_in_comp) >= 2

    # Slice excludes the $90k loan.
    assert "SUB" not in graph


def test_isolated_loans_stay_sparse(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    graph = _build(con)
    assert graph.degree("ISO1") == 0
    assert graph.degree("ISO2") == 0


def test_edge_type_attributes_present(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    graph = _build(con)
    assert graph.number_of_edges() > 0
    for _u, _v, data in graph.edges(data=True):
        assert data["type"] in EDGE_TYPES
        assert data["types"] <= set(EDGE_TYPES)
        assert data["types"]  # non-empty


def test_oversized_group_is_capped_not_a_clique(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    graph = _build(con)
    # The 5-member shared-address group exceeds max_group=3 → skipped entirely.
    big = ["BIG1", "BIG2", "BIG3", "BIG4", "BIG5"]
    assert graph.subgraph(big).number_of_edges() == 0


def test_builder_never_queries_fraud_cases(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)  # fraud_cases left EMPTY
    spy = _SpyCon(con)
    graph = build_loan_graph(spy, max_group=3, embedder=_StubEmbedder([_RING]))
    assert graph.number_of_nodes() == 10  # all $150k+ loans, SUB excluded
    assert spy.sql, "builder must execute at least one query"
    assert all("fraud_cases" not in q.lower() for q in spy.sql)


def test_edge_types_param_disables_relations(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    graph = build_loan_graph(
        con, max_group=3, edge_types=("address",),
        embedder=_StubEmbedder([_RING]),
    )
    for _u, _v, data in graph.edges(data=True):
        assert data["types"] == {"address"}
