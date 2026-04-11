"""News source tests — mock the feedparser adapter; test classifier + merge."""

from __future__ import annotations

from datetime import datetime, timedelta

from data_sources import news_source
from data_sources.news_source import (
    MATERIAL_KEYWORDS,
    NewsSource,
    classify_material,
)


def test_classify_material_categories():
    for text, category in (
        ("Company reports Q3 earnings beat", "earnings"),
        ("SEBI issues show-cause to promoter", "governance"),
        ("Alleged fraud under investigation", "governance"),
        ("Mega acquisition announced", "corporate_action"),
        ("Rating upgrade by Moody's", "rating"),
        ("CEO stepped down after 10 years", "leadership"),
        ("Pledge of promoter shares flagged", "promoter"),
    ):
        is_mat, cat = classify_material(text)
        assert is_mat is True
        assert cat == category


def test_classify_non_material():
    is_mat, cat = classify_material("Indian IT exports rose in FY25")
    assert is_mat is False
    assert cat == ""


def test_all_material_categories_present():
    assert set(MATERIAL_KEYWORDS) == {
        "earnings",
        "governance",
        "corporate_action",
        "promoter",
        "rating",
        "leadership",
    }


async def test_fetch_for_company_uses_google_query(monkeypatch):
    today = datetime.now().isoformat()

    captured = {}

    def fake_feed(url: str, source_name: str) -> list[dict]:
        captured["url"] = url
        captured["source_name"] = source_name
        return [
            {
                "headline": "Test Industries Q4 results beat estimates",
                "body": "Profit up 15%",
                "url": "https://example.com/1",
                "published_at": today,
                "source": source_name,
            },
            {
                "headline": "Stale headline",
                "body": "old body",
                "url": "https://example.com/2",
                "published_at": (datetime.now() - timedelta(days=90)).isoformat(),
                "source": source_name,
            },
            {
                "headline": "Generic macro piece",
                "body": "fluff",
                "url": "https://example.com/3",
                "published_at": today,
                "source": source_name,
            },
        ]

    monkeypatch.setattr(news_source, "_fetch_feed_sync", fake_feed)

    items = await NewsSource().fetch_for_company(
        "Test Industries Limited", ticker="TEST", within_days=30
    )
    assert "news.google.com" in captured["url"]
    assert "Test+Industries+Limited" in captured["url"] or "Test%20Industries%20Limited" in captured["url"]

    assert len(items) == 2  # stale one filtered by window
    assert all(i.ticker == "TEST" for i in items)
    material = [i for i in items if i.is_material]
    assert len(material) == 1
    assert material[0].material_category == "earnings"


async def test_fetch_for_company_returns_empty_on_blank_name():
    items = await NewsSource().fetch_for_company("", ticker="X")
    assert items == []


async def test_fetch_market_news_aggregates_feeds(monkeypatch):
    today = datetime.now().isoformat()

    def fake_feed(url: str, source_name: str) -> list[dict]:
        return [
            {
                "headline": f"{source_name} item",
                "body": "body",
                "url": f"https://example.com/{source_name}",
                "published_at": today,
                "source": source_name,
            }
        ]

    monkeypatch.setattr(news_source, "_fetch_feed_sync", fake_feed)

    items = await NewsSource().fetch_market_news(within_days=1)
    # One item per feed in MARKET_RSS_FEEDS.
    assert len(items) >= 1
    assert all(i.ticker == "" for i in items)  # market-wide, untagged
