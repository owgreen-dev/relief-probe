# docs — deep dives

Technical deep-dives and the engineering log. **Start with the main [README](../README.md)** for the project overview and results; these go deeper on individual pieces.

| doc | what it covers |
| --- | --- |
| [RESULTS.md](RESULTS.md) | **What worked, what didn't** — the method verdicts (3 retrieval wins, 5 prediction negatives) written for a reader. Start here for the results. |
| [EXAMPLE_OUTPUT.md](EXAMPLE_OUTPUT.md) | A real `relief-probe investigate` report (on synthetic data) — what the tool actually produces. |
| [NEXT_STEPS.md](NEXT_STEPS.md) | The **engineering working log** — milestone-by-milestone build history, every exploratory detector's real-data verdict (the honest negatives), and the open backlog. |
| [LLM_RESEARCH.md](LLM_RESEARCH.md) | Synthesis of multi-source research into *where* LLMs add signal for fraud detection (and where they don't) — the basis for the retrieval-vs-prediction thesis. |
| [LABEL_PRECISION.md](LABEL_PRECISION.md) | Hand-adjudicated precision of the DOJ-label entity resolution (~84–88% exact tier; ~91–99% for the LLM-recovered tier) — how trustworthy the ground truth is. |
| [PROSECUTION_PATTERN.md](PROSECUTION_PATTERN.md) | Methodology + limitations for the borrower/attorney-facing "Prosecution pattern" dashboard tab — a statistical comparison against public charges, **not** legal advice or a risk score. |
| [M7_PLAN.md](M7_PLAN.md) | Design + cost model for the cost-aware LLM triage cascade (Tier 1), and its honest null result. |
| [images/](images/) | Dashboard screenshots used in the README. |
