---
name: pick-stocks
description: Find diversified buy-the-dip candidates that fit a budget in your base currency — from a live screen of every listed stock in the markets and sectors the user names, or from the warm cache.
argument-hint: <amount in BASE_CURRENCY — e.g. 100000, 50k, 2.5m, 5L> [filters — e.g. "US and India tech, large caps"]
---

Find quality stocks in a dip for **$ARGUMENTS**. Split the arguments into
the budget (`100000`, `50k`, `2.5m`, `5L` — in `BASE_CURRENCY`, default
INR) and any filters (markets, sectors, size, valuation).

## Step 1 — Build the candidate set

**The user named filters, or the cache holds few names** → screen the
world for dips (map words to flags as in `/screen-stocks`):

```bash
python3 -m fundamental_engine screen <filters> --min-drawdown 20 --score 15 --json
```

The scored tickers join the cache — keep their list for Step 3. Without
filters, a dip screen of all 59 markets takes about 1½ minutes.

**No filters and a warm cache** → go to Step 3 without `--tickers`.

## Step 2 — Judge the strongest

`pick`'s default bar (composite ≥ 75 and BUY) needs Claude's leadership,
prospects and news judgments: a deterministic-only composite rarely
clears 70. Run the `/analyse-stock` recipe on the 3 strongest scored
names (fundamentals + valuation, deepest credible dip). Judge at most 5
in one pass and tell the user which ones you judged.

## Step 3 — Run pick

```bash
python3 -m fundamental_engine pick --amount <BUDGET> --tickers <scored tickers>
```

Every listing's price — USD, HKD, JPY, pence, … — is converted with the
FX rate cached at its refresh, so one budget sizes positions across
markets. Defaults: `--min-composite 75`, `--min-verdict BUY`; picks are
capped at 2 per sector and 4 per country. Share counts are indicative:
check each exchange's lot size (e.g. 100-share units in Japan) before
ordering. The command also writes `data_cache/dip_candidates.json`, and
names any chosen ticker that isn't cached.

## Step 4 — Interpret an empty result

1. **Nothing judged yet** — composites sit near 50–70 until Step 2 runs;
   say so rather than lowering the bar silently.
2. **Everything below the gate** — show what a wider gate finds, clearly
   labelled as below the bar:
   `python3 -m fundamental_engine pick --amount <BUDGET> --tickers … --min-composite 60 --min-verdict HOLD`
3. **No dips** — the dip gate needs 20%+ below the 52-week high plus two
   technical confirmations (below the 50-day average, RSI < 40, near the
   52-week low); a screen with `--min-drawdown 20` only covers the first.
4. **"(no FX rate)"** — that listing's currency had no Yahoo quote at
   refresh time; `/refresh-now <TICKER>` retries it.

## Step 5 — Add qualitative colour on top of the ranked picks

For each pick, read its cached evidence:

```bash
python3 -m fundamental_engine inspect <TICKER>
```

Tell the user:
- Why this dip is genuinely interesting (beyond the mechanical reason) —
  check the quarterly trend: a profit "jump" can be a year-ago one-off
- Which candidate you'd actually put money into first
- Concentration risks the mechanical diversification missed — one theme
  across sectors (AI hardware and its buyers), and currency exposure when
  several picks share one foreign currency
- What could go wrong

## Step 6 — Recommend a refresh if data is stale

If most `last_updated` timestamps are more than 2 days old (`brief` lists
stale rows), tell the user to run `/refresh-now all` before acting.
