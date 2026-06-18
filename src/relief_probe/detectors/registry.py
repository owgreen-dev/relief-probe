"""Detector registry.

New detectors register here. Keeping a single list lets the CLI, scoring, and
benchmark iterate over "all detectors" without import gymnastics.

Live in the default composite (validated to carry lift): naics_cohort_outlier
(relative $/job), payroll_cap_exceedance (absolute $/job), multiple_funded_loans
(entity resolution → borrowers holding more funded loans than the one-per-draw
PPP/PPS rule allows; Loop 1 — promoted after real-data validation showed genuine
independent lift, ~18-21x@500-1000, Jaccard <0.01 vs the $/job detectors).

Exploratory (built + tested, but OUT of the default composite) — held here until a
human validates real-data lift against the prosecuted-fraud labels and manually
promotes any that show it:

* ``duplicate_address_ring`` — an independent co-location / link-analysis signal
  that, on the real warehouse, shows ~base-rate (no) lift at every ring-size
  threshold and dilutes the composite mid-tail (see NEXT_STEPS H6).
* ``amount_anomaly`` — per-loan round-number + payroll-cap "bunching" tells of a
  fabricated/reverse-engineered loan amount (Loop 1); validated WEAK (flags ~13%
  of the slice, ~0 lift through k=1000), so it stays out of the composite.
* ``establishment_overcount`` — Census ZBP density signal (Loop 2): more PPP loans
  in a ZIP x NAICS cell than there are business establishments there. VALIDATED on the
  real warehouse: it has weak *standalone* independent lift (≈18x@500, recall ~1.8%
  @5000; Jaccard <0.01 vs the other detectors), BUT promoting it does NOT improve the
  composite (the few prosecuted loans it catches are already caught at those ranks;
  +1 hit @2000, otherwise identical), so it stays exploratory. Needs a 5-digit ZIP
  truncation to join real ZIP+4 loans (fixed in the detector).
* ``lender_concentration`` — an UNSUPERVISED, label-free LENDER signal (Loop 3): per
  originating lender, the RATE of program-rule-implausible ($/job over the per-employee
  payroll cap) loans, robust-z scored across lenders; flags every loan from a lender in
  the extreme upper tail (the GAO "a few fintech auto-approval lenders" motivation).
  VALIDATED on the real warehouse: ZERO lift (0 prosecuted hits in the top 5,000;
  independent of the other detectors but uncorrelated with the labels — high-cap-busting
  lenders aren't where prosecuted fraud sits), so it stays exploratory.

Kept for investigation/evidence and opt-in scoring; not in the headline ranking.

Planned: proceeds_anomaly.
"""

from __future__ import annotations

from relief_probe.detectors.amount_anomaly import AmountAnomalyDetector
from relief_probe.detectors.base import Detector
from relief_probe.detectors.duplicate_address_ring import DuplicateAddressRingDetector
from relief_probe.detectors.establishment_overcount import EstablishmentOvercountDetector
from relief_probe.detectors.lender_concentration import LenderConcentrationDetector
from relief_probe.detectors.multiple_funded_loans import MultipleFundedLoansDetector
from relief_probe.detectors.naics_cohort_outlier import NaicsCohortOutlierDetector
from relief_probe.detectors.payroll_cap import PayrollCapExceedanceDetector


def all_detectors() -> list[Detector]:
    """Production detectors used by the default score/composite/benchmark."""
    return [
        NaicsCohortOutlierDetector(),
        PayrollCapExceedanceDetector(),
        MultipleFundedLoansDetector(),
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
        EstablishmentOvercountDetector(),
        LenderConcentrationDetector(),
    ]


def get_detector(detector_id: str) -> Detector:
    for d in (*all_detectors(), *exploratory_detectors()):
        if d.detector_id == detector_id:
            return d
    raise KeyError(f"unknown detector: {detector_id!r}")
