"""Feature extraction for the learned PU scorer (pure NumPy/pandas, no sklearn).

Each loan in the $150k+ slice becomes a numeric feature row combining:

* **Structured program fields** — log amount, log jobs, log dollars-per-job, the
  payroll-proceed and forgiveness shares, term, SBA guaranty %, and a few binary
  fraud tells (single reported job, exact round-number amount, NAICS-72).
* **The unsupervised detector scores** — one column per ``detector_id`` in the
  ``signals`` table (0 when that detector did not fire). The detectors are NOT fit to
  labels, so using their scores as features is leakage-free; it lets the learned
  scorer combine them better than the hand-weighted composite.

Returns a dense matrix plus aligned loan_numbers and feature names. NaNs/nulls map to
neutral values so the matrix is model-ready.
"""

from __future__ import annotations

import json

import duckdb
import numpy as np
import pandas as pd

#: Structured feature columns, in a stable order.
_STRUCTURED = (
    "log_amount",
    "log_jobs",
    "log_amount_per_job",
    "payroll_share",
    "forgiveness_ratio",
    "term",
    "sba_guaranty_pct",
    "jobs_is_one",
    "round_amount",
    "is_naics_72",
)


def _structured_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Engineer the structured features from a raw loans frame."""
    amount = df["current_approval_amount"].astype(float)
    jobs = df["jobs_reported"].astype(float)
    safe_amount = amount.where(amount > 0, np.nan)
    out = pd.DataFrame(index=df.index)
    out["log_amount"] = np.log1p(amount.clip(lower=0).fillna(0.0))
    out["log_jobs"] = np.log1p(jobs.clip(lower=0).fillna(0.0))
    out["log_amount_per_job"] = np.log1p(
        (amount / jobs.clip(lower=1)).clip(lower=0).fillna(0.0)
    )
    out["payroll_share"] = (
        (df["payroll_proceed"].astype(float).fillna(0.0) / safe_amount)
        .clip(0, 2).fillna(0.0)
    )
    out["forgiveness_ratio"] = (
        (df["forgiveness_amount"].astype(float).fillna(0.0) / safe_amount)
        .clip(0, 2).fillna(0.0)
    )
    out["term"] = df["term"].astype(float).fillna(0.0)
    out["sba_guaranty_pct"] = df["sba_guaranty_pct"].astype(float).fillna(0.0)
    out["jobs_is_one"] = (jobs == 1).astype(float)
    out["round_amount"] = (
        (amount > 0) & (amount.fillna(-1) % 10_000 == 0)
    ).astype(float)
    naics = df["naics_code"].astype("string").fillna("")
    out["is_naics_72"] = naics.str.startswith("72").astype(float)
    return out[list(_STRUCTURED)]


def build_feature_matrix(
    con: duckdb.DuckDBPyConnection,
    *,
    min_amount: float = 150_000.0,
    loan_numbers: list[str] | None = None,
) -> tuple[np.ndarray, list[str], list[str]]:
    """Return ``(X, loan_numbers, feature_names)`` for the labelable slice.

    ``min_amount`` restricts to the $150k+ slice (where the labels live). Pass
    ``loan_numbers`` to score a specific subset instead. Detector-score columns are
    added for every ``detector_id`` present in ``signals`` (0 where absent).
    """
    where = "current_approval_amount >= ?"
    params: list = [min_amount]
    if loan_numbers is not None:
        if not loan_numbers:
            return np.empty((0, 0)), [], []
        placeholders = ", ".join("?" for _ in loan_numbers)
        where = f"loan_number IN ({placeholders})"
        params = list(loan_numbers)

    loans = con.execute(
        f"""
        SELECT loan_number, current_approval_amount, jobs_reported, payroll_proceed,
               forgiveness_amount, term, sba_guaranty_pct, naics_code
        FROM loans
        WHERE {where}
        """,
        params,
    ).fetch_df()
    if loans.empty:
        return np.empty((0, 0)), [], []
    loans["loan_number"] = loans["loan_number"].astype(str)

    feats = _structured_frame(loans)

    # Per-detector score columns, pivoted from signals (0 where a detector didn't fire).
    detectors = [
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT detector_id FROM signals ORDER BY detector_id"
        ).fetchall()
    ]
    det_cols: list[str] = []
    if detectors:
        sig = con.execute(
            "SELECT loan_number, detector_id, MAX(score) AS score "
            "FROM signals GROUP BY loan_number, detector_id"
        ).fetch_df()
        sig["loan_number"] = sig["loan_number"].astype(str)
        pivot = (
            sig.pivot(index="loan_number", columns="detector_id", values="score")
            .reindex(loans["loan_number"])
            .fillna(0.0)
        )
        for det in detectors:
            col = f"det_{det}"
            feats[col] = (
                pivot[det].to_numpy() if det in pivot.columns
                else np.zeros(len(loans))
            )
            det_cols.append(col)

    feature_names = list(_STRUCTURED) + det_cols
    X = feats[feature_names].to_numpy(dtype=np.float64)
    return X, loans["loan_number"].tolist(), feature_names


# ---------------------------------------------------------------------------
# Rich, leakage-guarded composite (Loop 6 — the LightGBM learned scorer)
# ---------------------------------------------------------------------------
#
# build_rich_feature_matrix() layers four more blocks on top of an
# at-origination structured block. It is a SUPERSET of signals — including the
# exploratory detectors that did not validate standalone — on the theory that a
# gradient-boosted model finds interactions the linear composite misses.
#
# Two leakage guards are the whole point (see SIGN-014 / SIGN-015):
#   * NO post-hoc / outcome features — forgiveness_*, loan_status_* are dropped;
#     every feature is knowable AT LOAN ORIGINATION.
#   * NO label-derived features — fraud_cases is never read here, and no
#     neighbour's fraud status enters a column. The similarity / graph layer
#     contributes only its UNSUPERVISED structural shape. The matrix is byte-for-
#     byte identical on an empty fraud_cases table.
#
# The MODEL is label-trained (that is expected and honest); the FEATURES are not.

#: Block A — at-origination structured columns (rich variant). Drops
#: forgiveness_ratio (post-hoc) and adds proceeds-breakdown shares + the
#: initial-vs-current amount delta.
_RICH_STRUCTURED = (
    "log_amount",
    "log_jobs",
    "log_amount_per_job",
    "payroll_share",
    "utilities_share",
    "rent_share",
    "mortgage_share",
    "health_share",
    "debt_share",
    "eidl_share",
    "amount_delta_share",
    "term",
    "sba_guaranty_pct",
    "jobs_is_one",
    "round_amount",
    "is_naics_72",
)

#: Block B (numerics) — rich evidence numbers pulled from signals.evidence_json,
#: per detector. ``(evidence_key, feature_column)``; absent/None → 0.0. These
#: sit alongside the per-detector ``det_*`` score columns (the worked + the
#: didn't-work union of signals).
_EVIDENCE_NUMERICS: dict[str, tuple[tuple[str, str], ...]] = {
    "naics_cohort_outlier": (
        ("robust_z", "cohort_robust_z"),
        ("x_cohort_median", "cohort_x_median"),
        ("cohort_size", "cohort_size"),
    ),
    "payroll_cap_exceedance": (("x_cap", "payroll_x_cap"),),
    "multiple_funded_loans": (
        ("excess_loans", "mfl_excess_loans"),
        ("n_loans", "mfl_n_loans"),
    ),
    "amount_anomaly": (
        ("round_score", "amt_round_score"),
        ("cap_maximization_score", "amt_cap_max_score"),
    ),
    "establishment_overcount": (
        ("ratio", "estab_ratio"),
        ("ppp_loan_count", "estab_ppp_count"),
        ("establishment_count", "estab_count"),
    ),
    "lender_concentration": (
        ("lender_robust_z", "lender_robust_z"),
        ("lender_suspicious_rate", "lender_suspicious_rate"),
    ),
    "naics_name_mismatch": (("mismatch_gap", "naics_mismatch_gap"),),
    "duplicate_address_ring": (("ring_size", "ring_size"),),
}

#: The evidence-numeric columns, in a stable order (the order detectors are
#: declared above, then their keys).
_EVIDENCE_COLS: tuple[str, ...] = tuple(
    col for spec in _EVIDENCE_NUMERICS.values() for _, col in spec
)

#: Block C — LABEL-FREE graph structural columns. ``(source_key, feature_column)``
#: from :func:`relief_probe.graph.features.graph_structural_features`.
_GRAPH_KEYS: tuple[tuple[str, str], ...] = (
    ("component_size", "g_component_size"),
    ("degree", "g_degree"),
    ("n_address_edges", "g_n_address_edges"),
    ("n_entity_edges", "g_n_entity_edges"),
    ("n_similarity_edges", "g_n_similarity_edges"),
    ("distinct_borrowers", "g_distinct_borrowers"),
    ("community_size", "g_community_size"),
)
_GRAPH_COLS: tuple[str, ...] = tuple(col for _, col in _GRAPH_KEYS)

#: Block D — PLODI-style geo/industry-normalized pay-ratio columns.
_PAY_RATIO_COLS = ("pay_ratio_pctile", "pay_ratio_cohort_size")

#: Block E — LightGBM-native categoricals. ``__missing__`` is the null sentinel.
_CATEGORICAL_COLS = (
    "cat_naics_sector",
    "cat_state",
    "cat_processing_method",
    "cat_business_type",
    "cat_rural_urban",
    "cat_nonprofit",
    "cat_franchise",
    "cat_originating_lender",
)

#: Loan columns the rich builder reads (all at-origination; NO forgiveness_*,
#: NO loan_status_*).
_RICH_FIELDS = (
    "loan_number",
    "current_approval_amount",
    "initial_approval_amount",
    "jobs_reported",
    "payroll_proceed",
    "utilities_proceed",
    "rent_proceed",
    "mortgage_interest_proceed",
    "health_care_proceed",
    "debt_interest_proceed",
    "refinance_eidl_proceed",
    "term",
    "sba_guaranty_pct",
    "naics_code",
    "borrower_state",
    "project_county_name",
    "processing_method",
    "business_type",
    "rural_urban_indicator",
    "nonprofit",
    "franchise_name",
    "originating_lender",
)

_MISSING = "__missing__"
_RARE = "__rare__"


def _rich_structured_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Block A: at-origination structured features (no post-hoc columns)."""
    amount = df["current_approval_amount"].astype(float)
    initial = df["initial_approval_amount"].astype(float)
    jobs = df["jobs_reported"].astype(float)
    safe_amount = amount.where(amount > 0, np.nan)

    def _share(col: str) -> pd.Series:
        return (
            (df[col].astype(float).fillna(0.0) / safe_amount).clip(0, 2).fillna(0.0)
        )

    out = pd.DataFrame(index=df.index)
    out["log_amount"] = np.log1p(amount.clip(lower=0).fillna(0.0))
    out["log_jobs"] = np.log1p(jobs.clip(lower=0).fillna(0.0))
    out["log_amount_per_job"] = np.log1p(
        (amount / jobs.clip(lower=1)).clip(lower=0).fillna(0.0)
    )
    out["payroll_share"] = _share("payroll_proceed")
    out["utilities_share"] = _share("utilities_proceed")
    out["rent_share"] = _share("rent_proceed")
    out["mortgage_share"] = _share("mortgage_interest_proceed")
    out["health_share"] = _share("health_care_proceed")
    out["debt_share"] = _share("debt_interest_proceed")
    out["eidl_share"] = _share("refinance_eidl_proceed")
    # Initial-vs-current delta share: a draw that grew/shrank after origination.
    out["amount_delta_share"] = (
        ((amount - initial) / safe_amount).clip(-2, 2).fillna(0.0)
    )
    out["term"] = df["term"].astype(float).fillna(0.0)
    out["sba_guaranty_pct"] = df["sba_guaranty_pct"].astype(float).fillna(0.0)
    out["jobs_is_one"] = (jobs == 1).astype(float)
    out["round_amount"] = (
        (amount > 0) & (amount.fillna(-1) % 10_000 == 0)
    ).astype(float)
    naics = df["naics_code"].astype("string").fillna("")
    out["is_naics_72"] = naics.str.startswith("72").astype(float)
    return out[list(_RICH_STRUCTURED)]


def _evidence_numeric_frame(
    con: duckdb.DuckDBPyConnection, loan_numbers: list[str]
) -> pd.DataFrame:
    """Block B numerics: rich evidence numbers per loan (max across signals)."""
    accum: dict[str, dict[str, float]] = {}
    rows = con.execute(
        "SELECT loan_number, detector_id, evidence_json FROM signals"
    ).fetchall()
    for ln, det, ej in rows:
        spec = _EVIDENCE_NUMERICS.get(str(det))
        if not spec:
            continue
        try:
            evidence = json.loads(ej) if ej else {}
        except (TypeError, ValueError):
            evidence = {}
        if not isinstance(evidence, dict):
            continue
        bucket = accum.setdefault(str(ln), {})
        for key, col in spec:
            value = evidence.get(key)
            if value is None:
                continue
            try:
                fv = float(value)
            except (TypeError, ValueError):
                continue
            if col not in bucket or fv > bucket[col]:
                bucket[col] = fv

    out = pd.DataFrame(0.0, index=range(len(loan_numbers)), columns=list(_EVIDENCE_COLS))
    for i, ln in enumerate(loan_numbers):
        bucket = accum.get(ln)
        if not bucket:
            continue
        for col, fv in bucket.items():
            out.iat[i, out.columns.get_loc(col)] = fv
    return out


def _graph_feature_frame(
    con: duckdb.DuckDBPyConnection, loan_numbers: list[str], *, min_amount: float
) -> pd.DataFrame:
    """Block C: LABEL-FREE graph structural features (0-filled if extra absent)."""
    out = pd.DataFrame(0.0, index=range(len(loan_numbers)), columns=list(_GRAPH_COLS))
    try:
        from relief_probe.graph.build import build_loan_graph
        from relief_probe.graph.features import graph_structural_features

        graph = build_loan_graph(con, min_amount=min_amount)
        per_loan = graph_structural_features(graph)
    except RuntimeError:
        # `graph` extra (NetworkX) not installed → structural block stays 0.
        return out
    for i, ln in enumerate(loan_numbers):
        feats = per_loan.get(ln)
        if not feats:
            continue
        for src, col in _GRAPH_KEYS:
            out.iat[i, out.columns.get_loc(col)] = float(feats.get(src, 0.0) or 0.0)
    return out


def _pay_ratio_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Block D: PLODI-style amount-per-job percentile within NAICS x state x county.

    The percentile of dollars-per-job *relative to industry-geography peers* is the
    geo/industry-normalized pay ratio PLODI found signal in. Cohort size travels
    with it so the model can down-weight thin (jumpy) cells. Neutral 0.5 for
    singletons / un-cohortable loans.
    """
    amount = df["current_approval_amount"].astype(float)
    jobs = df["jobs_reported"].astype(float)
    per_job = (amount / jobs.clip(lower=1)).fillna(0.0)
    grp = pd.DataFrame(
        {
            "naics": df["naics_code"].astype("string").fillna(""),
            "state": df["borrower_state"].astype("string").fillna(""),
            "county": df["project_county_name"].astype("string").fillna(""),
            "per_job": per_job.to_numpy(),
        }
    )
    g = grp.groupby(["naics", "state", "county"])["per_job"]
    out = pd.DataFrame(index=df.index)
    out["pay_ratio_pctile"] = g.rank(pct=True).fillna(0.5).to_numpy()
    out["pay_ratio_cohort_size"] = g.transform("size").astype(float).to_numpy()
    return out[list(_PAY_RATIO_COLS)]


def _categorical_frame(df: pd.DataFrame, *, lender_min_count: int) -> pd.DataFrame:
    """Block E: LightGBM-native categoricals (string categories, __missing__ null)."""

    def _str(col: str) -> pd.Series:
        return df[col].astype("string").str.strip().replace("", pd.NA).fillna(_MISSING)

    naics = df["naics_code"].astype("string").fillna("")
    sector = naics.str.slice(0, 2)
    sector = sector.where(sector.str.len() == 2, _MISSING)

    out = pd.DataFrame(index=df.index)
    out["cat_naics_sector"] = sector
    out["cat_state"] = _str("borrower_state")
    out["cat_processing_method"] = _str("processing_method")
    out["cat_business_type"] = _str("business_type")
    out["cat_rural_urban"] = _str("rural_urban_indicator")
    out["cat_nonprofit"] = _str("nonprofit")
    franchise = df["franchise_name"].astype("string").str.strip()
    out["cat_franchise"] = np.where(
        franchise.notna() & (franchise != ""), "Y", "N"
    )
    # Min-count encode the high-cardinality lender: rare lenders collapse to a
    # single __rare__ bucket so the model does not memorize a tiny book.
    lender = _str("originating_lender")
    counts = lender.value_counts()
    rare = set(counts[counts < lender_min_count].index) - {_MISSING}
    out["cat_originating_lender"] = lender.where(~lender.isin(rare), _RARE)
    return out[list(_CATEGORICAL_COLS)].astype("category")


def build_rich_feature_matrix(
    con: duckdb.DuckDBPyConnection,
    *,
    min_amount: float = 150_000.0,
    loan_numbers: list[str] | None = None,
    lender_min_count: int = 25,
) -> tuple[pd.DataFrame, list[str], list[str], list[str]]:
    """Return ``(X, loan_numbers, feature_names, categorical_features)`` (rich set).

    The leakage-guarded composite for the LightGBM learned scorer. ``X`` is a
    pandas ``DataFrame`` whose columns are ``feature_names`` in a deterministic
    order: numeric columns are ``float64`` and the categorical columns (listed in
    ``categorical_features``, a subset of ``feature_names``) carry pandas
    ``category`` dtype with a ``__missing__`` sentinel — ready to hand straight to
    LightGBM via ``categorical_feature=``.

    Blocks: (A) at-origination structured fields — NO forgiveness_*/loan_status_*;
    (B) per-detector ``det_*`` scores + rich evidence numerics; (C) LABEL-FREE
    graph structural features; (D) a PLODI-style geo/industry pay-ratio percentile;
    (E) categoricals. Missing detector signals → 0.0; missing categoricals →
    ``__missing__``. ``fraud_cases`` is never read, so the matrix is identical on an
    empty label table (SIGN-015).
    """
    where = "current_approval_amount >= ?"
    params: list = [min_amount]
    if loan_numbers is not None:
        if not loan_numbers:
            return pd.DataFrame(), [], [], []
        placeholders = ", ".join("?" for _ in loan_numbers)
        where = f"loan_number IN ({placeholders})"
        params = list(loan_numbers)

    loans = con.execute(
        f"SELECT {', '.join(_RICH_FIELDS)} FROM loans WHERE {where}",
        params,
    ).fetch_df()
    if loans.empty:
        return pd.DataFrame(), [], [], []
    loans["loan_number"] = loans["loan_number"].astype(str)
    loans = loans.reset_index(drop=True)
    lns = loans["loan_number"].tolist()

    # Block A — structured.
    parts = [_rich_structured_frame(loans)]

    # Block B — per-detector scores (reuse the existing pivot) + evidence numerics.
    detectors = [
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT detector_id FROM signals ORDER BY detector_id"
        ).fetchall()
    ]
    det_cols: list[str] = []
    det_frame = pd.DataFrame(index=loans.index)
    if detectors:
        sig = con.execute(
            "SELECT loan_number, detector_id, MAX(score) AS score "
            "FROM signals GROUP BY loan_number, detector_id"
        ).fetch_df()
        sig["loan_number"] = sig["loan_number"].astype(str)
        pivot = (
            sig.pivot(index="loan_number", columns="detector_id", values="score")
            .reindex(lns)
            .fillna(0.0)
        )
        for det in detectors:
            col = f"det_{det}"
            det_frame[col] = (
                pivot[det].to_numpy() if det in pivot.columns
                else np.zeros(len(loans))
            )
            det_cols.append(col)
    parts.append(det_frame)

    evidence = _evidence_numeric_frame(con, lns)
    evidence.index = loans.index
    parts.append(evidence)

    # Block C — graph structural (label-free; 0-filled without the graph extra).
    graph_frame = _graph_feature_frame(con, lns, min_amount=min_amount)
    graph_frame.index = loans.index
    parts.append(graph_frame)

    # Block D — PLODI-style pay-ratio percentile.
    parts.append(_pay_ratio_frame(loans))

    numeric = pd.concat(parts, axis=1).astype(np.float64)

    # Block E — categoricals (kept as category dtype, not folded into numeric).
    categoricals = _categorical_frame(loans, lender_min_count=lender_min_count)

    X = pd.concat([numeric, categoricals], axis=1)
    numeric_names = (
        list(_RICH_STRUCTURED)
        + det_cols
        + list(_EVIDENCE_COLS)
        + list(_GRAPH_COLS)
        + list(_PAY_RATIO_COLS)
    )
    categorical_features = list(_CATEGORICAL_COLS)
    feature_names = numeric_names + categorical_features
    X = X[feature_names]
    return X, lns, feature_names, categorical_features
