"""Price history — SQLite-backed + technical indicators (RSI14, SMA).

Stores daily closes per ticker. Provides 52w high/low, SMA(n), RSI14, and a
composite PriceSnapshot used by the dip detector.

Phase 2 demo path: synthesize_from_anchors() generates deterministic series
from a BalanceSheetSnapshot's anchors (low_52w / high_52w / current_price) so
`pick` can run without live price data. Phase 5 replaces this with yfinance.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PriceSnapshot:
    ticker: str
    current_price: float
    high_52w: float
    low_52w: float
    sma_50: float
    sma_200: float | None
    rsi14: float


class PriceHistory:
    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS prices ("
            " ticker TEXT NOT NULL,"
            " date TEXT NOT NULL,"
            " close REAL NOT NULL,"
            " PRIMARY KEY(ticker, date)"
            ")"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def load_rows(self, ticker: str, rows: Iterable[tuple[str, float]]) -> int:
        rows = list(rows)
        self._conn.executemany(
            "INSERT OR REPLACE INTO prices(ticker, date, close) VALUES (?, ?, ?)",
            [(ticker.upper(), d, float(c)) for d, c in rows],
        )
        self._conn.commit()
        return len(rows)

    def closes(self, ticker: str, limit: int = 300) -> list[float]:
        cur = self._conn.execute(
            "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT ?",
            (ticker.upper(), limit),
        )
        rows = cur.fetchall()
        rows.reverse()
        return [c for _, c in rows]

    def latest(self, ticker: str) -> float | None:
        cur = self._conn.execute(
            "SELECT close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            (ticker.upper(),),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def snapshot(self, ticker: str) -> PriceSnapshot | None:
        closes = self.closes(ticker, limit=260)
        if len(closes) < 15:
            return None
        last = closes[-1]
        window_52w = closes[-252:] if len(closes) >= 252 else closes
        sma_50_closes = closes[-50:] if len(closes) >= 50 else closes
        sma_50 = _sma(sma_50_closes)
        sma_200 = _sma(closes[-200:]) if len(closes) >= 200 else None
        return PriceSnapshot(
            ticker=ticker.upper(),
            current_price=last,
            high_52w=max(window_52w),
            low_52w=min(window_52w),
            sma_50=sma_50,
            sma_200=sma_200,
            rsi14=rsi14(closes),
        )


def rsi14(closes: list[float]) -> float:
    """Wilder's 14-period RSI. Returns 50 if insufficient data."""
    if len(closes) < 15:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for prev, curr in zip(closes, closes[1:]):
        change = curr - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:14]) / 14
    avg_loss = sum(losses[:14]) / 14
    for g, l in zip(gains[14:], losses[14:]):
        avg_gain = (avg_gain * 13 + g) / 14
        avg_loss = (avg_loss * 13 + l) / 14
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _sma(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def synthesize_from_anchors(
    *,
    ticker: str,
    current_price: float,
    high_52w: float,
    low_52w: float,
    days: int = 252,
    end_date: date | None = None,
) -> list[tuple[str, float]]:
    """Deterministic synthetic series with a seeded walk low → high → current.

    The series hits exactly low_52w and high_52w at fixed days (so 52w range is
    reconstructable) and ends at current_price. Used only for demos and tests
    until real price ingestion arrives in Phase 5.
    """
    end_date = end_date or date.today()
    seed = int(hashlib.md5(ticker.upper().encode()).hexdigest(), 16)
    rng_state = seed & 0x7FFFFFFF

    def rand() -> float:
        nonlocal rng_state
        rng_state = (1103515245 * rng_state + 12345) & 0x7FFFFFFF
        return (rng_state / 0x7FFFFFFF) - 0.5  # -0.5..0.5

    low_day = int(days * 0.15)
    high_day = int(days * 0.65)
    lo_bound = min(low_52w, current_price)
    hi_bound = max(high_52w, current_price)

    rows: list[tuple[str, float]] = []
    for i in range(days):
        if i <= low_day:
            anchor = low_52w
        elif i <= high_day:
            frac = (i - low_day) / max(high_day - low_day, 1)
            anchor = low_52w + (high_52w - low_52w) * frac
        else:
            frac = (i - high_day) / max(days - 1 - high_day, 1)
            anchor = high_52w + (current_price - high_52w) * frac

        noise = anchor * 0.01 * rand()
        close = anchor + noise
        # Clamp to the anchor range so min/max stay pinned.
        close = max(lo_bound, min(hi_bound, close))
        day = end_date - timedelta(days=days - 1 - i)
        rows.append((day.isoformat(), round(close, 4)))

    rows[low_day] = (rows[low_day][0], float(low_52w))
    rows[high_day] = (rows[high_day][0], float(high_52w))
    rows[-1] = (rows[-1][0], float(current_price))
    return rows
