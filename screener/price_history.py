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
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PriceSnapshot:
    ticker: str
    current_price: float
    high_52w: float  # over the available window, at most 252 closes
    low_52w: float
    sma_50: float | None  # None until there are 50 closes
    sma_200: float | None  # None until there are 200 closes
    rsi14: float
    closes_count: int  # how much history the stats above were built from


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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
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
        """PriceSnapshot from the stored closes — see `snapshot_from_closes`."""
        return snapshot_from_closes(ticker, self.closes(ticker, limit=SNAPSHOT_WINDOW))

    def tickers(self) -> list[str]:
        cur = self._conn.execute("SELECT DISTINCT ticker FROM prices ORDER BY ticker")
        return [row[0] for row in cur.fetchall()]

    def rename_ticker(self, old: str, new: str) -> int:
        """Re-key every row of `old` to `new` (used by the ticker-ID migration)."""
        cur = self._conn.execute(
            "UPDATE OR REPLACE prices SET ticker = ? WHERE ticker = ?",
            (new.upper(), old.upper()),
        )
        self._conn.commit()
        return cur.rowcount or 0

    def delete(self, ticker: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM prices WHERE ticker = ?", (ticker.upper(),)
        )
        self._conn.commit()
        return cur.rowcount or 0


SNAPSHOT_WINDOW = 260  # a year of trading days plus a little slack
MIN_SNAPSHOT_CLOSES = 15


def snapshot_from_closes(ticker: str, closes: list[float]) -> PriceSnapshot | None:
    """Price stats from the newest `SNAPSHOT_WINDOW` closes (oldest first).

    Shared by the live store and the backtester, so both judge a dip the
    same way. Each moving average is only reported once there is enough
    history to compute it honestly — a "50-day SMA" over 20 closes is a
    20-day SMA wearing a misleading label. Callers that need a full year
    of range (the dip detector) gate on `closes_count`.
    """
    window = closes[-SNAPSHOT_WINDOW:]
    if len(window) < MIN_SNAPSHOT_CLOSES:
        return None
    window_52w = window[-252:]
    return PriceSnapshot(
        ticker=ticker.upper(),
        current_price=window[-1],
        high_52w=max(window_52w),
        low_52w=min(window_52w),
        sma_50=_sma(window[-50:]) if len(window) >= 50 else None,
        sma_200=_sma(window[-200:]) if len(window) >= 200 else None,
        rsi14=rsi14(window),
        closes_count=len(window),
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
