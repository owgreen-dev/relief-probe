"""Entity-resolution key → "the same borrower at the same building".

Borrowers spread fraud across many loans under slightly different name spellings
and address formats. :func:`entity_key` composes the two existing normalizers —
:func:`relief_probe.labels.resolve.normalize_name` (corporate-suffix-stripped name
key) and :func:`relief_probe.detectors._address.normalize_address` (building-level
address key) — into one canonical key so that the same borrower at one building
groups together regardless of formatting.

We deliberately key on **name + building** (not name alone): a bare normalized
name collides across unrelated "ABC TRUCKING LLC" filings in different states, and
a bare address collides across unrelated tenants of one building. Requiring both
keeps the resolved entity precise.

False-positive modes (document, don't hide)
-------------------------------------------
* Two genuinely different businesses sharing a name *and* a building (rare, e.g. a
  rebrand) would merge.
* A borrower that legitimately moved buildings between draws would NOT merge — this
  key is conservative (precision over recall), matching the resolver's philosophy.

The function is pure: no warehouse access, no I/O.
"""

from __future__ import annotations

from relief_probe.detectors._address import normalize_address
from relief_probe.labels.resolve import normalize_name


def entity_key(
    borrower_name: str | None,
    address: str | None,
    city: str | None = None,
    state: str | None = None,
    zip_code: str | None = None,
) -> str | None:
    """Canonical key for one borrower at one building, or ``None`` if unkeyable.

    Combines the normalized borrower name with the normalized building address.
    Returns ``None`` when either the name or the address normalizes to blank, so
    unkeyable loans are *excluded* from grouping rather than mis-merged.
    """
    name = normalize_name(borrower_name)
    if not name:
        return None
    addr = normalize_address(address, city, state, zip_code)
    if not addr:
        return None
    return f"{name} @ {addr}"
