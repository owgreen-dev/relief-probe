# plans — autonomous-development audit trail

This folder is the **audit trail for the autonomous (TDD) build loops** used to develop several layers of this project (the detectors of Loops 1–5: establishment-overcount, lender-concentration, the fraud-ring graph, and the KYB layer).

Each loop was driven by the [Ralph](https://ghuntley.com/ralph/) technique — a fresh-context, test-driven autonomous loop that implements one feature per iteration and only commits when a verification command (`pytest` + `ruff`) passes:

- **`prd.json`** — the feature spec for the current/most-recent loop: each feature's acceptance criteria, guardrails, and `passes` status.
- **`progress.md`** — the loop's running notes: what was built each iteration, patterns discovered, and design decisions (so a fresh session can continue without re-deriving context).
- **`guardrails.md`** — the accumulated "signs": constraints learned from failures (e.g. *deterministic-first*, *never read labels in a detector*, *exploratory-only until validated*) that every iteration re-reads.

It's kept in the repo as **transparency** — a record of how the work was built and the discipline it was held to (label-free detectors, no invented numbers, honest-negative dispositions). For the *results* of those loops, see the [README](../README.md) and [docs/NEXT_STEPS.md](../docs/NEXT_STEPS.md).
