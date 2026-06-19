"""Offline tests for the KYB enrich-over-top-k orchestration — NO network, NO key.

Everything here runs against a seeded ``tmp_path`` warehouse with a deterministic
:class:`StubProvider` (or a counting wrapper). The hard cap, bounded fan-out,
within-run cache, graceful quota exhaustion, the grounded score refinement, and the
deterministic dossier are all asserted with the ``agent`` extra absent; the LLM
dossier path is checked only for its lazy import + clear key/extra gating.
"""

from __future__ import annotations

import datetime as dt

import pytest

from relief_probe.kyb.enrich import (
    KYB_WEIGHT,
    MAX_KYB,
    PPP_ELIGIBILITY_DATE,
    EnrichedLead,
    enrich_top_k,
    evidence_refinement,
    synthesize_dossier,
)
from relief_probe.kyb.provider import (
    KybEvidence,
    QuotaExhaustedError,
    StubProvider,
)
from relief_probe.warehouse import connect


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RELIEF_PROBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENCORPORATES_TOKEN", raising=False)


def _name(i: int) -> str:
    return f"Biz {i:03d} LLC"


def _seed(con, n: int) -> None:
    """Insert ``n`` loans, each with one signal of a distinct score (a clean
    composite ranking with ``n`` leads)."""
    rows = [
        (f"L{i:03d}", _name(i), "722511", "TX", 100_000.0 + i, 5.0)
        for i in range(n)
    ]
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code, "
        "borrower_state, current_approval_amount, jobs_reported) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.executemany(
        "INSERT INTO signals (loan_number, detector_id, score, evidence_json) "
        "VALUES (?, ?, ?, '{}')",
        [(f"L{i:03d}", "naics_cohort_outlier", float(i + 1)) for i in range(n)],
    )


def _evidence(*, reg=None, non_registered=False, addr=None, conf=0.9) -> KybEvidence:
    return KybEvidence(
        registration_date=reg,
        is_non_registered=non_registered,
        address_type=addr,
        matched_name="MATCH",
        match_confidence=conf,
        source="stub",
        raw_ref="https://oc/x",
    )


class _CountingStub:
    """A StubProvider that counts calls and can raise a quota error after N."""

    source = "stub"

    def __init__(self, fixtures=None, *, raise_after=None) -> None:
        self._inner = StubProvider(fixtures)
        self.calls = 0
        self.raise_after = raise_after

    def fetch(self, name, state, *, amount=None):
        self.calls += 1
        if self.raise_after is not None and self.calls > self.raise_after:
            raise QuotaExhaustedError("free-tier quota spent")
        return self._inner.fetch(name, state, amount=amount)


# --- evidence_refinement (pure, grounded) ------------------------------------


def test_refinement_none_is_zero():
    assert evidence_refinement(None) == (0.0, "no external registry evidence")


def test_refinement_non_registered_scales_with_confidence():
    bonus, reason = evidence_refinement(_evidence(non_registered=True, conf=0.5))
    assert bonus == pytest.approx(KYB_WEIGHT * 0.5)
    assert "non-registered" in reason


def test_refinement_registration_after_ppp_date_fires():
    after = PPP_ELIGIBILITY_DATE + dt.timedelta(days=120)
    bonus, reason = evidence_refinement(_evidence(reg=after, conf=1.0))
    assert bonus == pytest.approx(KYB_WEIGHT)
    assert "AFTER the Feb-15-2020" in reason


def test_refinement_registration_before_ppp_date_is_quiet():
    before = PPP_ELIGIBILITY_DATE - dt.timedelta(days=365)
    bonus, reason = evidence_refinement(_evidence(reg=before))
    assert bonus == 0.0
    assert "consistent with eligibility" in reason


def test_refinement_noncommercial_address_adds_small_bonus():
    bonus, _ = evidence_refinement(_evidence(addr="residential", conf=1.0))
    assert bonus == pytest.approx(0.5 * KYB_WEIGHT)


# --- enrich_top_k: end-to-end + telemetry + cap ------------------------------


def test_enrich_end_to_end_and_cap_hit(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con, 60)  # more than MAX_KYB so the cap can bite
    after = PPP_ELIGIBILITY_DATE + dt.timedelta(days=90)
    fixtures = {_name(i): _evidence(reg=after) for i in range(60)}
    provider = _CountingStub(fixtures)

    result = enrich_top_k(con, provider, top_k=MAX_KYB + 1, max_concurrency=4)
    tel = result["telemetry"]

    assert tel["cap_hit"] is True  # requested > MAX_KYB
    assert tel["n_leads"] <= MAX_KYB  # clamped to the hard cap
    assert tel["enriched"] == tel["n_leads"]
    assert tel["quota_exhausted"] is False
    assert tel["n_errors"] == 0
    assert provider.calls == tel["n_leads"]
    # Every enriched lead carries its evidence and a refined (>= composite) score.
    for lead in result["enriched"]:
        assert lead.evidence is not None
        assert lead.kyb_score >= lead.composite_score
    # Sorted highest refined-score first.
    scores = [lead.kyb_score for lead in result["enriched"]]
    assert scores == sorted(scores, reverse=True)


def test_enrich_empty_warehouse_is_safe(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    result = enrich_top_k(con, StubProvider(), top_k=10)
    assert result["enriched"] == []
    assert result["telemetry"]["n_leads"] == 0
    assert result["telemetry"]["enriched"] == 0


# --- graceful mid-run quota exhaustion ---------------------------------------


def test_quota_exhaustion_stops_clean_and_preserves(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con, 10)
    fixtures = {_name(i): _evidence(reg=PPP_ELIGIBILITY_DATE) for i in range(10)}
    provider = _CountingStub(fixtures, raise_after=4)

    # max_concurrency=1 makes the stop point deterministic: 4 succeed, the 5th
    # call raises QuotaExhaustedError and the rest are skipped cleanly.
    result = enrich_top_k(con, provider, top_k=10, max_concurrency=1)
    tel = result["telemetry"]

    assert tel["quota_exhausted"] is True
    assert tel["enriched"] == 4  # the four fetched before the quota tripped
    assert len(result["enriched"]) == 4
    assert provider.calls == 5  # the 5th is the one that raised


# --- within-run cache: a re-run is offline -----------------------------------


def test_cache_hit_avoids_refetch(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con, 5)
    fixtures = {_name(i): _evidence(reg=PPP_ELIGIBILITY_DATE) for i in range(5)}
    provider = _CountingStub(fixtures)
    cache: dict = {}

    first = enrich_top_k(con, provider, top_k=5, max_concurrency=1, cache=cache)
    assert provider.calls == 5
    assert first["telemetry"]["n_cache_hits"] == 0

    second = enrich_top_k(con, provider, top_k=5, max_concurrency=1, cache=cache)
    assert provider.calls == 5  # not re-invoked: every lead came from the cache
    assert second["telemetry"]["n_cache_hits"] == 5
    assert second["telemetry"]["enriched"] == 5


def test_provider_error_is_telemetered_not_fatal(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con, 3)

    class _Boom:
        source = "stub"

        def fetch(self, name, state, *, amount=None):
            raise ValueError("transient parse error")

    result = enrich_top_k(con, _Boom(), top_k=3, max_concurrency=1)
    tel = result["telemetry"]
    assert tel["n_errors"] == 3
    assert tel["quota_exhausted"] is False
    # Errored leads still appear, with no evidence (a re-run may retry them).
    assert all(lead.evidence is None for lead in result["enriched"])


# --- synthesize_dossier ------------------------------------------------------


def _lead() -> EnrichedLead:
    after = PPP_ELIGIBILITY_DATE + dt.timedelta(days=60)
    ev = _evidence(reg=after)
    bonus, reason = evidence_refinement(ev)
    return EnrichedLead(
        loan_number="L001",
        borrower_name="Suspicious Eats LLC",
        state="TX",
        amount=1_000_000.0,
        composite_score=1.2,
        evidence=ev,
        kyb_bonus=bonus,
        kyb_score=1.2 + bonus,
        kyb_reason=reason,
    )


def test_dossier_deterministic_needs_no_key():
    lead = _lead()
    note = synthesize_dossier(lead, lead.evidence)  # model=None
    assert "Suspicious Eats LLC" in note
    assert "lead for review, not proof" in note
    assert "AFTER the Feb-15-2020" in note


def test_dossier_none_evidence_is_grounded():
    lead = EnrichedLead(
        loan_number="L002",
        borrower_name="Mystery Co",
        state=None,
        amount=None,
        composite_score=0.5,
        evidence=None,
        kyb_bonus=0.0,
        kyb_score=0.5,
        kyb_reason="no external registry evidence",
    )
    note = synthesize_dossier(lead, None)
    assert "No external registry evidence" in note
    assert "lead for review, not proof" in note


def test_dossier_llm_path_gates_clearly(monkeypatch):
    """With the `agent` extra absent -> RuntimeError about the extra; with it
    present but no key -> RuntimeError about ANTHROPIC_API_KEY. Either way the
    LLM path never silently hits the network."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lead = _lead()
    try:
        import langchain_anthropic  # noqa: F401

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            synthesize_dossier(lead, lead.evidence, model="claude-haiku-4-5")
    except ImportError:
        with pytest.raises(RuntimeError, match="agent` extra"):
            synthesize_dossier(lead, lead.evidence, model="claude-haiku-4-5")
