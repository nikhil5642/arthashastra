---
name: screen-stocks
description: Screen every listed stock in the world — ~80,000 listings in 59 markets, through Yahoo's live screener — by the filters the user asks for (market, sector, size, P/E, ROE, debt, growth, dividend, dip), then score the best matches with the full deterministic pipeline.
argument-hint: <filters in plain words — e.g. "US and Japanese tech over $10B, P/E under 25, ROE above 15%, down 20%+">
---

Screen the global universe for **$ARGUMENTS** and report the best
matches, scored.

## Step 1 — Turn the request into flags

| The user says | Flag |
|---|---|
| a country or exchange ("US", "India", "Japan", "Hong Kong", "London", "Germany") | `--market us in jp hk gb de` — Yahoo market codes; no flag = all 59 markets |
| a sector ("tech", "banks", "pharma") | `--sector Technology` · `"Financial Services"` · `Healthcare` (Yahoo's 11 sectors) |
| an industry ("chip makers", "software") | `--industry Semiconductors "Software—Infrastructure"` |
| a size ("large caps", "over $10B", "under ₹5,000 Cr") | `--min-mcap` / `--max-mcap` in BASE_CURRENCY (`10B`, `500cr`, `2.5m`) — convert from the user's currency first |
| "cheap", "P/E under 20" | `--max-pe 20` (loss-makers never pass) |
| "profitable", "high ROE" | `--min-roe 15` (percent) |
| "low debt" | `--max-de 0.5` (debt / equity as a multiple) |
| "growing" | `--min-revenue-growth 10` (1-year, percent) |
| "dividend payers" | `--min-dividend-yield 3` (percent) |
| "beaten down", "in a dip" | `--min-drawdown 20` (% below the 52-week high) |
| "cheapest first", "biggest falls first" | `--sort earnings-yield` · `--sort drawdown` (default: `mcap`, largest first) |

Markets, sectors and industries are checked against Yahoo's own lists;
an unknown value prints the valid ones. If the user asks for something
the CLI can't filter on (say, "founder-led" or "no China exposure"),
apply the rest and say which part you'll judge by hand afterwards.

## Step 2 — Run the screen

```bash
python3 -m fundamental_engine screen <flags> --score 10 --json
```

Three stages, cheapest first:

1. **Yahoo, server-side:** the ratio filters run over every listing in
   each market; the largest matches come back.
2. **Here:** market cap in the base currency (live FX per listing), the
   dip filter, and one listing per company (the most traded — NSE over
   BSE). Depositary receipts and foreign copies are skipped: they carry
   another instrument's market cap and trade < 0.001% of it a day.
3. **`--score 10`:** the top 10 go through the full live fetch and the
   deterministic fundamentals + valuation scoring, and join the cache.

All 59 markets take about 1½ minutes; one or two markets take seconds;
`--score` adds a few seconds per ticker. `--scan 1000` reads deeper into
each market (small caps); `--limit` sets how many rows print.

## Step 3 — Report

1. **What was screened:** markets, filters, listings read, companies
   matched, receipts skipped.
2. **The scored table** — fundamentals, valuation, deterministic
   composite — and why the leaders lead. Read each leader's evidence:
   `python3 -m fundamental_engine inspect <TICKER>`.
3. **Anti-optimism:** screen ratios are Yahoo's trailing-twelve-month
   numbers, unaudited, and one-offs distort them — an investment gain can
   halve a P/E, a year-ago tax charge can make profit growth look huge.
   Name any leader whose screen numbers clash with its scored evidence.
   A screen is a shortlist, never a verdict.
4. **Next step:** `/analyse-stock <TICKER>` on the 2–3 strongest for the
   full five-pillar verdict, or `/pick-stocks <budget> <same filters>` to
   size a portfolio from them.

Until Claude judges them, leadership, prospects and news sit at a
neutral 50 — say so, and never present a deterministic-only composite as
a buy recommendation.
