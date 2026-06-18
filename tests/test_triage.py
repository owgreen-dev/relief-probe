"""Tests for M7 Tier 1 — the cost-aware LLM triage cascade.

The deterministic path is the gate: candidate selection, the heuristic judge,
re-ranking, the orchestration telemetry, and the validation gate must all run over
a seeded warehouse with the `agent` extra absent. The LLM judge is checked for
lazy import + clear key-gating only (guarded by ``pytest.importorskip``); the
judge interface is verified via a stub injected through ``triage()``.
"""

from __future__ import annotations

import pytest

from relief_probe.detectors.runner import run_all
from relief_probe.triage.core import (
    DEFAULT_TOP_K,
    MAX_TRIAGE,
    LoanCandidate,
    rerank,
    select_candidates,
    triage,
)
from relief_probe.triage.gate import validation_gate
from relief_probe.triage.judge import (
    VERDICTS,
    PlausibilityVerdict,
    _coerce_score,
    heuristic_judge,
)
from relief_probe.warehouse import connect

FRAUD = "OUT-A"
OTHER = "OUT-B"


def _seed(con):
    # 40 normal TX restaurants ~$10k/job -> a real 722511|TX cohort.
    rows = [
        (f"N{i:03d}", f"Normal Diner {i}", "722511", "TX", (9000 + i * 75) * 10, 10.0)
        for i in range(40)
    ]
    # Two flagged outliers in that cohort, with different plausibility profiles.
    rows.append((FRAUD, "Suspicious Eats LLC", "722511", "TX", 1_000_000.0, 1.0))
    rows.append((OTHER, "Marginal Cafe LLC", "722511", "TX", 300_000.0, 10.0))
    con.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code, "
        "borrower_state, current_approval_amount, jobs_reported) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    # The $1M/1-job outlier is the prosecuted label (>= $150k, in the slice).
    con.execute(
        "INSERT INTO fraud_cases (case_id, loan_number, source, match_method, "
        "match_confidence) VALUES ('c1', ?, 'doj', 'name+state+amount', 1.0)",
        [FRAUD],
    )


def _candidate(**kw) -> LoanCandidate:
    base = dict(
        loan_number="L1",
        borrower_name="X",
        naics_code="722511",
        state="TX",
        amount=1_000_000.0,
        jobs=1.0,
        payroll_proceed=None,
        composite_score=1.0,
        n_signals=1,
        detectors=["payroll_cap_exceedance"],
    )
    base.update(kw)
    return LoanCandidate(**base)


# --- PlausibilityVerdict -------------------------------------------------------


def test_verdict_of_derives_label_and_clamps():
    v = PlausibilityVerdict.of(3, ["egregious mismatch"])
    assert v.implausibility == 3
    assert v.verdict == "egregious"
    # Out-of-range scores clamp into the 0-3 rubric.
    assert PlausibilityVerdict.of(9, []).implausibility == 3
    assert PlausibilityVerdict.of(-1, []).implausibility == 0


def test_verdict_rejects_invalid_score():
    with pytest.raises(ValueError):
        PlausibilityVerdict(5, "egregious", [])


def test_coerce_score_handles_malformed_llm_output():
    # Plain ints pass through; bool is rejected; a leaked-markup string (the real
    # failure mode observed on Haiku structured output) still yields its digit.
    assert _coerce_score(2) == 2
    assert _coerce_score(2.0) == 2
    assert _coerce_score(True) == 0
    assert _coerce_score('2</implausibility>\n<parameter name="verdict">x') == 2
    assert _coerce_score("no digits here") == 0


# --- heuristic judge -----------------------------------------------------------


def test_heuristic_judge_scores_egregious_single_job_megaloan():
    # $1M / 1 job in NAICS 72: 34x the $29,167 cap (+2), single job >= $500k (+1),
    # exact round number (+1) -> clamped to the 3/3 rubric ceiling.
    (v,) = heuristic_judge([_candidate(amount=1_000_000.0, jobs=1.0)])
    assert v.implausibility == 3
    assert v.verdict == "egregious"
    assert v.reasons


def test_heuristic_judge_scores_plausible_loan_zero():
    # $310k / 12 jobs ~= $25.8k/job, below the $29,167 NAICS-72 cap, not round.
    (v,) = heuristic_judge([_candidate(amount=310_000.0, jobs=12.0)])
    assert v.implausibility == 0
    assert v.verdict == "plausible"


def test_heuristic_judge_is_deterministic_and_aligned():
    cands = [_candidate(amount=a, jobs=j) for a, j in ((1_000_000, 1), (310_000, 12))]
    assert heuristic_judge(cands) == heuristic_judge(cands)
    assert len(heuristic_judge(cands)) == len(cands)


# --- candidate selection + re-rank ---------------------------------------------


def test_select_candidates_pulls_fields_and_clamps_to_cap(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    run_all(con)
    # Requesting absurdly many is silently clamped to the hard cap.
    cands = select_candidates(con, MAX_TRIAGE + 5000)
    assert 0 < len(cands) <= MAX_TRIAGE
    by_id = {c.loan_number: c for c in cands}
    assert FRAUD in by_id  # the $1M outlier is a flagged composite lead
    assert by_id[FRAUD].naics_code == "722511"
    assert by_id[FRAUD].composite_score > 0


def test_rerank_blends_and_sorts_highest_first():
    a = _candidate(loan_number="A", composite_score=1.0)
    b = _candidate(loan_number="B", composite_score=1.0)
    verdicts = [PlausibilityVerdict.of(0, []), PlausibilityVerdict.of(3, [])]
    ranked = rerank([a, b], verdicts)
    # B's +0.5 implausibility bonus lifts it above A despite equal composite.
    assert [s.candidate.loan_number for s in ranked] == ["B", "A"]
    assert ranked[0].triage_score == pytest.approx(1.5)
    assert ranked[1].triage_score == pytest.approx(1.0)


def test_rerank_rejects_misaligned_inputs():
    with pytest.raises(ValueError):
        rerank([_candidate()], [])


# --- orchestration -------------------------------------------------------------


def test_triage_telemetry_and_default_judge(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    run_all(con)
    result = triage(con, top_k=DEFAULT_TOP_K)
    tel = result["telemetry"]
    assert tel["judge"] == "heuristic_judge"
    assert tel["model"] is None
    assert tel["cap_hit"] is False
    assert tel["n_judged"] == len(result["ranked"]) > 0
    # The labeled $1M/1-job outlier scores maximally implausible.
    top = {s.candidate.loan_number: s for s in result["ranked"]}
    assert top[FRAUD].verdict.implausibility == 3


def test_triage_reports_cap_hit(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    run_all(con)
    tel = triage(con, top_k=MAX_TRIAGE + 1)["telemetry"]
    assert tel["cap_hit"] is True
    assert tel["n_judged"] <= MAX_TRIAGE


def test_triage_accepts_a_custom_judge(tmp_path):
    # Proves the Judge interface is pluggable without any LLM/key — the same seam
    # the LlmJudge slots into.
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    run_all(con)

    def always_egregious(candidates):
        return [PlausibilityVerdict.of(3, ["stub"]) for _ in candidates]

    result = triage(con, top_k=10, judge=always_egregious)
    assert all(s.verdict.verdict in VERDICTS for s in result["ranked"])
    assert all(s.verdict.implausibility == 3 for s in result["ranked"])


# --- validation gate -----------------------------------------------------------


def test_validation_gate_shape_and_verdict(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    run_all(con)
    g = validation_gate(con, top_k=100)
    assert g["slice"] == ">=$150,000"
    assert g["n_labeled_fraud"] == 1  # only the $1M outlier is in the slice
    assert g["judge"] == "heuristic_judge"
    assert g["verdict"] in {"improved", "neutral", "regressed"}
    # ks never exceed the shortlist (re-ranking can't move lift beyond top_k).
    assert all(k <= 100 for k in g["ks"])
    for k in g["ks"]:
        assert set(g["per_k"][k]) == {"composite", "triage", "lift_delta"}


def test_validation_gate_reuses_head_without_rejudging(tmp_path):
    # On the LLM path the gate must NOT re-judge (that would double model cost).
    # Passing reranked_head means the judge is never called — a judge that raises
    # proves it.
    con = connect(tmp_path / "wh.duckdb")
    _seed(con)
    run_all(con)
    result = triage(con, top_k=100)
    head = [s.candidate.loan_number for s in result["ranked"]]

    def exploding_judge(_candidates):
        raise AssertionError("judge must not be called when reranked_head is given")

    g = validation_gate(con, top_k=100, judge=exploding_judge, reranked_head=head)
    assert g["verdict"] in {"improved", "neutral", "regressed"}


# --- LLM judge (lazy import + key gating + robustness) -------------------------


def test_llm_judge_lazy_import_and_key_gate(monkeypatch):
    pytest.importorskip("langchain_anthropic")
    from relief_probe.triage.judge import LlmJudge

    # With the extra present but no key, the judge must raise a clear error rather
    # than silently hitting the network.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    judge = LlmJudge(model="claude-haiku-4-5")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        judge([_candidate()])


def test_llm_judge_coerces_malformed_structured_output():
    # No extra/key needed: inject a stub client that returns the leaked-markup
    # field shape observed on a real run. The judge must still produce a 3, not crash.
    from relief_probe.triage.judge import LlmJudge

    judge = LlmJudge(model="x")

    class LeakyClient:
        def invoke(self, _messages):
            return {"implausibility": '3</implausibility>x', "reasons": ["mismatch"]}

    judge._client = LeakyClient()
    (v,) = judge([_candidate()])
    assert v.implausibility == 3
    assert judge.n_errors == 0


def test_llm_judge_falls_back_after_retries():
    # A persistently failing call exhausts retries and yields a neutral fallback
    # (so one bad loan never aborts a 300/1,000-loan batch), counted in n_errors.
    from relief_probe.triage.judge import LlmJudge

    judge = LlmJudge(model="x", max_retries=1)

    class BadClient:
        def invoke(self, _messages):
            raise RuntimeError("boom")

    judge._client = BadClient()
    (v,) = judge([_candidate()])
    assert v.implausibility == 0
    assert judge.n_errors == 1
    assert any("judge_error" in r for r in v.reasons)


def test_llm_judge_concurrent_preserves_order_and_counts_errors():
    # Fan-out path: a stub client echoes each loan's amount as the score so we can
    # assert order is preserved; a thread-safe counter tallies the forced failures.
    from relief_probe.triage.judge import LlmJudge

    judge = LlmJudge(model="x", max_concurrency=4, max_retries=0)

    class EchoClient:
        # Score = (amount // 1e6) clamped; loan "L7" (amount 7e6) -> 3 after clamp.
        def invoke(self, messages):
            human = messages[-1][1]
            if "FAIL" in human:
                raise RuntimeError("boom")
            amt = next(
                line for line in human.splitlines() if "approved_amount" in line
            )
            digits = "".join(ch for ch in amt if ch.isdigit())
            return {"implausibility": int(digits) // 1_000_000, "reasons": []}

    judge._client = EchoClient()
    cands = [
        _candidate(loan_number=f"L{i}", amount=float(i) * 1_000_000, jobs=1.0)
        for i in range(8)
    ]
    cands.append(_candidate(loan_number="FAIL", borrower_name="FAIL", amount=None))
    verdicts = judge(cands)
    assert len(verdicts) == len(cands)
    # Order preserved despite concurrent completion: L0..L7 map to clamped 0..3.
    assert [v.implausibility for v in verdicts[:8]] == [0, 1, 2, 3, 3, 3, 3, 3]
    assert judge.n_errors == 1  # only the FAIL candidate fell back
