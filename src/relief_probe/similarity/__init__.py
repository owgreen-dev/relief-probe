"""Similar-case retrieval — "show me loans like this one" for investigation.

A hybrid (keyword + semantic) similar-loan finder. NOT a detector: it emits no
``signals``, never joins the detector registry, and makes no fraud prediction. It
answers an *investigator's* question — which other loans resemble this one (by
business name and structured proximity) — to surface rings/templates and ground an
explanation.

Built on the session's finding that LLMs/embeddings fail at *prediction* over these
loans (they look plausible on their face) but excel at *retrieval/similarity* — so
this puts the embeddings where they actually work. Blocking-first (same area + dollar
band + a dollar threshold) keeps it cheap: per query we embed only a small candidate
pool, never the millions of names in the warehouse.

A resemblance is a *lead for review*, never proof — see ``RESPONSIBLE_USE.md``.
"""

from __future__ import annotations

from relief_probe.similarity.core import SIMILARITY_DISCLAIMER, find_similar

__all__ = ["find_similar", "SIMILARITY_DISCLAIMER"]
