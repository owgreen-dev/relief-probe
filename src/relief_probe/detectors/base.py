"""Detector output contract and base class.

Output contract (shared by every detector):

    (loan_number, detector_id, score, evidence_json)

written into the unified ``signals`` table. ``score`` is intended to be
comparable *within a detector* (typically a robust z-score or a normalized
intensity), not necessarily across detectors — composite aggregation lives in
``relief_probe.scoring``.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import duckdb
import pandas as pd


@dataclass(frozen=True)
class Signal:
    """One detector firing on one loan."""

    loan_number: str
    detector_id: str
    score: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "loan_number": self.loan_number,
            "detector_id": self.detector_id,
            "score": self.score,
            "evidence_json": json.dumps(self.evidence, default=str, sort_keys=True),
        }


class Detector(ABC):
    """Base class for all detectors.

    Subclasses set ``detector_id`` and implement :meth:`run`, which reads from
    the warehouse connection and returns a list of :class:`Signal`.
    """

    detector_id: str = "abstract"
    #: One-line description used in the README detector catalog.
    summary: str = ""

    @abstractmethod
    def run(self, con: duckdb.DuckDBPyConnection) -> list[Signal]:
        """Compute signals over the loan population. Must not write to the warehouse."""
        raise NotImplementedError

    @staticmethod
    def signals_to_frame(signals: list[Signal]) -> pd.DataFrame:
        return pd.DataFrame([s.to_row() for s in signals])
