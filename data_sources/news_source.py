"""News source — live RSS fetching, per-company queries.

Two fetch modes:
    1. fetch_for_company(company_name) — Google News RSS search scoped to one
       company. Every returned item is tagged to that ticker. No alias
       resolver needed; the query itself scopes the results.
    2. fetch_market_news() — general Indian market RSS aggregation for the
       market brief. Not ticker-tagged; Claude reads and judges relevance
       in the /market-brief skill.

Items run through a keyword gate (MATERIAL_KEYWORDS) to mark material events
vs fluff. Sentiment scoring is a Claude responsibility in the skill.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote_plus

log = logging.getLogger(__name__)

MATERIAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "earnings": (
        "earnings", "results", "quarterly", "q1 ", "q2 ", "q3 ", "q4 ",
        "fy result", "profit", "revenue",
    ),
    "governance": (
        "sebi", "fraud", "auditor", "scam", "investigation",
        "enforcement directorate", "ed raid",
    ),
    "corporate_action": (
        "merger", "acquisition", "takeover", "buyback", "dividend",
        "demerger", "spin-off",
    ),
    "promoter": ("pledge", "promoter", "insider trading", "bulk deal"),
    "rating": ("downgrade", "upgrade", "rating action", "default"),
    "leadership": (
        "ceo", "chairman", "resignation", "resigned", "appointed",
        "stepped down",
    ),
}

GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
)

MARKET_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    (
        "Economic Times Markets",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    ),
    (
        "Moneycontrol Latest",
        "https://www.moneycontrol.com/rss/latestnews.xml",
    ),
    (
        "Moneycontrol Business",
        "https://www.moneycontrol.com/rss/business.xml",
    ),
    (
        "Google News India Stocks",
        "https://news.google.com/rss/search?q=nse+bse+stocks+india&hl=en-IN&gl=IN&ceid=IN:en",
    ),
)


@dataclass(slots=True)
class NewsItem:
    ticker: str
    source: str
    headline: str
    url: str
    published_at: str
    body: str = ""
    is_material: bool = False
    material_category: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_material(text: str) -> tuple[bool, str]:
    lower = text.lower()
    for category, keywords in MATERIAL_KEYWORDS.items():
        if any(k in lower for k in keywords):
            return True, category
    return False, ""


class NewsSource:
    async def fetch_for_company(
        self,
        company_name: str,
        *,
        ticker: str = "",
        within_days: int = 30,
    ) -> list[NewsItem]:
        """Fetch news items for one company via a Google News RSS query.

        The query is quoted to match the canonical name exactly, so a fetch
        for 'Reliance Industries' will not return 'Reliance Power' items.
        """
        if not company_name:
            return []

        log.info(
            "news: live fetch for %r (window=%dd)",
            company_name,
            within_days,
        )
        query = quote_plus(f'"{company_name}"')
        url = GOOGLE_NEWS_RSS.format(query=query)

        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, _fetch_feed_sync, url, "Google News"
        )

        ticker_upper = ticker.strip().upper() or company_name.upper()
        return self._build_items(raw, ticker_upper, within_days)

    async def fetch_market_news(self, *, within_days: int = 7) -> list[NewsItem]:
        """Fetch general Indian market news across RSS feeds."""
        log.info(
            "news: live market fetch (%d feeds, window=%dd)",
            len(MARKET_RSS_FEEDS),
            within_days,
        )
        loop = asyncio.get_running_loop()
        raw_groups = await asyncio.gather(
            *(
                loop.run_in_executor(None, _fetch_feed_sync, url, source)
                for source, url in MARKET_RSS_FEEDS
            ),
            return_exceptions=True,
        )
        raw: list[dict[str, Any]] = []
        for group in raw_groups:
            if isinstance(group, Exception):
                log.warning("market news fetch error: %s", group)
                continue
            raw.extend(group)
        return self._build_items(raw, ticker="", within_days=within_days)

    def _build_items(
        self,
        raw: list[dict[str, Any]],
        ticker: str,
        within_days: int,
    ) -> list[NewsItem]:
        cutoff = datetime.now() - timedelta(days=within_days)
        items: list[NewsItem] = []
        for entry in raw:
            published_at = entry.get("published_at", "")
            if _parse_dt(published_at) < cutoff:
                continue
            headline = entry.get("headline", "")
            body = entry.get("body", "")
            is_mat, category = classify_material(f"{headline} {body}")
            items.append(
                NewsItem(
                    ticker=ticker,
                    source=entry.get("source", ""),
                    headline=headline,
                    url=entry.get("url", ""),
                    published_at=published_at,
                    body=body,
                    is_material=is_mat,
                    material_category=category,
                )
            )
        log.info("news: %d items within window", len(items))
        return items


def _fetch_feed_sync(url: str, source_name: str) -> list[dict[str, Any]]:
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser not installed — news aggregation disabled")
        return []

    try:
        d = feedparser.parse(url)
    except Exception as e:
        log.warning("feed %s parse failed: %s", url, e)
        return []

    out: list[dict[str, Any]] = []
    for entry in d.entries[:80]:
        out.append(
            {
                "headline": entry.get("title", "") or "",
                "body": _strip_html(
                    entry.get("summary", "") or entry.get("description", "") or ""
                ),
                "url": entry.get("link", "") or "",
                "published_at": entry.get("published", "")
                or entry.get("updated", "")
                or "",
                "source": source_name,
            }
        )
    return out


def _parse_dt(s: str) -> datetime:
    if not s:
        return datetime.min
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(s)
        if dt is None:
            return datetime.min
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        return datetime.min


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text or "").strip()
