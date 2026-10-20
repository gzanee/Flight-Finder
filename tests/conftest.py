"""Keep unit tests independent of IP, saved consent cookies and Wikipedia."""

import json
from datetime import date

import pytest

from flightcore import consent, flexible, routes


@pytest.fixture(autouse=True)
def offline_catalogs_and_consent(monkeypatch):
    monkeypatch.setattr(consent, "get_cookies", lambda **kwargs: {})
    snapshot = json.loads(routes.SNAPSHOT.read_text())
    monkeypatch.setattr(routes, "destinations", lambda origin: snapshot.get(origin, []))


@pytest.fixture
def frozen_today(monkeypatch):
    """Fix the API's "today" so requests with literal 2026 dates never expire."""

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 15)

    monkeypatch.setattr(flexible, "date", FrozenDate)
