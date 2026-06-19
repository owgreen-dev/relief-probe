"""Tests for LLM-adjudicated entity resolution (label recovery).

The blocking + acceptance + insertion logic runs offline with a stub adjudicator;
the real LlmAdjudicator is checked only for lazy import + key-gating + robustness.
"""

from __future__ import annotations

import pytest

from relief_probe.labels.llm_resolve import (
    AdjudicationRequest,
    AdjudicationVerdict,
    block_candidates,
    build_amount_index,
    extract_dollar_amounts,
    resolve_with_llm,
)
from relief_probe.warehouse import connect


def test_extract_dollar_amounts_filters_small():
    text = "obtained a $1,452,000 PPP loan, a $987654 EIDL, and a $300 refund in 2020"
    amts = extract_dollar_amounts(text, min_amount=5000)
    assert 1_452_000 in amts  # comma form
    assert 987_654 in amts  # bare-digit form, still $-prefixed
    assert 300 not in amts  # below the floor
    # A bare number with no "$" (e.g. the year) is NOT a dollar amount — precision.
    assert 2020 not in amts


def _seed(con):
    rows = [
        # An exact-name miss: the release will say "Johnson Trucking" but the loan is
        # under the owner's person name — only the amount ties them.
        ("L-SOLE", "Marcus Johnson", "TX", 1_452_000.0),
        # A decoy loan sharing the same amount but an unrelated name.
        ("L-DECOY", "Unrelated Bakery LLC", "OH", 1_452_000.0),
        # A loan whose amount nobody mentions.
        ("L-QUIET", "Quiet Co", "WA", 88_000.0),
    ]
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, borrower_state, "
        "current_approval_amount) VALUES (?, ?, ?, ?)",
        rows,
    )
    con.execute(
        "INSERT INTO press_releases (id, url, title, body, published_date, program, "
        "alleged_amount, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "rel-1",
            "https://justice.gov/x",
            "Texas man charged in PPP fraud",
            "Marcus Johnson, doing business as Johnson Trucking, obtained a "
            "$1,452,000 PPP loan using falsified records.",
            "2023-05-01",
            "ppp",
            1_452_000.0,
            "doj",
        ],
    )


def test_build_amount_index_and_block_candidates(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    idx = build_amount_index(con)
    assert {r[0] for r in idx[1_452_000]} == {"L-SOLE", "L-DECOY"}

    release = {
        "url": "https://justice.gov/x",
        "title": "Texas man charged in PPP fraud",
        "body": "Marcus Johnson ... obtained a $1,452,000 PPP loan ...",
        "alleged_amount": 1_452_000.0,
        "source": "doj",
        "published_date": None,
    }
    reqs = block_candidates(release, idx, already_labeled=set())
    # Both same-amount loans are candidates; the quiet loan is not (amount unmentioned).
    assert {r.loan_number for r in reqs} == {"L-SOLE", "L-DECOY"}


def test_block_candidates_skips_already_labeled_and_ambiguous(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    idx = build_amount_index(con)
    release = {
        "url": "u", "title": "", "body": "a $1,452,000 loan",
        "alleged_amount": None, "source": "doj", "published_date": None,
    }
    # Excluding an already-labeled loan drops it from the candidate set.
    reqs = block_candidates(release, idx, already_labeled={"L-DECOY"})
    assert {r.loan_number for r in reqs} == {"L-SOLE"}
    # A too-common amount (more candidates than the cap) is skipped entirely.
    reqs2 = block_candidates(
        release, idx, already_labeled=set(), max_candidates_per_amount=1
    )
    assert reqs2 == []


class _StubAdjudicator:
    """Accepts only the request whose borrower name contains a target token."""

    def __init__(self, accept_token: str) -> None:
        self.accept_token = accept_token

    def __call__(self, requests):
        out = []
        for r in requests:
            match = self.accept_token.lower() in r.borrower_name.lower()
            out.append(
                AdjudicationVerdict(
                    is_match=match,
                    confidence=0.9 if match else 0.1,
                    matched_name="Johnson Trucking" if match else "",
                    rationale="stub",
                )
            )
        return out


def test_resolve_with_llm_adds_only_the_adjudicated_match(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    summary = resolve_with_llm(con, _StubAdjudicator("johnson"), threshold=0.7)
    assert summary["candidates_adjudicated"] == 2  # L-SOLE + L-DECOY
    assert summary["new_loans_labeled"] == 1  # only the real person-name match
    labeled = {
        r[0]: r[1]
        for r in con.execute(
            "SELECT loan_number, match_method FROM fraud_cases"
        ).fetchall()
    }
    assert labeled == {"L-SOLE": "amount+llm"}  # decoy rejected, method marked


def test_resolve_with_llm_is_additive_and_skips_existing(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    # Pre-label the sole-prop loan via the "exact" path; the LLM pass must not touch it.
    con.execute(
        "INSERT INTO fraud_cases (case_id, loan_number, match_method, "
        "match_confidence, source) VALUES ('x', 'L-SOLE', 'name+amount', 0.8, 'doj')"
    )
    summary = resolve_with_llm(con, _StubAdjudicator("johnson"), threshold=0.7)
    # L-SOLE already labeled -> excluded from candidates -> nothing new.
    assert summary["new_loans_labeled"] == 0
    methods = [
        r[0] for r in con.execute(
            "SELECT match_method FROM fraud_cases WHERE loan_number = 'L-SOLE'"
        ).fetchall()
    ]
    assert methods == ["name+amount"]  # untouched


def test_llm_adjudicator_key_gated(monkeypatch):
    pytest.importorskip("langchain_anthropic")
    from relief_probe.labels.llm_resolve import LlmAdjudicator

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    adj = LlmAdjudicator(model="claude-haiku-4-5")
    req = AdjudicationRequest(
        "L1", "X", "TX", 100.0, 100, "t", "b", "u", None, "doj", None
    )
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        adj([req])


def test_llm_adjudicator_falls_back_to_no_match():
    # A persistently failing call must fall back to is_match=False (precision-safe),
    # never inject a label, and count the error.
    from relief_probe.labels.llm_resolve import LlmAdjudicator

    adj = LlmAdjudicator(model="x", max_retries=1)

    class BadClient:
        def invoke(self, _messages):
            raise RuntimeError("boom")

    adj._client = BadClient()
    req = AdjudicationRequest(
        "L1", "X", "TX", 100.0, 100, "t", "b", "u", None, "doj", None
    )
    (v,) = adj([req])
    assert v.is_match is False
    assert adj.n_errors == 1
