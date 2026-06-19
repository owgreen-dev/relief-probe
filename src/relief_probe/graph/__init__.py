"""Multi-relational fraud-ring graph layer (Loop 4 — EXPLORATORY).

A loan-level graph that links loans by RELATIONAL structure — shared building
address, resolved-borrower identity, and name+amount+area look-alike similarity —
so that community/ring structure can be tested against prosecuted-fraud labels.

THESIS: fraud over these PPP loans is coordinated/relational, not row-wise (the
two project wins were both relational — LLM entity-resolution and a name+amount+
area similarity homophily test). The single-edge-type ``duplicate_address_ring``
(shared address ALONE) was already validated NULL because legitimate co-location
dominates; the BET here is that COMBINING edge types separates real rings from
benign clustering.

Everything in this package is LABEL-FREE: it never reads ``fraud_cases`` (or any
label table) to build the graph or compute a feature — only the validation SCRIPT
(``scripts/validate_ring_graph.py``) reads labels, and only to evaluate. NetworkX
is imported lazily behind the optional ``graph`` extra.
"""

from __future__ import annotations

from relief_probe.graph.build import build_loan_graph

__all__ = ["build_loan_graph"]
