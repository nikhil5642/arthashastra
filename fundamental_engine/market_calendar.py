"""NSE market hours — weekday + time-of-day gate only.

No hardcoded holiday list. When the engine runs on a holiday, yfinance will
simply return the last trading day's data, which is fine for cache warming.
The calendar is used only to skip wasted fetches during off-hours.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN_TIME = time(9, 15)
MARKET_CLOSE_TIME = time(15, 30)


class MarketCalendar:
    def now_ist(self) -> datetime:
        return datetime.now(IST)

    def today(self) -> date:
        return self.now_ist().date()

    def is_weekday(self, dt: datetime | None = None) -> bool:
        dt = dt or self.now_ist()
        return dt.weekday() < 5

    def is_market_open(self, dt: datetime | None = None) -> bool:
        dt = dt or self.now_ist()
        if not self.is_weekday(dt):
            return False
        t = dt.time()
        return MARKET_OPEN_TIME <= t <= MARKET_CLOSE_TIME
