# relief-probe — reproducibility entry points.
#
# The "no hand-typed numbers" rule: every headline, figure, and per-method
# verdict in the README/docs is regenerated read-only from the DuckDB warehouse
# by one of the targets below — nothing in the prose is typed by hand and left
# to rot. `make regen` refreshes all of them; the README says so.
#
#   make warehouse   build the warehouse from public SBA/DOJ data (network, ~430 MB)
#   make benchmark   the live forward-lift table (relief-probe benchmark)
#   make figures     the README hero chart + its headline lift@k table
#   make validate    every per-method verdict harness (docs/RESULTS.md)
#   make regen       figures + benchmark + validate (everything the docs quote)
#
# Everything runs on a laptop via `uv`; no target invents a number.

.DEFAULT_GOAL := help

# Extras the reproducibility targets need (mirrors pyproject optional-deps).
FIG_EXTRAS  := --extra figures
VAL_EXTRAS  := --extra ml --extra graph --extra embeddings-lite
DEMO_EXTRAS := --extra viz --extra vision --extra embeddings-lite

.PHONY: help install test lint check demo warehouse benchmark figures validate regen clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Sync the project env (uv)
	uv sync

test: ## Run the test suite (offline; external/LLM paths are stubbed)
	uv run --extra vision --extra graph pytest -q

lint: ## Lint with ruff
	uvx ruff check .

check: lint test ## Lint + test (what CI runs)

demo: ## Launch the dashboard on a self-built synthetic warehouse (no data, no keys)
	RELIEF_PROBE_DEMO=1 uv run $(DEMO_EXTRAS) streamlit run app/dashboard.py

warehouse: ## Build the warehouse + labels from public SBA/DOJ data (network, ~430 MB)
	uv run relief-probe ingest --slice 150k_plus
	uv run relief-probe score
	uv run relief-probe fetch-labels
	uv run relief-probe resolve-labels

benchmark: ## Regenerate the live forward-lift table (needs the warehouse)
	uv run relief-probe benchmark

figures: ## Regenerate the README hero chart + its headline lift@k numbers (read-only)
	uv run $(FIG_EXTRAS) python scripts/make_readme_figures.py

validate: ## Re-run every per-method verdict harness quoted in docs/RESULTS.md (read-only)
	@for s in scripts/validate_*.py; do \
		echo "=== $$s ==="; \
		uv run $(VAL_EXTRAS) python $$s || exit $$?; \
	done

regen: figures benchmark validate ## Regenerate every number the README/docs quote

clean: ## Remove caches (keeps the warehouse)
	rm -rf .pytest_cache .ruff_cache **/__pycache__ __pycache__
