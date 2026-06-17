"""Resolve current SBA download URLs and fetch source files.

data.sba.gov (CKAN) can regenerate resource URLs on republish, so we resolve the
current CSV links from the ``package_show`` API at ingest time rather than
hardcoding them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from relief_probe.ingest.sources import (
    CKAN_API_URL,
    PPP_DATASET_ID,
    SLICES,
)


def resolve_ppp_resources(
    slice_name: str, *, timeout: int = 60
) -> list[dict[str, str]]:
    """Return ``[{name, url}, ...]`` for the CSV files in the requested slice.

    Resolves live from the CKAN ``package_show`` API; filenames are matched to
    the slice's substrings (see ``sources.SLICES``).
    """
    if slice_name not in SLICES:
        raise KeyError(
            f"unknown slice {slice_name!r} (choices: {', '.join(SLICES)})"
        )
    substrings = SLICES[slice_name]
    resp = requests.get(
        CKAN_API_URL, params={"id": PPP_DATASET_ID}, timeout=timeout
    )
    resp.raise_for_status()
    resources = resp.json()["result"]["resources"]

    out: list[dict[str, str]] = []
    for r in resources:
        url = r.get("url", "")
        if not url.lower().endswith(".csv"):
            continue
        fname = url.rsplit("/", 1)[-1]
        if any(sub in fname.lower() for sub in substrings):
            out.append({"name": fname, "url": url})
    if not out:
        raise LookupError(
            f"no CSV resources matched slice {slice_name!r} in the CKAN package"
        )
    out.sort(key=lambda d: d["name"])
    return out


def download_file(
    url: str, dest: Path, *, chunk: int = 1 << 20, timeout: int = 600
) -> Path:
    """Stream a URL to ``dest`` (creating parents). Returns the path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for c in r.iter_content(chunk_size=chunk):
                if c:
                    f.write(c)
    return dest


def sha256_of(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
