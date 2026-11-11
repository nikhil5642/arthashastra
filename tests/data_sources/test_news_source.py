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


# ── Cleaning the feed: publishers, duplicates, junk ──────────────────────────


def _entry(headline: str, source: str, published: str, *, body: str = "") -> dict:
    return {"headline": headline, "body": body, "url": f"https://x/{hash((headline, source))}",
            "published_at": published, "source": source}


def _recent(days_ago: int = 0) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


def test_publisher_is_read_from_the_feed_and_cut_from_the_headline(monkeypatch):
    import sys
    from types import SimpleNamespace

    fake_feedparser = SimpleNamespace(
        parse=lambda url: SimpleNamespace(entries=[
            {"title": "Alphabet sinks 10% as it holds capex - CNBC",
             "summary": '<a href="x">Alphabet sinks 10% as it holds capex</a>&nbsp;&nbsp;<font>CNBC</font>',
             "link": "https://news.example/1", "published": "Wed, 28 Oct 2026 21:00:00 GMT",
             "source": {"title": "CNBC", "href": "https://cnbc.com"}},
            {"title": "AT&amp;T - a dash in the name stays - Reuters", "summary": "Real summary text",
             "link": "https://news.example/2", "published": "Wed, 28 Oct 2026 22:00:00 GMT",
             "source": {"title": "Reuters"}},
        ])
    )
    monkeypatch.setitem(sys.modules, "feedparser", fake_feedparser)
    first, second = news_source._fetch_feed_sync("https://feed", "Google News")
    assert (first["headline"], first["source"]) == ("Alphabet sinks 10% as it holds capex", "CNBC")
    assert first["body"] == ""  # Google's "summary" only repeats the headline
    assert second["headline"] == "AT&T - a dash in the name stays"
    assert second["body"] == "Real summary text"


def test_syndicated_copies_merge_into_one_story_with_its_coverage():
    raw = [
        _entry("Alphabet takes $548 million stake in Qualcomm", "Reuters", _recent(2)),
        _entry("Alphabet takes $548 million stake in Qualcomm", "Euronext", _recent(2)),
        _entry("Alphabet takes $548 million stake in Qualcomm", "Reuters", _recent(2)),  # same outlet twice
        _entry("Alphabet stock sinks on search miss", "Reuters", _recent(0)),
    ]
    items = NewsSource()._build_items(raw, "GOOG", within_days=30)
    assert [(i.headline, i.coverage) for i in items] == [
        ("Alphabet stock sinks on search miss", 1),  # newest first
        ("Alphabet takes $548 million stake in Qualcomm", 2),
    ]


def test_quote_pages_and_filing_spam_are_dropped():
    junk = [
        "Micron Technology, Inc. (MU) stock price, news, quote and history",
        "Bloks Group Limited (0325.HK) Seasonal returns chart",
        "Mao Geping Cosmetics Co., Ltd. (MAOGEP80.BK) Options Chain",
        "Meta Platforms, Inc. $META Holdings Cut by One Way Holdings LLC",
        "Taiwan Semiconductor Manufacturing Company Ltd. $TSM Shares Purchased by Candriam S.C.A.",
        "Taiwan Semiconductor Manufactur (TSM) Stock Forecasts",
    ]
    raw = [_entry(h, "Yahoo Finance", _recent()) for h in junk]
    raw.append(_entry("Micron pauses work on $4.5 billion Taiwan plant", "Bloomberg", _recent()))
    items = NewsSource()._build_items(raw, "MU", within_days=30)
    assert [i.headline for i in items] == ["Micron pauses work on $4.5 billion Taiwan plant"]


def test_a_broker_upgrade_is_not_a_rating_action_and_default_search_is_not_a_default():
    assert classify_material("Atour Lifestyle upgraded to Buy by analysts")[0] is False
    assert classify_material("Apple keeps Google as default search engine")[0] is False
    assert classify_material("S&P revises Meta outlook to negative") == (True, "rating")
    assert classify_material("Evergrande defaults on dollar bond") == (True, "rating")


def test_governance_names_trouble_not_topics():
    assert classify_material("Nvidia hires law firm to investigate chief's affair") == (True, "governance")
    assert classify_material("Nvidia shaken by spy scandal") == (True, "governance")
    # "regulatory" and "sanctions" as topics say nothing about how a company is run.
    assert classify_material("Alphabet's regulatory advantage never gets talked about")[0] is False
    assert classify_material("US sanctions cut semiconductor sales to China")[0] is False


def test_digest_counts_the_last_weeks_stories_per_category():
    from data_sources.news_source import NewsCategory, NewsItem, digest

    def story(headline: str, category: str, days_ago: int, coverage: int = 1) -> NewsItem:
        return NewsItem(ticker="NVDA", source="X", headline=headline, url="u",
                        published_at=_recent(days_ago), is_material=bool(category),
                        material_category=category, coverage=coverage)

    radar = digest([
        story("Board probes CEO", "governance", 1),
        story("Nvidia investigates spy plot", "governance", 2, coverage=4),
        story("Old probe", "governance", 20),  # outside the 7-day window
        story("Q3 results beat", "earnings", 3),
        story("Chip stocks slide", "", 1),
    ])
    assert (radar.window_days, radar.stories) == (7, 4)
    governance = radar.categories[NewsCategory.GOVERNANCE]
    assert (governance.stories, governance.widest_headline, governance.widest_coverage) == (
        2, "Nvidia investigates spy plot", 4
    )
    assert NewsCategory.RATING not in radar.categories
    assert radar.to_dict()["categories"]["earnings"]["stories"] == 1
