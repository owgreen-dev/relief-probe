"""Detector registry.

New detectors register here. Keeping a single list lets the CLI, scoring, and
benchmark iterate over "all detectors" without import gymnastics.

Detectors land in M2 (see NEXT_STEPS.md). Planned launch set, all over public
PPP loan fields:
  - naics_cohort_outlier   loan $ / job reported far above NAICS×state peers
  - threshold_bunching     loan amount clustered just under a rule threshold
                           (e.g. the $20,833 sole-proprietor / $2M caps)
  - proceeds_anomaly       payroll-proceed share implausible vs jobs/term
  - duplicate_identity     same address / borrower across many loans (ring signal)
  - lender_concentration   originating lender's book skewed to flagged loans
"""

from __future__ import annotations

from relief_probe.detectors.base import Detector


def all_detectors() -> list[Detector]:
    """Instantiate every registered detector with default parameters."""
    return []


def get_detector(detector_id: str) -> Detector:
    for d in all_detectors():
        if d.detector_id == detector_id:
            return d
    raise KeyError(f"unknown detector: {detector_id!r}")
