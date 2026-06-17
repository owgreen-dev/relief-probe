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
