"""Tests for the label-free structural features + exploratory ring detector (G-002).

A one-hot stub embedder makes the similarity relation exact and assertable. The
seed plants a multi-edge-type RING (shared building + a duplicate-funding entity
pair + look-alike names → address + entity + similarity edges, 2 distinct
borrowers), an ADDRESS-ONLY pair (2 distinct borrowers but only ONE edge relation
→ the detector must stay quiet: the address-alone-null callback), and isolated
loans. All on an EMPTY ``fraud_cases`` table — features and the detector are
label-free.
"""

from __future__ import annotations

import numpy as np
import pytest

from relief_probe.detectors.registry import (
    all_detectors,
    exploratory_detectors,
    get_detector,
)
from relief_probe.graph.build import build_loan_graph
from relief_probe.graph.detector import MultiRelationalRingDetector
from relief_probe.graph.features import graph_structural_features
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
    """Wraps a connection and records every SQL string executed."""

    def __init__(self, con) -> None:
        self._con = con
        self.sql: list[str] = []

    def execute(self, query, *args, **kwargs):
        self.sql.append(query)
        return self._con.execute(query, *args, **kwargs)


def _seed(con) -> None:
    rows = [
        # The ring: two duplicate-funding filings of one borrower at one building
        # (entity + address + similarity edges) PLUS a DISTINCT borrower at the
        # same building (address edge) → component spans 3 relations, 2 borrowers.
        ("RING1", _RING, "100 Main St", "SF", "CA", "94105", "561720", 200_000.0),
        ("RING2", _RING, "100 Main St", "SF", "CA", "94105-1234", "561720", 202_000.0),
        ("RING3", "Moonrise Cleaning LLC", "100 Main St", "SF", "CA", "94105",
         "561720", 198_000.0),
        # Address-ONLY pair: distinct borrowers share a building but nothing else
        # (different names → no entity/similarity edge) → ONE relation → no fire.
        ("ADDR1", "Foo Holdings LLC", "200 Elm St", "Reno", "NV", "89501", "111111",
         160_000.0),
        ("ADDR2", "Bar Ventures LLC", "200 Elm St", "Reno", "NV", "89501", "222222",
         161_000.0),
        # Isolated loans: distinct names + distinct buildings → degree 0.
        ("ISO1", "Acme Bakery LLC", "500 Oak Ave", "LA", "CA", "90001", "722511",
         300_000.0),
        ("ISO2", "Bobs Plumbing Inc", "900 Pine Rd", "Austin", "TX", "78701", "238220",
         175_000.0),
    ]
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, borrower_address, "
        "borrower_city, borrower_state, borrower_zip, naics_code, "
        "current_approval_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def _embedder() -> _StubEmbedder:
    return _StubEmbedder([_RING])


# --- structural features --------------------------------------------------------

def test_structural_features_on_a_multi_edge_type_ring(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    graph = build_loan_graph(con, embedder=_embedder())
    feats = graph_structural_features(graph)

    # The ring is one 3-loan component with 2 distinct borrowers.
    for ln in ("RING1", "RING2", "RING3"):
        assert feats[ln]["component_size"] == 3
        assert feats[ln]["distinct_borrowers"] == 2
        # Small component → community is the component itself.
        assert feats[ln]["community_size"] == 3

    # RING1 neighbours RING2 (address+entity+similarity) and RING3 (address).
    assert feats["RING1"]["degree"] == 2
    assert feats["RING1"]["n_address_edges"] == 2  # both neighbours share address
    assert feats["RING1"]["n_entity_edges"] == 1   # only RING2 (same entity key)
    assert feats["RING1"]["n_similarity_edges"] == 1  # only RING2 (same name)

    # Isolated loans are their own singleton component.
    assert feats["ISO1"]["component_size"] == 1
    assert feats["ISO1"]["degree"] == 0
    assert feats["ISO1"]["distinct_borrowers"] == 1


# --- exploratory detector -------------------------------------------------------

def test_detector_fires_on_ring_and_is_quiet_elsewhere(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    sigs = MultiRelationalRingDetector(embedder=_embedder()).run(con)
    flagged = {s.loan_number for s in sigs}

    # Fires on every loan of the multi-relational ring.
    assert flagged == {"RING1", "RING2", "RING3"}

    # The address-ONLY pair (one relation) does NOT fire — the address-alone redo.
    assert "ADDR1" not in flagged
    assert "ADDR2" not in flagged
    # Isolated loans never fire.
    assert "ISO1" not in flagged
    assert "ISO2" not in flagged

    ev = next(s for s in sigs if s.loan_number == "RING1").evidence
    assert ev["distinct_borrowers"] == 2
    assert set(ev["edge_types"]) == {"address", "entity", "similarity"}
    assert len(ev["edge_types"]) >= 2


def test_detector_min_borrowers_gate(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    # Raising the borrower floor above the ring's 2 silences it.
    sigs = MultiRelationalRingDetector(embedder=_embedder(), min_borrowers=3).run(con)
    assert sigs == []


# --- registry disposition (SIGN-010: exploratory only) --------------------------

def test_fraud_ring_graph_is_exploratory_not_in_default_composite():
    assert "fraud_ring_graph" not in {d.detector_id for d in all_detectors()}
    assert "fraud_ring_graph" in {d.detector_id for d in exploratory_detectors()}
    assert get_detector("fraud_ring_graph").detector_id == "fraud_ring_graph"


# --- label-free (never reads fraud_cases) ---------------------------------------

def test_detector_never_queries_fraud_cases(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)  # fraud_cases left EMPTY
    spy = _SpyCon(con)
    sigs = MultiRelationalRingDetector(embedder=_embedder()).run(spy)
    assert {s.loan_number for s in sigs} == {"RING1", "RING2", "RING3"}
    assert spy.sql, "detector must execute at least one query"
    assert all("fraud_cases" not in q.lower() for q in spy.sql)
