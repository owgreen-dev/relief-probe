"""KYB (know-your-business) external-evidence layer — Loop 5.

Tier-B of the KYB thesis: verify a borrower against an EXTERNAL registry
(does it exist? registered when? real address?) — the "bring NEW information"
angle relief-probe has found AI/ML actually wins at, vs row-wise prediction over
the loan's own fields.

DETERMINISTIC-FIRST: everything here imports and tests with NO network and NO
token. :class:`StubProvider` is the default in tests; :class:`OpenCorporatesProvider`
lazily imports ``requests`` inside its methods and raises a clear error when
``OPENCORPORATES_TOKEN`` is unset. Every output is a LEAD for review, never proof —
see ``RESPONSIBLE_USE.md`` for the FCRA-adjacency / ToS / defamation surface.
"""

from __future__ import annotations

from relief_probe.kyb.provider import (
    EvidenceProvider,
    KybEvidence,
    OpenCorporatesProvider,
    StubProvider,
)

__all__ = [
    "EvidenceProvider",
    "KybEvidence",
    "OpenCorporatesProvider",
    "StubProvider",
]
