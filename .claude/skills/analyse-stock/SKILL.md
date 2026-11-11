---
name: analyse-stock
description: Full fundamental analysis of any listed equity — US, India, China / Hong Kong, Japan, Europe, or anywhere else Yahoo lists it. Always starts from a clean slate — wipes any prior judgment, refreshes live data, then re-scores. Includes anti-optimism anchors so disrupted businesses get downgraded, not excused.
argument-hint: <company name or ticker — e.g. Microsoft, GOOGL, RELIANCE.NS, 0700.HK, 7203.T>
---

Run the full five-pillar fundamental analysis for **$ARGUMENTS**.

**Every run starts from a clean slate.** Step 2 deletes any previously
cached judgment for this ticker before re-fetching data, so stale state
never leaks into the verdict.

**Hard rules — read every time:**
- Always resolve the input first, even if it already looks like a ticker.
- Use the CLI for every piece of data. Do not `import` Python modules or
  write ad-hoc Python — `inspect` exposes everything the cache holds.
- The Python executable is `python3`, not `python`.
- Write the judgment file with the Write tool; never `echo >` / heredocs.
- **No optimism by default.** The rubric below has explicit downward
  anchors for each pillar. Apply them — a business in structural decline
  must not score BUY just because the balance sheet is clean.
- **No brand priors.** Microsoft, Tencent, and HDFC Bank get exactly the
  same rubric. If the verdict clashes with the reputation, walk through
  the numbers before doubting the math.
- **Every URGENT early concern is answered by name** in the evidence of
  the pillar it bears on (step 3.2). An unanswered URGENT concern means
  the analysis is not finished.

## Step 1 — Resolve to a canonical ticker

```bash
python3 -m fundamental_engine resolve "$ARGUMENTS" --json
```

Tickers are Yahoo symbols: US listings have no suffix (`MSFT`, `GOOGL`);
everything else carries its exchange suffix (`RELIANCE.NS`, `0700.HK`,
`7203.T`, `SAP.DE`, `SHEL.L`). Pick the company's **primary home-market
listing**, not an ADR or OTC line: `7203.T` over `TM`, `0700.HK` over
`TCEHY`, `RELIANCE.NS` over `RELIANCE.BO`. Capture:

- `TICKER` — the full symbol, suffix included
- `COMPANY` — the `long_name` field

If the match is ambiguous (several unrelated companies, or you can't tell
which listing is primary), show the top 3 candidates and ask the user.

## Step 2 — Wipe any stale judgment AND refresh fundamentals

```bash
python3 -m fundamental_engine refresh <TICKER> --rejudge
```

`--rejudge` deletes `data_cache/judgments/<TICKER>.json` before the live
fetch and prints one of:

    rejudge: cleared MSFT judgment (7 days old)
    rejudge: no prior judgment for MSFT — this will be a fresh analysis

**Remember which line appeared** — you report it in step 13.

The refresh always hits live data: yfinance for every listing, plus
screener.in for Indian (`.NS` / `.BO`) listings, which adds a deeper
history and wins on the ratios it publishes, plus SEC filings for US
companies' long history (ten fiscal years of margins). It ends with the
early-signal tally, e.g. `early signals: 4 concerns (1 urgent) · 1 strength`.

## Step 3 — Read the deterministic baseline

```bash
python3 -m fundamental_engine analyse <TICKER>
```

Read the "Analysis context" banner, the **Early signals** panel, the
Financial Profile, and the Data quality section — not just the
fundamentals score.

- **Two pillars are already scored and are not yours to change:**
  fundamentals (35%) and valuation (15%). Fundamentals reads the latest
  audited fiscal year. Valuation sets today's price against what the
  business can keep earning — earnings yield, FCF yield over the trailing
  twelve months (P/B vs ROE for banks) and P/E against its own history —
  and is capped at 70 for slow growers (value-trap guard). Use both in
  your narrative; never re-score them. Your three pillars are leadership
  (25%), prospects (15%) and news (10%).
- **Two P/Es.** When the header reads `P/E 14.9 (core 26.7)` or
  `(mid-cycle 19.2)`, the valuation pillar used the second number: the
  reported profit includes one-off gains, or sits on a margin far above
  the company's own norm. Quote the second number. Never call a stock
  cheap on the first.

- **`Cached judgment: none`** is what you want here — step 2 wiped it.
- **Currencies.** Prices and market cap are in the trading currency;
  statement amounts in the reporting currency. They can differ (Tencent
  trades in HKD and reports in CNY; London prices are in pence). Never
  compare across them by hand.
- **Data quality.** Every "Source conflict" line is two sources
  disagreeing on a scored ratio; the report shows which one was kept.
  A STALE annual period means the numbers may predate a material change —
  say so and weight the verdict down.
- **Coverage differs by market.** Indian listings carry screener depth:
  10-year history, quarterly promoter / FII / DII flows, GNPA / NNPA for
  banks. Elsewhere the snapshot is yfinance-only: 4 annual periods, a
  latest insider and institutional %, no pledge or insider-flow history.
  Where a scored input is missing, check it yourself in filings and news
  (insider sales, share pledges, dual-class control) before scoring
  leadership.
- **Growth-deceleration VETO** (`VETO: 3y growth decelerating ...`) —
  the company is in the slow bucket and every later anchor caps harder.
- **Rubric.** "Bank rubric applied" means a bank / lender / insurer;
  CASA / CRAR (capital adequacy) are never in the data — verify them.
- **Multi-year trend** (ROCE / operating margin / borrowings, where
  history exists) — a 15% ROE that was 25% three years ago is a
  different story from one that was 10%. Factor direction into prospects.
- **Ownership tape:**
  - **Insider / promoter stake Δ ≤ −1pt** = insider selling — a material
    red flag for leadership. **Δ ≥ +1pt** = insider accumulation.
  - **FII ≤ −2pts YoY** (Indian listings) = foreign money exiting; with
    **DII ≥ +2pts** the narrative is contested, not consensus.
  - **Retail shareholder count spiking while institutions exit** =
    "retail catching a falling knife". Raise the risk note.
- **Valuation line** (P/E · P/B · FCF yield · dividend yield). A multiple
  compressed versus its own history means the market already de-rated the
  stock — you are not early; ask whether the re-rating is done or still
  unfolding.
- **"Changed since <date>"** (shown when the ticker was analysed before):
  a verdict that moved, and concerns or strengths that are new or worse
  since that look. Lead your answer with these.

## Step 3.2 — Read the early signals

The scores above describe the last audited year. The "Early signals"
panel is what the newest quarters, the latest balance sheet, the
ownership tape and the week's headlines show since — concerns (▼) first,
then strengths (▲). Its footer says which periods it could see; "nothing
flagged" under "no quarterly statements published" means *not looked at*,
not *clean*.

Signals state facts. They have not moved any score — weighing them is
your job, in the pillar each one bears on:

| Signal | Bears on | What to do |
|---|---|---|
| Quote doesn't reconcile (URGENT) | everything price-based | Stop. This listing is a depositary receipt or carries a wrong currency label. Go back to step 1 and analyse the primary listing. |
| Profit from unusual items | valuation narrative, leadership | Find the item in the filings (investment gain, asset sale, tax credit) and name it. Quote the core P/E. |
| Margin at a cyclical peak | prospects | Do not extrapolate today's margin. Judge the tailwind at the company's normal margin; if prices, inventories or new capacity say the cycle is turning, the structural-disruption cap applies (prospects ≤ 55). |
| Free cash flow falling · capex outrunning cash flow | leadership (capital allocation), prospects (capex / guidance clarity) | Find the cause. Operating cash falling is margin compression by another name. A build-out is only as good as the return management has committed to: no stated return, no credit for the spend — capital-allocation and guidance-clarity sub-scores ≤ 60. |
| Heavy stock-based pay · dilution | leadership (capital allocation) | Stock pay is a real cost that free cash flow leaves out — restate FCF net of it. With the share count also rising ≥ 3%, capital allocation ≤ 50. |
| Revenue shrinking or decelerating | growth bucket (step 4) | The 3y CAGR looks backward. Bucket on the slower of the two: the 3y pace, or the latest quarter's year-on-year growth. |
| Margin compressing · profit falling while sales grow | prospects, news | Mix, pricing, or input costs? Persistent pricing pressure is the structural-disruption anchor. |
| Debt rising · goodwill jump | leadership (capital allocation) | What was the money for — an acquisition (price paid against what it earns), buybacks at a high multiple, capex? Unexplained leverage counts against capital allocation. |
| Negative equity | leadership, risk | Buybacks at a cash machine, or accumulated losses? The second is far worse; say which. |
| Receivables or inventory outpacing sales | leadership (integrity), prospects | The classic early tell of sales pulled forward or demand softening. Check the filings for an explanation; an unexplained build lowers the integrity score. |
| Insider selling · foreign institutions selling · retail crowding | leadership, risk | See the ownership tape rules above. |
| Bad loans rising (lenders) | prospects, leadership | Underwriting is the franchise. Say where the rise came from — one account, one segment, or the whole book. A broad-based rise is a structural headwind (prospects ≤ 55). |
| Governance news burst | leadership (integrity), news | **Unjudged.** Read every governance-tagged story in step 5 and sort it: enforcement, fraud, auditor, restatement (→ integrity-veto rules, step 8) · ordinary litigation or antitrust (→ news) · keyword noise (say so). |
| Credit-rating news | news | A downgrade is a material negative (news ≤ 35). Check which way it went. |
| Deep price drawdown | news | Find what the market is reacting to before trusting the statements. |
| Stale statements | confidence | The scores may predate a material change — say so and weight the verdict down. |

**Strengths are context, not credit.** Revenue accelerating, margins
expanding, debt falling, buybacks, insiders buying: each can support a
score *inside* its caps. None of them lifts a cap, and none offsets an
URGENT concern. A strength that ends "…paid for out of profit that may
not repeat" has already been discounted by the report: read it as a
description of the peak, not as an improvement.

## Step 3.5 — Write the business snapshot

Before any pillar scoring, write a 4-sentence business snapshot. This is
the most important step — skip it and you end up scoring ratios without
understanding the business.

1. **What does the company make or do?** Products, services, revenue mix
   — specific, not "technology" or "conglomerate". Read the "Business"
   panel (Yahoo's long business summary) before relying on memory.
2. **Who are the customers?** B2B / B2C, concentrated / diversified,
   domestic / export, sticky / promotional.
3. **What is the moat?** Brand, scale, network effects, switching costs,
   patents, regulation, location, cost advantage — or *none*, which is
   itself important to state.
4. **What is the main risk to the business *model*?** Disruption,
   regulation, customer concentration, cyclicality, commodity exposure,
   controlling-shareholder governance, geopolitics.

Every pillar score must be consistent with this snapshot.

**Sell-side consensus vs revealed flows.** The header shows the analyst
bundle (target, upside, consensus). Consensus is NOT load-bearing — it
lags and carries investment-banking bias. When a Strong Buy consensus
contradicts insider selling or foreign-institution exits, trust the flows
and flag the divergence in your news evidence.

**Line-item questions** ("has the dividend payout risen?", "when did
capex spike?") — read the cached snapshot instead of re-scraping:

```bash
python3 -m fundamental_engine inspect <TICKER> --section raw_profit_loss
```

Indian listings carry every screener table (`raw_profit_loss`,
`raw_balance_sheet`, `raw_cash_flow`, `raw_ratios`, `raw_quarters`,
`raw_shareholding_quarterly`); `inspect <TICKER>` with no section prints
the score, judgment, and full snapshot.

## Step 4 — Growth-bucket classification

From the 3y CAGRs — or the latest quarter's year-on-year growth when an
early signal says revenue is shrinking or decelerating, whichever is
slower — pick the bucket:

- **Both CAGRs ≥ 12%** — growth business; normal scoring applies.
- **Both CAGRs 7–12%** — steady compounder; normal scoring.
- **Both CAGRs 3–7%** — SLOW business. Hard caps kick in.
- **Both < 3%, or negative** — STRUCTURAL DECLINE. Severe caps; only an
  explicit, pipeline-backed turnaround rescues it.

CAGRs are in the reporting currency. In a high-inflation economy
(Turkey, Argentina, Egypt, Nigeria, Pakistan, …) nominal growth flatters
the business: deflate by local inflation and bucket on **real** growth.
Write down the bucket — it drives every cap in steps 8–10.

## Step 5 — Fetch recent news

```bash
python3 -m fundamental_engine news <TICKER> --days 30 --json
```

The JSON has two parts:

- `stories` — one entry per story, newest first, already de-noised
  (quote pages and 13F filing spam removed) and de-duplicated: `coverage`
  is how many outlets carried the same headline. `material_category` is a
  keyword tag (governance · rating · insider · leadership ·
  corporate_action · earnings) — a first sort, not a judgment.
- `radar` — the last 7 days' story count per category, with the most
  widely carried headline. This is what the governance and credit-rating
  early signals were counted from.

Flag items as **material** when they touch: governance (regulator action,
fraud, auditor, investigation, restatement), credit-rating actions,
insider / promoter dealings (sales, pledges, block deals), leadership
changes, corporate actions (M&A, buyback, spin-off, delisting), or
earnings and guidance. Drop the rest. A broker's upgrade or downgrade is
sell-side opinion, not a rating action.

A keyword tag can be wrong in both directions: read the governance-tagged
stories before treating them as red flags, and skim the untagged ones for
anything the keywords missed.

The feed is one global English edition. For companies whose news is
mostly in another language (China, Japan, Korea, …) and the feed looks
thin, run your own web search in that language for the same 30 days —
regulator notices and results announcements first.

**Also look for structural disruption signals:**
- Is the core product or service being automated or commoditised?
- Are customers deferring or cancelling multi-year deals?
- Has the moat eroded against new entrants?
- Are margins compressing in the last 2–4 quarters?

These shape the prospects and news scores directly.

## Step 6 — Fetch research on the primary leader

Identify the current CEO / chair / managing director (or the controlling
shareholder, if they run it). If unsure, ask the user.

```bash
python3 -m fundamental_engine research "<Leader Name>" --ticker <TICKER> --json
```

This hits live Wikipedia and, when `BRAVE_API_KEY` is set, Brave Search.

## Step 7 — Classify the leadership mode

- **FOUNDER_LED** — the founder still runs or controls the company, however
  long it has been listed (NVIDIA, Meta, a recent IPO).
- **FAMILY_CONTROLLED** — a family / promoter group controls it, 2nd+
  generation or a family bloc ≥ 25% (Indian promoter groups, Korean
  chaebol, Hong Kong tycoon families, European family firms).
- **STATE_CONTROLLED** — a government or state entity controls it
  (Chinese SOEs, Indian PSUs, Gulf state companies).
- **PROFESSIONAL** — widely held, professional management (Microsoft,
  Infosys, HDFC Bank).

## Step 8 — Score leadership (0–100) with anti-optimism anchors

**Base rubric by mode:**
- FOUNDER_LED: integrity 35% + execution track record 30% + capital
  allocation 20% + key-person / succession risk 15%
- FAMILY_CONTROLLED: governance 30% + pledge / encumbrance clean 25% +
  succession 20% + related-party dealings 15% + family reputation 10%
- STATE_CONTROLLED: minority-shareholder treatment 30% + capital-allocation
  discipline 25% + policy / political interference risk 20% + governance &
  disclosure 15% + management stability 10%
- PROFESSIONAL: CEO track record 35% + board independence 20% + capital
  allocation 20% + governance 15% + tenure stability 10%

**Anti-optimism anchors (apply these — do not hand-wave):**

1. **Integrity veto** (all modes): fraud, enforcement action by any
   securities regulator or prosecutor (SEC / DOJ, SEBI / ED, CSRC, SFC,
   FCA / SFO, BaFin, JFSA, …), auditor resignation or qualified opinion,
   restatement, a material weakness in internal controls, a substantiated
   fraud short-report, or a prior venture's bankruptcy → clamp leadership
   ≤ 30 **and** set `"integrity_veto": true`. Python then caps the whole
   composite at 54 (AVOID): fraud comes with excellent *reported* numbers.
2. **Growth-accountability cap** (PROFESSIONAL / STATE_CONTROLLED): if the
   CEO's tenure covers the 3y window and BOTH CAGRs are < 7%, the CEO owns
   the outcome — track record ≤ 55, whatever the tenure.
3. **Structural disruption cap** (all modes): core line automating away,
   deal pipeline shrinking, margins compressing → leadership ≤ 65.
4. **"Stable insider" trap:** no points for "N-year veteran, internal
   promotion" unless they actually delivered growth or margin expansion.
5. **Control without accountability:** dual-class / super-voting shares,
   pyramids, or a controlling holder with material related-party dealings
   → governance sub-score ≤ 50 until disclosures show minorities treated
   fairly.
6. **Early concerns on capital allocation** (step 3.2): heavy stock pay
   with a rising share count → capital allocation ≤ 50; capex taking
   nearly all operating cash flow, or debt / goodwill jumping, with no
   stated return → capital allocation ≤ 60.

## Step 9 — Score prospects (0–100) with anti-optimism anchors

**Base rubric:** moat 40% + tailwind 30% + capex / guidance clarity 30%.

**Anchors:**

1. **Tailwind caps by growth bucket (step 4):**
   - CAGRs ≥ 12% → tailwind can be 60–90
   - CAGRs 7–12% → tailwind capped at 70
   - CAGRs 3–7% → tailwind capped at **45**
   - CAGRs < 3% / negative → tailwind capped at **30**
2. **Structural-disruption cap:** an active disruptive headwind (AI
   automating legacy revenue, EV replacing ICE, digital-native replacing
   incumbent) → prospects ≤ **55** regardless of moat.
3. **Jurisdiction cap:** material exposure to sanctions or export
   controls, a VIE structure, capital controls, or ADR-delisting risk →
   prospects ≤ **55** regardless of moat.
4. **Moat without growth is brittle:** moat can still score 70–90 when
   growth is slow, but the weighted score obeys the caps above.
5. **Peak earnings are not the trend:** with a "margin at a cyclical
   peak" signal, judge the tailwind at the company's normal margin. If
   the cycle shows signs of turning, the structural-disruption cap
   applies.

## Step 10 — Score news (0–100) with anti-optimism anchors

Rolling 30-day weighted sentiment × relevance over material items only.

- Material negatives in governance / rating → clamp to ≤ 35.
- Structural-disruption headlines (layoffs, guidance cut, deal
  cancellation, margin compression) → clamp to ≤ 50.
- A quiet window (no material items) is **neutral, not bullish** — 55,
  not 70. "Nothing bad happened" is not a buy signal.
- A governance burst you read and found to be noise or ordinary
  litigation does not clamp the score — but say in the evidence that you
  read it and what it was.

## Step 11 — Write the judgment file

Use the Write tool to create `data_cache/judgments/<TICKER>.json` (the
full symbol, e.g. `MSFT.json`, `RELIANCE.NS.json`) with this schema:

    {
      "ticker": "<TICKER>",
      "leader_name": "<Leader Name>",
      "leadership": {
        "score": <int 0-100>,
        "mode": "FOUNDER_LED|FAMILY_CONTROLLED|STATE_CONTROLLED|PROFESSIONAL",
        "integrity_score": <int 0-100>,
        "integrity_veto": <true|false>,
        "evidence": ["bullet 1", "..."],
        "red_flags": ["..."],
        "summary": "one paragraph",
        "anchors_applied": ["growth_accountability_cap", "..."]
      },
      "prospects": {
        "score": <int 0-100>,
        "evidence": ["bullet 1", "..."],
        "anchors_applied": ["tailwind_cap_slow_growth", "jurisdiction_cap", "..."]
      },
      "news": {
        "score": <int 0-100>,
        "evidence": ["bullet cites a specific headline", "..."],
        "material_negatives": ["..."]
      },
      "updated_at": "<ISO timestamp>",
      "judged_by": "claude"
    }

`anchors_applied` is mandatory whenever a cap fires — it is the audit
trail for future sessions.

`updated_at` must be the real current time: it is what ages the judgment.
After 30 days the news pillar stops counting (its window has passed);
after 90 days leadership and prospects are flagged STALE and count at
lower confidence. An integrity veto never expires.

Before writing, check every URGENT concern from step 3.2 appears by name
in the evidence (or `red_flags`) of the pillar it bears on, with what you
found.

## Step 12 — Render the final blended report

```bash
python3 -m fundamental_engine analyse <TICKER>
```

The banner should now say `Cached judgment: 0 days old` with your leader
name. If a gate fired, it is printed under the verdict. Sanity ladder:

- **≥ 80** (STRONG_BUY): underpriced quality grower, ≥ 12% CAGRs, no
  disruption or jurisdiction flags.
- **70–79** (BUY): steady compounder (7–12% CAGR), clean governance,
  clear catalyst.
- **55–69** (HOLD): slow grower, mixed tailwinds, or
  balance-sheet-strong-but-growth-challenged.
- **40–54** (AVOID): structural disruption not priced in, governance
  fragility, pledge-veto or integrity-gate cases.
- **< 40** (RED_FLAG): avoid.

If the band feels wrong, recheck the anchors — do not override the math.
A BUY or better standing next to an URGENT concern needs a sentence in
your answer on why the concern does not change the call.

## Step 13 — Present to the user

1. **Fresh-state confirmation.** One sentence from step 2: "Started
   fresh — no prior judgment existed" or "Wiped a {N}-day-old judgment
   and started fresh".
2. **Verdict + composite + confidence**, one key fact per pillar, the main
   risks, a clear buy / hold / avoid call. Quote prices in the listing's
   currency, and the P/E the valuation used (core or mid-cycle) when it
   differs from the reported one.
3. **What is new.** The early concerns and strengths, most severe first,
   each with what you found when you looked into it — and anything in the
   "Changed since" panel.
4. **Name the anchors and gates you applied**, and any data-quality
   caveat (source conflicts, stale statements, no quarterly statements,
   fields missing for this market) that limits confidence.
