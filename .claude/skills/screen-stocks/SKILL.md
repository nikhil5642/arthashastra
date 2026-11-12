---
name: screen-stocks
description: Find companies anywhere in the world from a plain-language ask — "a chip maker with good fundamentals", "cheap Japanese industrials", "small Indian banks growing fast". Reads every listed stock that matches through Yahoo's live screener, orders them by a size-blind preview of the fundamentals rubric, scores the best in full and ranks them. Use for any "find me / which / what are the best …" question.
argument-hint: <what you're looking for, in plain words — e.g. "chip makers with good fundamentals", "US and Japanese tech, P/E under 25, down 20%+">
---

Find companies for **$ARGUMENTS** and answer with a ranked shortlist.

The ask will usually be vague. That is normal: work out what was meant,
say in one line what you assumed, and answer. **Do not ask a clarifying
question when a sensible default exists.**

## Step 1 — Read the ask

Four things, each with a default:

| Question | How to tell | Default when the ask doesn't say |
|---|---|---|
| **What kind of company?** | "chip makers", "banks", "pharma", "defence" → `--industry` / `--sector` | no filter (every sector) |
| **Where?** | a country or exchange → `--market us in jp hk gb de` (Yahoo codes) | every market (59) |
| **What size?** | "large caps", "small caps", "over $10B" → `--min-mcap` / `--max-mcap` in BASE_CURRENCY (`10B`, `500cr`) | **none — every size** |
| **Good at what?** | see the table below → `--rank` and, only for stated numbers, a filter | `--rank risk-adjusted` |

**Never add a size filter the user did not ask for.** Four listed
companies in five are small, and the best business in an industry is as
likely to be one of them: of the 113 chip makers with a 15%+ return on
equity (November 2026), five of the six best on fundamentals were not
large caps. The screen is built to be fair to every size — keep it so.

What "good" means → how to rank:

| The user says | Flags |
|---|---|
| "good fundamentals", "quality", "strong business", "solid balance sheet", "well run" | `--rank fundamentals` — and still show each card's `risk_adjusted` beside it |
| "cheap", "undervalued", "good value", "reasonably priced" | `--rank valuation` |
| "good stock", "worth buying", "best", or nothing in particular | `--rank risk-adjusted` (the default) |
| "the better business and the better price, whatever the risk" | `--rank both` |
| "P/E under 20" · "ROE above 15" · "low debt" · "growing 10%+" · "dividend 3%+" | `--max-pe 20` · `--min-roe 15` · `--max-de 0.5` · `--min-revenue-growth 10` · `--min-dividend-yield 3` |
| "beaten down", "in a dip", "fallen a lot" | `--min-drawdown 20`, and `--sort drawdown` for "biggest falls first" |
| "the biggest", "the largest" | `--sort mcap` — the only time size decides the order |

Don't turn a quality word into a hard filter. "Good fundamentals" is a
ranking, not `--min-roe 15`: the rubric averages a dozen inputs, and a
company can miss one mark and still score 95. Filter only on numbers the
user actually gave.

Industry and sector names are forgiving — Yahoo's own name in any case or
punctuation, or part of it:

| The user says | Flag |
|---|---|
| chip makers, semiconductors | `--industry Semiconductors` (add `"Semiconductor Equipment"` only if they mean the tool makers too) |
| banks | `--industry bank` (both bank industries) |
| software | `--industry software` |
| pharma, drug makers | `--industry "drug manufacturers"` |
| tech · financials · healthcare · energy | `--sector tech` · `financial` · `health` · `energy` |

A name that matches nothing prints Yahoo's full list. Yahoo files each
company under one industry: Samsung Electronics makes memory chips and is
"Consumer Electronics". If you know a company belongs and the screen
can't see it, add it in Step 4 with `compare`.

Themes Yahoo has no industry for ("AI stocks", "EV supply chain"): name
the candidates yourself — of every size, not just the famous ones — and
use `/compare-stocks`.

## Step 2 — Run the screen

```bash
python3 -m fundamental_engine screen <flags> --score 25 --rank <risk-adjusted|fundamentals|valuation|both> --json
```

What it does:

1. **Reads every match.** One query spans all the chosen markets and
   returns every listing that passes, of every size — not each market's
   largest. Depositary receipts and duplicate listings are dropped; each
   company keeps its home-currency listing.
2. **Previews them, blind to size.** For each of the fundamentals
   rubric's screenable inputs (returns on equity, capital and assets;
   debt / equity; current and quick ratios; revenue and profit growth) it
   asks Yahoo which companies clear the rubric's "good" and "excellent"
   marks, and orders the companies by the weighted share of marks cleared.
   Companies level on the preview take turns across large, mid and small.
3. **Scores the first 25 in full** — live statements, the real rubric —
   and ranks them by what you asked for.

An industry takes half a minute to a minute the first time. A broad ask —
a whole sector, every market, thousands of companies ("small-cap tech")
— takes two to three minutes, because every match is previewed: give the
command a 10-minute timeout rather than narrowing the ask to save time.
Scores are reused for 12 hours, so a follow-up ("what about cheaper
ones?", "only Japan") is fast.

## Step 3 — Check the run before you trust it

Read these fields first:

- `unread_markets` not empty → Yahoo did not answer for those markets;
  the list is incomplete. Run it again, and say so if it persists.
- `read_whole: false` → the ask was too broad to read whole and only each
  market's largest listings were read. **Small companies are missing.**
  Add an industry, a sector or a market and run again.
- `ordered_by: "size_spread"` → the rubric preview could not run (Yahoo did
  not answer every question, or the read was not whole); the order is an
  even spread across sizes, not a quality order. Run it again; if it
  persists, narrow it and say so.
- `companies_matched` against the number scored, and `next_in_line` (the
  best unscored companies, with their previews): if the next in line
  preview as well as the ones you scored, go deeper — `--score 40`, or
  `--score <companies_matched>` when that is a few dozen. What is already
  scored is reused.
- `size_mix` and `scored_size_mix` → how many large, mid and small
  companies matched and were scored. Tell the user.
- `not_scored` → Yahoo had no statements for these; name them if relevant.

## Step 4 — Add what the screen could not see (optional)

If a company you know belongs was filed under another industry, score it
alongside:

```bash
python3 -m fundamental_engine compare <scored tickers…> <the extra tickers…> --rank fundamentals --json
```

## Step 5 — Answer

Lead with the answer, not the method.

1. **The shortlist** — the top three to five by what was asked. For each:
   the score, the three or four numbers behind it (from the card's `why`:
   ROE / ROCE, debt, growth, cash conversion), its size (market cap and
   band), its valuation score and P/E, and every URGENT early concern by
   name. Then one line on which you would look at first and why.
2. **One line on coverage**: how many companies matched, how many were
   scored, from which sizes — "386 aerospace and defence companies
   worldwide; scored the 25 best previews: 7 large, 12 mid, 6 small."
3. **What you assumed**, in a line: "every market, every size, ranked on
   fundamentals."
4. **What the numbers can't tell you**:
   - A card with `awaiting_judgment` has no leadership, prospects or news
     score yet — its composite is half placeholder. Never present a
     deterministic-only composite as a buy recommendation.
   - Good fundamentals is not a good price: give the valuation beside it.
     A 95 on fundamentals at 18 on valuation is an excellent business at a
     price that already says so.
   - For a small company say what a reader should check: how much trades
     a day (`traded_value_base` in `matches`), whether the statements are
     in English, whether quarterly statements exist (no quarters means
     fewer early signals, not a clean bill).
5. **Next step**: `/analyse-stock <TICKER>` on the two or three strongest
   for the full five-pillar verdict; `/compare-stocks` to set specific
   names side by side; `/pick-stocks <budget> <same ask>` to size a
   portfolio.

## Size and risk — keep the two apart

The search is blind to size so that every company gets looked at. That
is not a claim that every size is equally safe. Smaller companies are
riskier on average, measurably: across 293 cached companies (November
2026) the share price's yearly swing ran 34% above $200B, 46% for
$10–200B, 56% for $2–10B, 64% for $300M–2B and 70% below that, and years
ending in a loss rose from 6% of the record to 32%.

So risk is measured for each company, not assumed from its size — a fifth
of the companies above $10B were wilder than the median one below $2B.
Every card carries a `risk` block; **state it for every company you
recommend**:

- `volatility_1y_pct` — the yearly swing of the price (30% is calm, 70%
  moves about 4% on an ordinary day);
- `worst_fall_3y_pct` — its deepest fall in three years;
- `traded_per_day_base` — what changes hands a day. Set it against what
  the user would invest: a holding larger than a tenth of a day's trading
  is hard to sell;
- `loss_years` of `fiscal_years` — how steady, and how long, the record is.

Risk changes no pillar and no verdict, but it does order the shortlist
by default: `--rank risk-adjusted` ranks on `risk_adjusted` = `both` less
`risk_discount` — 0–30 points for a price that swings more than 30% a year,
for the share of loss years on record, and for a record under four fiscal
years; a volatility that can't be measured costs 10 (unknown is not calm).
Put the risk beside the scores, in plain words: "the best business on the
list, and the riskiest to hold — it swings twice as much as TSMC and trades
₹2 Cr a day."

For a small company, also give `scale_vs_largest_pct` (its market cap as a
share of the largest company the screen matched) and say what that scale
means for its **position risk** — one supplier or foundry, a few
customers, a leader that can out-spend or bundle it — from evidence, not
from the size alone (`/analyse-stock` step 9 says how).

When the user asks for something **safe, stable or low-risk**, that is
them asking about risk: rank on fundamentals, then order your answer by
the measured risk, and a size floor (`--min-mcap`) is a fair reading of
the ask — say that you applied it.

What you must not do is treat unfamiliarity as risk, or fame as safety.
The numbers above are the risk; whether you have heard of the company is
not.
