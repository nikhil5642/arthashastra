# signals/

File-based pub/sub for analyst events. The engine writes signals during
`refresh_one` / `refresh_all`; `brief` and `picks_report` read them to
surface recent activity and to gate on `LEADERSHIP_RED_FLAG` blocks.

## Modules

| Module | Purpose |
|---|---|
| `signal_bus.py` | `Signal` (dataclass), `SignalType` / `Severity` enums, `SignalBus` (append-only JSON log backed by a file). `path=` is always required at construction time — there is no module-level default that could silently disagree with `DATA_CACHE_DIR`. |

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
  and `picks_report.run_pick_command` emit. Everything else reads.
- **Append-only log, bounded.** `MAX_SIGNALS_KEPT = 500`. The bus
  truncates on emit to keep the file small.
- **`path=` is required.** Callers always pass
  `cfg.data_cache_dir / "signals.json"` explicitly.
