"""Composite aggregation + ranking of loans by detector signals.

The composite is deliberately simple and interpretable — no ML, by choice, until
the benchmark (Layer 3) justifies a learned scorer:

    composite = max(score) + CORROBORATION_WEIGHT * (n_signals - 1)

i.e. the strongest single signal, with a bonus for each *additional* detector that
also fired (corroboration across independent schemes is the senior signal). Detector
scores are not strictly cross-comparable, so this is crude triage, not calibrated
risk — and we say so.
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
        SELECT
            s.loan_number,
            l.borrower_name,
            l.naics_code,
            l.borrower_state                              AS state,
            l.current_approval_amount                     AS amount,
            l.jobs_reported,
            MAX(s.score) + ? * (COUNT(*) - 1)             AS composite_score,
            COUNT(*)                                       AS n_signals,
            LIST(DISTINCT s.detector_id)                  AS detectors
        FROM signals s
        LEFT JOIN loans l USING (loan_number)
        GROUP BY ALL
        ORDER BY composite_score DESC
    """
    if limit is not None:
        sql += f"\n        LIMIT {int(limit)}"
    return con.execute(sql, [CORROBORATION_WEIGHT]).fetch_df()
