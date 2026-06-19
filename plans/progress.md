# Ralph Progress Log

Milestone: Loop 4 — multi-relational fraud-ring GRAPH layer (NetworkX, `graph` extra)
Branch: `m7-tier1-and-m8-ai-followups` (deps live only here, not main)
Verify: `uv run --extra vision --extra graph pytest && uvx ruff check .`

## This milestone (Loop 4)

Build a graph that links loans by RELATIONAL structure and test whether ring/community
structure concentrates prosecuted loans — the relational thesis (fraud here is
coordinated, not row-wise). THESIS + honest callback: three prediction attempts were
NEGATIVES (LLM reranker, name<->NAICS mismatch, PU-bagging scorer); the two relational
wins were LLM entity-resolution (+79 labels) and a name+amount+area similarity homophily
test (~3.4x). The single-edge-type `duplicate_address_ring` (address alone) was already
NULL (legit co-location dominates), so this loop's BET is that COMBINING edge types
(address + entity + similarity) + community detection separates real rings from benign
clustering. An honest NEGATIVE is acceptable. Features G-001..G-004 in plans/prd.json.

CRITICAL: graph FEATURES must be LABEL-FREE (never read fraud_cases to compute a
feature/signal — prove with an empty-fraud_cases test); only the G-003 validation SCRIPT
reads labels (like benchmark). Register the ring detector in
`registry.exploratory_detectors()` ONLY (SIGN-010) — NOT all_detectors(); promotion is a
MANUAL human decision AFTER this loop. Never touch the real data/ warehouse in tests
(SIGN-007: seed tmp_path). No invented numbers in docs (qualitative). networkx behind the
`graph` extra, imported lazily.

## Codebase Patterns

- Detectors: subclass `detectors/base.py::Detector`, `run(con) -> list[Signal]`,
  READ-ONLY, graceful on empty input. New detectors -> `exploratory_detectors()`.
- REUSE (exact): `detectors/_address.py::normalize_address` (building-level key, ZIP[:5]);
  `detectors/_entity.py::entity_key` (normalized name@address); `similarity/core.py`
  (blocking-first name+$+area look-alikes — reuse its blocking + the embedders) and
  `embeddings.py` (`HashingEmbedder` offline default; `Model2VecEmbedder` = embeddings-lite);
  `benchmark/core.py::temporal_label_split(con, year)` + `ranking_metrics` +
  `positive_rank_stats`; `stats.py::robust_z`.
- loans has: loan_number, borrower_name, borrower_address?, borrower_city, borrower_state,
  borrower_zip (mix of 5-digit + ZIP+4 -> [:5]), naics_code, current_approval_amount,
  jobs_reported, originating_lender. fraud_cases has charge_date (DATE) for the holdout.
- Scale by SPARSITY: GROUP BY a normalized key in Python, link WITHIN the group, CAP groups
  above max_group (a shared key with thousands must not form a clique). ~965k slice nodes.
- Style: `from __future__ import annotations`, typed, docstrings, ruff line-length 90.
  Commit ONE feature per iteration. Mirror existing tests (tests/test_ring_detector.py,
  tests/test_similarity.py for stub-embedder seeding + tmp_path warehouse patterns).

## Environment (IMPORTANT — do not regress)

- Verify: `uv run --extra vision --extra graph pytest && uvx ruff check .` (the `graph`
  extra = networkx; pure-python, no torch). `uvx ruff check .` is the lint command.
- The `agent` / `embeddings` / `embeddings-lite` extras stay OPT-IN; their tests
  `pytest.importorskip`. networkx is opt-in too (lazy import + clear RuntimeError).
- This loop runs on the PR branch `m7-tier1-and-m8-ai-followups` (deps not on main).

## Key Files (Loop 4)

- NEW: `src/relief_probe/graph/__init__.py`, `src/relief_probe/graph/build.py`,
  `src/relief_probe/graph/features.py`
- NEW: `tests/test_graph_build.py`, `tests/test_graph_features.py`
- NEW: `scripts/validate_ring_graph.py`
- `src/relief_probe/detectors/registry.py` (add the ring detector to exploratory_detectors)
- `pyproject.toml` (add the `graph` extra), `README.md`, `NEXT_STEPS.md` (qualitative)

## Learnings (append as you go)

- (none yet — first iteration)
