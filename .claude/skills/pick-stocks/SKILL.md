---
name: pick-stocks
description: Find diversified buy-the-dip candidates across every market in the warm cache that fit a budget in your base currency.
argument-hint: <amount in BASE_CURRENCY — e.g. 100000, 50k, 2.5m, 5L>
---

Surface quality stocks currently in a dip that fit the user's budget of
**$ARGUMENTS**. `pick` reads from the cache; it does not fetch live data.

## Step 1 — Run the pick command

```bash
python3 -m fundamental_engine pick --amount "$ARGUMENTS"
```

The budget is in the base currency (`BASE_CURRENCY`, default INR). Every
listing's price — USD, HKD, JPY, pence, … — is converted with the FX rate
cached at its last refresh, so one budget sizes positions across markets.
Defaults: `--min-composite 75`, `--min-verdict BUY`. Picks are capped at
2 per sector and 4 per country. Share counts are indicative: check each
exchange's lot size (e.g. 100-share units in Japan) before ordering. The
command also writes `data_cache/dip_candidates.json`.

## Step 2 — Interpret an empty result

1. **Empty cache** — nothing loaded yet. Run `/analyse-stock` on a
   handful of names (any market) to populate the cache.
2. **Everything below the gate** — widen it:
   `python3 -m fundamental_engine pick --amount "$ARGUMENTS" --min-composite 60 --min-verdict HOLD`
3. **Pending judgments dragging composites down** — tickers without a
   judgment file sit at neutral 50 on three pillars. Suggest
   `/analyse-stock <TICKER>` on the strongest fundamentals scores.
4. **"(no FX rate)"** — that listing's currency had no Yahoo quote at
   refresh time; `/refresh-now <TICKER>` retries it.

## Step 3 — Add qualitative colour on top of the ranked picks

For each pick, read its cached evidence:

```bash
python3 -m fundamental_engine inspect <TICKER>
```

Tell the user:
- Why this dip is genuinely interesting (beyond the mechanical reason)
- Which candidate you'd actually put money into first
- Concentration risks the mechanical diversification missed — including
  currency exposure when several picks share one foreign currency
- What could go wrong

## Step 4 — Recommend a refresh if data is stale

If most `last_updated` timestamps are more than 2 days old (`brief`
lists stale rows), tell the user to run `/refresh-now all` before acting.
