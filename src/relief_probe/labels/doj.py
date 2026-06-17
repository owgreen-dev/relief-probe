"""Scrape DOJ press releases for PPP/EIDL loan-fraud enforcement.

DOJ exposes a public JSON API (``/api/v1/press_releases.json``) over all ~267k
press releases. It does NOT support server-side keyword/topic filtering, so we page
newest-first (``sort=date&direction=DESC``), keep releases that look like
SBA-loan fraud (by the "COVID-Related Fraud" topic OR a loan keyword), and stop once
we pass a cutoff date — COVID-loan fraud cannot predate 2020.

Parsing is separated from fetching so it is unit-testable offline:
  * ``clean_body`` / ``extract_amount`` / ``classify_program`` / ``parse_release``
    are pure functions over a raw API record.
  * ``iter_raw_releases`` / ``fetch_doj_cases`` do the live paging.

Amount extraction is heuristic (the largest dollar figure in the text — usually the
scheme total), and ``parse_release`` does NOT try to extract the business name: the
resolver (next step) instead searches for loan borrower names appearing verbatim in
the release body, which is more reliable than NER on prose.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import time
from collections.abc import Iterator

import requests

API_URL = "https://www.justice.gov/api/v1/press_releases.json"
COVID_FRAUD_TOPIC_UUID = "6827a162-b783-4ffa-a6c2-596fc9f07e39"  # "COVID-Related Fraud"

# Loan-fraud relevance (we want PPP/EIDL, not unemployment — those won't resolve to
# a loan). Lowercased substring tests against title + body.
_PPP_KW = ("paycheck protection", "ppp loan", " ppp ", "(ppp)")
_EIDL_KW = ("economic injury disaster", " eidl", "eidl ", "(eidl)")
_OTHER_LOAN_KW = (
    "sba loan",
    "covid-19 relief loan",
    "covid relief loan",
    "pandemic relief loan",
    "small business administration",
)
_LOAN_KW = _PPP_KW + _EIDL_KW + _OTHER_LOAN_KW

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_AMOUNT_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?", re.IGNORECASE
)
_MULT = {"thousand": 1_000.0, "million": 1_000_000.0, "billion": 1_000_000_000.0}


def clean_body(body_html: str) -> str:
    """Strip HTML tags + unescape entities + collapse whitespace."""
    text = html.unescape(_TAG_RE.sub(" ", body_html or ""))
    return _WS_RE.sub(" ", text).strip()


def extract_amount(text: str) -> float | None:
    """Largest dollar figure in ``text`` (heuristic for the scheme total)."""
    best: float | None = None
    for m in _AMOUNT_RE.finditer(text):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        val *= _MULT.get(unit, 1.0)
        if best is None or val > best:
            best = val
    return best


def classify_program(blob: str) -> str:
    """Tag a (lowercased) text as ppp / eidl / both / other."""
    has_ppp = any(k in blob for k in _PPP_KW)
    has_eidl = any(k in blob for k in _EIDL_KW)
    if has_ppp and has_eidl:
        return "both"
    if has_ppp:
        return "ppp"
    if has_eidl:
        return "eidl"
    return "other"


def _topic_uuids(raw: dict) -> set[str]:
    return {t.get("uuid", "") for t in (raw.get("topic") or [])}


def is_loan_fraud(raw: dict, text: str) -> bool:
    """True if the release looks like SBA loan fraud (keyword-gated for resolvability).

    We require a loan keyword (not merely the COVID topic), because the topic also
    covers unemployment / price-gouging cases that cannot resolve to a PPP loan.
    """
    blob = (raw.get("title", "") + " " + text).lower()
    return any(k in blob for k in _LOAN_KW)


def parse_release(raw: dict) -> dict | None:
    """Parse one raw API record into a staged row, or None if not loan-relevant."""
    text = clean_body(raw.get("body", ""))
    if not is_loan_fraud(raw, text):
        return None
    url = raw.get("url", "")
    blob = (raw.get("title", "") + " " + text).lower()
    try:
        published = dt.datetime.fromtimestamp(int(raw["date"]), dt.UTC).date()
    except (KeyError, ValueError, TypeError):
        published = None
    return {
        "id": hashlib.sha256(url.encode()).hexdigest()[:16],
        "source": "doj",
        "url": url,
        "title": raw.get("title", ""),
        "published_date": published,
        "program": classify_program(blob),
        "alleged_amount": extract_amount(text),
        "body": text,
    }


def _published_date(raw: dict) -> dt.date | None:
    """Parse the publication date; None if absent or implausible (stray date=0)."""
    try:
        d = dt.datetime.fromtimestamp(int(raw["date"]), dt.UTC).date()
    except (KeyError, ValueError, TypeError, OSError):
        return None
    # DOJ has occasional bad-date records (e.g. epoch 0 -> 1970) that survive even a
    # date-sorted listing; treat anything pre-2015 as a data artifact, not a boundary.
    return d if d.year >= 2015 else None


def _fetch_page(
    sess: requests.Session,
    page: int,
    page_size: int,
    timeout: int = 60,
    retries: int = 3,
) -> list[dict]:
    """Fetch one page, retrying transient errors (a long backfill is ~1800 pages)."""
    params = {
        "pagesize": page_size,  # API caps effective size at 250
        "page": page,
        # Sort by publication date (NOT `created`) so `date` is monotonic
        # descending and the min_date early-stop is meaningful.
        "sort": "date",
        "direction": "DESC",
    }
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = sess.get(API_URL, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp.json().get("results", [])
        except (requests.RequestException, ValueError) as exc:  # incl. JSON decode
            last_exc = exc
            time.sleep(2**attempt)  # 1s, 2s, 4s backoff
    raise RuntimeError(f"DOJ page {page} failed after {retries} attempts") from last_exc


def iter_doj_pages(
    *,
    min_date: dt.date,
    page_size: int = 250,
    max_pages: int = 2000,
    session: requests.Session | None = None,
) -> Iterator[list[dict]]:
    """Yield one list of parsed loan-fraud rows per page, newest-first.

    Drops releases older than ``min_date`` and stops once an entire page predates it
    (robust to stray old-dated records). Yielding per page lets callers persist
    incrementally so a mid-run failure doesn't discard the whole backfill.
    """
    sess = session or requests.Session()
    for page in range(max_pages):
        results = _fetch_page(sess, page, page_size)
        if not results:
            return
        rows = []
        for raw in results:
            published = _published_date(raw)
            if published is not None and published < min_date:
                continue
            row = parse_release(raw)
            if row is not None:
                rows.append(row)
        yield rows
        page_dates = [d for d in (_published_date(r) for r in results) if d]
        if page_dates and max(page_dates) < min_date:
            return


def iter_raw_releases(
    *,
    page_size: int = 250,
    max_pages: int = 400,
    session: requests.Session | None = None,
    timeout: int = 60,
) -> Iterator[dict]:
    """Yield raw API records newest-first, across pages."""
    sess = session or requests.Session()
    for page in range(max_pages):
        results = _fetch_page(sess, page, page_size, timeout)
        if not results:
            return
        yield from results


def fetch_doj_cases(
    *,
    min_date: dt.date | None = None,
    page_size: int = 250,
    max_pages: int = 2000,
    session: requests.Session | None = None,
    progress=None,
) -> list[dict]:
    """Collect all parsed loan-fraud rows back to ``min_date`` (default 2020-01-01).

    Convenience wrapper over :func:`iter_doj_pages` for callers that want everything
    in memory; the CLI uses ``iter_doj_pages`` directly to store incrementally.
    """
    if min_date is None:
        min_date = dt.date(2020, 1, 1)
    kept: list[dict] = []
    for page_idx, rows in enumerate(
        iter_doj_pages(
            min_date=min_date,
            page_size=page_size,
            max_pages=max_pages,
            session=session,
        ),
        start=1,
    ):
        kept.extend(rows)
        if progress:
            progress(page_idx, len(kept))
    return kept


_STORE_COLS = (
    "id",
    "source",
    "url",
    "title",
    "published_date",
    "program",
    "alleged_amount",
    "body",
)


def store_releases(con, records: list[dict]) -> int:
    """Insert parsed releases into ``press_releases`` (idempotent on id)."""
    if not records:
        return 0
    before = con.execute("SELECT COUNT(*) FROM press_releases").fetchone()[0]
    rows = [tuple(rec.get(c) for c in _STORE_COLS) for rec in records]
    con.executemany(
        "INSERT OR IGNORE INTO press_releases "
        "(id, source, url, title, published_date, program, alleged_amount, body) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    after = con.execute("SELECT COUNT(*) FROM press_releases").fetchone()[0]
    return after - before
