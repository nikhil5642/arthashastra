"""`migrate-ids` — one-time move from bare NSE tickers to Yahoo symbols.

Until 2026-11 every Indian listing was cached under its bare NSE symbol
("RELIANCE") with ".NS" silently assumed. Canonical IDs are now full
Yahoo symbols ("RELIANCE.NS", "MSFT", "0700.HK"), so a legacy key would
be read as a non-existent US ticker. This command:

  1. copies data_cache/ to a timestamped backup next to it,
  2. renames every legacy key to "<KEY>.NS" in scores.json,
     dip_candidates.json, signals.json, prices.db, and the per-ticker
     judgments/, snapshots/, news/ files,
  3. leaves a marker so it never runs twice — a second run would turn a
     genuine US ticker such as "MSFT" into "MSFT.NS".

Any dot-less key present before the migration is a legacy Indian key:
the old code could not cache anything else.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig
from screener.price_history import PriceHistoryStore
from signals.fileio import atomic_write_text

LEGACY_SUFFIX = ".NS"
MARKER_FILENAME = ".ids_migrated"
_PER_TICKER_DIRS: tuple[str, ...] = ("judgments", "snapshots", "news")


def run_migrate_command(*, dry_run: bool = False) -> int:
    cache_dir = AnalystConfig.from_env().data_cache_dir
    marker = cache_dir / MARKER_FILENAME
    if marker.exists():
        print("migrate-ids: already done — nothing to do.")
        return 0

    renames = _legacy_renames(cache_dir)
    if not renames:
        print("migrate-ids: no legacy tickers found.")
        if not dry_run:
            atomic_write_text(marker, datetime.now().isoformat())
        return 0

    print(f"migrate-ids: {len(renames)} legacy ticker(s) → Yahoo symbols")
    for old, new in sorted(renames.items()):
        print(f"  {old:<14} → {new}")
    if dry_run:
        print("(dry run — nothing changed)")
        return 0

    print(f"Backup: {_backup(cache_dir)}")
    _migrate_keyed_json(cache_dir / "scores.json", renames)
    _migrate_keyed_json(cache_dir / "dip_candidates.json", renames)
    _migrate_signals(cache_dir / "signals.json", renames)
    for name in _PER_TICKER_DIRS:
        _migrate_ticker_files(cache_dir / name, renames)
    _migrate_prices(cache_dir / "prices.db", renames)
    atomic_write_text(marker, datetime.now().isoformat())
    print("migrate-ids: done.")
    return 0


# ── Discovery ────────────────────────────────────────────────────────────────


def _legacy_renames(cache_dir: Path) -> dict[str, str]:
    """Every dot-less ticker in the cache → its '.NS' Yahoo symbol."""
    tickers: set[str] = set()
    tickers.update(JsonStore(cache_dir / "scores.json").read())
    tickers.update(JsonStore(cache_dir / "dip_candidates.json").read())
    for name in _PER_TICKER_DIRS:
        folder = cache_dir / name
        if folder.is_dir():
            tickers.update(p.stem for p in folder.glob("*.json"))
    if (cache_dir / "prices.db").exists():
        with PriceHistoryStore(cache_dir / "prices.db") as store:
            tickers.update(store.tickers())
    return {t: f"{t}{LEGACY_SUFFIX}" for t in tickers if t and "." not in t}


def _backup(cache_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = cache_dir.with_name(f"{cache_dir.name}.backup-{stamp}")
    shutil.copytree(cache_dir, target)
    return target


# ── Rewrites ─────────────────────────────────────────────────────────────────


def _retag(row: object, renames: dict[str, str]) -> object:
    """Rename the `ticker` field inside a row (or each row of a list)."""
    if isinstance(row, list):
        return [_retag(item, renames) for item in row]
    if isinstance(row, dict) and isinstance(row.get("ticker"), str):
        return {**row, "ticker": renames.get(row["ticker"], row["ticker"])}
    return row


def _migrate_keyed_json(path: Path, renames: dict[str, str]) -> None:
    store = JsonStore(path)
    data = store.read()
    if data:
        store.write({renames.get(k, k): _retag(v, renames) for k, v in data.items()})


def _migrate_signals(path: Path, renames: dict[str, str]) -> None:
    if not path.exists():
        return
    try:
        rows = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return
    if isinstance(rows, list):
        atomic_write_text(path, json.dumps(_retag(rows, renames), indent=2))


def _migrate_ticker_files(folder: Path, renames: dict[str, str]) -> None:
    if not folder.is_dir():
        return
    for old_path in folder.glob("*.json"):
        new_name = renames.get(old_path.stem)
        if new_name is None:
            continue
        try:
            content = json.loads(old_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        new_path = old_path.with_name(f"{new_name}.json")
        atomic_write_text(new_path, json.dumps(_retag(content, renames), indent=2))
        old_path.unlink()


def _migrate_prices(db_path: Path, renames: dict[str, str]) -> None:
    if not db_path.exists():
        return
    with PriceHistoryStore(db_path) as store:
        for old, new in renames.items():
            store.rename_ticker(old, new)
