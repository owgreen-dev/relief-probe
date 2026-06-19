"""Address normalization → a canonical building-level key.

This is the foundation of the duplicate-address *ring* signal: many distinct
borrowers keyed to the same physical building is a link-analysis red flag that is
entirely independent of the dollars-per-job detectors.

:func:`normalize_address` collapses the common formatting variations of a single
street address (case, punctuation, whitespace, spelled-out vs. abbreviated USPS
suffixes) onto one key so that loans in the same building group together.

Building-level grouping is deliberate
-------------------------------------
We **strip unit/suite designators** (``SUITE``/``STE``/``UNIT``/``APT``/``#`` +
number) on purpose: a ring frequently spreads across many suites of one building,
so collapsing to the building is the signal we want — not noise.

False-positive modes (document, don't hide)
-------------------------------------------
* Shared office buildings, coworking spaces, and strip malls legitimately host
  many unrelated businesses at one street address.
* Registered-agent / mail-forwarding services (e.g. a filing company's address)
  appear on thousands of unrelated filings.

So a shared-address ring is a **review lead**, never proof — the detector surfaces
it; a human adjudicates.
"""

from __future__ import annotations

import re

#: Spelled-out USPS street suffixes → their canonical abbreviation.
_SUFFIXES: dict[str, str] = {
    "STREET": "ST",
    "AVENUE": "AVE",
    "ROAD": "RD",
    "BOULEVARD": "BLVD",
    "DRIVE": "DR",
    "LANE": "LN",
    "COURT": "CT",
    "PLACE": "PL",
}

#: Unit/suite designator + its number, stripped so suites collapse to the building.
_UNIT_RE = re.compile(
    r"\b(?:SUITE|STE|UNIT|APT|APARTMENT)\b\.?\s*#?\s*[\w-]+",
    re.IGNORECASE,
)
#: Bare ``#123`` unit markers (no keyword).
_HASH_UNIT_RE = re.compile(r"#\s*[\w-]+")
#: Anything that isn't a word char or whitespace → dropped to a space.
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _norm_street(address: str) -> str:
    """Canonicalize a street line: strip units, punctuation, normalize suffixes."""
    s = address.upper()
    s = _UNIT_RE.sub(" ", s)
    s = _HASH_UNIT_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    tokens = [_SUFFIXES.get(tok, tok) for tok in s.split()]
    return " ".join(tokens)


def normalize_address(
    address: str | None,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
) -> str | None:
    """Return a canonical building-level key, or ``None`` if unkeyable.

    The key combines the normalized street line with city/state/ZIP so that
    identical street lines in different places do not collide. Returns ``None``
    when the street is missing/blank or too sparse to key reliably (so such
    loans are *excluded* from ring grouping rather than mis-grouped).

    The function is pure: no warehouse access, no I/O.
    """
    if not address or not address.strip():
        return None

    street = _norm_street(address)
    if not street:  # nothing survived (e.g. address was only punctuation/units)
        return None

    parts = [street]
    if city and city.strip():
        parts.append(_WS_RE.sub(" ", _PUNCT_RE.sub(" ", city.upper())).strip())
    if state and state.strip():
        parts.append(state.strip().upper())
    if zip_code and str(zip_code).strip():
        # Use the 5-digit ZIP; ignore the +4 which varies within a building.
        parts.append(str(zip_code).strip()[:5])

    return " | ".join(p for p in parts if p)
