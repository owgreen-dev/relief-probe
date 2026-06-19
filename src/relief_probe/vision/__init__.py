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

SYNTHETIC_NOTE = (
    "SYNTHETIC DEMO — methodology/plumbing only, NOT a validated capability. "
    "The ELA detector here is trained and evaluated on SYNTHETIC spliced images "
    "(clean patches recompressed at a lower JPEG quality), which are trivially "
    "separable. High accuracy on this synthetic task proves the wiring works; it "
    "does NOT imply real-world document-forgery detection. The real anchor datasets "
    "(IDNet ID-forgery, 'Find it again!' receipt-tamper) are wired in but not run "
    "here. Treat any score as a screening aid, never proof — see RESPONSIBLE_USE.md."
)

__all__ = ["SYNTHETIC_NOTE", "ela_features", "ela_image"]
