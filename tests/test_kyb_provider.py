"""Offline tests for the KYB evidence providers — NEVER a real network call.

The live OpenCorporates client is exercised only via an injected transport (a fake
``session`` whose ``.get`` returns a canned response); the token gate, cache, and
name/state disambiguation are all asserted without touching the network.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from relief_probe.kyb.provider import (
    KybEvidence,
    OpenCorporatesProvider,
    StubProvider,
    _cache_key,
)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    """Point all config paths (incl. kyb_cache_dir) at a throwaway tmp tree."""
    monkeypatch.setenv("RELIEF_PROBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("OPENCORPORATES_TOKEN", raising=False)


class _FakeResponse:
    def __init__(self, payload: dict, *, status_ok: bool = True) -> None:
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self) -> None:
        if not self._status_ok:
            import requests

            raise requests.HTTPError("boom")

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Records every call and returns canned payloads in order (no network)."""

    def __init__(self, *payloads: dict) -> None:
        self._payloads = list(payloads)
        self.calls = 0

    def get(self, url, params=None, timeout=None):  # noqa: D401 - transport stub
        self.calls += 1
        payload = self._payloads[min(self.calls - 1, len(self._payloads) - 1)]
        return _FakeResponse(payload)


def _company(name, *, jurisdiction="us_ca", date="2020-03-01", url="https://oc/x"):
    return {
        "company": {
            "name": name,
            "jurisdiction_code": jurisdiction,
            "incorporation_date": date,
            "opencorporates_url": url,
            "registered_address_in_full": "1 Main St",
        }
    }


def _payload(*companies) -> dict:
    return {"results": {"companies": list(companies)}}


# (a) token gate -------------------------------------------------------------


def test_opencorporates_requires_token():
    provider = OpenCorporatesProvider()  # no token, no session
    with pytest.raises(RuntimeError, match="OPENCORPORATES_TOKEN"):
        provider.fetch("Acme Widgets LLC", "CA")


# (b) stub determinism -------------------------------------------------------


def test_stub_provider_known_and_unknown():
    ev = KybEvidence(
        registration_date=dt.date(2019, 1, 1),
        is_non_registered=False,
        address_type=None,
        matched_name="ACME WIDGETS LLC",
        match_confidence=0.9,
        source="stub",
        raw_ref="https://oc/acme",
    )
    provider = StubProvider({"Acme Widgets, LLC": ev})
    # Keyed by normalize_name: punctuation/casing/suffix differences still hit.
    assert provider.fetch("ACME WIDGETS LLC", "CA") is ev
    assert provider.fetch("acme widgets", "CA") is ev
    assert provider.fetch("Unknown Co", "TX") is None


# (c) injected transport parses + caches, second fetch is offline ------------


def test_injected_transport_parses_and_caches():
    payload = _payload(
        _company("Acme Widgets LLC", date="2020-04-15", url="https://oc/acme")
    )
    session = _FakeSession(payload)
    provider = OpenCorporatesProvider(token="t", session=session)

    ev = provider.fetch("Acme Widgets LLC", "CA")
    assert ev is not None
    assert ev.registration_date == dt.date(2020, 4, 15)
    assert ev.matched_name == "Acme Widgets LLC"
    assert ev.raw_ref == "https://oc/acme"
    assert ev.source == "opencorporates"
    assert ev.is_non_registered is False
    assert session.calls == 1

    # Second fetch reads the on-disk cache WITHOUT calling the transport.
    ev2 = provider.fetch("Acme Widgets LLC", "CA")
    assert session.calls == 1  # not re-invoked
    assert ev2 is not None
    assert ev2.registration_date == ev.registration_date


def test_non_registered_when_registry_empty():
    session = _FakeSession(_payload())  # zero companies
    provider = OpenCorporatesProvider(token="t", session=session)
    ev = provider.fetch("Ghost Shell Co", "NV")
    assert ev is not None
    assert ev.is_non_registered is True
    assert ev.registration_date is None
    assert ev.matched_name is None


# (d) disambiguation by state ------------------------------------------------


def test_disambiguation_picks_loan_state_match():
    payload = _payload(
        _company("Summit Trading Co", jurisdiction="us_tx", url="https://oc/tx"),
        _company("Summit Trading Co", jurisdiction="us_ca", url="https://oc/ca"),
    )
    provider = OpenCorporatesProvider(token="t", session=_FakeSession(payload))
    ev = provider.fetch("Summit Trading Co", "CA")
    assert ev is not None
    assert ev.raw_ref == "https://oc/ca"  # the CA record wins on the state signal
    assert ev.match_confidence >= 0.6


def test_ambiguous_match_is_low_confidence():
    # Same name in two states, neither the loan's state -> no state corroboration.
    payload = _payload(
        _company("Summit Trading Co", jurisdiction="us_tx", url="https://oc/tx"),
        _company("Summit Trading Co", jurisdiction="us_ny", url="https://oc/ny"),
    )
    provider = OpenCorporatesProvider(token="t", session=_FakeSession(payload))
    ev = provider.fetch("Summit Trading Co", "CA")
    assert ev is not None
    assert ev.match_confidence < 0.6  # surfaced as a low-confidence lead


def test_name_mismatch_returns_none_not_false_signal():
    payload = _payload(_company("Totally Different Inc", jurisdiction="us_ca"))
    provider = OpenCorporatesProvider(token="t", session=_FakeSession(payload))
    # Companies exist but none match -> "don't know", never a non-registered claim.
    assert provider.fetch("Acme Widgets LLC", "CA") is None


# (e) corrupt cache is re-fetched, never crashes -----------------------------


def test_corrupt_cache_is_refetched():
    from relief_probe.config import kyb_cache_dir

    payload = _payload(_company("Acme Widgets LLC"))
    session = _FakeSession(payload)
    provider = OpenCorporatesProvider(token="t", session=session)

    # Pre-seed a truncated/corrupt cache file for this query.
    cache_path = kyb_cache_dir() / f"{_cache_key('Acme Widgets LLC', 'CA')}.json"
    cache_path.write_text('{"results": {"companies": [')  # invalid JSON

    ev = provider.fetch("Acme Widgets LLC", "CA")
    assert ev is not None
    assert session.calls == 1  # corrupt cache forced a live (transport) re-fetch
    # And the cache was rewritten with valid JSON.
    assert json.loads(cache_path.read_text())["results"]["companies"]
