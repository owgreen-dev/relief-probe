"""Lightweight text embeddings for semantic detectors (deterministic-first).

The :class:`Embedder` protocol returns L2-normalized row vectors so a dot product
is a cosine similarity. Two implementations, mirroring the project's
deterministic-first / key-gated discipline:

* :class:`HashingEmbedder` (default) — a dependency-free, deterministic char-n-gram
  *hashing* embedder. It is a **lexical proxy**: it captures literal substring
  overlap between two strings (e.g. "nail" in both a business name and a "nail
  salon" title), not true synonymy. It needs no model, no download, and no network,
  so every semantic detector + its tests run offline and reproducibly. Use a stable
  hash (``hashlib``), never the salted builtin ``hash()``, so vectors are identical
  across processes.
* :class:`SentenceTransformerEmbedder` — real *semantic* embeddings via a local
  sentence-transformers model, behind the optional ``embeddings`` extra. This is
  the upgrade that understands "nail spa" ≈ "personal care" without shared letters;
  imported lazily so the core env never needs torch.

The honest framing (cf. the vision tab and the LLM triage path): the default
HashingEmbedder proves the machinery and gives a weak lexical signal; the real
semantic signal needs the ``embeddings`` extra.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    """Maps texts to an ``(n, dim)`` array of L2-normalized row vectors."""

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalize; zero rows stay zero (cosine with them is 0)."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class HashingEmbedder:
    """Deterministic char-n-gram hashing embedder — offline, no deps, no model.

    Each text is lowercased and decomposed into character n-grams (default 3–5);
    every n-gram is hashed (stable ``md5``) to a column with a sign, accumulated,
    then the row is L2-normalized. A cheap lexical proxy for semantic similarity:
    strings sharing substrings land near each other. No torch, no network — the
    default so semantic detectors are testable in the core env.
    """

    def __init__(self, *, dim: int = 256, ngram: tuple[int, int] = (3, 5)) -> None:
        self.dim = dim
        self.ngram = ngram

    def _hash(self, gram: str) -> tuple[int, float]:
        # Stable across processes (builtin hash() is salted by PYTHONHASHSEED).
        digest = hashlib.md5(gram.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % self.dim
        sign = 1.0 if digest[4] & 1 else -1.0
        return idx, sign

    def embed(self, texts: list[str]) -> np.ndarray:
        lo, hi = self.ngram
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            s = (text or "").lower().strip()
            if not s:
                continue
            padded = f" {s} "  # word-boundary grams matter for short names
            for n in range(lo, hi + 1):
                for i in range(len(padded) - n + 1):
                    idx, sign = self._hash(padded[i : i + n])
                    out[row, idx] += sign
        return _l2_normalize(out)


class Model2VecEmbedder:
    """Torch-free semantic embeddings via model2vec (the ``embeddings-lite`` extra).

    model2vec serves *static* distilled embeddings: a precomputed token-embedding
    table looked up and pooled in pure NumPy — no torch, no GPU, no neural-net
    forward pass. The model is ~30 MB and encoding is near-instant on CPU, which
    makes it the right semantic option on a machine without a GPU (and far lighter
    than the sentence-transformers/torch stack). Genuinely semantic (distilled from
    a real sentence encoder), just lower-fidelity than a live transformer. Lazily
    imported; a missing extra raises a clear, actionable error.
    """

    def __init__(self, *, model_name: str = "minishlab/potion-base-8M") -> None:
        self.model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from model2vec import StaticModel
        except ImportError as exc:  # pragma: no cover - only without the extra
            raise RuntimeError(
                "Torch-free semantic embeddings need the `embeddings-lite` extra. "
                "Install it with `uv sync --extra embeddings-lite`, or use the "
                "default HashingEmbedder (a lexical proxy)."
            ) from exc
        self._model = StaticModel.from_pretrained(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        model = self._ensure_model()
        vecs = np.asarray(model.encode(texts), dtype=np.float32)
        return _l2_normalize(vecs)


class SentenceTransformerEmbedder:
    """Real semantic embeddings via sentence-transformers (the ``embeddings`` extra).

    Lazily imports ``sentence_transformers`` and loads a small local model
    (default ``BAAI/bge-small-en-v1.5``); a missing extra raises a clear, actionable
    error. Output is L2-normalized so a dot product is cosine similarity.
    """

    def __init__(self, *, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self.model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - only without the extra
            raise RuntimeError(
                "Semantic embeddings need the `embeddings` extra. Install it with "
                "`uv sync --extra embeddings`, or use the default HashingEmbedder "
                "(a lexical proxy)."
            ) from exc
        self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        model = self._ensure_model()
        vecs = np.asarray(
            model.encode(texts, normalize_embeddings=True, show_progress_bar=False),
            dtype=np.float32,
        )
        return _l2_normalize(vecs)
