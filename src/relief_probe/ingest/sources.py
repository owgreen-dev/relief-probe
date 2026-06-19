"""Registry of public data sources (verified June 2026).

data.sba.gov is a **CKAN** instance. Resource download URLs can be regenerated on
republish, so we never hardcode them — we resolve the current CSV links at ingest
time from the CKAN ``package_show`` API (match the ``ppp-foia`` dataset, read its
``resources[].url``). See ``download.resolve_ppp_resources``.

The PPP FOIA release splits into 13 CSVs that share ONE schema:
  * ``public_150k_plus_*.csv``      — loans >= $150k (~1M rows; big-dollar fraud)
  * ``public_up_to_150k_1..12_*.csv`` — loans < $150k (~10.5M rows, ~8 GB total)

So a "slice" selects which files to pull; column mapping is identical for all.
"""

from __future__ import annotations

CKAN_API_URL = "https://data.sba.gov/api/3/action/package_show"
PPP_DATASET_ID = "ppp-foia"

# slice name -> substring that must appear in the CSV filename to be included.
SLICES: dict[str, tuple[str, ...]] = {
    "150k_plus": ("150k_plus",),
    "under_150k": ("up_to_150k",),
    "all": ("150k_plus", "up_to_150k"),
}

DEFAULT_SLICE = "150k_plus"

# Census ZIP Business Patterns (ZBP) — establishment counts by ZIP x NAICS.
# Used to detect PPP loan over-density vs the real number of businesses in a cell.
# The annual ZBP files live under the Census "datasets" tree; the canonical landing
# page is documented here rather than hardcoded into the loader, because the per-year
# file URLs change and the real download + ingest is a MANUAL post-loop step. The
# loader (ingest/establishments.load_zbp_csv) takes a LOCAL path — no network.
ZBP_LANDING_URL = "https://www.census.gov/programs-surveys/cbp/data/datasets.html"
ZBP_DATASET_NOTE = (
    "Census ZIP Business Patterns (ZBP): establishments by ZIP x NAICS. "
    "Pick a vintage close to (but predating) the PPP program; note the ZBP vintage "
    "predates 2020 growth, a known false-positive mode. Verified as-of June 2026."
)
