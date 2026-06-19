# Contributing

Thanks for looking. This project is a **research/portfolio artifact** (see
[RESPONSIBLE_USE.md](RESPONSIBLE_USE.md)), but it's structured so a stranger can pick up a
thread and add to it cleanly. Forks and "I tried this and here's what I found" PRs — including
**honest negatives** — are genuinely welcome.

## Setup

```bash
uv run pytest                                     # core suite — optional-extra tests auto-skip
uvx ruff check .                                  # lint (line length 90)
```

`uv` provisions everything (the dev group installs test deps automatically). Optional features
live behind [pyproject](pyproject.toml) extras (`agent`, `viz`, `ml`, `vision`,
`embeddings-lite`, `graph`) so the core stays light and runs with no network or API keys.
Tests that need an extra (e.g. the `graph` layer's networkx) **skip** cleanly without it — so
bare `uv run pytest` is green, and the full run below exercises everything. The verification
command this repo holds itself to:

```bash
uv run --extra vision --extra graph pytest && uvx ruff check .
```

CI runs exactly this on every push (`.github/workflows/test.yml`).

## The one rule: build → validate → honest disposition

This project's whole personality is **measuring whether an idea works and reporting the
result either way.** A new signal isn't "done" when it's built — it's done when it's been
validated against the real DOJ-prosecution labels and *honestly dispositioned*:

1. **Build it** as a self-contained module (a detector subclasses `detectors/base.py::Detector`
   and emits `Signal`s; a new external-evidence source implements
   `kyb/provider.py::EvidenceProvider`; etc.). One file, typed, with a docstring.
2. **Register it EXPLORATORY**, never in the production composite — add to
   `registry.exploratory_detectors()`, **not** `all_detectors()`. Promotion is a manual
   decision *after* it earns its place.
3. **Validate on real labels** with a `scripts/validate_*.py` harness (mirror an existing one):
   does it concentrate prosecuted loans on the temporal holdout, and does it beat the composite?
4. **Disposition honestly.** If it shows independent lift, promote it (with the numbers). If it
   doesn't — **that's a valid, welcome result.** Document the negative; keep it exploratory.
   The repo has more honest negatives than wins, and that's the point.

### Non-negotiables (the "guardrails")

- **Deterministic-first / key-gated.** Builds + tests with **no network and no API keys** —
  external/LLM paths are stubbed and lazily imported; a missing extra/key raises a clear error.
- **Detectors are label-free.** A detector must never read `fraud_cases` (that leaks the answer
  and inherits prosecution bias) — prove it with an empty-`fraud_cases` test. Only *validation
  scripts* read labels.
- **No invented numbers** in docs. Real numbers come from a real run; everything else is
  qualitative.
- **Read-only over the warehouse**; tests seed a `tmp_path` DuckDB, never the real `data/`.
- **Style:** `from __future__ import annotations`, typed, docstrings, ruff line-length 90,
  one logical change per commit.

A good model to copy is any existing detector: build one feature at a time, each landing green
(`pytest` + `ruff`) before the next — the commit history shows the per-feature progression.

## Good first directions

See the **Roadmap** in the [README](README.md#roadmap--where-to-take-this-next) — each item is
scoped and points at the pattern to reuse.

## Responsible use

This tool surfaces **leads for review of public data — never accusations of fraud.** Anything
you add must honor that: keep detectors label-free, never present an individual as fraudulent on
the basis of a score, and keep public-facing examples anonymized or synthetic. See
[RESPONSIBLE_USE.md](RESPONSIBLE_USE.md) — especially before touching the KYB layer, which names
real businesses/people and carries FCRA/ToS/defamation obligations.
