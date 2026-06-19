"""name<->NAICS mismatch detector — does the business NAME fit its claimed INDUSTRY?

Domain rationale
----------------
The composite ranks on the *numbers* (dollars-per-job, cap exceedance, duplicate
funding) and never reads the *meaning* of ``borrower_name`` or the NAICS industry.
That text is unused, discriminative information: a loan whose borrower name is a poor
semantic fit for its declared industry ("Elite Nail Spa LLC" coded as Construction)
is exactly the world-knowledge mismatch our LLM-as-judge tried (and, as a saturated
0-3 score, failed) to capture (see docs/LLM_RESEARCH.md). Here we capture it as a
**continuous** feature instead.

How
---
We embed each business name and every candidate NAICS title with a shared
:class:`~relief_probe.embeddings.Embedder`, then for each loan score the *declared*
industry's similarity against ALL industries as a **normalized gap**:

    mismatch = (best_similarity - declared_similarity) / (best - worst)

so a name whose declared industry is its *best* match scores 0, and one whose
declared industry is as bad as the *worst* candidate scores 1 — "the declared
industry is this far down from the best fit, relative to the full spread." Using the
relative gap (not a raw cosine) sidesteps the anisotropy/saturation that sinks fixed
cosine thresholds on short strings ("Semantics at an Angle", 2504.16318), and unlike
a strict rank it is robust to ties (a degenerate all-equal row scores 0, not noise).

Industry titles
---------------
By default we rank against the canonical **2-digit NAICS sector** titles (bundled
below — official Census sector names, not invented), so the detector runs with zero
downloads at sector granularity. Load finer titles into the ``naics_titles`` table
(``relief-probe ingest-naics PATH``) and they're used instead, at whatever
granularity they're keyed.

Honesty (mirrors the vision tab / LLM triage paths)
---------------------------------------------------
The default :class:`~relief_probe.embeddings.HashingEmbedder` is a *lexical* proxy
(shared substrings), so by itself it is a weak, noisy signal — it proves the
machinery. The real *semantic* signal ("nail spa" ~ "personal care" with no shared
letters) needs the ``embeddings`` extra (:class:`SentenceTransformerEmbedder`).
EXPLORATORY by default (SIGN-010): registered out of the production composite,
pending real-data validation against the prosecuted labels. Read-only; ``[]`` on
empty input.
"""

from __future__ import annotations

import duckdb
import numpy as np

from relief_probe.detectors.base import Detector, Signal
from relief_probe.embeddings import Embedder, HashingEmbedder

#: Canonical Census 2-digit NAICS sector titles (the official scheme; the 31-33 /
#: 44-45 / 48-49 ranges each map to one sector). Used when ``naics_titles`` is empty.
NAICS_SECTOR_TITLES: dict[str, str] = {
    "11": "Agriculture, Forestry, Fishing and Hunting",
    "21": "Mining, Quarrying, and Oil and Gas Extraction",
    "22": "Utilities",
    "23": "Construction",
    "31": "Manufacturing",
    "32": "Manufacturing",
    "33": "Manufacturing",
    "42": "Wholesale Trade",
    "44": "Retail Trade",
    "45": "Retail Trade",
    "48": "Transportation and Warehousing",
    "49": "Transportation and Warehousing",
    "51": "Information",
    "52": "Finance and Insurance",
    "53": "Real Estate and Rental and Leasing",
    "54": "Professional, Scientific, and Technical Services",
    "55": "Management of Companies and Enterprises",
    "56": "Administrative and Support and Waste Management and Remediation Services",
    "61": "Educational Services",
    "62": "Health Care and Social Assistance",
    "71": "Arts, Entertainment, and Recreation",
    "72": "Accommodation and Food Services",
    "81": "Other Services (except Public Administration)",
    "92": "Public Administration",
}


def mismatch_score(row: np.ndarray, declared_idx: int) -> float:
    """Normalized gap of the declared industry below the best-matching one, in [0, 1].

    ``(best - declared) / (best - worst)``: 0 when the declared industry IS the best
    fit for the name, →1 when it is the worst. A degenerate row (all similarities
    equal — e.g. a name that matches nothing) has zero spread and scores 0, so "no
    information" never masquerades as a mismatch.
    """
    best = float(row.max())
    worst = float(row.min())
    spread = best - worst
    if spread <= 1e-9:
        return 0.0
    return (best - float(row[declared_idx])) / spread


class NaicsNameMismatchDetector(Detector):
    detector_id = "naics_name_mismatch"
    summary = (
        "Business name is a poor semantic fit for its declared NAICS industry "
        "(continuous embedding-mismatch percentile)."
    )

    def __init__(
        self,
        *,
        embedder: Embedder | None = None,
        min_mismatch: float = 0.6,
    ) -> None:
        #: Embedder for names + industry titles. Default is the offline lexical
        #: HashingEmbedder; pass a SentenceTransformerEmbedder for the real signal.
        self.embedder = embedder or HashingEmbedder()
        #: Flag a loan when its normalized mismatch gap is at least this (0.6 => the
        #: declared industry sits >=60% of the way from the best fit to the worst).
        self.min_mismatch = min_mismatch

    def _title_map(self, con: duckdb.DuckDBPyConnection) -> dict[str, str]:
        """code -> industry title, from the naics_titles table or the sector default."""
        try:
            rows = con.execute(
                "SELECT naics_code, title FROM naics_titles "
                "WHERE naics_code IS NOT NULL AND title IS NOT NULL"
            ).fetchall()
        except duckdb.CatalogException:
            rows = []
        if rows:
            return {str(code): str(title) for code, title in rows}
        return dict(NAICS_SECTOR_TITLES)

    def _resolve_title(
        self, naics_code: str, title_map: dict[str, str]
    ) -> str | None:
        """Title for a loan's code: exact match, else its 2-digit sector."""
        if naics_code in title_map:
            return title_map[naics_code]
        return title_map.get(naics_code[:2])

    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        title_map = self._title_map(con)
        # Stable list of distinct candidate titles to rank each name against.
        titles = sorted(set(title_map.values()))
        if not titles:
            return []

        loans = con.execute(
            """
            SELECT loan_number, borrower_name, naics_code
            FROM loans
            WHERE borrower_name IS NOT NULL AND borrower_name <> ''
              AND naics_code IS NOT NULL AND naics_code <> ''
            """
        ).fetchall()
        if not loans:
            return []

        # Resolve each loan's declared title; drop loans with no resolvable industry.
        resolved: list[tuple[str, str, str]] = []  # (loan_number, name, title)
        for loan_number, name, naics_code in loans:
            title = self._resolve_title(str(naics_code), title_map)
            if title is not None:
                resolved.append((str(loan_number), str(name), title))
        if not resolved:
            return []

        title_index = {t: i for i, t in enumerate(titles)}
        title_vecs = self.embedder.embed(titles)  # (n_titles, dim), L2-normalized

        # Embed DISTINCT names once, then map each loan back to its name's row.
        distinct_names = sorted({name for _, name, _ in resolved})
        name_index = {n: i for i, n in enumerate(distinct_names)}
        name_vecs = self.embedder.embed(distinct_names)  # (n_names, dim)

        # Cosine sims: (n_names, n_titles). Rows are L2-normalized, so dot = cosine.
        sims = name_vecs @ title_vecs.T
        n_titles = len(titles)

        signals: list[Signal] = []
        for loan_number, name, title in resolved:
            row = sims[name_index[name]]
            declared_idx = title_index[title]
            mismatch = round(mismatch_score(row, declared_idx), 4)
            if mismatch < self.min_mismatch:
                continue
            best_i = int(np.argmax(row))
            signals.append(
                Signal(
                    loan_number=loan_number,
                    detector_id=self.detector_id,
                    score=mismatch,
                    evidence={
                        "declared_title": title,
                        "declared_similarity": round(float(row[declared_idx]), 4),
                        "best_match_title": titles[best_i],
                        "best_similarity": round(float(row[best_i]), 4),
                        "mismatch_gap": mismatch,
                        "n_titles": n_titles,
                        "embedder": type(self.embedder).__name__,
                    },
                )
            )
        return signals
