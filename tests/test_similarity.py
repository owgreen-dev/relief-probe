"""Tests for the similar-loan retrieval engine (offline, deterministic).

A one-hot stub embedder (cosine 1.0 for identical names, 0.0 otherwise) makes the
name-similarity exact and assertable without any real model. The seed plants a
name-identical "ring" plus structurally-close decoys and several loans that MUST be
blocked out (wrong state, outside the dollar band, below the threshold).
"""

from __future__ import annotations

import numpy as np
import pytest

from relief_probe.similarity.core import find_similar
from relief_probe.similarity.explain import deterministic_summary
from relief_probe.warehouse import connect

TARGET = "TARGET"


class _StubEmbedder:
    """One-hot vocabulary embedder: cosine 1.0 with self, 0.0 with anything else."""

    def __init__(self, vocab: list[str]) -> None:
        self.index = {t: i for i, t in enumerate(vocab)}
        self.dim = len(vocab)

    def embed(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            if t in self.index:
                out[r, self.index[t]] = 1.0
        return out


_RING = "Sunrise Janitorial LLC"
_VOCAB = [_RING, "Acme Bakery LLC", "Bobs Plumbing Inc", "City Cafe"]


def _seed(con):
    # cols: loan_number, name, city, state, zip, naics, amount, jobs
    rows = [
        (TARGET, _RING, "San Francisco", "CA", "94105", "561720", 200_000.0, 5.0),
        # The ring: name-identical, in-band ($150k-$250k), same state.
        ("RING1", _RING, "San Francisco", "CA", "94105-1234", "561720", 200_000.0, 4.0),
        ("RING2", _RING, "Oakland", "CA", "94110", "561720", 202_000.0, 6.0),
        ("RING3", _RING, "San Francisco", "CA", "94105", "561720", 198_000.0, 3.0),
        # Decoys: in-band + same state but unrelated names + different industry.
        ("DECOY1", "Acme Bakery LLC", "LA", "CA", "90001", "722511", 245_000.0, 9.0),
        ("DECOY2", "Bobs Plumbing Inc", "LA", "CA", "90002", "238220", 246_000.0, 8.0),
        ("DECOY3", "City Cafe", "LA", "CA", "90003", "722513", 247_000.0, 7.0),
        # Must be BLOCKED OUT:
        ("FARAMT", _RING, "San Francisco", "CA", "94105", "561720", 900_000.0, 1.0),
        ("OUTSTATE", _RING, "Austin", "TX", "78701", "561720", 200_000.0, 2.0),
        ("SUBTHRESH", _RING, "San Francisco", "CA", "94105", "561720", 90_000.0, 1.0),
        # Lonely (no in-band peers) + degenerate targets for graceful paths:
        ("LONELY", _RING, "San Francisco", "CA", "94105", "561720", 5_000_000.0, 1.0),
        ("NONAME", None, "San Francisco", "CA", "94105", "561720", 200_000.0, 1.0),
        ("NOAMT", _RING, "San Francisco", "CA", "94105", "561720", None, 1.0),
    ]
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, borrower_city, "
        "borrower_state, borrower_zip, naics_code, current_approval_amount, "
        "jobs_reported) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    # One ring member is a prosecuted case.
    con.execute(
        "INSERT INTO fraud_cases (case_id, loan_number, source, match_method, "
        "match_confidence) VALUES ('c1', 'RING3', 'doj', 'name+state+amount', 1.0)"
    )


def _stub():
    e = _StubEmbedder(_VOCAB)
    return {"embedder": e, "lexical": e}


def test_ring_ranks_top_with_components_and_fraud_flag(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    res = find_similar(con, TARGET, k=5, **_stub())
    assert res["available"] is True
    lns = [n["loan_number"] for n in res["neighbors"]]
    # The three ring members (name-identical + closest dollars) take the top 3.
    assert set(lns[:3]) == {"RING1", "RING2", "RING3"}
    # Component scores are exposed for explainability.
    top = res["neighbors"][0]
    assert top["semantic_sim"] == 1.0  # identical name under the stub
    assert top["same_naics"] is True
    assert set(top) >= {
        "semantic_sim", "lexical_sim", "amount_delta_pct", "same_naics",
        "same_state", "same_zip5", "is_fraud", "rank", "fused_score",
    }
    # The prosecuted ring member is flagged.
    by_ln = {n["loan_number"]: n for n in res["neighbors"]}
    assert by_ln["RING3"]["is_fraud"] is True
    assert res["summary"]["n_fraud_neighbors"] == 1


def test_blocking_excludes_band_state_and_threshold(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    res = find_similar(con, TARGET, k=20, **_stub())
    lns = {n["loan_number"] for n in res["neighbors"]}
    assert "FARAMT" not in lns  # $900k -> outside the +/-25% dollar band
    assert "SUBTHRESH" not in lns  # $90k -> below the $150k threshold
    assert "OUTSTATE" not in lns  # TX -> blocked by same_state
    assert TARGET not in lns  # self-excluded
    # k=20 but only 6 candidates qualify -> tiny pool returns them all.
    assert len(res["neighbors"]) == 6


def test_all_states_includes_out_of_state_ring_member(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    res = find_similar(con, TARGET, k=20, same_state=False, **_stub())
    lns = {n["loan_number"] for n in res["neighbors"]}
    assert "OUTSTATE" in lns  # now in the pool, and name-identical -> ranks high


def test_zip_plus_four_truncation_same_zip5(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    res = find_similar(con, TARGET, k=20, **_stub())
    by_ln = {n["loan_number"]: n for n in res["neighbors"]}
    # Target zip "94105"; RING1 zip "94105-1234" -> same_zip5 True after [:5].
    assert by_ln["RING1"]["same_zip5"] is True
    assert by_ln["RING2"]["same_zip5"] is False  # 94110


def test_graceful_shapes(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    assert find_similar(con, "NOPE", **_stub())["reason"] == "loan_not_found"
    assert find_similar(con, "NONAME", **_stub())["reason"] == "missing_name"
    assert find_similar(con, "NOAMT", **_stub())["reason"] == "missing_amount"
    lonely = find_similar(con, "LONELY", **_stub())
    assert lonely["reason"] == "empty_pool"
    assert lonely["neighbors"] == []
    assert lonely["target"]["loan_number"] == "LONELY"


def test_deterministic_summary_is_grounded(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    res = find_similar(con, TARGET, k=5, **_stub())
    text = deterministic_summary(res)
    assert "prosecuted case" in text
    assert "lead for review" in text
    # Empty result still summarizes cleanly.
    empty = deterministic_summary(find_similar(con, "LONELY", **_stub()))
    assert "empty_pool" in empty


def test_explain_cluster_key_gated(monkeypatch, tmp_path):
    pytest.importorskip("langchain_anthropic")
    from relief_probe.similarity.explain import explain_cluster

    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    res = find_similar(con, TARGET, k=5, **_stub())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        explain_cluster(res, model="claude-haiku-4-5")
