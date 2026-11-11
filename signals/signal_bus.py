"""Signal bus — append-only JSON log of analyst events.

Every `analyse` / `refresh` records what changed since the previous look
at a ticker (a new verdict, a new or worsened early concern, a new
strength); `pick` adds the dips it found; `brief` reads the log back.

All communication happens through a JSON file whose path is always
supplied explicitly by the caller (typically
`AnalystConfig.data_cache_dir / "signals.json"`) — there is no module-level
default path that could silently disagree with DATA_CACHE_DIR.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from signals.fileio import atomic_write_text, file_lock

log = logging.getLogger(__name__)

MAX_SIGNALS_KEPT = 500


class Severity(StrEnum):
    """Least to most severe — `rank` follows the definition order."""

    INFO = "INFO"       # context — no action needed
    ALERT = "ALERT"     # material — worth surfacing in the next brief
    URGENT = "URGENT"   # read before trusting the headline numbers

    @property
    def rank(self) -> int:
        """INFO 0 < ALERT 1 < URGENT 2."""
        return tuple(Severity).index(self)


class SignalType(StrEnum):
    """Everything the bus carries. Each type has an emitter — no placeholders."""

    # Lifecycle
    UNIVERSE_REFRESHED = "UNIVERSE_REFRESHED"
    SCORE_UPDATED = "SCORE_UPDATED"

    # What changed between two looks at one ticker (fundamental_engine.tracking)
    VERDICT_CHANGED = "VERDICT_CHANGED"
    EARLY_CONCERN = "EARLY_CONCERN"
    EARLY_STRENGTH = "EARLY_STRENGTH"

    # Buy triggers (pick)
    DIP_DETECTED = "DIP_DETECTED"


@dataclass
class Signal:
    signal_type: SignalType
    severity: Severity
    ticker: str
    message: str
    timestamp: str = ""
    data: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> dict[str, object]:
        return {
            "signal_type": self.signal_type.value,
            "severity": self.severity.value,
            "ticker": self.ticker,
            "message": self.message,
            "timestamp": self.timestamp,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> Signal | None:
        """Parse one stored row; None when it doesn't fit the schema.

        One corrupt or future-version row must not make `read_all()` raise
        for every reader, so unknown enum values and missing fields are
        skipped rather than propagated.
        """
        try:
            signal_type = SignalType(str(d.get("signal_type")))
            severity = Severity(str(d.get("severity")))
        except ValueError:
            return None
        message = d.get("message")
        if not isinstance(message, str):
            return None
        ticker = d.get("ticker")
        timestamp = d.get("timestamp")
        data = d.get("data")
        return cls(
            signal_type=signal_type,
            severity=severity,
            ticker=ticker if isinstance(ticker, str) else "",
            message=message,
            timestamp=timestamp if isinstance(timestamp, str) else "",
            data={str(k): v for k, v in data.items()} if isinstance(data, dict) else {},
        )


class SignalBus:
    """Append-only JSON log. Callers must pass the file path explicitly."""

    def __init__(self, *, path: Path, max_kept: int = MAX_SIGNALS_KEPT) -> None:
        self._path = path
        self._max_kept = max_kept
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, signal: Signal) -> None:
        self.emit_many([signal])

    def emit_many(self, signals: Sequence[Signal]) -> None:
        """Append several signals in one locked read-modify-write."""
        if not signals:
            return
        with file_lock(self._path):
            rows = self._load_raw()
            rows.extend(signal.to_dict() for signal in signals)
            if len(rows) > self._max_kept:
                rows = rows[-self._max_kept:]
            atomic_write_text(self._path, json.dumps(rows, indent=2))

    def read_all(self) -> list[Signal]:
        parsed = (Signal.from_dict(d) for d in self._load_raw())
        return [s for s in parsed if s is not None]

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
        with file_lock(self._path):
            atomic_write_text(self._path, "[]")

    def _load_raw(self) -> list[dict[str, object]]:
        if not self._path.exists():
            return []
        try:
            parsed = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("signals file %s corrupt — returning empty list", self._path)
            return []
        if not isinstance(parsed, list):
            log.warning(
                "signals file %s top-level is %s, not a list — returning empty list",
                self._path,
                type(parsed).__name__,
            )
            return []
        return [row for row in parsed if isinstance(row, dict)]
