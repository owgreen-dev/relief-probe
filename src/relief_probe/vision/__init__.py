"""Document-authenticity (vision) layer — Layer 5.

A supporting-document forgery check to pair with the tabular loan-fraud core: PPP
fraud is overwhelmingly fabricated supporting documents (fake payroll, IDs, bank
statements). This module detects image tampering with **Error Level Analysis (ELA)**
features + a light classifier — CPU-friendly, no GPU or 400 GB download required.

Honest scope (see RESPONSIBLE_USE.md): ELA flags *recompression/splice artifacts*,
not "fraud". It is a screening aid, not proof. Real anchor datasets (IDNet ID-forgery,
"Find it again!" receipt-tamper) are wired in ``datasets`` for when you want them; the
``vision`` extra (pillow + scikit-learn) keeps this out of the core install.
"""

from __future__ import annotations

from relief_probe.vision.ela import ela_features, ela_image

__all__ = ["ela_features", "ela_image"]
