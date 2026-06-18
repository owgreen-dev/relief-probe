"""Detector registry.

New detectors register here. Keeping a single list lets the CLI, scoring, and
benchmark iterate over "all detectors" without import gymnastics.

Live in the default composite (validated to carry lift): naics_cohort_outlier
(relative $/job), payroll_cap_exceedance (absolute $/job).

Exploratory (built + tested, but OUT of the default composite) — held here until a
human validates real-data lift against the prosecuted-fraud labels and manually
promotes any that show it:

* ``duplicate_address_ring`` — an independent co-location / link-analysis signal
  that, on the real warehouse, shows ~base-rate (no) lift at every ring-size
  threshold and dilutes the composite mid-tail (see NEXT_STEPS H6).
* ``amount_anomaly`` — per-loan round-number + payroll-cap "bunching" tells of a
  fabricated/reverse-engineered loan amount (Loop 1).
* ``multiple_funded_loans`` — entity resolution → borrowers holding more funded
  loans than the one-per-draw PPP/PPS rule allows (Loop 1).

Kept for investigation/evidence and opt-in scoring; not in the headline ranking.

Planned: proceeds_anomaly, lender_concentration.
"""

from __future__ import annotations

from relief_probe.detectors.amount_anomaly import AmountAnomalyDetector
from relief_probe.detectors.base import Detector
from relief_probe.detectors.duplicate_address_ring import DuplicateAddressRingDetector
from relief_probe.detectors.multiple_funded_loans import MultipleFundedLoansDetector
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

    See the module docstring: these are independent signals with no validated
    real-data lift yet, so they stay out of the headline ranking until a human
    promotes them. Run them explicitly (e.g.
    ``run_all(con, detectors=[*all_detectors(), *exploratory_detectors()])``) for
    ad-hoc investigation.
    """
    return [
        DuplicateAddressRingDetector(),
        AmountAnomalyDetector(),
        MultipleFundedLoansDetector(),
    ]


def get_detector(detector_id: str) -> Detector:
    for d in (*all_detectors(), *exploratory_detectors()):
        if d.detector_id == detector_id:
            return d
    raise KeyError(f"unknown detector: {detector_id!r}")
