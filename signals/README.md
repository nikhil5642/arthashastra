# signals/

File-based pub/sub for analyst events, plus the crash-safe file
primitives every JSON store uses. Every `analyse` / `refresh` writes what
it found and what changed since the previous look at that ticker; `pick`
adds the dips it found; `brief` reads the log back.

This is the leaf of the architecture: it imports nothing from the rest
of the project.

## Modules

| Module | Purpose |
|---|---|
| `signal_bus.py` | `Signal` (dataclass), `SignalType` / `Severity` `StrEnum`s, `SignalBus` (append-only JSON log backed by a file; `emit`, and `emit_many` for several rows in one locked write). `path=` is always required at construction time. A corrupt or unknown row is skipped, never fatal to `read_all()`. |
| `fileio.py` | `atomic_write_text` (temp file + `os.replace`, so readers never see a half-written file) and `file_lock` (advisory lock around read-modify-write). Used by the signal bus, `JsonStore`, `JudgmentStore`, and `SnapshotStore`. |

## Signal types

Every `SignalType` has an emitter — there are no placeholder types.

| Type | Emitted by | When |
|---|---|---|
| `UNIVERSE_REFRESHED` | `Engine.refresh_all` | once per full refresh |
| `SCORE_UPDATED` | `tracking.record_analysis` | every analysis; the message says how far the composite moved |
| `VERDICT_CHANGED` | `tracking.record_analysis` | the composite crossed into another band since the last look |
| `EARLY_CONCERN` | `tracking.record_analysis` | a material early concern that is new, or more severe than last time |
| `EARLY_STRENGTH` | `tracking.record_analysis` | a material early strength that is new |
| `DIP_DETECTED` | `commands.pick` | a quality dip passed every gate |

## Severity

`INFO` < `ALERT` < `URGENT` (`Severity.rank`). The same scale grades bus
rows, early signals and dip signals: INFO is context, ALERT is material,
URGENT should be read before trusting the headline numbers. INFO early
signals stay in the stock's own report and never reach the bus.

## Rules

- **Few writers, many readers.** `fundamental_engine.tracking.record_analysis`
  (which `analyse`, `refresh`, `screen --score` and the engine all end
  in), `Engine.refresh_all`, and `commands.pick.run_pick_command` emit.
  Everything else reads.
- **Writes are atomic and locked**, so the engine and a CLI command
  touching the same file can't lose each other's rows.
- **Append-only log, bounded.** `MAX_SIGNALS_KEPT = 500`. The bus
  truncates on emit to keep the file small.
- **`path=` is required.** Callers always pass
  `cfg.data_cache_dir / "signals.json"` explicitly.
- **Don't add a type without an emitter.** A type nothing emits reads as
  a feature that exists. The bus once defined 22 types and emitted three;
  `pick` and `brief` checked for a `LEADERSHIP_RED_FLAG` that nothing
  ever wrote.
