"""Run all detectors over the loan population and persist to ``signals``."""

from __future__ import annotations

import duckdb

from relief_probe.detectors.base import Detector
from relief_probe.detectors.registry import all_detectors


def run_all(
    con: duckdb.DuckDBPyConnection,
    detectors: list[Detector] | None = None,
) -> dict[str, int]:
    """Run detectors, replace ``signals``, return per-detector counts.

    Defaults to the production set (``all_detectors()``). Pass an explicit
    ``detectors`` list to include exploratory ones (e.g. the duplicate-address ring
    detector) for ad-hoc scoring without putting them in the headline composite.

    Detectors are pure (they only read), so we collect all signals first and write
    once — the ``signals`` table always reflects exactly the last scoring run.
    """
    dets = detectors if detectors is not None else all_detectors()
    counts: dict[str, int] = {}
    collected: list = []
    for det in dets:
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
