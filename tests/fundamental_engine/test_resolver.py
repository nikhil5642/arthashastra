"""Resolver tests — mock yfinance search to keep hermetic."""

from __future__ import annotations

from fundamental_engine import resolver
from fundamental_engine.resolver import (
    TickerMatch,
    pick_best,
    resolve_name,
)


async def test_resolve_name_returns_nse_matches(monkeypatch):
    def fake_resolve_sync(query: str, limit: int) -> list[TickerMatch]:
        return [
            TickerMatch(
                ticker="RELIANCE",
                symbol="RELIANCE.NS",
                exchange="NSE",
                short_name="RELIANCE INDUSTRIES LTD",
                long_name="Reliance Industries Limited",
                score=105.0,
            ),
            TickerMatch(
                ticker="RELIANCE",
                symbol="RELIANCE.BO",
                exchange="BSE",
                short_name="RELIANCE INDUSTRIES LTD",
                long_name="Reliance Industries Limited",
                score=95.0,
            ),
        ]

    monkeypatch.setattr(resolver, "_resolve_sync", fake_resolve_sync)

    matches = await resolve_name("Reliance Industries")
    assert len(matches) == 2
    assert matches[0].exchange == "NSE"
    assert matches[0].score > matches[1].score


async def test_resolve_name_empty_list(monkeypatch):
    monkeypatch.setattr(resolver, "_resolve_sync", lambda q, l: [])
    matches = await resolve_name("nosuchcompany")
    assert matches == []


def test_pick_best():
    m1 = TickerMatch("A", "A.NS", "NSE", "A Ltd", "A Limited", 100.0)
    m2 = TickerMatch("B", "B.BO", "BSE", "B Ltd", "B Limited", 80.0)
    assert pick_best([m1, m2]) is m1
    assert pick_best([]) is None


def test_ticker_match_to_dict():
    m = TickerMatch("X", "X.NS", "NSE", "X Ltd", "X Limited", 60.0)
    d = m.to_dict()
    assert d["ticker"] == "X"
    assert d["exchange"] == "NSE"
    assert d["score"] == 60.0
