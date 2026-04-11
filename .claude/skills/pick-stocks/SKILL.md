---
name: pick-stocks
description: Find diversified buy-the-dip candidates from the warm cache that fit a budget.
argument-hint: <amount — e.g. 100000, 1L, 5L>
---

Surface quality stocks currently in a dip that fit the user's budget
of **$ARGUMENTS**. `pick` reads from the cache; it does not fetch
live data itself.

## Step 1 — Run the pick command

```bash
python3 -m fundamental_engine pick --amount "$ARGUMENTS"
```

Defaults: `--min-composite 75`, `--min-verdict BUY`. The output
includes a ranked candidate table + a portfolio rollup. It also
writes `data_cache/dip_candidates.json`.

## Step 2 — Interpret an empty result

If the table is empty, it's one of:

1. **Empty cache** — no tickers have been loaded yet. Tell the user
   to run `/analyse-stock <name-or-ticker>` on a handful of stocks
   first (or `/refresh-now` for several) to populate `scores.json`.
2. **Everything below the gate** — rerun with widened gates:
   `python3 -m fundamental_engine pick --amount "$ARGUMENTS" --min-composite 60 --min-verdict HOLD`
3. **Pending judgments dragging composites down** — check
   `data_cache/judgments/`. Any ticker without a judgment file is
   stuck in pending mode. Suggest running `/analyse-stock <TICKER>`
   on the most promising stocks (by `fundamentals_score` in
   `data_cache/scores.json`) to unlock real composites.

## Step 3 — Add qualitative colour on top of the ranked picks

For each pick Python surfaced, read the cached score:

```bash
cat data_cache/scores.json
```

Look at the per-pillar evidence and tell the user:
- Why this dip is genuinely interesting (beyond the mechanical reason)
- Which candidate you'd actually put money into first
- Concentration risks the mechanical diversification missed
- What could go wrong

## Step 4 — Recommend a refresh if data is stale

If the `last_updated` timestamp on most cache entries is more than
2 days old, tell the user to run `/refresh-now all` before acting
on the picks.
