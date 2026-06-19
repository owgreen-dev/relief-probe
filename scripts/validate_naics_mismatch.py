"""Validate the name<->NAICS mismatch detector on real labels (read-only).

Does the semantic name<->industry mismatch concentrate prosecuted loans near the
top? We can't embed all 11.4M names on CPU, so we estimate on a subset: all labeled
loans + a random sample of unlabeled $150k+ loans. We rank that subset by mismatch
score under BOTH embedders (the lexical HashingEmbedder default and the real semantic
SentenceTransformer) and report where the labels fall (PU-honest mean percentile +
recall@k). Sector-granularity titles unless naics_titles is loaded.

Honest scope: this is a subsample estimate (inflated base rate vs the full
population), so read the mean-percentile / recall *concentration*, not the absolute
lift. Run: `uv run --extra embeddings python scripts/validate_naics_mismatch.py`.
"""

from __future__ import annotations

import duckdb

from relief_probe.benchmark.core import positive_rank_stats, ranking_metrics
from relief_probe.detectors.naics_mismatch import NaicsNameMismatchDetector
from relief_probe.embeddings import HashingEmbedder, Model2VecEmbedder
from relief_probe.warehouse import connect
from relief_probe.warehouse.db import init_schema

SAMPLE = 20_000
MIN_AMOUNT = 150_000
KS = (50, 100, 250, 500, 1000)


def _load_subset() -> tuple[duckdb.DuckDBPyConnection, set[str]]:
    with connect(read_only=True) as real:
        labeled = [
            str(r[0])
            for r in real.execute(
                "SELECT DISTINCT loan_number FROM fraud_cases "
                "WHERE loan_number IS NOT NULL"
            ).fetchall()
        ]
        cols = "loan_number, borrower_name, naics_code, current_approval_amount"
        # Sample AFTER filtering: sample the filtered subquery, not the whole table
        # (a top-level USING SAMPLE samples pre-WHERE and then the filter decimates it).
        sample = real.execute(
            f"SELECT {cols} FROM ("
            f"  SELECT {cols} FROM loans "
            f"  WHERE current_approval_amount >= ? "
            f"    AND borrower_name IS NOT NULL AND naics_code IS NOT NULL"
            f") USING SAMPLE {SAMPLE} ROWS (reservoir, 0)",
            [MIN_AMOUNT],
        ).fetchall()
        placeholders = ", ".join("?" for _ in labeled)
        labeled_rows = real.execute(
            f"SELECT {cols} FROM loans WHERE loan_number IN ({placeholders}) "
            "AND borrower_name IS NOT NULL AND naics_code IS NOT NULL",
            labeled,
        ).fetchall()
        has_titles = real.execute(
            "SELECT COUNT(*) FROM naics_titles"
        ).fetchone()[0]
        titles = (
            real.execute("SELECT naics_code, title FROM naics_titles").fetchall()
            if has_titles
            else []
        )

    rows = {str(r[0]): r for r in sample}
    for r in labeled_rows:  # ensure every labeled loan is in the subset
        rows[str(r[0])] = r

    mem = duckdb.connect(":memory:")
    init_schema(mem)
    mem.executemany(
        "INSERT INTO loans (loan_number, borrower_name, naics_code, "
        "current_approval_amount) VALUES (?, ?, ?, ?)",
        list(rows.values()),
    )
    if titles:
        mem.executemany(
            "INSERT OR IGNORE INTO naics_titles (naics_code, title) VALUES (?, ?)",
            titles,
        )
    present_labels = {ln for ln in labeled if ln in rows}
    print(
        f"Subset: {len(rows):,} loans "
        f"({len(present_labels)} labeled), titles="
        f"{'naics_titles' if titles else 'bundled 2-digit sectors'}."
    )
    return mem, present_labels


def _rank(con, embedder) -> list[str]:
    # min_mismatch=0 -> a signal per loan, so we can rank the whole subset by score.
    det = NaicsNameMismatchDetector(embedder=embedder, min_mismatch=0.0)
    sigs = det.run(con)
    sigs.sort(key=lambda s: s.score, reverse=True)
    return [s.loan_number for s in sigs]


def main() -> None:
    mem, positives = _load_subset()
    total = mem.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    base_rate = len(positives) / total if total else 0.0
    print(f"base rate in subset: {base_rate:.4%}\n")

    for label, embedder in (
        ("lexical (HashingEmbedder)", HashingEmbedder()),
        ("semantic (Model2Vec, torch-free)", Model2VecEmbedder()),
    ):
        print(f"=== {label} ===")
        ranked = _rank(mem, embedder)
        m = ranking_metrics(ranked, positives, base_rate, ks=KS)
        pr = positive_rank_stats(ranked, positives, total)
        pct = pr["mean_percentile_in_ranking"]
        verdict = "BETTER than random" if (pct or 1) < 0.5 else "no better than random"
        print(
            f"  concentration: mean percentile {pct} "
            f"({verdict}; 0.5 = random, lower = top-concentrated)"
        )
        for k in KS:
            print(
                f"    @{k}: {m[k]['hits']} hits · recall {m[k]['recall']} · "
                f"lift {m[k]['lift']}x"
            )
        print()


if __name__ == "__main__":
    main()
