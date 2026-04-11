# screener/

Pure-logic screening layer. Takes pre-computed scores and real price
history, produces buy-the-dip signals.

## Modules

| Module | Purpose |
|---|---|
| `price_history.py` | `PriceHistoryStore` — SQLite wrapper over `data_cache/prices.db`. One row per `(ticker, date)`. Exposes `snapshot(ticker)` returning a real `PriceSnapshot` with SMA(50/200) and Wilder RSI14. `rsi14()` is a standalone function for pure unit tests. |
| `dip_detector.py` | `check_dip()` — multi-gate buy-the-dip logic. Gates: quality (composite + verdict), drawdown ≥ 20% from 52w high, ≥ 2 technical confirmations (below 50DMA, RSI < 40, near 52w low), not on the blocked-ticker list. Returns a `DipCheck` containing a `DipSignal` when all gates pass. |

## Rules

- **No synthetic prices.** The `prices.db` is populated by
  `engine.refresh_one` from the same yfinance `.history()` call that
  powers the fundamentals snapshot. There is no longer any function that
  generates fake daily closes from anchors.
- **Tickers without cached price history are skipped** — `pick` surfaces
  a "N skipped — run refresh" warning rather than silently filtering
  them out.
- **`check_dip` is pure** — no file or network I/O. It takes a
  `StockScore` + `PriceSnapshot` and returns a verdict. All persistence
  happens in the calling layer (`reports/picks_report.py`).
