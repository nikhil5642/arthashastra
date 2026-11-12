# Issues found by the cross-segment sweep (2026-11-13)

Written for whoever picks this up next (Opus). Read `CLAUDE.md` first — its
rules bind every fix (anti-optimism, unknown is not good, no size preference,
nothing hardcoded per market, hermetic tests, no `Any`).

**What was run:** 12 screens (chips, Indian banks, US software, pharma, Japanese
industrials, energy, European insurance, US real estate, gold, HK/China
utilities, tech under $500M, beaten-down consumer), 4 themed comparisons (AI
infrastructure, EVs, defence, recent IPOs), `pick`, `brief`, two `ipo` lookups,
four single `analyse` runs. All 24 exited 0; no crash, no NaN/inf in any text,
no unread markets.

- Re-run: `python3 scripts/sweep.py data_cache/reports/sweep_<date>.json`
- Raw results of this run: `data_cache/reports/sweep_2026-11-13.json`
  (per company: F = fundamentals, V = valuation, adj = risk-adjusted, flags).

## Status (updated 2026-11-13, after a first round of fixes)

| # | Issue | Status |
|---|---|---|
| 1 | Implausibly low P/E | **Partly done.** Checked YB, 3640.T, AWJ.AX, 300750.SZ: Yahoo's market cap agrees with price × shares within 7% and the currencies reconcile, so the P/E is what the data says — no data bug found. Added the `PRICED_FOR_DOUBT` early signal (ALERT when 0 < P/E < 5). The valuation score still takes the figure at face value and these still rank first. **Open, needs the user's yes:** whether a P/E under 5 should cap valuation (a scoring change). |
| 2 | Thin rubric coverage | **Done for ranking.** `StockScore.fundamentals_coverage_pct`; a card under 60% is `thin`, marked in the table and ranked after the rest. Rows cached before this default to 100% until refreshed. **Open:** insurers (40% unscored each) and the Indian banks' missing components — find which inputs are missing and why. |
| 3 | `size_spread` under the limit | **Done.** The limit counted listings read (duplicates included), not companies; `PREVIEW_LIMIT` raised 2,000 → 4,000 (energy: 2,825 listings, previewed in 64 s). **Open:** tech under $500M reads 10,693 listings to match 444, because the size filter is applied after FX conversion — the preview still falls back there. |
| 4 | High fundamentals, mostly loss years | Open (needs a signal; no rubric change without the user). |
| 5 | Exactly 50.0 | **Verified, nothing to fix.** XOM, 2222.SR, MC.PA, 6101.T are all the growth veto, with the evidence line saying so. |
| 6 | Negative risk-adjusted | **Done** (floored at 0). The other small items are open. |

Each issue below gives the evidence, where to look, and what "fixed" means.
They are ordered by how much they can mislead a user. **Verify each from the
live numbers before changing code** (`python3 -m fundamental_engine analyse
<TICKER>` then `inspect <TICKER> --section …`); several may turn out to be
correct behaviour that only needs a clearer label.

---

## 1. Implausibly low P/E scores valuation 100 and wins the ranking  — HIGH

| Ticker | Screen | P/E used | V | Rank |
|---|---|---|---|---|
| 3640.T Densan | chips | 3.5 | 100 | #1, BUY |
| YB Yuanbao | US software | 2.6 | 100 | #1, BUY |
| MBAI MBody AI | US software | 0.5 | 100 | (85% of rubric unscored) |
| AWJ.AX Auric Mining | gold | 1.5 | 65 | |

Also check the V = 100 cases with no P/E flag: `300750.SZ` (CATL — a P/E that
low is not credible for it), `MERK.JK`, `BOTX.ST`, `PSE.OL`, `1940.HK`.

Likely causes, to check per ticker: (a) price and earnings in different
currencies or units that the reconciliation in
`data_sources/fundamentals_source/valuation.py` (`_reconciles`,
`quote_unreliable`) does not catch because Yahoo labels both the same (YB is
a US listing of a Chinese company; CATL has A and H shares); (b) a one-off
gain not caught by `core_earnings_ratio`; (c) stale share count.

Fixed means: a same-currency P/E that low is either explained in the
valuation evidence (one-off removed, peak capped) or the quote is treated as
unreliable (neutral 50, zero confidence, `UNRELIABLE_QUOTE` signal). Do not
add a blanket "P/E < N is wrong" constant without checking what real, honest
cases look like in the cache; prefer a reconciliation test (e.g. market cap
from Yahoo vs price × shares, earnings yield vs FCF yield wildly apart).
Add a hermetic test per cause found.

## 2. A score resting on a sliver of the rubric ranks beside full ones — HIGH

- `MBAI`: F 75.0 with 85% of the rubric unscored.
- `ECRO`, `HGPI` (US real estate): F 65.0 with 85% unscored and valuation
  unscored — still shown as HOLD and placed in the shortlist.
- European insurers: 40–60% unscored for all eight (`LRE.L` F 91.2 on 40% of
  the rubric, BUY). Insurers are routed to a rubric whose inputs they mostly
  lack — see `is_bank_snapshot` in
  `data_sources/fundamentals_source/snapshot.py`.
- Indian banks: `KARURVYSYA.NS` F 93.8 (#1) with 40% unscored; `TMB.NS`,
  `UJJIVANSFB.NS` the same. Find which bank components are missing
  (asset quality? franchise?) and why screener did not supply them.

Where: `scorers/fundamentals_scorer/{industrial,bank}.py` already return
`confidence` and `data_completeness_pct`, and now add a "Not scored for lack
of data (N%)" evidence line. `fundamental_engine/scorecard.py` ranks without
looking at either.

Fixed means: a card whose fundamentals rest on less than a stated share of
the rubric (the screen preview already uses `MIN_COVERAGE = 0.6` for the same
idea) is marked thin, ranks after well-covered cards, and the table says so.
Unknown must not be good, and must not be bad either — it ranks last among
equals, it is not scored 0. Separately decide whether insurers need their own
component set; if so that is a new rubric, not a tweak.

## 3. `ordered_by: size_spread` on screens small enough to preview — MEDIUM

- energy (1,169 matched) and tech under $500M (445 matched) came back
  `ordered_by: "size_spread"`, although `PREVIEW_LIMIT` is 2,000 and Japanese
  industrials (973) and pharma (993) were previewed.
- The shortlists show it: energy and small tech returned mostly AVOID /
  RED_FLAG names, i.e. not the best of the field.
- beaten-down consumer (2,778) is over the limit, so `size_spread` is expected
  there — but the result is equally weak; consider previewing after the
  drawdown filter narrows the field.

Where: `fundamental_engine/commands/screen.py` (the `Order` decision) and
`fundamental_engine/preview.py`. Find out why these two fell back (a failed
preview query? `--sector` with several industries? `--max-mcap` changing the
path?). Fixed means: any screen under the limit is ordered by the preview, or
the output states the specific reason it was not.

## 4. Fundamentals near the top with most years in loss — MEDIUM (may be by design)

`PLTR` F 94.3 with 5 of 8 loss years, `TOST` 91.6 with 5 of 7, `RDDT` 92.9
with 3 of 4. The rubric reads the latest audited year only, so a company one
year into profit scores like one with a decade of it. The risk discount
already pulls these down in the default ranking (PLTR adj 49.2), so this may
be acceptable — but `--rank fundamentals` shows them at the top with no
warning. Minimum fix: an early signal ("profitable in N of M years on
record") so the report and `/analyse-stock` see it. Do not change the rubric
without the user's yes.

## 5. Exactly 50.0 fundamentals for very large, profitable companies — VERIFY

`2222.SR` (Saudi Aramco), `XOM`, `CVX`, `MC.PA` (LVMH), `6101.T`,
`002210.SZ` all show F = 50.0 → AVOID. This is probably the growth-deceleration
veto capping at 50 (both 3-year CAGRs under 3%) — which is intended and must
not be weakened. Verify it is the veto and not the "no inputs → neutral 50"
path, and that the evidence line says so plainly. If it is the veto, nothing
to fix; if any is the neutral prior, that is a data bug.

## 6. Small things

- **Negative risk-adjusted scores** (`600121.SS` −7.0, `SCKT` −10.0,
  `DCSI.CN` −18.3, `LCID` −2.7): floor at 0 in `Scorecard.risk_adjusted`.
- **`pick --amount 10L` found no candidates** across 92 followed companies.
  Check whether that is the dip gates being strict (fine) or the new
  volatility sizing / verdict filter removing everything (not fine); the
  message blames a cold cache, which is not the reason here.
- **`TATAMOTORS.NS` → no_data** (the company demerged and its symbols
  changed). The not-scored line could suggest `resolve` for a ticker Yahoo
  no longer knows.
- **Volatility unknown** for `GOKEX.BO` and `SPCX`: expected for SPCX (five
  months listed); check why a long-listed `.BO` stock has under 120 closes.
- **Scored size mix leans large** at `--score 8` (software 5/3/0, pharma
  6/2/0, gold 4/2/0 large/mid/small). At the default `--score 25` this was
  balanced on two industries; re-check on these three before concluding
  anything.
- **Cached Anthropic IPO judgment** (`data_cache/judgments/ANT.json`) still
  carries an integrity veto set under the old "any auditor change" wording.
  The skill is corrected; re-running `/analyse-ipo anthropic` rewrites it.
  The model doing so must disclose it is made by Anthropic.

## What looked right

- No crashes, NaN, unread markets or unscored tickers other than the one
  delisted symbol; every screen read its whole field.
- Risk-adjusted ranking behaves as intended: NVDA and TSMC rank above MU and
  SK hynix (similar fundamentals, twice the volatility); loss-making EV and
  small-tech names fall to the bottom.
- The "recently listed" signal and the 2-year growth fallback fire where they
  should (SPCX, 277A.T, AWJ.AX, 0118.KL).
- `ipo` handles a filed company, and a nonsense name, cleanly.
