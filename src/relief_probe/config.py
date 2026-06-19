"""Runtime configuration and canonical filesystem paths.

Everything the project writes lives under ``data/`` and is reproducible from
public sources, so the whole tree is gitignored. Paths can be overridden with
the ``RELIEF_PROBE_DATA_DIR`` environment variable (useful for tests and CI).
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent.parent

# Default LLM for the optional agentic/narrative paths. Haiku 4.5 by choice — the
# LLM only runs on a small top-k subset (see M7), so the cheap/fast model is the
# right default ($1/$5 per 1M tok vs Opus $5/$25). Override with RELIEF_PROBE_LLM_MODEL.
DEFAULT_LLM_MODEL = "claude-haiku-4-5"


def llm_model() -> str:
    """Model id for LLM paths — ``RELIEF_PROBE_LLM_MODEL`` or the Haiku default."""
    return os.environ.get("RELIEF_PROBE_LLM_MODEL", DEFAULT_LLM_MODEL)


def load_env(path: Path | str | None = None) -> bool:
    """Load ``KEY=VALUE`` pairs from a ``.env`` file into ``os.environ``.

    Minimal dotenv (no dependency): existing environment variables are NOT
    overridden, so an explicitly exported key always wins. Looks at ``path`` if
    given, else the current directory's ``.env`` then the repo-root ``.env``.
    Returns True if a file was loaded. Lines may be blank, ``# comments``, or
    ``KEY=VALUE`` (an optional leading ``export`` and surrounding quotes are
    stripped). Keep secrets out of git — ``.env`` is gitignored.
    """
    candidates = [Path(path)] if path else [Path.cwd() / ".env", REPO_ROOT / ".env"]
    for p in candidates:
        if not p.is_file():
            continue
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export ") :].strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)
        return True
    return False


def data_dir() -> Path:
    """Root of the local data tree (raw downloads, DuckDB warehouse)."""
    env = os.environ.get("RELIEF_PROBE_DATA_DIR")
    base = Path(env).expanduser().resolve() if env else REPO_ROOT / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def raw_dir() -> Path:
    p = data_dir() / "raw"
    p.mkdir(parents=True, exist_ok=True)
    return p


def kyb_cache_dir() -> Path:
    """Cache dir for KYB (know-your-business) external-evidence responses.

    Mirrors :func:`raw_dir`; one JSON file per resolved query so a re-run is offline
    and the OpenCorporates rate limit is spent at most once per borrower (and the
    stored response carries the attribution ``raw_ref`` for the free-tier ToS).
    """
    p = raw_dir() / "kyb"
    p.mkdir(parents=True, exist_ok=True)
    return p


def opencorporates_token() -> str | None:
    """OpenCorporates API token from the environment, or None if unset.

    The Tier-B KYB client is gated on this: with no token the live provider raises
    a clear error rather than hitting the network. Keep it in ``.env`` (gitignored).
    """
    token = os.environ.get("OPENCORPORATES_TOKEN")
    return token or None


def warehouse_path() -> Path:
    """Path to the DuckDB warehouse file."""
    return data_dir() / "relief_probe.duckdb"
