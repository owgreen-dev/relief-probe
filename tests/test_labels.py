"""Offline tests for the DOJ scraper's parsing + staging (no network)."""

from __future__ import annotations

import datetime as dt

from relief_probe.labels.doj import (
    classify_program,
    clean_body,
    extract_amount,
    parse_release,
    store_releases,
)
from relief_probe.warehouse import connect

# A raw API record shaped exactly like the live DOJ JSON (verified June 2026).
RAW_PPP = {
    "title": "Texas Man Sentenced for Pandemic Fraud Conspiracy",
    "body": "<p>A Katy, Texas man was sentenced for fraudulently obtaining a "
    "<strong>$476,420</strong> Paycheck Protection Program (PPP) loan and "
    "an additional $80,000 he was not entitled to.</p>",
    "date": "1700000000",  # 2023-11-14
    "url": "https://www.justice.gov/opa/pr/texas-man-sentenced-pandemic-fraud",
    "topic": [
        {"uuid": "6827a162-b783-4ffa-a6c2-596fc9f07e39", "name": "COVID-Related Fraud"}
    ],
    "uuid": "abc",
}
RAW_IRRELEVANT = {
    "title": "Former Trooper Pleads Guilty to Civil Rights Violation",
    "body": "<p>Unrelated to any loan program.</p>",
    "date": "1700000000",
    "url": "https://www.justice.gov/opa/pr/unrelated",
    "topic": [],
    "uuid": "def",
}


def test_clean_body_strips_html():
    assert clean_body("<p>Hello   <b>world</b></p>") == "Hello world"


def test_extract_amount_takes_largest_and_handles_units():
    assert extract_amount("got $476,420 and $80,000") == 476420.0
    assert extract_amount("a $1.2 million scheme") == 1_200_000.0
    assert extract_amount("no dollars here") is None


def test_classify_program():
    assert classify_program("paycheck protection program loan") == "ppp"
    assert classify_program("economic injury disaster (eidl) loan") == "eidl"
    assert classify_program("both ppp and eidl funds") == "both"
    assert classify_program("unemployment benefits") == "other"


def test_parse_release_keeps_loan_fraud_and_drops_others():
    row = parse_release(RAW_PPP)
    assert row is not None
    assert row["program"] == "ppp"
    assert row["alleged_amount"] == 476420.0
    assert row["published_date"] == dt.date(2023, 11, 14)
    assert row["source"] == "doj"
    assert len(row["id"]) == 16

    assert parse_release(RAW_IRRELEVANT) is None


def test_store_releases_idempotent(tmp_path):
    con = connect(tmp_path / "wh.duckdb")
    row = parse_release(RAW_PPP)
    assert store_releases(con, [row]) == 1
    assert store_releases(con, [row]) == 0  # same id -> ignored
    assert con.execute("SELECT COUNT(*) FROM press_releases").fetchone()[0] == 1
