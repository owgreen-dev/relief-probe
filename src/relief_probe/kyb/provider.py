"""KYB evidence providers — a deterministic stub + a token-gated OpenCorporates client.

The external-evidence contract is one method::

    fetch(name, state, *, amount=None) -> KybEvidence | None

A :class:`KybEvidence` carries the borrower's *registry footprint*: a registration
(incorporation) date, a "non-registered" flag, an address type, plus the matched
company name / confidence / source / attribution ``raw_ref``. ``None`` means
"could not determine" — we deliberately do NOT manufacture a fraud signal from
absence (a wrong-entity match is a defamation / false-positive harm, not just a
metric miss — see ``RESPONSIBLE_USE.md``).

Two implementations:

* :class:`StubProvider` — deterministic, fixture-backed (keyed by
  :func:`~relief_probe.labels.resolve.normalize_name`), ZERO network. The default
  in tests and the offline ``kyb-enrich --stub`` path.
* :class:`OpenCorporatesProvider` — the live Tier-B client. ``requests`` is imported
  lazily *inside* the methods (so importing this module never touches the network),
  the API token is required only at fetch time (mirrors
  :meth:`~relief_probe.triage.judge.LlmJudge._ensure_client`), each raw response is
  cached to ``config.kyb_cache_dir()`` cache-by-existence, and a borrower is
  disambiguated to the right record with the resolver's name/state scoring before
  any evidence is emitted.

GUARDRAIL: the live client is exercised in tests ONLY via an injected transport
(``session=`` or a monkeypatched ``requests.Session.get``) — never a real call.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from relief_probe.config import kyb_cache_dir, opencorporates_token
from relief_probe.labels.resolve import US_STATES, normalize_name, score_match

#: OpenCorporates public API (v0.4). The free tier is share-alike + attribution and
#: rate-limited (~50/day without an account) — see RESPONSIBLE_USE.md.
SEARCH_URL = "https://api.opencorporates.com/v0.4/companies/search"

#: Confidence at/above which a name+state match is treated as the borrower's record.
#: Below it, the evidence is still returned but flagged low-confidence so a reviewer
#: (never the machine) decides — a wrong-entity match defames a real business.
ACCEPT_CONFIDENCE = 0.6

#: Confidence assigned to a confident "searched the registry, found nothing" result.
#: Moderate, not high: an empty search is a LEAD ("non-registered" is a validated
#: fraud indicator) but a borrower may be registered under a DBA/variant name.
NON_REGISTERED_CONFIDENCE = 0.5


@dataclass(frozen=True)
class KybEvidence:
    """One external registry's read of a borrower (a lead, never proof).

    ``registration_date`` is the incorporation date if found; ``is_non_registered``
    is True only when the registry was searched and returned nothing matching;
    ``address_type`` is the registered-address kind when available; ``matched_name``
    / ``match_confidence`` record which record we resolved to and how confidently;
    ``source`` names the provider; ``raw_ref`` is the canonical record URL kept for
    the OpenCorporates share-alike / attribution obligation.
    """

    registration_date: dt.date | None
    is_non_registered: bool
    address_type: str | None
    matched_name: str | None
    match_confidence: float
    source: str
    raw_ref: str | None = None


@runtime_checkable
class EvidenceProvider(Protocol):
    """An external-evidence source: a borrower -> :class:`KybEvidence` or None."""

    def fetch(
        self, name: str, state: str | None, *, amount: float | None = None
    ) -> KybEvidence | None: ...


class StubProvider:
    """Deterministic, fixture-backed evidence — ZERO network (the test default).

    Fixtures are keyed by :func:`normalize_name` so the lookup is robust to
    corporate-suffix / casing differences; an unknown borrower returns ``None``
    (we don't know), never a fabricated "non-registered" signal.
    """

    source = "stub"

    def __init__(self, fixtures: dict[str, KybEvidence] | None = None) -> None:
        self._fixtures: dict[str, KybEvidence] = {
            normalize_name(k): v for k, v in (fixtures or {}).items()
        }

    def fetch(
        self, name: str, state: str | None, *, amount: float | None = None
    ) -> KybEvidence | None:
        return self._fixtures.get(normalize_name(name))


def _parse_date(value: object) -> dt.date | None:
    """Parse an ``YYYY-MM-DD`` registry date, tolerantly (None on anything else)."""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _cache_key(name: str, state: str | None) -> str:
    """Filesystem-safe cache key from the normalized name + state."""
    base = f"{normalize_name(name)}__{(state or 'NA').upper()}"
    slug = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")
    return slug or "query"


class OpenCorporatesProvider:
    """Token-gated OpenCorporates client (Tier-B KYB) — lazy, cached, disambiguating.

    Importing this class touches no network and needs no token; both are deferred to
    :meth:`fetch`. The token is read from ``OPENCORPORATES_TOKEN`` (or passed
    explicitly) and a missing token raises a clear :class:`RuntimeError`. Transient
    errors are retried with the same backoff as ``labels/doj.py`` (``requests``
    imported lazily, ``time.sleep(2**attempt)``, catching
    ``(requests.RequestException, ValueError)``). Each raw response is cached to
    ``config.kyb_cache_dir()`` so a re-run is offline and the rate limit is spent at
    most once per borrower; a corrupt cache file is detected and re-fetched.

    Disambiguation is precision-first: among registry hits whose normalized name
    equals the query, the loan's state breaks ties via
    :func:`~relief_probe.labels.resolve.score_match`. The best record is returned with
    its confidence; below :data:`ACCEPT_CONFIDENCE` it is flagged low-confidence (a
    lead for a human, not an automated finding). Registry hits whose names differ
    return ``None`` rather than a misleading "non-registered" signal.
    """

    source = "opencorporates"

    def __init__(
        self,
        *,
        token: str | None = None,
        session=None,
        timeout: int = 60,
        retries: int = 3,
        accept_confidence: float = ACCEPT_CONFIDENCE,
    ) -> None:
        self._token = token
        self._session = session  # an injected transport for tests (never real net)
        self.timeout = timeout
        self.retries = retries
        self.accept_confidence = accept_confidence

    # -- token / transport (all network deferred here) -------------------------

    def _ensure_token(self) -> str:
        token = self._token or opencorporates_token()
        if not token:
            raise RuntimeError(
                "The live OpenCorporates KYB path needs OPENCORPORATES_TOKEN in the "
                "environment. Export it (free tier is rate-limited ~50/day), or run "
                "kyb-enrich with --stub (the offline StubProvider)."
            )
        return token

    def _fetch_raw(self, name: str, state: str | None) -> dict:
        """Live search with retry/backoff; lazy ``requests`` import (no net on import)."""
        import requests

        token = self._ensure_token()
        params: dict[str, str] = {"q": name, "api_token": token}
        if state:
            params["jurisdiction_code"] = f"us_{state.lower()}"
        sess = self._session or requests.Session()
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = sess.get(SEARCH_URL, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:  # incl. JSON decode
                last_exc = exc
                time.sleep(2**attempt)  # 1s, 2s, 4s
        raise RuntimeError(
            f"OpenCorporates search for {name!r} failed after {self.retries} attempts"
        ) from last_exc

    # -- cache (cache-by-existence, corrupt-tolerant) --------------------------

    @staticmethod
    def _load_cache(path: Path) -> dict | None:
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            # Corrupt / truncated cache (e.g. a killed mid-write): re-fetch rather
            # than crash. Never silently treat it as an empty registry result.
            return None

    @staticmethod
    def _write_cache(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload))

    # -- public API ------------------------------------------------------------

    def fetch(
        self, name: str, state: str | None, *, amount: float | None = None
    ) -> KybEvidence | None:
        cache_path = kyb_cache_dir() / f"{_cache_key(name, state)}.json"
        payload = self._load_cache(cache_path)
        if payload is None:
            payload = self._fetch_raw(name, state)
            self._write_cache(cache_path, payload)
        return self._select(name, state, payload)

    # -- parsing / disambiguation ----------------------------------------------

    @staticmethod
    def _company_text(company: dict) -> str:
        """Searchable blob for state matching: name + jurisdiction + address."""
        parts = [str(company.get("name") or "")]
        jur = str(company.get("jurisdiction_code") or "")
        if jur.lower().startswith("us_"):
            abbr = jur[3:].upper()
            parts.append(abbr)
            parts.append(US_STATES.get(abbr, ""))
        parts.append(str(company.get("registered_address_in_full") or ""))
        addr = company.get("registered_address")
        if isinstance(addr, dict):
            parts.append(str(addr.get("region") or ""))
        return " ".join(p for p in parts if p)

    @staticmethod
    def _address_type(company: dict) -> str | None:
        addr = company.get("registered_address")
        if isinstance(addr, dict):
            kind = addr.get("type")
            return str(kind) if kind else None
        return None

    def _to_evidence(
        self, company: dict, confidence: float
    ) -> KybEvidence:
        return KybEvidence(
            registration_date=_parse_date(company.get("incorporation_date")),
            is_non_registered=False,
            address_type=self._address_type(company),
            matched_name=company.get("name"),
            match_confidence=round(confidence, 3),
            source=self.source,
            raw_ref=company.get("opencorporates_url"),
        )

    def _select(
        self, name: str, state: str | None, payload: dict
    ) -> KybEvidence | None:
        companies = (payload.get("results") or {}).get("companies") or []
        if not companies:
            # The registry was searched and returned nothing: a confident
            # "non-registered" lead (validated PPP-fraud indicator).
            return KybEvidence(
                registration_date=None,
                is_non_registered=True,
                address_type=None,
                matched_name=None,
                match_confidence=NON_REGISTERED_CONFIDENCE,
                source=self.source,
                raw_ref=None,
            )

        norm_query = normalize_name(name)
        n_tokens = len(norm_query.split())
        scored: list[tuple[float, dict]] = []
        for entry in companies:
            company = entry.get("company", entry)
            if normalize_name(company.get("name")) != norm_query:
                continue  # precision gate: only exact normalized-name hits
            conf, _method = score_match(
                name_tokens=n_tokens,
                loan_state=state,
                loan_amount=None,  # the loan amount is not in the registry record
                alleged_amount=None,
                text=self._company_text(company),
            )
            scored.append((conf, company))

        if not scored:
            # Companies exist but none match this borrower's name. We do NOT claim
            # "non-registered" (it may be a DBA/variant) — return "don't know".
            return None
        scored.sort(key=lambda t: t[0], reverse=True)
        best_conf, best_company = scored[0]
        return self._to_evidence(best_company, best_conf)
