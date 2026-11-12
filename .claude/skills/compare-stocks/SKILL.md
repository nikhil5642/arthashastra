---
name: compare-stocks
description: Set named companies side by side — "AMD or Nvidia?", "which is the better business, TSMC, Samsung or SK hynix?", "compare these five" — or rank a shortlist you put together yourself for a theme Yahoo has no industry for. Scores each one live with the deterministic pillars and ranks them on reward less measured risk (the default), fundamentals, valuation, or both.
argument-hint: <two or more companies or tickers — e.g. "AMD vs Nvidia", "TSMC, Samsung, SK hynix on fundamentals">
---

Compare **$ARGUMENTS** and say which comes out ahead, and on what.

## Step 1 — Work out who and on what

- **Who.** Resolve every name to its primary home-market listing
  (`7203.T` over `TM`, `2330.TW` over `TSM`):

  ```bash
  python3 -m fundamental_engine resolve "<name>" --json
  ```

  For a theme rather than a list ("AI infrastructure", "defence
  electronics", "Indian EV suppliers"), name the candidates yourself —
  and name them from every size, not only the companies everyone has
  heard of. Eight to fifteen is a good shortlist.

- **On what.** `--rank fundamentals` for "the better business" (still
  show each card's `risk_adjusted` beside it), `--rank valuation` for "the
  cheaper one", `--rank both` for business and price whatever the risk,
  and `--rank risk-adjusted` (the default) for "the better buy" or when
  the ask doesn't say.

## Step 2 — Run the comparison

```bash
python3 -m fundamental_engine compare <TICKER> <TICKER> … --rank <risk-adjusted|fundamentals|valuation|both> --json
```

Each company is scored like `refresh` — live statements, the real
rubric — unless the cache holds a score fetched in the last 12 hours
(`--reuse-hours 0` forces everything live). The JSON has:

- `scored` — one card per company, best first: `fundamentals`,
  `valuation` (null when no price ratio could be scored), `both`,
  `risk_discount` and `risk_adjusted` (`both` less the discount),
  `composite` and `verdict`, `scale_vs_largest_pct` (filled when the
  cards came from a screen; null for a plain comparison), `market_cap_base`, `why` (the evidence line
  behind every sub-score), `early_signals`, and `awaiting_judgment` (the
  Claude pillars still at the neutral 50).
- `not_scored` — tickers Yahoo had no statements for, with the reason.

## Step 3 — Answer

1. **Who comes out ahead, and on what** — one sentence.
2. **A small table**: one row per company — fundamentals, valuation,
   market cap, and the single number that most separates it from the rest.
3. **Why**, from each card's `why` lines: where the leader is stronger
   (returns, debt, growth, cash conversion) and where it is not. Quote the
   numbers, not adjectives.
4. **Early concerns**, by name, for any company that carries an URGENT or
   ALERT one — a leader with an urgent concern is not a clean win.
5. **The price**: a better business at three times the multiple is a
   different question from a better investment. Give both scores.
6. **What is not judged yet**: for every card with `awaiting_judgment`,
   leadership, prospects and news are placeholders. The comparison is on
   the deterministic pillars only; say so, and offer `/analyse-stock` on
   the one or two that matter.

Give each company's measured risk beside its scores — the card's `risk`
block: yearly price swing, worst fall in three years, what trades a day,
loss years on record. Smaller companies are riskier on average, and this
is where it shows for the ones in front of you.

Risk changes no pillar and no verdict, but it does order the list by
default: `risk_adjusted` is `both` less `risk_discount` — 0–30 points for
a price that swings more than 30% a year, the share of loss years, and a
record under four fiscal years; a volatility that can't be measured costs
10. Say when the risk discount is what reorders two companies.

When a small company is set against its industry's leader, give its scale
against the leader (market cap and revenue side by side, or
`scale_vs_largest_pct` when the cards came from a screen) and its **position risk** from evidence: dependence on one
supplier or foundry and whether capacity is contracted, customer
concentration, whether the leader out-spends it on R&D and capex in
absolute terms or can bundle its product, and cash runway if it loses
money (`/analyse-stock` step 9). Being the leader is not a moat by itself
either.

No brand priors: the better-known company gets no benefit of the doubt
on the *scores*, and neither does the underdog. If the
result clashes with reputation, walk through the numbers before doubting
the math.
