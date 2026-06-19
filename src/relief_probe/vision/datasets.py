"""Datasets for the document-authenticity model.

Two paths:
  * ``make_synthetic`` — generate clean + copy-move-tampered JPEGs locally, so the
    pipeline (and tests) run with zero downloads. Deterministic given ``seed``.
  * real anchors — resolvers/notes for the public forgery datasets. "Find it again!"
    is a single direct zip; IDNet is large (CC0 on Zenodo/HF). We don't auto-download
    these (size/scope); the model trains on any folder of ``authentic/`` + ``forged/``.

Honest gap (state it): no public fake-paystub/bank-statement dataset exists — real
fraudulent financial docs are never released — so financial-doc tamper detection here
is demonstrated on synthesized/edited images, not authentic leaked fakes.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

# Real anchor datasets (verified June 2026) — see research notes. Not auto-downloaded.
FIND_IT_AGAIN_ZIP = "http://l3i-share.univ-lr.fr/2023Finditagain/findit2.zip"
IDNET_INFO = (
    "IDNet synthetic ID-forgery dataset — CC0, ~600K-840K images (~400 GB). "
    "Zenodo: https://zenodo.org/records/13855175 ; HF: cactuslab/IDNet-2025. "
    "Too large to auto-fetch; download a subset and point --data-dir at it."
)


def _random_image(rng: np.random.Generator, size: int = 128) -> Image.Image:
    """A smooth, photo-like base image (gradient + low-freq noise + a shape)."""
    yy, xx = np.mgrid[0:size, 0:size] / size
    base = np.stack(
        [
            128 + 100 * np.sin(2 * np.pi * (xx + rng.random())),
            128 + 100 * np.cos(2 * np.pi * (yy + rng.random())),
            128 + 80 * np.sin(2 * np.pi * (xx + yy + rng.random())),
        ],
        axis=-1,
    )
    base += rng.normal(0, 6, base.shape)
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")


def _tamper(img: Image.Image, rng: np.random.Generator) -> Image.Image:
    """Splice a patch with a DIFFERENT compression history onto the image.

    We crop a region, recompress it hard (JPEG q25), and paste it back elsewhere. The
    pasted region now carries a distinct compression history, so under ELA it lights up
    at a different error level — the classic double-compression splice signal (a real
    copy-move from the same image at the same quality would be near-invisible to ELA).
    """
    out = img.copy()
    w, h = out.size
    pw, ph = w // 4, h // 4
    sx, sy = int(rng.integers(0, w - pw)), int(rng.integers(0, h - ph))
    dx, dy = int(rng.integers(0, w - pw)), int(rng.integers(0, h - ph))
    patch = out.crop((sx, sy, sx + pw, sy + ph))
    buf = io.BytesIO()
    patch.save(buf, "JPEG", quality=25)
    buf.seek(0)
    out.paste(Image.open(buf), (dx, dy))
    return out


def make_synthetic(
    out_dir: Path | str, *, n_per_class: int = 60, seed: int = 0, size: int = 128
) -> dict[str, int]:
    """Write ``authentic/`` and ``forged/`` JPEGs under ``out_dir``. Deterministic."""
    rng = np.random.default_rng(seed)
    out = Path(out_dir)
    (out / "authentic").mkdir(parents=True, exist_ok=True)
    (out / "forged").mkdir(parents=True, exist_ok=True)
    for i in range(n_per_class):
        base = _random_image(rng, size)
        # Authentic: a clean JPEG (single compression history).
        base.save(out / "authentic" / f"img_{i:04d}.jpg", "JPEG", quality=92)
        # Forged: same base, copy-move tampered, then re-saved.
        _tamper(base, rng).save(out / "forged" / f"img_{i:04d}.jpg", "JPEG", quality=92)
    return {"authentic": n_per_class, "forged": n_per_class}


def iter_labeled_images(data_dir: Path | str) -> list[tuple[Path, int]]:
    """List ``(path, label)`` from ``authentic/`` (0) and ``forged/`` (1) subdirs."""
    root = Path(data_dir)
    items: list[tuple[Path, int]] = []
    for sub, label in (("authentic", 0), ("forged", 1)):
        d = root / sub
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif"}:
                    items.append((p, label))
    return items
