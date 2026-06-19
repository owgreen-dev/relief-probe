"""Offline tests for the PPP loader and warehouse schema.

These never touch the network: we build a tiny CSV with the real PPP FOIA header
(verified against the public file) and assert the loader maps + types it correctly.
"""

from __future__ import annotations

import datetime as dt

from relief_probe.ingest.loader import load_ppp_csv
from relief_probe.warehouse import connect

# The real 53-column PPP FOIA header (public_150k_plus_*.csv), verified June 2026.
PPP_HEADER = (
    "LoanNumber,DateApproved,SBAOfficeCode,ProcessingMethod,BorrowerName,"
    "BorrowerAddress,BorrowerCity,BorrowerState,BorrowerZip,LoanStatusDate,"
    "LoanStatus,Term,SBAGuarantyPercentage,InitialApprovalAmount,"
    "CurrentApprovalAmount,UndisbursedAmount,FranchiseName,"
    "ServicingLenderLocationID,ServicingLenderName,ServicingLenderAddress,"
    "ServicingLenderCity,ServicingLenderState,ServicingLenderZip,"
    "RuralUrbanIndicator,HubzoneIndicator,LMIIndicator,BusinessAgeDescription,"
    "ProjectCity,ProjectCountyName,ProjectState,ProjectZip,CD,JobsReported,"
    "NAICSCode,Race,Ethnicity,UTILITIES_PROCEED,PAYROLL_PROCEED,"
    "MORTGAGE_INTEREST_PROCEED,RENT_PROCEED,REFINANCE_EIDL_PROCEED,"
    "HEALTH_CARE_PROCEED,DEBT_INTEREST_PROCEED,BusinessType,"
    "OriginatingLenderLocationID,OriginatingLender,OriginatingLenderCity,"
    "OriginatingLenderState,Gender,Veteran,NonProfit,ForgivenessAmount,"
    "ForgivenessDate"
)

# One real-shaped row (the public sample) + one with a blank amount (-> NULL).
ROW_REAL = (
    '9547507704,05/01/2020,0464,PPP,"SUMTER COATINGS, INC.",2410 Highway 15 South,'
    "Sumter,SC,29150-9662,12/18/2020,Paid in Full,24,100,769358.78,769358.78,0,,"
    "19248,Synovus Bank,1148 Broadway,COLUMBUS,GA,31901-2429,U,N,N,"
    "Existing or more than 2 years old,Sumter,SUMTER,SC,29150-9662,SC-05,62,"
    "325510,Unanswered,Unknown/NotStated,,769358.78,,,,,,Corporation,19248,"
    "Synovus Bank,COLUMBUS,GA,Unanswered,Unanswered,,773553.37,11/20/2020"
)
ROW_BLANK_AMT = (
    "9999999999,06/15/2020,0464,PPS,JOHN DOE LLC,1 Main St,Anywhere,TX,75001,"
    ",Active Un-Forgiven,60,100,,,,,,Some Bank,1 Bank St,Dallas,TX,75001,U,N,N,"
    "Unknown,Anywhere,DALLAS,TX,75001,TX-01,1,541110,Unanswered,Unknown/NotStated,"
    ",,,,,,,Sole Proprietorship,1,Some Bank,Dallas,TX,Male Owned,Non-Veteran,,,"
)


def _write_csv(path, *rows):
    path.write_text("\n".join([PPP_HEADER, *rows]) + "\n")
    return path


def test_loader_maps_and_types(tmp_path):
    csv = _write_csv(tmp_path / "ppp.csv", ROW_REAL, ROW_BLANK_AMT)
    con = connect(tmp_path / "wh.duckdb")
    inserted = load_ppp_csv(con, csv)
    assert inserted == 2

    row = con.execute(
        "SELECT borrower_name, borrower_state, date_approved, "
        "current_approval_amount, jobs_reported, naics_code, forgiveness_amount "
        "FROM loans WHERE loan_number = '9547507704'"
    ).fetchone()
    name, state, approved, amount, jobs, naics, forgiven = row
    assert name == "SUMTER COATINGS, INC."  # quoted comma survives
    assert state == "SC"
    assert approved == dt.date(2020, 5, 1)  # MM/DD/YYYY parsed
    assert amount == 769358.78
    assert jobs == 62.0
    assert naics == "325510"
    assert forgiven == 773553.37

    # Blank numeric -> NULL, not a load failure.
    amt = con.execute(
        "SELECT current_approval_amount FROM loans WHERE loan_number = '9999999999'"
    ).fetchone()[0]
    assert amt is None


def test_loader_is_idempotent(tmp_path):
    csv = _write_csv(tmp_path / "ppp.csv", ROW_REAL)
    con = connect(tmp_path / "wh.duckdb")
    assert load_ppp_csv(con, csv) == 1
    # Re-loading the same file inserts nothing (INSERT OR IGNORE on the PK).
    assert load_ppp_csv(con, csv) == 0
    assert con.execute("SELECT COUNT(*) FROM loans").fetchone()[0] == 1
