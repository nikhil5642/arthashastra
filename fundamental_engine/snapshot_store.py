"""Per-ticker snapshot cache — the full `BalanceSheetSnapshot` behind a score.

`analyse` and `refresh` save the snapshot they just scored to
`data_cache/snapshots/<TICKER>.json`. That keeps every line item screener
showed (`raw_profit_loss`, `raw_balance_sheet`, `raw_quarters`, …) plus
provenance and source conflicts reachable through `inspect <TICKER>`,
so a follow-up question doesn't need a re-scrape.

One file per ticker, written only by the command operating on that ticker.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from data_sources.fundamentals_source import BalanceSheetSnapshot
from signals.fileio import atomic_write_text

log = logging.getLogger(__name__)


class SnapshotStore:
    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir / "snapshots"
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, ticker: str) -> Path:
        return self._dir / f"{ticker.upper()}.json"

    def save(self, snap: BalanceSheetSnapshot) -> Path:
        fp = self.path_for(snap.ticker)
        atomic_write_text(fp, json.dumps(snap.to_dict(), indent=2, default=str))
        return fp

    def get(self, ticker: str) -> BalanceSheetSnapshot | None:
        fp = self.path_for(ticker)
        if not fp.exists():
            return None
        try:
            parsed = json.loads(fp.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("snapshot file corrupt: %s", fp)
            return None
        if not isinstance(parsed, dict):
            log.warning("snapshot file %s: top-level is not a dict", fp)
            return None
        return BalanceSheetSnapshot.from_dict(parsed)
