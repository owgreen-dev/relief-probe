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

- **G-001 (graph builder) DONE.** `src/relief_probe/graph/{__init__,build}.py` +
  `tests/test_graph_build.py`. `build_loan_graph(con, *, min_amount=150_000.0,
  edge_types=("address","entity","similarity"), max_group=50, sim_threshold=0.85,
  amount_band=50_000.0, embedder=None)` → a `networkx.Graph`. Nodes = $150k+
  loan_numbers with attrs (borrower_name, norm_name, borrower_state, amount).
- networkx is imported LAZILY via `_require_networkx()` inside the function (clear
  RuntimeError if the `graph` extra is absent); `graph/__init__.py` only imports
  `build_loan_graph` (which doesn't import nx at module load), so the package
  imports fine without the extra. The `graph` extra was ALREADY in pyproject.toml.
- Edges collapse onto one undirected edge carrying `type` (a single representative
  in EDGE_TYPES) + `types` (a SET) — a pair linked by >1 relation keeps all types,
  so a component's edge-type span is recoverable (key for G-002's >=2-types rule).
- Sparsity: address/entity = clique within a shared-key group via `_link_clique`;
  similarity = pairwise-above-threshold within a (state, round(amount/amount_band))
  block via `_link_similarity` (embed once, L2-normalized rows → dot = cosine).
  ANY group/block with size > max_group is SKIPPED entirely (not capped-to-N) — a
  giant shared key cannot form a clique.
- TEST GOTCHA: the similarity block is keyed (state, round(amount/amount_band)).
  `round()` is banker's rounding, and a *decoy in the same state+band* inflates the
  ring's block past max_group and silently kills its similarity edges. Keep test
  decoys in a different state OR a clearly different amount band. Used a one-hot
  `_StubEmbedder` (cosine 1.0 iff identical name) for deterministic similarity
  edges + a `_SpyCon` wrapper to prove no `fraud_cases` query (label-free).
- LABEL-FREE proven: `test_builder_never_queries_fraud_cases` runs on empty
  fraud_cases and asserts no executed SQL contains "fraud_cases".

- **G-002 (features + exploratory detector) DONE.**
  `src/relief_probe/graph/features.py::graph_structural_features(graph, *,
  min_community_size=5)` → `dict[loan_number, {component_size, degree,
  n_address_edges, n_entity_edges, n_similarity_edges, distinct_borrowers,
  community_size}]`. All LABEL-FREE (reads only the graph shape + the `norm_name`
  node attr). Community detection (`nx.community.greedy_modularity_communities`)
  runs ONLY on components >= min_community_size; smaller components are their own
  community (community_size == component_size). nx imported LAZILY (mirrors build).
- `src/relief_probe/graph/detector.py::MultiRelationalRingDetector` (detector_id
  `fraud_ring_graph`): builds the graph, computes features, fires on every loan in
  a component spanning >= `min_edge_types` (default 2) distinct edge relations AND
  >= `min_borrowers` (default 2) distinct borrowers. Score =
  `log1p(distinct_borrowers) + log1p(community_size)` (label-free, monotonic in
  ring strength). Ctor takes `embedder` + `build_kwargs` for injection/tests.
- Registered in `exploratory_detectors()` ONLY (SIGN-010) — `all_detectors()`
  UNCHANGED; `get_detector('fraud_ring_graph')` resolves it; registry docstring
  updated. The detector module imports `build_loan_graph`/`graph_structural_features`
  (neither imports nx at module load) + `Detector`/`Embedder`, so registry imports
  fine WITHOUT the `graph` extra; `import networkx` happens inside `run()`.
- TEST DESIGN (`tests/test_graph_features.py`): the ring needs >=2 edge types AND
  >=2 distinct borrowers, which a single identical-name address clique CANNOT give
  (distinct_borrowers would be 1). So plant a duplicate-funding PAIR (same name+addr
  → entity+address+similarity edges) PLUS a DISTINCT borrower at the same building
  (address edge) → component spans 3 relations, 2 borrowers. Added an ADDRESS-ONLY
  pair (2 borrowers, 1 relation) to prove the edge-type gate (the address-alone-null
  callback: shared address alone does NOT fire). Reused the one-hot `_StubEmbedder`
  + `_SpyCon` from test_graph_build. Label-free proven via the spy (no fraud_cases).

- **G-003 (real-data validation script) DONE.** `scripts/validate_ring_graph.py`
  — READ-ONLY via `connect(read_only=True)`. Builds the graph over the $150k+ slice
  DIRECTLY on the real read-only connection (no in-memory copy / no random sample —
  `build_loan_graph` and `temporal_label_split` are both read-only). Ranks ALL slice
  loans by a LABEL-FREE structural score `ring_score = log1p(distinct_borrowers) +
  log1p(community_size)` (matches the detector formula). Evaluates ONLY on H7
  out-of-time positives (`temporal_label_split(con, HOLDOUT_YEAR=2023)` → test set,
  charged > 2023) intersected with the slice, via `ranking_metrics` +
  `positive_rank_stats`. Prints mean-percentile verdict (0.5 = random) + recall@k.
- COMPOSITE COMPARISON without writing: `composite_ranking(con)` only READS the
  `signals` table (read-only-safe), so we compare on the SAME held-out labels. Guarded
  with try/except + count — if `signals` is empty (no prior `relief-probe run`), the
  composite line is skipped with a clear message rather than crashing.
- TRACTABILITY/HONESTY: pure-python NetworkX over ~965k nodes is heavy; `MIN_AMOUNT`
  is a knob to shrink the slice (a higher-amount slice, NOT a random sample, so
  edges/rings stay intact — random sampling shatters rings). Documented in the module
  docstring along with the read-the-CONTRAST + address-alone-null + honest-NEGATIVE
  caveats (mirrors validate_naics_mismatch).
- UNIT TEST (`tests/test_validate_ring_graph.py`): the real run is a manual artifact,
  so the test only covers the warehouse-free parts — imports the script via
  `importlib.util.spec_from_file_location` (proves it imports with NO `graph` extra at
  module load, since build/features import nx lazily) and exercises the pure helpers
  `ring_score` (monotonic, matches formula) + `rank_loans_by_structure` (score-desc,
  ties broken by loan_number). No conftest/pythonpath existed for scripts/, hence the
  importlib-by-path approach.
