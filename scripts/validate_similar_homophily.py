"""Validate whether prosecuted loans CLUSTER — neighbor homophily (read-only).

The similar-case finder is an investigation tool, not a predictor. But it has a
testable hypothesis: if fraud is committed in rings/templates, then a prosecuted
loan's nearest look-alikes should themselves be prosecuted MORE than chance. This
script measures that homophily on the real labels.

We can't search all 11.3M loans, so we estimate on a subset: all labeled loans + a
random sample of unlabeled $150k+ loans, in an in-memory warehouse. For each labeled
loan we take its k nearest neighbors (via find_similar within the subset) and count
how many are also labeled; the homophily rate vs the subset base rate is the lift.

Honest scope: the in-memory subset INFLATES the base rate (20k of ~1M), and blocking
within a sparse subset yields small pools — so read the *contrast* between the
homophily rate and this subset's base rate, not the absolute number. A null result
(neighbors no more labeled than chance) means prosecuted loans don't resemble each
other by name+amount+area — an honest, useful finding either way.

Run: `uv run --extra embeddings-lite python scripts/validate_similar_homophily.py`.
"""

from __future__ import annotations

import duckdb
import numpy as np

from relief_probe.embeddings import HashingEmbedder, Model2VecEmbedder
from relief_probe.similarity.core import find_similar
from relief_probe.warehouse import connect
from relief_probe.warehouse.db import init_schema

SAMPLE = 20_000
MIN_AMOUNT = 150_000
K = 10
_COLS = (
    "loan_number, borrower_name, borrower_city, borrower_state, borrower_zip, "
    "naics_code, current_approval_amount, jobs_reported"
)


def _load_subset() -> tuple[duckdb.DuckDBPyConnection, set[str]]:
    with connect(read_only=True) as real:
        labeled = [
            str(r[0])
            for r in real.execute(
                "SELECT DISTINCT loan_number FROM fraud_cases "
                "WHERE loan_number IS NOT NULL"
            ).fetchall()
        ]
        sample = real.execute(
            f"SELECT {_COLS} FROM ("
            f"  SELECT {_COLS} FROM loans "
            f"  WHERE current_approval_amount >= ? "
            f"    AND borrower_name IS NOT NULL AND borrower_name <> ''"
            f") USING SAMPLE {SAMPLE} ROWS (reservoir, 0)",
            [MIN_AMOUNT],
        ).fetchall()
        placeholders = ", ".join("?" for _ in labeled)
        labeled_rows = real.execute(
            f"SELECT {_COLS} FROM loans WHERE loan_number IN ({placeholders}) "
            "AND borrower_name IS NOT NULL AND borrower_name <> ''",
            labeled,
        ).fetchall()

    rows = {str(r[0]): r for r in sample}
    for r in labeled_rows:  # ensure every labeled loan is present
        rows[str(r[0])] = r

    mem = duckdb.connect(":memory:")
    init_schema(mem)
    mem.executemany(
        "INSERT INTO loans (loan_number, borrower_name, borrower_city, "
        "borrower_state, borrower_zip, naics_code, current_approval_amount, "
        "jobs_reported) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        list(rows.values()),
    )
    # Re-create the fraud_cases labels inside the subset so find_similar's is_fraud
    # flag works (and so neighbors can be checked for label membership).
    present = {ln for ln in labeled if ln in rows}
    mem.executemany(
        "INSERT INTO fraud_cases (case_id, loan_number, source, match_method, "
        "match_confidence) VALUES (?, ?, 'doj', 'subset', 1.0)",
        [(f"c{i}", ln) for i, ln in enumerate(present)],
    )
    print(f"Subset: {len(rows):,} loans ({len(present)} labeled).")
    return mem, present


def main() -> None:
    mem, positives = _load_subset()
    total = mem.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    base_rate = len(positives) / total if total else 0.0
    print(f"subset base rate: {base_rate:.4%}  (k={K})\n")

    for label, embedder in (
        ("lexical (HashingEmbedder)", HashingEmbedder()),
        ("semantic (Model2Vec, torch-free)", Model2VecEmbedder()),
    ):
        rates: list[float] = []
        n_with_pool = 0
        for ln in positives:
            res = find_similar(
                mem, ln, k=K, min_amount=MIN_AMOUNT,
                embedder=embedder, lexical=HashingEmbedder(),
            )
            if not res["available"] or not res["neighbors"]:
                continue
            n_with_pool += 1
            hits = sum(1 for n in res["neighbors"] if n["is_fraud"])
            rates.append(hits / len(res["neighbors"]))
        homophily = float(np.mean(rates)) if rates else 0.0
        lift = homophily / base_rate if base_rate else 0.0
        verdict = "CLUSTERS (> chance)" if lift > 1.0 else "no clustering (<= chance)"
        print(f"=== {label} ===")
        print(
            f"  {n_with_pool}/{len(positives)} labeled loans had a non-empty pool; "
            f"mean neighbor-label rate {homophily:.4f} vs base {base_rate:.4f} "
            f"-> homophily lift [bold]{lift:.2f}x[/] — {verdict}\n".replace(
                "[bold]", ""
            ).replace("[/]", "")
        )


if __name__ == "__main__":
    main()
