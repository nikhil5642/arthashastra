"""Name → ticker resolver for Indian equities.

Uses yfinance's search endpoint to turn a free-text name ("Reliance",
"HDFC Bank", "Bajaj Finance", "reliance ltd") into a canonical NSE/BSE
ticker. Yahoo's search is picky — it returns zero hits for queries like
"reliance ltd" or "hdfc bank ltd." — so we clean common corporate
suffixes and retry before giving up.

No hardcoded ticker lists — everything comes from Yahoo's search API.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

NSE_EXCHANGE_CODES: frozenset[str] = frozenset({"NSI"})
BSE_EXCHANGE_CODES: frozenset[str] = frozenset({"BSE"})
INDIAN_EXCHANGE_CODES: frozenset[str] = NSE_EXCHANGE_CODES | BSE_EXCHANGE_CODES

# Corporate suffixes to strip from a free-text query before passing to Yahoo.
# Yahoo's search treats these as noise and returns nothing for queries like
# "reliance ltd" even though "reliance" matches RELIANCE.NS fine.
_SUFFIX_WORDS: tuple[str, ...] = (
    "limited",
    "ltd",
    "ltd.",
    "corporation",
    "corp",
    "corp.",
    "company",
    "co",
    "co.",
    "inc",
    "inc.",
    "incorporated",
    "plc",
    "llp",
    "pvt",
    "pvt.",
    "private",
)


@dataclass(slots=True)
class TickerMatch:
    ticker: str  # bare ticker without exchange suffix (e.g. "RELIANCE")
    symbol: str  # yfinance symbol (e.g. "RELIANCE.NS")
    exchange: str  # "NSE" | "BSE"
    short_name: str
    long_name: str
    score: float  # higher = better match (NSE primary > BSE > other)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "short_name": self.short_name,
            "long_name": self.long_name,
            "score": self.score,
        }


def clean_query(query: str) -> str:
    """Strip punctuation + trailing corporate suffix words."""
    q = re.sub(r"[^\w\s&-]", " ", query or "").strip()
    while True:
        parts = q.split()
        if not parts:
            break
        if parts[-1].lower() in _SUFFIX_WORDS:
            parts.pop()
            q = " ".join(parts)
            continue
        break
    return q.strip()


async def resolve_name(query: str, *, limit: int = 8) -> list[TickerMatch]:
    """Return Indian-exchange ticker candidates for a free-text query."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _resolve_sync, query, limit)


def _resolve_sync(query: str, limit: int) -> list[TickerMatch]:
    variants: list[str] = []
    raw = (query or "").strip()
    if raw:
        variants.append(raw)
    cleaned = clean_query(raw)
    if cleaned and cleaned.lower() != raw.lower():
        variants.append(cleaned)
    # Final fallback: first word only. Turns "HDFC Bank Ltd" → "HDFC"
    # when nothing else worked.
    if cleaned:
        first = cleaned.split()[0] if cleaned.split() else ""
        if first and first.lower() != cleaned.lower():
            variants.append(first)

    for variant in variants:
        matches = _search_once(variant, limit=limit)
        if matches:
            return matches
    return []


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

    quotes = getattr(search, "quotes", None) or []
    matches: list[TickerMatch] = []
    for q in quotes:
        exchange = q.get("exchange") or ""
        if exchange not in INDIAN_EXCHANGE_CODES:
            continue
        symbol = q.get("symbol") or ""
        if not symbol:
            continue
        bare = symbol.split(".")[0]
        short_name = q.get("shortname") or ""
        long_name = q.get("longname") or short_name

        exch_label = "NSE" if exchange in NSE_EXCHANGE_CODES else "BSE"
        score = _score_match(query, short_name, long_name, exch_label)

        matches.append(
            TickerMatch(
                ticker=bare,
                symbol=symbol,
                exchange=exch_label,
                short_name=short_name,
                long_name=long_name,
                score=score,
            )
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]


def _score_match(query: str, short_name: str, long_name: str, exchange: str) -> float:
    """Rank match quality: exact names > prefix > substring, NSE > BSE."""
    q = clean_query(query).lower()
    s = clean_query(short_name).lower()
    l = clean_query(long_name).lower()

    score = 0.0
    if q and (q == s or q == l):
        score += 100
    if q and (s.startswith(q) or l.startswith(q)):
        score += 40
    if q and (q in s or q in l):
        score += 20
    if exchange == "NSE":
        score += 5
    return score


def pick_best(matches: list[TickerMatch]) -> TickerMatch | None:
    return matches[0] if matches else None
