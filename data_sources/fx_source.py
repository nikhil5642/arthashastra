"""Live FX — converts a listing's currency into the user's base currency.

Two jobs:

  - `major_unit(currency)`: Yahoo quotes some exchanges in minor units —
    London in pence ("GBp"), Johannesburg in cents ("ZAc"), Tel Aviv in
    agorot ("ILA"). Prices there are 1/100 of the major currency, while
    Yahoo's market cap is already in the major unit (verified on SHEL.L:
    price 3,722.5 GBp, market cap £210.8B).
  - `conversion_rate(currency, base)`: how many units of `base` one unit
    of `currency` buys, minor units included, from Yahoo's FX pairs
    (e.g. `USDINR=X`). Same currency → no request.
"""

from __future__ import annotations

import asyncio
import logging
import math

log = logging.getLogger(__name__)

# Minor-unit currency codes as Yahoo reports them → (major code, divisor).
_MINOR_UNITS: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 100.0),
    "GBX": ("GBP", 100.0),
    "ZAc": ("ZAR", 100.0),
    "ILA": ("ILS", 100.0),
}


def major_unit(currency: str) -> tuple[str, float]:
    """'GBp' → ('GBP', 100.0); 'usd' → ('USD', 1.0); '' → ('', 1.0)."""
    if currency in _MINOR_UNITS:
        return _MINOR_UNITS[currency]
    return currency.upper(), 1.0


async def conversion_rate(currency: str, base: str) -> float | None:
    """Units of `base` per ONE unit of `currency`; None when unknown."""
    if not currency or not base:
        return None
    major, divisor = major_unit(currency)
    if major == base.upper():
        return 1.0 / divisor
    loop = asyncio.get_running_loop()
    rate = await loop.run_in_executor(None, _fetch_major_rate_sync, major, base.upper())
    return rate / divisor if rate is not None else None


def _fetch_major_rate_sync(from_currency: str, to_currency: str) -> float | None:
    """Latest close of Yahoo's `<FROM><TO>=X` pair. Tests monkeypatch this."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — FX conversion disabled")
        return None
    pair = f"{from_currency}{to_currency}=X"
    try:
        closes = yf.Ticker(pair).history(period="5d")["Close"].dropna()
    except Exception as e:
        log.warning("FX fetch failed for %s: %s", pair, e)
        return None
    if closes.empty:
        return None
    rate = float(closes.iloc[-1])
    return rate if math.isfinite(rate) and rate > 0 else None
