"""Label construction: scrape enforcement records, resolve them to loans.

This is the project's differentiating work — building forward-validation labels
from public DOJ/SBA-OIG enforcement, then entity-resolving them back to loan
records. Labels are positive-unlabeled (PU): see RESPONSIBLE_USE.md.
"""

from __future__ import annotations

from relief_probe.labels.doj import fetch_doj_cases, iter_doj_pages, store_releases

__all__ = ["fetch_doj_cases", "iter_doj_pages", "store_releases"]
