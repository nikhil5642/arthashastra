# screener/

Pure-logic screening layer. Takes pre-computed scores and real price
history, produces buy-the-dip signals.

## Modules

| Module | Purpose |
|---|---|
| `price_history.py` | `PriceHistoryStore` — SQLite wrapper over `data_cache/prices.db`, one row per `(ticker, date)` in the listing's trading currency. `snapshot(ticker)` returns a real `PriceSnapshot` with Wilder RSI14, SMA50 / SMA200 only once there are 50 / 200 closes, and `closes_count`. |
| `dip_detector.py` | `check_dip()` — multi-gate buy-the-dip logic. Gates: not blocked (integrity veto), quality (composite + verdict), at least 120 daily closes, drawdown ≥ 20% from the 52w high, ≥ 2 technical confirmations (below 50DMA, RSI < 40, near 52w low). Earnings divergence (latest-quarter PAT ≥ +15% YoY, revenue not shrinking) adds a ranking bonus and, with a ≥ 30% drawdown, URGENT severity. Early signals are not a gate — a dip usually comes with concerns — `pick` shows them beside each candidate instead. |

## Rules

- **No synthetic prices.** The `prices.db` is populated by
  `engine.refresh_one` from the same yfinance `.history()` call that
  powers the fundamentals snapshot. There is no longer any function that
  generates fake daily closes from anchors.
- **Tickers without cached price history are skipped** — `pick` surfaces
  a "N skipped — run refresh" warning rather than silently filtering
  them out.
- **`check_dip` is pure** — no file or network I/O. It takes a
  `StockScore` + `PriceSnapshot` (+ cached YoY growth) and returns a
  verdict. All persistence happens in `fundamental_engine/commands/pick.py`.
