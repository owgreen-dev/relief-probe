"""Detector registry.

New detectors register here. Keeping a single list lets the CLI, scoring, and
benchmark iterate over "all detectors" without import gymnastics.

Live: naics_cohort_outlier (relative $/job), payroll_cap_exceedance (absolute
$/job), duplicate_address_ring (independent link-analysis / co-location signal).
Planned: proceeds_anomaly, lender_concentration.
"""

from __future__ import annotations

from relief_probe.detectors.base import Detector
from relief_probe.detectors.duplicate_address_ring import DuplicateAddressRingDetector
from relief_probe.detectors.naics_cohort_outlier import NaicsCohortOutlierDetector
from relief_probe.detectors.payroll_cap import PayrollCapExceedanceDetector


def all_detectors() -> list[Detector]:
    """Instantiate every registered detector with default parameters."""
    return [
        NaicsCohortOutlierDetector(),
        PayrollCapExceedanceDetector(),
        DuplicateAddressRingDetector(),
    ]


def get_detector(detector_id: str) -> Detector:
    for d in all_detectors():
        if d.detector_id == detector_id:
            return d
    raise KeyError(f"unknown detector: {detector_id!r}")
