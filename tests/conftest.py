"""Keep unit tests independent of IP, saved consent cookies and Wikipedia."""

import json

import pytest

from flightcore import consent, routes


@pytest.fixture(autouse=True)
def offline_catalogs_and_consent(monkeypatch):
    monkeypatch.setattr(consent, "get_cookies", lambda **kwargs: {})
    snapshot = json.loads(routes.SNAPSHOT.read_text())
    monkeypatch.setattr(routes, "destinations", lambda origin: snapshot.get(origin, []))
