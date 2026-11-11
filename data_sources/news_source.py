"""News source — live Google News RSS, one query per company.

`fetch_for_company(company_name)` searches Google News for one company, in
any market, through a single global English edition — the same query
shape for Microsoft, Tencent, or Reliance. Every returned item is tagged
to that ticker; the exact-phrase query itself scopes the results, so no
alias resolver is needed. For companies with thin English coverage the
`/analyse-stock` skill tells Claude to search in the local language too.

The raw feed is mostly noise, so three things happen before anyone reads it:

  - Junk is dropped: quote pages, chart pages and 13F filing spam match the
    company's name but report nothing.
  - Syndicated copies are merged into one story, with `coverage` counting
    the outlets that carried it. Stories come back newest first.
  - Each story runs through a keyword gate (MATERIAL_KEYWORDS) to mark
    material events vs fluff. Categories are checked in severity order —
    governance before earnings — so "regulator probes Q4 results" is tagged
    governance, the category that drives the news-pillar clamp. Keywords
    match at word starts ("pledge" catches "pledged", "ed raid" can't fire
    inside "listed raid").

`digest(items)` then counts the last week's stories per category — the
facts behind the governance and credit-rating early signals. Sentiment
scoring is a Claude responsibility in the skill.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import TypedDict
from urllib.parse import quote_plus

from data_sources.names import news_query_name

log = logging.getLogger(__name__)

RADAR_WINDOW_DAYS = 7  # how far back `digest` counts stories
MAX_FEED_ENTRIES = 100  # Google News returns at most this many


class FeedEntry(TypedDict):
    """Shape of one raw RSS entry after `_fetch_feed_sync` normalises it.

    All five fields are always populated (possibly empty-string) so
    downstream code can read them without None-checks.
    """

    headline: str
    body: str
    url: str
    published_at: str
    source: str  # the publisher (Reuters, Bloomberg, …), or the feed's name if unknown


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
#
# GOVERNANCE names trouble, not topics: "regulator" and "sanctions" alone
# also match "regulatory advantage" and "chip-export sanctions", which say
# nothing about how a company is run, so they are not keywords.
#
# RATING means CREDIT ratings (agencies, outlooks, defaults). A broker
# moving a stock from Hold to Buy is sell-side opinion, not a rating
# action, so bare "upgrade" / "downgrade" are deliberately not keywords —
# and nor is bare "default" ("the default search engine").
MATERIAL_KEYWORDS: dict[NewsCategory, tuple[str, ...]] = {
    NewsCategory.GOVERNANCE: (
        "fraud", "scam", "scandal", "probe", "investigat", "enforcement",
        "regulatory action", "securities and exchange commission", "sec charges", "sebi",
        "csrc", "enforcement directorate", "ed raid", "money laundering", "antitrust",
        "auditor", "restatement", "accounting irregularit", "whistleblower", "class action",
        "short seller", "short-seller", "sanctioned", "fined", "indict", "arrest", "bribe",
        "embezzle", "insider trading", "misconduct", "subpoena", "espionage", "spy",
    ),
    NewsCategory.RATING: (
        "rating action", "credit rating", "rating outlook", "outlook to negative",
        "outlook negative", "moody", "fitch", "debt default", "defaults on", "defaulted",
        "junk status",
    ),
    NewsCategory.INSIDER: (
        "pledge", "promoter", "insider", "stake sale", "sells stake",
        "bulk deal", "block deal",
    ),
    NewsCategory.LEADERSHIP: (
        "ceo", "chief executive", "cfo", "chairman", "resignation", "resigned",
        "resigns", "appointed", "stepped down", "steps down",
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

# Pages that carry a company's name but no news: finance-portal quote and
# chart pages, and the "$TICKER Shares Bought by Some Fund LLC" filing spam.
_NOISE_PATTERN = re.compile(
    r"stock price, news, quote"
    r"|stock quote price and forecast"
    r"|seasonal returns? chart"
    r"|options chain"
    r"|company profile"
    r"|analyst ratings, estimates"
    r"|major holders"
    r"|valuation measures"
    r"|recent insider transactions"
    r"|latest stock news"
    r"|stock forecasts?$"
    r"|\b(?:balance sheet|income statement|cash flow) - yahoo"
    r"|\$[a-z][a-z.\-]{0,9}\s+(?:shares|stock|holdings|position)\b",
    re.IGNORECASE,
)

# One global English edition for every company, whatever its market.
GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
)


@dataclass(slots=True)
class NewsItem:
    """One story: a headline, who published it, and how widely it was carried."""

    ticker: str
    source: str
    headline: str
    url: str
    published_at: str  # ISO, UTC
    body: str = ""
    is_material: bool = False
    material_category: str = ""
    coverage: int = 1  # outlets that carried this same headline

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CategoryDigest:
    """One category's stories inside the radar window."""

    stories: int
    widest_headline: str  # the story the most outlets carried (the newest on a tie)
    widest_coverage: int


@dataclass(frozen=True, slots=True)
class NewsDigest:
    """How many stories each material category saw in the last `window_days`."""

    window_days: int
    stories: int
    categories: dict[NewsCategory, CategoryDigest] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "window_days": self.window_days,
            "stories": self.stories,
            "categories": {
                category.value: asdict(entry) for category, entry in self.categories.items()
            },
        }


def classify_material(text: str) -> tuple[bool, str]:
    lower = text.lower()
    for category, pattern in _MATERIAL_PATTERNS.items():
        if pattern.search(lower):
            return True, category
    return False, ""


def digest(
    items: list[NewsItem], *, window_days: int = RADAR_WINDOW_DAYS, now: datetime | None = None
) -> NewsDigest:
    """Count the material stories of the last `window_days`, per category."""
    cutoff = (now or _utc_now()) - timedelta(days=window_days)
    recent = [item for item in items if _parse_dt(item.published_at) >= cutoff]
    categories: dict[NewsCategory, CategoryDigest] = {}
    for category in NewsCategory:
        tagged = [item for item in recent if item.material_category == category.value]
        if not tagged:
            continue
        widest = max(tagged, key=lambda item: (item.coverage, item.published_at))
        categories[category] = CategoryDigest(
            stories=len(tagged),
            widest_headline=widest.headline,
            widest_coverage=widest.coverage,
        )
    return NewsDigest(window_days=window_days, stories=len(recent), categories=categories)


class NewsSource:
    async def fetch_for_company(
        self,
        company_name: str,
        *,
        ticker: str = "",
        within_days: int = 30,
    ) -> list[NewsItem]:
        """Stories about one company from a Google News RSS query, newest first.

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
        """Window, de-noise, merge syndicated copies, tag, and sort newest first."""
        cutoff = _utc_now() - timedelta(days=within_days)
        stories: dict[str, NewsItem] = {}
        outlets: dict[str, set[str]] = {}
        for entry in raw:
            published = _parse_dt(entry.get("published_at", ""))
            headline = entry.get("headline", "")
            if published < cutoff or _NOISE_PATTERN.search(headline):
                continue
            key = _story_key(headline) or entry.get("url", "")
            source = entry.get("source", "")
            outlets.setdefault(key, set()).add(source)
            if key in stories:
                continue
            body = entry.get("body", "")
            is_material, category = classify_material(f"{headline} {body}")
            stories[key] = NewsItem(
                ticker=ticker,
                source=source,
                headline=headline,
                url=entry.get("url", ""),
                published_at=published.isoformat(timespec="seconds"),
                body=body,
                is_material=is_material,
                material_category=category,
            )
        for key, item in stories.items():
            item.coverage = len(outlets[key])
        items = sorted(stories.values(), key=lambda item: item.published_at, reverse=True)
        log.info("news: %d stories within window (from %d feed entries)", len(items), len(raw))
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
    for entry in d.entries[:MAX_FEED_ENTRIES]:
        publisher = _publisher(entry) or source_name
        headline = _without_publisher(entry.get("title", "") or "", publisher)
        out.append(
            FeedEntry(
                headline=headline,
                body=_body(entry.get("summary", "") or entry.get("description", "") or "", headline),
                url=entry.get("link", "") or "",
                published_at=entry.get("published", "")
                or entry.get("updated", "")
                or "",
                source=publisher,
            )
        )
    return out


# ── Feed-entry cleaning ──────────────────────────────────────────────────────


def _publisher(entry: object) -> str:
    """The outlet behind an entry — Google News puts it in `<source>`."""
    source = entry.get("source") if hasattr(entry, "get") else None  # type: ignore[union-attr]
    title = source.get("title") if hasattr(source, "get") else None  # type: ignore[union-attr]
    return title.strip() if isinstance(title, str) else ""


def _without_publisher(title: str, publisher: str) -> str:
    """'Alphabet sinks 10% - CNBC' → 'Alphabet sinks 10%'."""
    title = html.unescape(title).strip()
    suffix = f" - {publisher}"
    return title[: -len(suffix)].rstrip() if publisher and title.endswith(suffix) else title


def _body(summary: str, headline: str) -> str:
    """The entry's summary — empty when it only repeats the headline, as Google's does."""
    text = " ".join(html.unescape(_strip_html(summary)).split())
    return "" if headline and text.startswith(headline) else text


def _story_key(headline: str) -> str:
    """Headline reduced to its letters and digits — equal for syndicated copies."""
    return re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()


def _utc_now() -> datetime:
    """Now, as naive UTC — everything here compares in naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    return re.sub(r"<[^>]+>", "", text or "").strip()
