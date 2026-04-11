"""Signal schema — the language between feeds and on-demand reports.

The cache warmer writes signals. CLI commands (analyse / pick / brief) read them
to enrich reports. All communication happens through data_cache/signals.json.

Shape copied from trading/feeds/signals.py (Signal, SignalBus, Severity) so the
two projects share vocabulary without runtime coupling. The SignalType enum is
analyst-specific — no options/IV/OI types from trading.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)

SIGNALS_FILE = Path(__file__).parent.parent / "data_cache" / "signals.json"
MAX_SIGNALS_KEPT = 500  # larger than trading's 100 — analyst runs on longer horizons


class Severity(str, Enum):
    INFO = "INFO"       # FYI — no action needed
    ALERT = "ALERT"     # worth surfacing in next brief
    URGENT = "URGENT"   # surface on next CLI invocation regardless of freshness


class SignalType(str, Enum):
    # System / lifecycle
    UNIVERSE_REFRESHED = "UNIVERSE_REFRESHED"
    SCORE_UPDATED = "SCORE_UPDATED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    DATA_STALE = "DATA_STALE"
    FEED_ERROR = "FEED_ERROR"

    # Dip / buy triggers
    DIP_DETECTED = "DIP_DETECTED"
    QUALITY_DIP = "QUALITY_DIP"
    DEEP_VALUE = "DEEP_VALUE"

    # Leadership
    LEADERSHIP_NEWS = "LEADERSHIP_NEWS"
    LEADERSHIP_RED_FLAG = "LEADERSHIP_RED_FLAG"

    # Fundamentals events
    EARNINGS_BEAT = "EARNINGS_BEAT"
    EARNINGS_MISS = "EARNINGS_MISS"
    PROMOTER_SELLING = "PROMOTER_SELLING"
    PROMOTER_BUYING = "PROMOTER_BUYING"
    PLEDGE_INCREASE = "PLEDGE_INCREASE"
    CREDIT_DOWNGRADE = "CREDIT_DOWNGRADE"
    AUDITOR_CHANGE = "AUDITOR_CHANGE"

    # Price action
    PRICE_GAP = "PRICE_GAP"
    VOLUME_SPIKE = "VOLUME_SPIKE"


@dataclass
class Signal:
    signal_type: SignalType
    severity: Severity
    ticker: str
    message: str
    timestamp: str = ""
    data: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "signal_type": self.signal_type.value,
            "severity": self.severity.value,
            "ticker": self.ticker,
            "message": self.message,
            "timestamp": self.timestamp,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Signal:
        return cls(
            signal_type=SignalType(d["signal_type"]),
            severity=Severity(d["severity"]),
            ticker=d.get("ticker", ""),
            message=d["message"],
            timestamp=d.get("timestamp", ""),
            data=d.get("data", {}),
        )


class SignalBus:
    """Append-only JSON log of analyst events. Single writer = cache warmer."""

    def __init__(self, path: Path = SIGNALS_FILE, *, max_kept: int = MAX_SIGNALS_KEPT) -> None:
        self._path = path
        self._max_kept = max_kept
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, signal: Signal) -> None:
        signals = self._load_raw()
        signals.append(signal.to_dict())
        if len(signals) > self._max_kept:
            signals = signals[-self._max_kept:]
        self._path.write_text(json.dumps(signals, indent=2))

    def read_all(self) -> list[Signal]:
        return [Signal.from_dict(d) for d in self._load_raw()]

    def read_unread(self, last_read_ts: str = "") -> list[Signal]:
        all_signals = self.read_all()
        if not last_read_ts:
            return all_signals
        return [s for s in all_signals if s.timestamp > last_read_ts]

    def read_urgent(self) -> list[Signal]:
        return [s for s in self.read_all() if s.severity == Severity.URGENT]

    def read_for_ticker(self, ticker: str) -> list[Signal]:
        return [s for s in self.read_all() if s.ticker.upper() == ticker.upper()]

    def clear(self) -> None:
        self._path.write_text("[]")

    def _load_raw(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("signals.json corrupt — returning empty list")
            return []
