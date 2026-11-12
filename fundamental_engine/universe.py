"""Universe of tickers to scan — derived from cached state, not hardcoded.

The analyst does not ship with a curated list of companies. The
universe is whatever the user has loaded via `refresh <ticker>` or
`analyse <ticker>`, or whatever the engine has warmed. Commands like
`pick` and `brief` scan this emergent universe.

All load paths return typed `ExtendedScore` objects — callers should
never touch the raw `scores.json` dict directly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fundamental_engine.cache import JsonStore
from fundamental_engine.dip_candidate import DipCandidateRecord
from fundamental_engine.extended_score import ExtendedScore

log = logging.getLogger(__name__)


def load_universe(cache_dir: Path) -> list[str]:
    """The tickers the user follows, sorted alphabetically.

    Every cached row except the ones a screen or a comparison scored in
    passing: `refresh all` and the backtests work on what was asked about
    by name, not on everything that ever crossed a screen.
    """
    cached = JsonStore(cache_dir / "scores.json").read()
    in_passing = {t for t, extended in load_scores(cache_dir).items() if not extended.followed}
    return sorted(ticker for ticker in cached if ticker not in in_passing)


def load_scores(cache_dir: Path) -> dict[str, ExtendedScore]:
    """Return cached scores keyed by ticker, parsed into typed records.

    Rows that fail `ExtendedScore.from_dict` are skipped — they're
    almost always partially-written entries from an aborted refresh
    and the missing fields would cause downstream ranking to break.
    """
    raw = JsonStore(cache_dir / "scores.json").read()
    out: dict[str, ExtendedScore] = {}
    for ticker, data in raw.items():
        if not isinstance(data, dict):
            log.debug("load_scores: skipping non-object row for %s", ticker)
            continue
        entry = {**data, "ticker": ticker}
        parsed = ExtendedScore.from_dict(entry)
        if parsed is None:
            log.debug("load_scores: skipping unparseable row for %s", ticker)
            continue
        out[ticker] = parsed
    return out


def load_dip_candidates(cache_dir: Path) -> dict[str, DipCandidateRecord]:
    """Return cached dip candidates keyed by ticker, parsed into typed records."""
    raw = JsonStore(cache_dir / "dip_candidates.json").read()
    out: dict[str, DipCandidateRecord] = {}
    for ticker, data in raw.items():
        if not isinstance(data, dict):
            log.debug("load_dip_candidates: skipping non-object row for %s", ticker)
            continue
        parsed = DipCandidateRecord.from_dict(ticker, data)
        if parsed is None:
            log.debug(
                "load_dip_candidates: skipping unparseable row for %s", ticker
            )
            continue
        out[ticker] = parsed
    return out
