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
        ("Pledge of promoter shares flagged", "insider"),
        ("SEC charges former executives with fraud", "governance"),
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
        "insider",
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
    assert "gl=US" in captured["url"]  # one global edition, whatever the market
    # Exact-phrase query on the name people actually write — legal suffix dropped.
    assert "%22Test+Industries%22" in captured["url"]
    assert "Limited" not in captured["url"]

    assert len(items) == 2  # stale one filtered by window
    assert all(i.ticker == "TEST" for i in items)
    material = [i for i in items if i.is_material]
    assert len(material) == 1
    assert material[0].material_category == "earnings"


async def test_fetch_for_company_returns_empty_on_blank_name():
    items = await NewsSource().fetch_for_company("", ticker="X")
    assert items == []


def test_governance_outranks_earnings_in_one_headline():
    """The category that drives the news clamp must win a tie."""
    assert classify_material("SEBI probes Q4 results restatement") == (True, "governance")


def test_keywords_only_match_at_word_starts():
    assert classify_material("Shares listed raid the record books")[0] is False
    assert classify_material("Promoter shares pledged again") == (True, "insider")
    assert classify_material("Sector rotation lifts tech")[0] is False  # "sec" ≠ "sector"


def test_feed_entries_are_normalised(monkeypatch):
    import sys
    from types import SimpleNamespace

    fake_feedparser = SimpleNamespace(
        parse=lambda url: SimpleNamespace(entries=[
            {"title": "Samsung beats on memory demand", "summary": "<b>Strong</b> quarter",
             "link": "https://news.example/1", "published": "Thu, 06 Nov 2026 08:00:00 GMT"},
            {"title": None, "description": "no title", "updated": "2026-11-05T10:00:00Z"},
        ])
    )
    monkeypatch.setitem(sys.modules, "feedparser", fake_feedparser)
    entries = news_source._fetch_feed_sync("https://feed", "Google News")
    assert entries[0] == {
        "headline": "Samsung beats on memory demand", "body": "Strong quarter",
        "url": "https://news.example/1", "published_at": "Thu, 06 Nov 2026 08:00:00 GMT",
        "source": "Google News",
    }
    assert entries[1]["headline"] == "" and entries[1]["body"] == "no title"


def test_feed_parse_failure_is_empty(monkeypatch):
    import sys
    from types import SimpleNamespace

    def boom(url: str):
        raise ValueError("bad xml")

    monkeypatch.setitem(sys.modules, "feedparser", SimpleNamespace(parse=boom))
    assert news_source._fetch_feed_sync("https://feed", "x") == []


def test_published_dates_parse_to_naive_utc():
    from datetime import datetime

    assert news_source._parse_dt("2026-11-05T10:00:00Z") == datetime(2026, 11, 5, 10, 0)
    assert news_source._parse_dt("Thu, 06 Nov 2026 08:00:00 +0530") == datetime(2026, 11, 6, 2, 30)
    assert news_source._parse_dt("not a date") == datetime.min
    assert news_source._parse_dt("") == datetime.min
