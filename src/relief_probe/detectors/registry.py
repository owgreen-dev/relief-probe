"""Detector registry.

New detectors register here. Keeping a single list lets the CLI, scoring, and
benchmark iterate over "all detectors" without import gymnastics.

Live in the default composite (validated to carry lift): naics_cohort_outlier
(relative $/job), payroll_cap_exceedance (absolute $/job).

Exploratory (built + tested, but OUT of the default composite): duplicate_address_ring
— a genuinely independent co-location / link-analysis signal that, on the real
warehouse, shows ~base-rate (no) lift against the prosecuted-fraud labels at every
ring-size threshold and dilutes the composite mid-tail (see NEXT_STEPS H6). Kept for
investigation/evidence and opt-in scoring; not in the headline ranking.

Planned: proceeds_anomaly, lender_concentration.
"""

from __future__ import annotations

from relief_probe.detectors.base import Detector
from relief_probe.detectors.duplicate_address_ring import DuplicateAddressRingDetector
from relief_probe.detectors.naics_cohort_outlier import NaicsCohortOutlierDetector
from relief_probe.detectors.payroll_cap import PayrollCapExceedanceDetector


def all_detectors() -> list[Detector]:
    """Production detectors used by the default score/composite/benchmark."""
    return [
        NaicsCohortOutlierDetector(),
        PayrollCapExceedanceDetector(),
    ]


def exploratory_detectors() -> list[Detector]:
    """Detectors built + tested but excluded from the default composite.

    See the module docstring: duplicate_address_ring is independent but has no
    validated lift, so it stays out of the headline ranking. Run it explicitly
    (e.g. ``run_all(con, detectors=[*all_detectors(), *exploratory_detectors()])``)
    for ad-hoc investigation.
    """
    return [
        DuplicateAddressRingDetector(),
    ]


def get_detector(detector_id: str) -> Detector:
    for d in (*all_detectors(), *exploratory_detectors()):
        if d.detector_id == detector_id:
            return d
    raise KeyError(f"unknown detector: {detector_id!r}")
