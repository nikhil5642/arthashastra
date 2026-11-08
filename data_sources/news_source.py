"""News source — live Google News RSS, one query per company.

`fetch_for_company(company_name)` searches Google News for one company, in
any market, through a single global English edition — the same query
shape for Microsoft, Tencent, or Reliance. Every returned item is tagged
to that ticker; the exact-phrase query itself scopes the results, so no
alias resolver is needed. For companies with thin English coverage the
`/analyse-stock` skill tells Claude to search in the local language too.

Items run through a keyword gate (MATERIAL_KEYWORDS) to mark material events
vs fluff. Categories are checked in severity order — governance before
earnings — so "regulator probes Q4 results" is tagged governance, the
category that drives the news-pillar clamp. Keywords match at word starts
("pledge" catches "pledged", "ed raid" can't fire inside "listed raid").
Sentiment scoring is a Claude responsibility in the skill.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import TypedDict
from urllib.parse import quote_plus

from data_sources.names import news_query_name

log = logging.getLogger(__name__)


class FeedEntry(TypedDict):
    """Shape of one raw RSS entry after `_fetch_feed_sync` normalises it.

    All five fields are always populated (possibly empty-string) so
    downstream code can read them without None-checks.
    """

    headline: str
    body: str
    url: str
    published_at: str
    source: str

class NewsCategory(StrEnum):
    """Categories a material news event can fall into.

    Used as typed keys for `MATERIAL_KEYWORDS` so typos at the call
    site surface immediately rather than silently missing a match.
    `str` subclass (StrEnum) means JSON serialization still produces
    the lowercased labels downstream code already expects.
    """

    GOVERNANCE = "governance"
    RATING = "rating"
    INSIDER = "insider"
    LEADERSHIP = "leadership"
    CORPORATE_ACTION = "corporate_action"
    EARNINGS = "earnings"


# One universal keyword set for every market, ordered by severity: the
# first matching category wins, so a headline that mentions both a probe
# and quarterly results is tagged governance. Regulator names cover the
# largest markets; the generic terms ("probe", "enforcement", "fraud")
# catch the rest, and Claude judges every item in the skill anyway.
MATERIAL_KEYWORDS: dict[NewsCategory, tuple[str, ...]] = {
    NewsCategory.GOVERNANCE: (
        "fraud", "scam", "probe", "investigation", "enforcement", "regulator",
        "securities and exchange commission", "sec charges", "sebi", "csrc",
        "enforcement directorate", "ed raid", "money laundering", "antitrust",
        "auditor", "restatement", "whistleblower", "class action", "sanctions",
    ),
    NewsCategory.RATING: (
        "downgrade", "upgrade", "rating action", "credit rating", "default",
    ),
    NewsCategory.INSIDER: (
        "pledge", "promoter", "insider", "stake sale", "sells stake",
        "bulk deal", "block deal",
    ),
    NewsCategory.LEADERSHIP: (
        "ceo", "chief executive", "cfo", "chairman", "resignation", "resigned",
        "appointed", "stepped down", "steps down",
    ),
    NewsCategory.CORPORATE_ACTION: (
        "merger", "acquisition", "acquire", "takeover", "buyback", "dividend",
        "demerger", "spin-off", "delisting",
    ),
    NewsCategory.EARNINGS: (
        "earnings", "results", "quarterly", "q1", "q2", "q3", "q4",
        "fy result", "profit", "revenue", "guidance",
    ),
}

_MATERIAL_PATTERNS: dict[NewsCategory, re.Pattern[str]] = {
    category: re.compile(r"\b(?:" + "|".join(map(re.escape, keywords)) + ")")
    for category, keywords in MATERIAL_KEYWORDS.items()
}

# One global English edition for every company, whatever its market.
GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def classify_material(text: str) -> tuple[bool, str]:
    lower = text.lower()
    for category, pattern in _MATERIAL_PATTERNS.items():
        if pattern.search(lower):
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

        The query is the exact-phrase form of the name with legal suffixes
        dropped (`news_query_name`): "Reliance Industries Limited" →
        "Reliance Industries", which still won't match 'Reliance Power'.
        """
        if not company_name:
            return []

        log.info(
            "news: live fetch for %r (window=%dd)",
            company_name,
            within_days,
        )
        query = quote_plus(f'"{news_query_name(company_name)}"')
        url = GOOGLE_NEWS_RSS.format(query=query)

        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(
            None, _fetch_feed_sync, url, "Google News"
        )

        ticker_upper = ticker.strip().upper() or company_name.upper()
        return self._build_items(raw, ticker_upper, within_days)

    def _build_items(
        self,
        raw: list[FeedEntry],
        ticker: str,
        within_days: int,
    ) -> list[NewsItem]:
        # Compare everything in naive UTC. `_parse_dt` normalises to naive
        # UTC already; we do the same for `cutoff` so the filter doesn't
        # silently drop or keep items based on the host system's timezone.
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=within_days
        )
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


def _fetch_feed_sync(url: str, source_name: str) -> list[FeedEntry]:
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

    out: list[FeedEntry] = []
    for entry in d.entries[:80]:
        out.append(
            FeedEntry(
                headline=entry.get("title", "") or "",
                body=_strip_html(
                    entry.get("summary", "") or entry.get("description", "") or ""
                ),
                url=entry.get("link", "") or "",
                published_at=entry.get("published", "")
                or entry.get("updated", "")
                or "",
                source=source_name,
            )
        )
    return out


def _parse_dt(s: str) -> datetime:
    """Parse a published-at string to a naive-UTC datetime."""
    if not s:
        return datetime.min
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
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
