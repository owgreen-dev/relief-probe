"""Composite aggregation + ranking of loans by detector signals.

The composite is deliberately simple and interpretable — no ML, by choice, until
the benchmark (Layer 3) justifies a learned scorer:

    composite = max(percentile(score)) + CORROBORATION_WEIGHT * (n_signals - 1)

i.e. the strongest single signal, with a bonus for each *additional* detector that
also fired (corroboration across independent schemes is the senior signal).

Why percentile, not the raw score: detector scores live on incomparable scales — a
robust z-score (cohort outlier) ranges into the dozens while a cap-exceedance ratio
sits near 1–300. Taking ``max`` of the *raw* scores let whichever detector happened
to emit the larger numbers dominate the ranking regardless of how discriminative it
actually is. We instead normalise each detector's scores to their within-detector
percentile (``CUME_DIST``, in (0, 1]) before combining, so "top of detector A" and
"top of detector B" carry equal weight and corroboration is what breaks ties. This
is still crude triage, not calibrated risk — and we say so.
"""

from __future__ import annotations

import duckdb
import pandas as pd

CORROBORATION_WEIGHT = 0.5


def composite_ranking(
    con: duckdb.DuckDBPyConnection, *, limit: int | None = None
) -> pd.DataFrame:
    """Return loans ranked by composite risk score (highest first)."""
    sql = """
        WITH normed AS (
            SELECT
                loan_number,
                detector_id,
                CUME_DIST() OVER (
                    PARTITION BY detector_id ORDER BY score
                )                                          AS pct_score
            FROM signals
        )
        SELECT
            n.loan_number,
            l.borrower_name,
            l.naics_code,
            l.borrower_state                              AS state,
            l.current_approval_amount                     AS amount,
            l.jobs_reported,
            MAX(n.pct_score) + ? * (COUNT(*) - 1)         AS composite_score,
            COUNT(*)                                       AS n_signals,
            LIST(DISTINCT n.detector_id)                  AS detectors
        FROM normed n
        LEFT JOIN loans l USING (loan_number)
        GROUP BY ALL
        ORDER BY composite_score DESC
    """
    if limit is not None:
        sql += f"\n        LIMIT {int(limit)}"
    return con.execute(sql, [CORROBORATION_WEIGHT]).fetch_df()
