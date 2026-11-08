# signals/

File-based pub/sub for analyst events, plus the crash-safe file
primitives every JSON store uses. The engine writes signals during
`refresh_one` / `refresh_all`; `brief` and `pick` read them to surface
recent activity and to block tickers with `LEADERSHIP_RED_FLAG`.

## Modules

| Module | Purpose |
|---|---|
| `signal_bus.py` | `Signal` (dataclass), `SignalType` / `Severity` `StrEnum`s, `SignalBus` (append-only JSON log backed by a file). `path=` is always required at construction time. A corrupt or unknown row is skipped, never fatal to `read_all()`. |
| `fileio.py` | `atomic_write_text` (temp file + `os.replace`, so readers never see a half-written file) and `file_lock` (advisory lock around read-modify-write). Used by the signal bus, `JsonStore`, `JudgmentStore`, and `SnapshotStore`. |

## Signal types

Grouped in `SignalType`:

- **System / lifecycle** — `UNIVERSE_REFRESHED`, `SCORE_UPDATED`,
  `BUDGET_EXHAUSTED`, `DATA_STALE`, `FEED_ERROR`
- **Dip / buy triggers** — `DIP_DETECTED`, `QUALITY_DIP`, `DEEP_VALUE`
- **Leadership** — `LEADERSHIP_NEWS`, `LEADERSHIP_RED_FLAG`
- **Fundamentals events** — `EARNINGS_BEAT`, `EARNINGS_MISS`,
  `PROMOTER_SELLING`, `PROMOTER_BUYING`, `PLEDGE_INCREASE`,
  `CREDIT_DOWNGRADE`, `AUDITOR_CHANGE`
- **Price action** — `PRICE_GAP`, `VOLUME_SPIKE`

## Rules

- **Single writer, many readers.** Only `engine.refresh_one` / `refresh_all`
  and `commands.pick.run_pick_command` emit. Everything else reads.
- **Writes are atomic and locked**, so the engine and a CLI command
  touching the same file can't lose each other's rows.
- **Append-only log, bounded.** `MAX_SIGNALS_KEPT = 500`. The bus
  truncates on emit to keep the file small.
- **`path=` is required.** Callers always pass
  `cfg.data_cache_dir / "signals.json"` explicitly.
