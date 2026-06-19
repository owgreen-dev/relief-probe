"""Tests for the name<->NAICS embedding-mismatch detector + the Embedder layer.

Everything runs offline: the default HashingEmbedder needs no model/network, and a
tiny stub Embedder lets us assert the mismatch-percentile logic deterministically
without depending on any particular embedding model's geometry.
"""

from __future__ import annotations

import numpy as np

from relief_probe.detectors.naics_mismatch import (
    NAICS_SECTOR_TITLES,
    NaicsNameMismatchDetector,
    mismatch_score,
)
from relief_probe.embeddings import HashingEmbedder, _l2_normalize
from relief_probe.warehouse import connect


def test_hashing_embedder_is_deterministic_and_normalized():
    emb = HashingEmbedder(dim=64)
    a = emb.embed(["Elite Nail Spa", "Elite Nail Spa", "Joe's Construction"])
    b = emb.embed(["Elite Nail Spa", "Elite Nail Spa", "Joe's Construction"])
    assert np.allclose(a, b)  # stable across calls (md5, not salted hash())
    # Rows are L2-normalized (unit norm), so a dot product is a cosine.
    norms = np.linalg.norm(a, axis=1)
    assert np.allclose(norms, 1.0)
    # Identical strings embed identically; the two "Elite Nail Spa" rows match.
    assert np.allclose(a[0], a[1])
    # Shared substrings -> higher cosine than unrelated strings.
    assert float(a[0] @ a[1]) > float(a[0] @ a[2])


def test_mismatch_score_normalized_gap():
    row = np.array([0.1, 0.2, 0.9, 0.3], dtype=np.float32)
    # Declared is the best fit (idx 2) -> no mismatch.
    assert mismatch_score(row, 2) == 0.0
    # Declared is the worst fit (idx 0, sim 0.1) -> full mismatch (best-worst spread).
    assert mismatch_score(row, 0) == 1.0
    # Declared is middling (idx 3, sim 0.3): gap (0.9-0.3)/(0.9-0.1) = 0.75.
    assert abs(mismatch_score(row, 3) - 0.75) < 1e-6
    # Degenerate all-equal row -> zero spread -> 0 (no info isn't a mismatch).
    assert mismatch_score(np.array([0.5, 0.5, 0.5]), 1) == 0.0


def test_l2_normalize_handles_zero_rows():
    m = np.array([[0.0, 0.0], [3.0, 4.0]], dtype=np.float32)
    out = _l2_normalize(m)
    assert np.allclose(out[0], [0.0, 0.0])  # zero row stays zero, no div-by-zero
    assert np.allclose(out[1], [0.6, 0.8])


class _StubEmbedder:
    """Deterministic Embedder over a fixed vocabulary -> orthonormal basis vectors.

    Each known phrase maps to a distinct one-hot axis, so cosine is 1.0 for a
    phrase with itself and 0.0 with any other. Lets the test construct an exact
    name<->title match (or mismatch) and assert the percentile logic precisely.
    """

    def __init__(self, vocab: list[str]) -> None:
        self.index = {text: i for i, text in enumerate(vocab)}
        self.dim = len(vocab)

    def embed(self, texts):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            if t in self.index:
                out[r, self.index[t]] = 1.0
        return out


def _seed_three_sector_loans(con):
    # Three loans; borrower_name set to EQUAL a sector title so the stub embedder
    # gives an exact match to that sector and 0 to the others.
    rows = [
        # name exactly matches its declared sector (72) -> best match -> no mismatch
        ("GOOD", "Accommodation and Food Services", "722511"),
        # name matches sector 23 (Construction) but declared NAICS is 72 -> mismatch
        ("BAD", "Construction", "722511"),
        # name matches no title at all -> all sims 0 -> declared is tied, not flagged
        ("NEUTRAL", "Zzz Unrelated Name", "541110"),
    ]
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code) VALUES (?, ?, ?)",
        rows,
    )


def test_detector_flags_name_industry_mismatch(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed_three_sector_loans(con)
    # Stub vocab = the sector titles + the unrelated name.
    vocab = sorted(set(NAICS_SECTOR_TITLES.values())) + ["Zzz Unrelated Name"]
    det = NaicsNameMismatchDetector(embedder=_StubEmbedder(vocab), min_mismatch=0.85)
    signals = det.run(con)
    flagged = {s.loan_number: s for s in signals}

    # BAD: name="Construction" but declared sector 72 (Accommodation) -> declared is
    # a poor match (sim 0) while "Construction" is the perfect match -> high mismatch.
    assert "BAD" in flagged
    assert flagged["BAD"].score >= 0.85
    assert flagged["BAD"].evidence["best_match_title"] == "Construction"
    assert flagged["BAD"].evidence["declared_title"] == (
        "Accommodation and Food Services"
    )
    # GOOD: name matches its declared sector exactly -> declared IS best -> not flagged.
    assert "GOOD" not in flagged


def test_detector_uses_naics_titles_table_when_present(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    con.execute(
        "INSERT INTO naics_titles (naics_code, title) VALUES "
        "('722511', 'Full-Service Restaurants'), ('541110', 'Offices of Lawyers')"
    )
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code) VALUES (?, ?, ?)",
        [("L1", "Offices of Lawyers", "722511")],  # lawyer name, restaurant NAICS
    )
    det = NaicsNameMismatchDetector(
        embedder=_StubEmbedder(["Full-Service Restaurants", "Offices of Lawyers"]),
        min_mismatch=0.5,
    )
    (sig,) = det.run(con)
    assert sig.loan_number == "L1"
    assert sig.evidence["declared_title"] == "Full-Service Restaurants"
    assert sig.evidence["best_match_title"] == "Offices of Lawyers"
    assert sig.evidence["n_titles"] == 2


def test_detector_graceful_on_empty(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    assert NaicsNameMismatchDetector().run(con) == []


def test_detector_registered_exploratory_not_production():
    from relief_probe.detectors.registry import (
        all_detectors,
        exploratory_detectors,
        get_detector,
    )

    ids_all = {d.detector_id for d in all_detectors()}
    ids_exp = {d.detector_id for d in exploratory_detectors()}
    assert "naics_name_mismatch" not in ids_all  # SIGN-010: not promoted
    assert "naics_name_mismatch" in ids_exp
    assert get_detector("naics_name_mismatch").detector_id == "naics_name_mismatch"
