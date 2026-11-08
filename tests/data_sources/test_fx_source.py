"""FX tests — minor units and conversion, with the Yahoo fetch mocked."""

from __future__ import annotations

import pytest

from data_sources import fx_source
from data_sources.fx_source import conversion_rate, major_unit


def test_major_unit_handles_pence_and_cents():
    assert major_unit("GBp") == ("GBP", 100.0)
    assert major_unit("ZAc") == ("ZAR", 100.0)
    assert major_unit("usd") == ("USD", 1.0)


async def test_same_currency_needs_no_request(monkeypatch):
    def boom(*_):
        raise AssertionError("no FX request expected")

    monkeypatch.setattr(fx_source, "_fetch_major_rate_sync", boom)
    assert await conversion_rate("INR", "INR") == 1.0


async def test_pence_quote_converts_through_pounds(monkeypatch):
    monkeypatch.setattr(fx_source, "_fetch_major_rate_sync", lambda f, t: 125.0)
    # 1 penny = 1.25 INR when 1 GBP = 125 INR.
    assert await conversion_rate("GBp", "INR") == pytest.approx(1.25)


async def test_unknown_currency_or_missing_quote_is_none(monkeypatch):
    monkeypatch.setattr(fx_source, "_fetch_major_rate_sync", lambda f, t: None)
    assert await conversion_rate("USD", "INR") is None
    assert await conversion_rate("", "INR") is None


def test_yahoo_fx_pair_latest_close(monkeypatch):
    import pandas as pd
    import yfinance

    class FakePair:
        def __init__(self, symbol: str) -> None:
            assert symbol == "USDINR=X"

        def history(self, period: str) -> pd.DataFrame:
            return pd.DataFrame({"Close": [88.1, float("nan"), 88.3]})

    monkeypatch.setattr(yfinance, "Ticker", FakePair)
    assert fx_source._fetch_major_rate_sync("USD", "INR") == 88.3


def test_yahoo_fx_failures_are_none(monkeypatch):
    import pandas as pd
    import yfinance

    class EmptyPair:
        def __init__(self, symbol: str) -> None: ...

        def history(self, period: str) -> pd.DataFrame:
            return pd.DataFrame({"Close": []})

    monkeypatch.setattr(yfinance, "Ticker", EmptyPair)
    assert fx_source._fetch_major_rate_sync("XYZ", "INR") is None

    def boom(symbol: str):
        raise RuntimeError("down")

    monkeypatch.setattr(yfinance, "Ticker", boom)
    assert fx_source._fetch_major_rate_sync("USD", "INR") is None
