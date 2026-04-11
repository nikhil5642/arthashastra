"""Fundamentals data source — live yfinance + screener.in.

Priority order:
    1. yfinance statements + history: D/E, interest coverage, CAGRs, price range,
       and the raw daily close series used downstream for SMA / RSI.
    2. screener.in HTML scrape: ROE, ROCE, promoter holding, pledge %, market cap.

yfinance's .info / .fast_info are unreliable in 2026 due to Yahoo rate limiting —
we use .history(), .balance_sheet, .income_stmt, .cashflow instead. screener
picks up whatever yfinance misses and takes precedence for pre-computed ratios
(more reliable than our statement-based computations for Indian stocks).

No fixtures, no hardcoded data — every BalanceSheetSnapshot is built from
live API calls at fetch time.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import httpx

log = logging.getLogger(__name__)

SCORED_FIELDS: tuple[str, ...] = (
    "debt_to_equity",
    "interest_coverage",
    "roe",
    "roce",
    "rev_3y_cagr",
    "pat_3y_cagr",
    "ocf_to_pat",
    "promoter_holding",
    "pledge_pct",
)

SCREENER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
SCREENER_BASE_URL = "https://www.screener.in/company"
HTTP_TIMEOUT_SECONDS = 20.0

# Fields where screener.in is authoritative (pre-computed, India-specific).
_SCREENER_WINS: frozenset[str] = frozenset({
    "roe",
    "roce",
    "promoter_holding",
    "pledge_pct",
    "market_cap_cr",
})

PriceRow = tuple[str, float]  # (ISO date, adjusted close)


@dataclass(slots=True)
class BalanceSheetSnapshot:
    ticker: str
    as_of_date: str
    debt_to_equity: float | None = None
    interest_coverage: float | None = None
    roe: float | None = None
    roce: float | None = None
    rev_3y_cagr: float | None = None
    pat_3y_cagr: float | None = None
    ocf_to_pat: float | None = None
    promoter_holding: float | None = None
    pledge_pct: float | None = None
    current_price: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    market_cap_cr: float | None = None
    company_name: str = ""
    sector: str = ""
    industry: str = ""
    source: str = "unknown"
    field_sources: dict[str, str] = field(default_factory=dict)

    @property
    def data_completeness_pct(self) -> float:
        present = sum(1 for f in SCORED_FIELDS if getattr(self, f) is not None)
        return round(100.0 * present / len(SCORED_FIELDS), 1)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BalanceSheetSnapshot":
        allowed = set(cls.__dataclass_fields__)
        kwargs = {k: v for k, v in data.items() if k in allowed}
        kwargs.setdefault("ticker", "")
        kwargs.setdefault("as_of_date", date.today().isoformat())
        return cls(**kwargs)


@dataclass(slots=True)
class FetchResult:
    """Everything a single yfinance + screener fetch produces.

    `snapshot` carries the deterministic inputs the fundamentals scorer
    needs. `price_history` carries the raw daily close series the engine
    persists to the price history store so downstream dip detection uses
    real SMA / RSI values instead of synthesised ones.
    """

    snapshot: BalanceSheetSnapshot
    price_history: list[PriceRow] = field(default_factory=list)


class FundamentalsSource:
    """Async facade over live yfinance + screener.in."""

    async def fetch(self, ticker: str) -> BalanceSheetSnapshot:
        """Convenience wrapper returning just the snapshot."""
        result = await self.fetch_full(ticker)
        return result.snapshot

    async def fetch_full(self, ticker: str) -> FetchResult:
        """Fetch snapshot + raw daily close history in a single round trip."""
        bare = ticker.strip().upper()
        log.info("fundamentals: live fetch for %s", bare)

        yf_task = _fetch_yfinance(bare)
        screener_task = _fetch_screener_in(bare)
        yf_result, screener_data = await asyncio.gather(
            yf_task, screener_task, return_exceptions=True
        )

        yf_data: dict[str, Any]
        price_history: list[PriceRow]
        if isinstance(yf_result, Exception):
            log.warning("yfinance failed for %s: %s", bare, yf_result)
            yf_data, price_history = {}, []
        else:
            yf_data, price_history = yf_result

        if isinstance(screener_data, Exception):
            log.warning("screener.in failed for %s: %s", bare, screener_data)
            screener_data = {}

        merged = _merge(yf_data, screener_data)
        merged["ticker"] = bare
        merged["as_of_date"] = date.today().isoformat()
        merged["source"] = _source_tag(yf_data, screener_data)

        allowed = set(BalanceSheetSnapshot.__dataclass_fields__)
        kwargs = {k: v for k, v in merged.items() if k in allowed}
        snap = BalanceSheetSnapshot(**kwargs)
        snap.field_sources = _field_sources(yf_data, screener_data)
        return FetchResult(snapshot=snap, price_history=price_history)


# ── yfinance adapter ──────────────────────────────────────────────────────────


async def _fetch_yfinance(ticker: str) -> tuple[dict[str, Any], list[PriceRow]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_yfinance_sync, ticker)


def _fetch_yfinance_sync(ticker: str) -> tuple[dict[str, Any], list[PriceRow]]:
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed")
        return {}, []

    from fundamental_engine.ticker import yf_symbol

    symbol = yf_symbol(ticker)

    try:
        t = yf.Ticker(symbol)
    except Exception as e:
        log.warning("yfinance Ticker() failed for %s: %s", ticker, e)
        return {}, []

    hist = _safe(lambda: t.history(period="1y", auto_adjust=True), label=f"history {ticker}")
    bs = _safe(lambda: t.balance_sheet, label=f"balance_sheet {ticker}")
    is_ = _safe(lambda: t.income_stmt, label=f"income_stmt {ticker}")
    cf = _safe(lambda: t.cashflow, label=f"cashflow {ticker}")

    out: dict[str, Any] = {}
    price_history: list[PriceRow] = []

    # Price context + daily closes from the same history call.
    if hist is not None and not hist.empty:
        closes = hist["Close"].dropna()
        if len(closes) > 0:
            out["current_price"] = float(closes.iloc[-1])
            out["high_52w"] = float(hist["High"].max())
            out["low_52w"] = float(hist["Low"].min())
            for ts, close in closes.items():
                try:
                    date_str = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]
                    price_history.append((date_str, float(close)))
                except Exception:
                    continue

    total_debt = _bs_first(bs, ("Total Debt", "Long Term Debt"))
    equity = _bs_first(
        bs, ("Common Stock Equity", "Stockholders Equity", "Total Stockholder Equity")
    )
    if total_debt is not None and equity is not None and equity > 0:
        out["debt_to_equity"] = round(total_debt / equity, 3)

    ebit = _bs_first(is_, ("EBIT", "Operating Income"))
    interest = _bs_first(is_, ("Interest Expense",))
    # Interest coverage only makes sense when EBIT is positive — a loss-making
    # company has no coverage no matter what the ratio looks like numerically.
    if (
        ebit is not None
        and ebit > 0
        and interest is not None
        and interest != 0
    ):
        out["interest_coverage"] = round(ebit / abs(interest), 2)

    net_income = _bs_first(is_, ("Net Income", "Net Income Common Stockholders"))
    if net_income is not None and equity is not None and equity > 0:
        out["roe"] = round(net_income / equity * 100, 2)

    total_assets = _bs_first(bs, ("Total Assets",))
    current_liabilities = _bs_first(
        bs, ("Current Liabilities", "Total Current Liabilities")
    )
    if (
        ebit is not None
        and total_assets is not None
        and current_liabilities is not None
    ):
        capital_employed = total_assets - current_liabilities
        if capital_employed > 0:
            out["roce"] = round(ebit / capital_employed * 100, 2)

    rev_cagr = _compute_cagr(is_, ("Total Revenue", "Operating Revenue"), years=3)
    if rev_cagr is not None:
        out["rev_3y_cagr"] = rev_cagr
    pat_cagr = _compute_cagr(
        is_, ("Net Income", "Net Income Common Stockholders"), years=3
    )
    if pat_cagr is not None:
        out["pat_3y_cagr"] = pat_cagr

    ocf = _bs_first(
        cf,
        (
            "Operating Cash Flow",
            "Cash Flow From Continuing Operating Activities",
        ),
    )
    if ocf is not None and net_income is not None and net_income > 0:
        out["ocf_to_pat"] = round(ocf / net_income, 3)

    info = _safe(lambda: t.info or {}, label=f"info {ticker}") or {}
    if info:
        out.setdefault("company_name", info.get("longName") or info.get("shortName") or "")
        out.setdefault("sector", info.get("sector") or "")
        out.setdefault("industry", info.get("industry") or "")

    return out, price_history


def _safe(fn, *, label: str):
    try:
        return fn()
    except Exception as e:
        log.debug("yfinance %s raised %s", label, e)
        return None


def _bs_first(df, candidates: tuple[str, ...]) -> float | None:
    if df is None:
        return None
    try:
        if df.empty:
            return None
    except Exception:
        return None
    for name in candidates:
        try:
            if name in df.index:
                val = df.loc[name].iloc[0]
                if val is None:
                    continue
                v = float(val)
                if v != v:  # NaN guard
                    continue
                return v
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None


def _compute_cagr(
    df, candidates: tuple[str, ...], *, years: int
) -> float | None:
    if df is None:
        return None
    try:
        if df.empty:
            return None
    except Exception:
        return None
    for name in candidates:
        if name not in df.index:
            continue
        try:
            series = df.loc[name]
            if len(series) < years + 1:
                continue
            current = float(series.iloc[0])
            past = float(series.iloc[years])
            if past <= 0 or current <= 0:
                continue
            return round((((current / past) ** (1 / years)) - 1) * 100, 2)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return None


# ── screener.in adapter ───────────────────────────────────────────────────────


async def _fetch_screener_in(ticker: str) -> dict[str, Any]:
    urls = [
        f"{SCREENER_BASE_URL}/{ticker.upper()}/consolidated/",
        f"{SCREENER_BASE_URL}/{ticker.upper()}/",
    ]
    headers = {"User-Agent": SCREENER_USER_AGENT}

    resp = None
    for url in urls:
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                break
        except Exception as e:
            log.warning("screener.in fetch error (%s): %s", url, e)
            continue

    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp is not None else "no response"
        log.warning("screener.in returned %s for %s", status, ticker)
        return {}

    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        log.warning("selectolax not installed — screener parsing skipped")
        return {}

    tree = HTMLParser(resp.text)
    out: dict[str, Any] = {}

    for item in tree.css("ul#top-ratios li"):
        name_el = item.css_first(".name")
        val_el = item.css_first(".number")
        if not (name_el and val_el):
            continue
        name = name_el.text(strip=True).lower()
        raw = val_el.text(strip=True)
        if "roce" in name:
            v = _parse_pct(raw)
            if v is not None:
                out["roce"] = v
        elif "roe" in name:
            v = _parse_pct(raw)
            if v is not None:
                out["roe"] = v
        elif "market cap" in name:
            v = _parse_float(raw)
            if v is not None:
                out["market_cap_cr"] = v
        elif "current price" in name:
            v = _parse_float(raw)
            if v is not None:
                out["current_price"] = v

    h1 = tree.css_first("h1")
    if h1:
        text = h1.text(strip=True)
        if text:
            out["company_name"] = text

    sh = tree.css_first("#shareholding")
    if sh is not None:
        for row in sh.css("table tbody tr"):
            cells = row.css("td")
            if not cells:
                continue
            label = cells[0].text(strip=True).lower().rstrip("+").strip()
            vals = [c.text(strip=True) for c in cells[1:]]
            if not vals:
                continue
            latest = _parse_pct(vals[-1])
            if latest is None:
                continue
            if label.startswith("promoter") and "pledge" not in label:
                out["promoter_holding"] = latest
            elif "pledge" in label:
                out["pledge_pct"] = latest

    if sh is not None and "promoter_holding" in out and "pledge_pct" not in out:
        out["pledge_pct"] = 0.0

    return out


def _parse_pct(text: str) -> float | None:
    if text is None:
        return None
    try:
        cleaned = text.strip().replace(",", "").rstrip("%")
        return float(cleaned)
    except (ValueError, AttributeError):
        return None


def _parse_float(text: str) -> float | None:
    if text is None:
        return None
    try:
        return float(text.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return None


# ── Merge logic ───────────────────────────────────────────────────────────────


def _merge(
    yf_data: dict[str, Any], screener_data: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(yf_data)
    for key, value in screener_data.items():
        if value is None:
            continue
        if key in _SCREENER_WINS or key not in merged or merged[key] in (None, ""):
            merged[key] = value
    return merged


def _source_tag(yf_data: dict[str, Any], screener_data: dict[str, Any]) -> str:
    yf_ok = bool(yf_data)
    sc_ok = bool(screener_data)
    if yf_ok and sc_ok:
        return "yfinance+screener"
    if yf_ok:
        return "yfinance"
    if sc_ok:
        return "screener"
    return "empty"


def _field_sources(
    yf_data: dict[str, Any], screener_data: dict[str, Any]
) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in SCORED_FIELDS:
        if f in screener_data and (f in _SCREENER_WINS or f not in yf_data):
            out[f] = "screener"
        elif f in yf_data:
            out[f] = "yfinance"
    return out
