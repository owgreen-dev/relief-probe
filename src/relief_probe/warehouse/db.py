"""DuckDB connection management and schema bootstrap.

The warehouse is a single DuckDB file. Unlike a per-provider time series, the PPP
grain is **one row per loan** — there is no year dimension on the entity, because
every PPP loan was originated in 2020–2021. The "forward" aspect of the benchmark
comes from the *labels*: DOJ/SBA-OIG fraud enforcement actions that post-date the
loans (prosecutions still landing 2024–2026 under the 10-year statute), stored in
``fraud_cases``.

Schema:
  loans         dimension+fact: the public SBA FOIA loan record, one row per loan.
  fraud_cases   labels: DOJ/OIG-charged fraud, entity-resolved back to a loan_number
                where possible (PU-learning positives — sparse and biased toward
                egregious, *caught* cases; see RESPONSIBLE_USE.md).
  signals       output contract: every detector writes (loan_number, detector_id,
                score, evidence_json) here.

Raw → warehouse column mapping is documented in docs/SCHEMA.md.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from relief_probe.config import warehouse_path

# DDL kept declarative and idempotent (CREATE TABLE IF NOT EXISTS) so
# `init_schema` is safe to call on every connect.
SCHEMA_SQL = """
-- Fact+dimension: one row per PPP loan (SBA FOIA public release).
-- Columns map to the raw CSV headers (see docs/SCHEMA.md):
--   loan_number              <- LoanNumber
--   date_approved            <- DateApproved (MM/DD/YYYY)
--   borrower_name            <- BorrowerName
--   borrower_{city,state,zip}<- BorrowerCity / BorrowerState / BorrowerZip
--   loan_status              <- LoanStatus  (e.g. 'Paid in Full', 'Charged Off')
--   initial_approval_amount  <- InitialApprovalAmount
--   current_approval_amount  <- CurrentApprovalAmount
--   jobs_reported            <- JobsReported   (key denominator for $/job outliers)
--   naics_code               <- NAICSCode      (cohort key)
--   *_proceed                <- the seven loan-use proceed columns
--   forgiveness_amount       <- ForgivenessAmount
CREATE TABLE IF NOT EXISTS loans (
    loan_number               VARCHAR PRIMARY KEY,
    date_approved             DATE,
    processing_method         VARCHAR,     -- 'PPP' (1st draw) / 'PPS' (2nd draw)
    borrower_name             VARCHAR,
    borrower_address          VARCHAR,
    borrower_city             VARCHAR,
    borrower_state            VARCHAR,
    borrower_zip              VARCHAR,
    loan_status               VARCHAR,
    loan_status_date          DATE,
    term                      INTEGER,
    sba_guaranty_pct          DOUBLE,
    initial_approval_amount   DOUBLE,
    current_approval_amount   DOUBLE,
    undisbursed_amount        DOUBLE,
    franchise_name            VARCHAR,
    servicing_lender_name     VARCHAR,
    servicing_lender_state    VARCHAR,
    rural_urban_indicator     VARCHAR,
    business_age_description  VARCHAR,
    project_county_name       VARCHAR,
    project_state             VARCHAR,
    project_zip               VARCHAR,
    jobs_reported             DOUBLE,
    naics_code                VARCHAR,
    race                      VARCHAR,
    ethnicity                 VARCHAR,
    gender                    VARCHAR,
    veteran                   VARCHAR,
    nonprofit                 VARCHAR,
    business_type             VARCHAR,
    originating_lender        VARCHAR,
    originating_lender_state  VARCHAR,
    utilities_proceed         DOUBLE,
    payroll_proceed           DOUBLE,
    mortgage_interest_proceed DOUBLE,
    rent_proceed              DOUBLE,
    refinance_eidl_proceed    DOUBLE,
    health_care_proceed       DOUBLE,
    debt_interest_proceed     DOUBLE,
    forgiveness_amount        DOUBLE,
    forgiveness_date          DATE
);

-- Labels: DOJ / SBA-OIG fraud enforcement, entity-resolved to a loan where we can.
-- These are PU-learning positives: confirmed/charged fraud is a tiny (<0.1%) and
-- biased sample of estimated fraud, so model output is recall-on-known-fraud, not a
-- true fraud rate (see RESPONSIBLE_USE.md). loan_number is NULL until resolved.
CREATE TABLE IF NOT EXISTS fraud_cases (
    case_id          VARCHAR,            -- our id (hash of source_url + defendant)
    loan_number      VARCHAR,            -- resolved match into loans, NULL if unmatched
    defendant_name   VARCHAR,
    business_name    VARCHAR,
    alleged_amount   DOUBLE,             -- $ amount alleged/charged, when stated
    charge_date      DATE,               -- enforcement/charge date (post-dates loans)
    source           VARCHAR,            -- 'doj' / 'sba_oig'
    source_url       VARCHAR,
    match_method     VARCHAR,            -- how we linked it to a loan (e.g. 'name_state')
    match_confidence DOUBLE             -- 0..1 resolution confidence
);

-- Staging: raw enforcement press releases (DOJ etc.), pre-resolution. The
-- entity-resolution step reads these (loan-relevant ones) and produces
-- fraud_cases rows linked to loan_number. `body` is cleaned text kept so the
-- resolver can search for loan borrower names appearing verbatim in a release.
CREATE TABLE IF NOT EXISTS press_releases (
    id             VARCHAR PRIMARY KEY,   -- stable hash of source_url
    source         VARCHAR,               -- 'doj'
    url            VARCHAR,
    title          VARCHAR,
    published_date DATE,
    program        VARCHAR,               -- 'ppp' / 'eidl' / 'both' / 'other'
    alleged_amount DOUBLE,                 -- best-effort max $ mentioned (heuristic)
    body           VARCHAR                 -- cleaned release text
);

-- Census ZIP Business Patterns (ZBP): how many business establishments actually
-- exist in a given ZIP x NAICS cell. Joined directly on loans.borrower_zip (no
-- zip->county crosswalk). Powers the establishment_overcount detector: PPP loan
-- DENSITY per industry-geography that far exceeds the real establishment count is a
-- fraud signal (Griffin, Kruger & Mahajan, J.Finance 2023). Raw ZBP headers map as:
--   zip            <- zip   (5-digit ZIP code)
--   naics          <- naics (industry code; ZBP publishes 2/4/6-digit rollups)
--   establishments <- est   (number of establishments in that ZIP x NAICS cell)
-- (zip, naics) is the natural key so re-loads are idempotent (INSERT OR IGNORE).
CREATE TABLE IF NOT EXISTS establishments (
    zip            VARCHAR,
    naics          VARCHAR,
    establishments INTEGER,
    PRIMARY KEY (zip, naics)
);

-- Output contract: every detector emits rows here.
-- evidence_json is a JSON string describing why the loan was flagged.
CREATE TABLE IF NOT EXISTS signals (
    loan_number   VARCHAR,
    detector_id   VARCHAR,
    score         DOUBLE,
    evidence_json VARCHAR
);
"""


def connect(
    path: Path | str | None = None, *, read_only: bool = False
) -> duckdb.DuckDBPyConnection:
    """Open (and schema-init) the warehouse. Defaults to the configured path."""
    db_path = Path(path) if path is not None else warehouse_path()
    con = duckdb.connect(str(db_path), read_only=read_only)
    if not read_only:
        init_schema(con)
    return con


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create all tables if they do not already exist."""
    con.execute(SCHEMA_SQL)
