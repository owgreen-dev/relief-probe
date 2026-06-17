"""Entity resolution: link DOJ press releases to PPP loan records.

This is the differentiating, hard step. There is no public per-loan fraud-label
list (the 2026 "562,000 referred loans" figure is flagged-not-charged and not
downloadable), so the only positive labels we can build are prosecuted cases
matched back to the loan file. We therefore optimize for **precision** — a false
positive label silently corrupts the benchmark — accepting limited recall.

Method (precision-first)
------------------------
1. Build an index of normalized loan borrower names -> loan rows.
2. For each loan-fraud release, pull candidate business-name phrases from its title +
   body, normalize them the same way, and look them up in the index.
3. Confirm/disambiguate each name hit with independent signals from the release text:
     * the loan's **state** appears (abbrev or full name), and/or
     * the loan's **amount** appears verbatim, or the release's alleged scheme amount
       is close to the loan amount.
   A bare name match is weak (many "ABC TRUCKING LLC"); name + state + amount is strong.
4. Emit a ``fraud_cases`` row per accepted match, with ``match_method`` and a
   ``match_confidence`` in [0, 1]. Only matches at/above a threshold are written.

Known limitations (documented, not hidden)
-------------------------------------------
* Misses sole-proprietor loans filed under a person's name (the release names the
  person, the loan file may carry the individual, but matching person names is far
  noisier — deferred).
* Misses misspellings / DBA / abbreviation mismatches (no fuzzy edit distance yet).
* Labels are positive-unlabeled and prosecution-biased; see RESPONSIBLE_USE.md.
"""

from __future__ import annotations

import hashlib
import re

import duckdb
import pandas as pd

# Corporate tokens stripped during normalization so "PREMIER CARE STAFFING INC" and
# "Premier Care Staffing" collapse to the same key.
_CORP_SUFFIXES = {
    "INC", "INCORPORATED", "LLC", "LLP", "LP", "LTD", "CO", "COMPANY", "CORP",
    "CORPORATION", "PLLC", "PLC", "PC", "PA", "GROUP", "HOLDINGS", "ENTERPRISES",
    "ENTERPRISE", "SERVICES", "SVCS", "DBA", "THE", "AND", "OF",
}
_NONALNUM = re.compile(r"[^A-Z0-9 ]+")
_WS = re.compile(r"\s+")

# Longest candidate org-name n-gram we'll consider (tokens).
_MAX_NGRAM = 6

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia",
}

ACCEPT_THRESHOLD = 0.6
# A name mapping to more than this many loans is too generic to accept on name alone.
MAX_AMBIGUITY = 25


def _normalize_tokens(text: str | None) -> list[str]:
    """Uppercase, punctuation->space, drop corporate/stopword tokens -> token list."""
    if not text:
        return []
    toks = _WS.sub(" ", _NONALNUM.sub(" ", text.upper())).strip().split()
    return [t for t in toks if t not in _CORP_SUFFIXES]


def normalize_name(name: str | None) -> str:
    """Canonical key for a single name (token list joined by spaces)."""
    return " ".join(_normalize_tokens(name))


def extract_candidates(text: str, *, n_min: int = 2, n_max: int = _MAX_NGRAM) -> set[str]:
    """Every contiguous n-gram (len ``n_min``..``n_max``) of the normalized text.

    We don't try to detect org-name boundaries in prose (greedy capture wrongly
    absorbed trailing "of New York" into the name). Instead we emit all n-grams and
    let the loan-name index decide which are real borrowers — only phrases that match
    an actual borrower name survive.
    """
    toks = _normalize_tokens(text)
    out: set[str] = set()
    for n in range(n_min, n_max + 1):
        for i in range(len(toks) - n + 1):
            out.add(" ".join(toks[i : i + n]))
    return out


def _amount_in_text(amount: float, text: str) -> bool:
    """True if the dollar amount appears verbatim (with thousands separators)."""
    whole = int(round(amount))
    return f"{whole:,}" in text or str(whole) in text


def score_match(
    *,
    name_tokens: int,
    loan_state: str | None,
    loan_amount: float | None,
    alleged_amount: float | None,
    text: str,
) -> tuple[float, str]:
    """Confidence in [0,1] for a name hit, plus a ``+``-joined method string."""
    conf = 0.4
    methods = ["name"]
    upper = (text or "").upper()

    if loan_state:
        state_full = US_STATES.get(loan_state, "").upper()
        if re.search(rf"\b{re.escape(loan_state)}\b", upper) or (
            state_full and state_full in upper
        ):
            conf += 0.25
            methods.append("state")

    if loan_amount:
        if _amount_in_text(loan_amount, text):
            conf += 0.30
            methods.append("amount")
        elif alleged_amount and abs(alleged_amount - loan_amount) / loan_amount <= 0.2:
            conf += 0.15
            methods.append("amount~")

    if name_tokens >= 3:  # more specific names are less collision-prone
        conf += 0.10
    return min(conf, 1.0), "+".join(methods)


def build_loan_index(
    con: duckdb.DuckDBPyConnection,
) -> dict[str, list[tuple[str, str, float]]]:
    """norm_name -> list of (loan_number, state, amount) over the loan population."""
    index: dict[str, list[tuple[str, str, float]]] = {}
    for loan_number, name, state, amount in con.execute(
        "SELECT loan_number, borrower_name, borrower_state, current_approval_amount "
        "FROM loans WHERE borrower_name IS NOT NULL"
    ).fetchall():
        key = normalize_name(name)
        if len(key.split()) >= 2:  # skip ultra-generic single-token names
            index.setdefault(key, []).append((str(loan_number), state, amount))
    return index


def resolve_release(
    release: dict,
    index: dict[str, list[tuple[str, str, float]]],
    *,
    threshold: float = ACCEPT_THRESHOLD,
) -> list[dict]:
    """Return accepted fraud_cases rows for one staged release (may be empty)."""
    text = (release.get("title", "") or "") + " . " + (release.get("body", "") or "")
    alleged = release.get("alleged_amount")
    best: dict[str, dict] = {}  # loan_number -> best row

    for key in extract_candidates(text):
        hits = index.get(key)
        if not hits:
            continue
        ambiguous = len(hits) > MAX_AMBIGUITY
        n_tokens = len(key.split())
        for loan_number, state, amount in hits:
            conf, method = score_match(
                name_tokens=n_tokens,
                loan_state=state,
                loan_amount=amount,
                alleged_amount=alleged,
                text=text,
            )
            # A too-generic name needs corroboration beyond the name itself.
            if ambiguous and method == "name":
                continue
            if conf < threshold:
                continue
            row = {
                "case_id": hashlib.sha256(
                    f"{release.get('url', '')}|{loan_number}".encode()
                ).hexdigest()[:16],
                "loan_number": loan_number,
                "defendant_name": None,  # person-name extraction deferred
                "business_name": key,
                "alleged_amount": alleged,
                "charge_date": release.get("published_date"),
                "source": release.get("source", "doj"),
                "source_url": release.get("url"),
                "match_method": method,
                "match_confidence": round(conf, 3),
            }
            prev = best.get(loan_number)
            if prev is None or row["match_confidence"] > prev["match_confidence"]:
                best[loan_number] = row
    return list(best.values())


_FRAUD_COLS = (
    "case_id", "loan_number", "defendant_name", "business_name", "alleged_amount",
    "charge_date", "source", "source_url", "match_method", "match_confidence",
)


def resolve_all(
    con: duckdb.DuckDBPyConnection,
    *,
    threshold: float = ACCEPT_THRESHOLD,
    programs: tuple[str, ...] = ("ppp", "eidl", "both"),
    progress=None,
) -> dict:
    """Resolve all staged loan-fraud releases to loans; replace ``fraud_cases``.

    Returns a summary: releases scanned, releases matched, loans labeled.
    """
    index = build_loan_index(con)
    placeholders = ", ".join("?" for _ in programs)
    releases = con.execute(
        f"SELECT url, title, body, published_date, program, alleged_amount, source "
        f"FROM press_releases WHERE program IN ({placeholders})",
        list(programs),
    ).fetch_df()

    matched_releases = 0
    rows: list[dict] = []
    for i, rec in enumerate(releases.itertuples(index=False), start=1):
        # DuckDB DATE comes back as a pandas Timestamp (or NaT) via fetch_df.
        pub = None if pd.isna(rec.published_date) else rec.published_date.date()
        release = {
            "url": rec.url,
            "title": rec.title,
            "body": rec.body,
            "published_date": pub,
            "alleged_amount": rec.alleged_amount,
            "source": rec.source,
        }
        matches = resolve_release(release, index, threshold=threshold)
        if matches:
            matched_releases += 1
            rows.extend(matches)
        if progress and i % 500 == 0:
            progress(i, len(rows))

    con.execute("DELETE FROM fraud_cases")
    if rows:
        # Dedupe by case_id (a loan can be named in multiple releases).
        seen: dict[str, dict] = {}
        for r in rows:
            seen[r["case_id"]] = r
        data = [tuple(r[c] for c in _FRAUD_COLS) for r in seen.values()]
        con.executemany(
            f"INSERT INTO fraud_cases ({', '.join(_FRAUD_COLS)}) "
            f"VALUES ({', '.join('?' for _ in _FRAUD_COLS)})",
            data,
        )
    distinct_loans = con.execute(
        "SELECT COUNT(DISTINCT loan_number) FROM fraud_cases"
    ).fetchone()[0]
    return {
        "releases_scanned": len(releases),
        "releases_matched": matched_releases,
        "loans_labeled": distinct_loans,
    }
