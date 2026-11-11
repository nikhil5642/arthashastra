"""Shared test setup — one network guard, no fixture data.

Tests construct `BalanceSheetSnapshot` / `ScorerResult` / `NewsItem`
directly in each test body (no fixture JSON files). Monkeypatching of
live data source adapters (yfinance, screener.in, Wikipedia, Brave)
happens at the function level in the specific test files.

The one shared piece is `no_live_news_feed`: every refresh now reads the
company's news feed for the early-signal radar, so the feed library is
replaced with one that returns an empty feed. A test that cares about
news monkeypatches `news_source._fetch_feed_sync` (or installs its own
fake `feedparser`), which overrides this.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def no_live_news_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    empty_feed = SimpleNamespace(parse=lambda url: SimpleNamespace(entries=[]))
    monkeypatch.setitem(sys.modules, "feedparser", empty_feed)
