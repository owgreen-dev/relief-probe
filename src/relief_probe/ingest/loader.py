"""Load a downloaded PPP FOIA CSV into the ``loans`` table.

We read with ``all_varchar=true`` and cast explicitly with ``TRY_CAST`` so a blank
or malformed value becomes NULL instead of aborting the load. Dates are
``MM/DD/YYYY``. Loads are idempotent via ``INSERT OR IGNORE`` on the
``loan_number`` primary key, so re-running (or loading overlapping files) is safe.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

# Maps raw CSV header -> (warehouse column, SQL expression over the raw varchar).
# Order here defines the INSERT column order.
_PPP_INSERT = """
INSERT OR IGNORE INTO loans (
    loan_number, date_approved, processing_method, borrower_name, borrower_address,
    borrower_city, borrower_state, borrower_zip, loan_status, loan_status_date,
    term, sba_guaranty_pct, initial_approval_amount, current_approval_amount,
    undisbursed_amount, franchise_name, servicing_lender_name, servicing_lender_state,
    rural_urban_indicator, business_age_description, project_county_name, project_state,
    project_zip, jobs_reported, naics_code, race, ethnicity, gender, veteran, nonprofit,
    business_type, originating_lender, originating_lender_state, utilities_proceed,
    payroll_proceed, mortgage_interest_proceed, rent_proceed, refinance_eidl_proceed,
    health_care_proceed, debt_interest_proceed, forgiveness_amount, forgiveness_date
)
SELECT
    LoanNumber,
    TRY_CAST(strptime(DateApproved, '%m/%d/%Y') AS DATE),
    ProcessingMethod,
    BorrowerName,
    BorrowerAddress,
    BorrowerCity,
    BorrowerState,
    BorrowerZip,
    LoanStatus,
    TRY_CAST(strptime(LoanStatusDate, '%m/%d/%Y') AS DATE),
    TRY_CAST(Term AS INTEGER),
    TRY_CAST(SBAGuarantyPercentage AS DOUBLE),
    TRY_CAST(InitialApprovalAmount AS DOUBLE),
    TRY_CAST(CurrentApprovalAmount AS DOUBLE),
    TRY_CAST(UndisbursedAmount AS DOUBLE),
    FranchiseName,
    ServicingLenderName,
    ServicingLenderState,
    RuralUrbanIndicator,
    BusinessAgeDescription,
    ProjectCountyName,
    ProjectState,
    ProjectZip,
    TRY_CAST(JobsReported AS DOUBLE),
    NAICSCode,
    Race,
    Ethnicity,
    Gender,
    Veteran,
    NonProfit,
    BusinessType,
    OriginatingLender,
    OriginatingLenderState,
    TRY_CAST(UTILITIES_PROCEED AS DOUBLE),
    TRY_CAST(PAYROLL_PROCEED AS DOUBLE),
    TRY_CAST(MORTGAGE_INTEREST_PROCEED AS DOUBLE),
    TRY_CAST(RENT_PROCEED AS DOUBLE),
    TRY_CAST(REFINANCE_EIDL_PROCEED AS DOUBLE),
    TRY_CAST(HEALTH_CARE_PROCEED AS DOUBLE),
    TRY_CAST(DEBT_INTEREST_PROCEED AS DOUBLE),
    TRY_CAST(ForgivenessAmount AS DOUBLE),
    TRY_CAST(strptime(ForgivenessDate, '%m/%d/%Y') AS DATE)
FROM {rel}
WHERE LoanNumber IS NOT NULL
"""


def load_ppp_csv(con: duckdb.DuckDBPyConnection, csv_path: Path) -> int:
    """Load one PPP FOIA CSV into ``loans``. Returns rows inserted."""
    rel = (
        f"read_csv('{csv_path}', header=true, all_varchar=true, "
        "ignore_errors=true)"
    )
    before = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    con.execute(_PPP_INSERT.format(rel=rel))
    after = con.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    return after - before
