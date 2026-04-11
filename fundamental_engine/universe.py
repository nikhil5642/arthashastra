"""Universe of tickers to scan — derived from cached state, not hardcoded.

The analyst does not ship with a curated list of companies. The universe is
whatever the user has loaded via `refresh <ticker>` or `analyse <ticker>`,
or whatever the engine has warmed. Commands like `pick` and `brief` scan
this emergent universe.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fundamental_engine.cache import JsonStore

log = logging.getLogger(__name__)


def load_universe(cache_dir: Path) -> list[str]:
    """Return the tickers we currently hold score data for, sorted alphabetically."""
    scores = JsonStore(cache_dir / "scores.json").read()
    return sorted(scores.keys())


def load_universe_with_metadata(cache_dir: Path) -> list[dict]:
    """Return cached score entries as a list of dicts (one per ticker)."""
    scores = JsonStore(cache_dir / "scores.json").read()
    out = []
    for ticker, data in scores.items():
        entry = {"ticker": ticker}
        entry.update(data)
        out.append(entry)
    out.sort(key=lambda e: e["ticker"])
    return out
