"""Name → ticker resolver for any listed equity, on any exchange.

Turns free text ("Microsoft", "Alphabet", "Tencent", "Toyota Motor Corp",
"reliance ltd") into Yahoo symbols — the project's canonical ticker IDs
(MSFT, GOOGL, 0700.HK, 7203.T, RELIANCE.NS) — via yfinance's search.

Yahoo returns every listing of a company: the primary listing, ADRs, and
secondary venues. Only equities are kept, ranked by how well the name
matches; Yahoo's own relevance order breaks ties, which puts the primary
listing first for most companies. Because Yahoo returns nothing for
queries like "reliance ltd", legal suffixes are stripped with the shared
`data_sources.names.clean_company_name` and shorter variants retried.

No hardcoded ticker or exchange lists — everything comes from Yahoo.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from data_sources.names import clean_company_name

log = logging.getLogger(__name__)


@dataclass(slots=True)
class TickerMatch:
    ticker: str  # canonical ID = Yahoo symbol, e.g. "MSFT", "RELIANCE.NS"
    exchange: str  # Yahoo's display name, e.g. "NASDAQ", "NSE", "HKSE"
    short_name: str
    long_name: str
    score: float  # name-match quality; higher = better

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "exchange": self.exchange,
            "short_name": self.short_name,
            "long_name": self.long_name,
            "score": self.score,
        }


async def resolve_name(query: str, *, limit: int = 8) -> list[TickerMatch]:
    """Return equity listings matching a free-text query, best first."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _resolve_sync, query, limit)


def pick_best(matches: list[TickerMatch]) -> TickerMatch | None:
    return matches[0] if matches else None


def _resolve_sync(query: str, limit: int) -> list[TickerMatch]:
    for variant in _query_variants(query):
        matches = _search_once(variant, limit=limit)
        if matches:
            return matches
    return []


def _query_variants(query: str) -> list[str]:
    """The raw query, then without legal suffixes, then just the first word."""
    raw = (query or "").strip()
    cleaned = clean_company_name(raw)
    first_word = cleaned.split()[0] if cleaned else ""
    variants: list[str] = []
    for candidate in (raw, cleaned, first_word):
        if candidate and candidate.lower() not in {v.lower() for v in variants}:
            variants.append(candidate)
    return variants


def _search_once(query: str, *, limit: int) -> list[TickerMatch]:
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — resolver disabled")
        return []

    try:
        search = yf.Search(query, max_results=max(limit * 2, 10))
    except Exception as e:
        log.warning("yfinance search failed for %r: %s", query, e)
        return []

    matches: list[TickerMatch] = []
    for quote in getattr(search, "quotes", None) or []:
        if quote.get("quoteType") != "EQUITY":
            continue
        symbol = quote.get("symbol") or ""
        if not symbol:
            continue
        short_name = quote.get("shortname") or ""
        long_name = quote.get("longname") or short_name
        matches.append(
            TickerMatch(
                ticker=symbol.upper(),
                exchange=quote.get("exchDisp") or quote.get("exchange") or "",
                short_name=short_name,
                long_name=long_name,
                score=_score_match(query, short_name, long_name),
            )
        )

    # Stable sort: equal scores keep Yahoo's relevance order.
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]


def _score_match(query: str, short_name: str, long_name: str) -> float:
    """Exact name > prefix > substring."""
    q = clean_company_name(query).lower()
    names = (clean_company_name(short_name).lower(), clean_company_name(long_name).lower())
    if not q:
        return 0.0
    score = 0.0
    if q in names:
        score += 100
    if any(name.startswith(q) for name in names):
        score += 40
    if any(q in name for name in names):
        score += 20
    return score
