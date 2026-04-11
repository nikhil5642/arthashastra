"""Real daily-close price history — SQLite-backed + technical indicators.

Stores daily closes per ticker. Provides 52w high/low, SMA(n), RSI14, and a
composite PriceSnapshot used by the dip detector.

The engine's refresh loop persists yfinance's real `history()` output into
this store at the same time it persists the extended StockScore. picks /
brief / analyse all read real SMA / RSI values from here — there is no
synthetic price generator anywhere in the project.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
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


class PriceHistoryStore:
    """Thin SQLite wrapper. One row per (ticker, date)."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
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
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "PriceHistoryStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def load_rows(
        self, ticker: str, rows: Iterable[tuple[str, float]]
    ) -> int:
        """Insert-or-replace rows. Returns the number of rows written."""
        rows = list(rows)
        if not rows:
            return 0
        self._conn.executemany(
            "INSERT OR REPLACE INTO prices(ticker, date, close) VALUES (?, ?, ?)",
            [(ticker.upper(), d, float(c)) for d, c in rows],
        )
        self._conn.commit()
        return len(rows)

    def closes(self, ticker: str, limit: int = 300) -> list[float]:
        cur = self._conn.execute(
            "SELECT date, close FROM prices "
            "WHERE ticker = ? ORDER BY date DESC LIMIT ?",
            (ticker.upper(), limit),
        )
        rows = cur.fetchall()
        rows.reverse()
        return [c for _, c in rows]

    def latest(self, ticker: str) -> float | None:
        cur = self._conn.execute(
            "SELECT close FROM prices WHERE ticker = ? "
            "ORDER BY date DESC LIMIT 1",
            (ticker.upper(),),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def snapshot(self, ticker: str) -> PriceSnapshot | None:
        """Return a PriceSnapshot built from at least 15 real daily closes."""
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

    def delete(self, ticker: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM prices WHERE ticker = ?", (ticker.upper(),)
        )
        self._conn.commit()
        return cur.rowcount or 0


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
