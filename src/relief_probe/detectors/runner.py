"""Run all detectors over the loan population and persist to ``signals``."""

from __future__ import annotations

import duckdb

from relief_probe.detectors.base import Detector
from relief_probe.detectors.registry import all_detectors


def run_all(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Run every registered detector, replace ``signals``, return per-detector counts.

    Detectors are pure (they only read), so we collect all signals first and write
    once — the ``signals`` table always reflects exactly the last scoring run.
    """
    counts: dict[str, int] = {}
    collected: list = []
    for det in all_detectors():
        sigs = det.run(con)
        counts[det.detector_id] = len(sigs)
        collected.extend(sigs)

    con.execute("DELETE FROM signals")
    if collected:
        frame = Detector.signals_to_frame(collected)
        con.register("_signals_tmp", frame)
        con.execute(
            "INSERT INTO signals (loan_number, detector_id, score, evidence_json) "
            "SELECT loan_number, detector_id, score, evidence_json FROM _signals_tmp"
        )
        con.unregister("_signals_tmp")
    return counts
