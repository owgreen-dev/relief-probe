"""Error Level Analysis (ELA) feature extraction.

ELA re-saves an image as JPEG at a known quality and measures the per-pixel
difference from the original. Authentic regions compress uniformly; a spliced or
edited region has a different compression history, so it lights up at a different
error level. We reduce the ELA image to a small, scale-invariant feature vector for a
classifier — the *spatial unevenness* of the error (high inter-block variance) is the
key tamper signal.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageChops

#: Feature names, in the order ``ela_features`` returns them (stable for the model).
FEATURE_NAMES = (
    "ela_mean",
    "ela_std",
    "ela_max",
    "ela_p95",
    "ela_p99",
    "block_mean_std",   # std of per-block mean error -> spatial unevenness
    "block_max_ratio",  # hottest block mean / overall mean -> localized tamper
)


def ela_image(img: Image.Image, *, quality: int = 90) -> Image.Image:
    """Return the ELA difference image (RGB) for ``img`` at the given JPEG quality."""
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    resaved = Image.open(buf)
    return ImageChops.difference(rgb, resaved)


def ela_features(
    img: Image.Image, *, quality: int = 90, grid: int = 8
) -> np.ndarray:
    """Reduce an image's ELA map to the fixed feature vector in ``FEATURE_NAMES``."""
    diff = np.asarray(ela_image(img, quality=quality), dtype=np.float64)
    mag = diff.mean(axis=2) if diff.ndim == 3 else diff  # per-pixel error magnitude

    overall_mean = float(mag.mean())
    feats = [
        overall_mean,
        float(mag.std()),
        float(mag.max()),
        float(np.percentile(mag, 95)),
        float(np.percentile(mag, 99)),
    ]

    # Block statistics: average error within a grid x grid tiling.
    h, w = mag.shape
    block_means = []
    for r in range(grid):
        for c in range(grid):
            block = mag[
                r * h // grid : (r + 1) * h // grid,
                c * w // grid : (c + 1) * w // grid,
            ]
            if block.size:
                block_means.append(block.mean())
    block_means_arr = np.asarray(block_means, dtype=np.float64)
    feats.append(float(block_means_arr.std()))
    feats.append(
        float(block_means_arr.max() / overall_mean) if overall_mean > 0 else 0.0
    )
    return np.asarray(feats, dtype=np.float64)
