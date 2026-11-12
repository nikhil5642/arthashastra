---
name: analyse-ipo
description: Analyse a company that is preparing to list — "is Anthropic filing?", "analyse the Cerebras IPO", "what do we know about the X offering?". Finds its SEC registration and stage, reads the prospectus, transcribes the audited statements, scores them on the same rubric as any listed company, and judges leadership, prospects and news with IPO-specific anti-optimism anchors.
argument-hint: <company name — e.g. Anthropic, "Cerebras Systems", Fictional Widgets>
---

Analyse the planned listing of **$ARGUMENTS**.

**Hard rules — read every time:**
- **A prospectus is a selling document.** Only the audited statements
  count as evidence. Growth claims, market sizes and "momentum" that are
  not in the audited numbers are the seller's case, not facts.
- **Terms come from the prospectus only.** A valuation in the press
  ("raising at $X billion", "could be valued at …") is not a term. No
  price range in the filing → no valuation pillar. Never put a number
  from the news in the `offering` block.
- **Unknown is not good.** No price history means volatility, worst fall
  and P/E history are unknown, not calm. Leave out any statement line you
  can't find rather than guess.
- **Excitement is not evidence.** A hot listing, oversubscription talk,
  famous backers and a famous founder count for nothing. News around a
  listing is neutral (55), not bullish.
- **When the company is Anthropic:** say plainly, before the analysis,
  that you are Claude, made by Anthropic, so the reader can weigh that.
  Hold yourself to the filed numbers only — no inside knowledge, no
  favourable reading, the same anchors as any other issuer.
- Use the CLI for data. The Python executable is `python3`. Write files
  with the Write tool.

## Step 1 — Find the registration

```bash
python3 -m fundamental_engine ipo "$ARGUMENTS" --json
```

Read `registration`:

- `stage` — `draft` (only confidential drafts are public), `filed` (a
  public prospectus, no price), `priced` (the final prospectus is filed;
  trading within days), `trading`.
- `proposed_ticker`, `industry`, `amendments`, `first_public_filing`,
  `latest_prospectus.url`, `filings` (the timeline).
- `other_matches` — other registrants with a similar name. If the first
  match is plainly the wrong company, re-run with a more specific name.

Then branch:

- **`trading`** — it already lists. Hand over to
  `/analyse-stock <proposed_ticker>`. In the answer, note the
  `RECENTLY_LISTED` early signal it will carry for its first year: every
  price-based measure covers only months, and the lock-up has usually not
  ended. Stop here.
- **`registration` is null** — SEC has no public registration under that
  name. Say so honestly, and that this does **not** mean the company is
  not preparing one: confidential drafts are invisible until it files
  publicly, and a listing on another exchange is not an SEC filing. Search
  the web for a prospectus filed on another exchange (Hong Kong, India's
  DRHP, London, …) or for credible reporting of a confidential filing. If
  a prospectus exists elsewhere, continue from step 2 with that document.
  If none does, stop: report the status, and **never analyse press
  rumours of a valuation as if they were terms**.
- **`draft`** — only drafts are public; their statements may be thin or
  redacted. Read the newest one if it carries statements, and say clearly
  that the filing is not yet public in full.

## Step 2 — Read the prospectus and transcribe the statements

Fetch `latest_prospectus.url` with the web fetch tool (Python does not
fetch the document; SEC refuses its generic client). A large prospectus
(over 10 MB) is refused by the fetch tool for its size: download it to the
scratchpad instead with `curl` and a generic browser-style User-Agent —
never a person's name or email — strip the HTML to text, and search that
file for the sections below rather than reading it whole. A prospectus is
long: find "Summary consolidated financial data", the audited
consolidated statements of operations, balance sheets and cash flows,
"Use of proceeds", "Principal and selling stockholders", "Shares eligible
for future sale" (lock-ups), "Capitalization", "Certain relationships and
related-party transactions", and "Risk factors".

Write `data_cache/ipos/<slug>.json`, where `<slug>` is the file name the
`ipo` command printed in `write_statements_to` (the registrant's name,
lower-case, underscores). Format — this example is a fictional company:

```json
{
  "company": "Fictional Widgets, Inc.",
  "ticker": "FWDG",
  "currency": "USD",
  "amounts_in": 1000000,
  "source_url": "https://www.sec.gov/Archives/edgar/data/0000000/000000000000000000/fwdg-s1a.htm",
  "transcribed_at": "2026-11-12T10:30:00",
  "sector": "Technology",
  "industry": "Software—Infrastructure",
  "country": "United States",
  "business_summary": "Sells widget-orchestration software to mid-sized manufacturers.",
  "notes": [
    "Latest balance sheet is the pro forma one: convertible preferred stock converts to common at listing.",
    "Capital spend is purchases of property and equipment plus capitalised software."
  ],
  "offering": {"price_low": 18.0, "price_high": 21.0, "shares_after_offering": 412000000},
  "fiscal_years": [
    {
      "fiscal_year_end": "2025-12-31",
      "revenue": 1250.0, "cost_of_revenue": 480.0, "gross_profit": 770.0,
      "operating_income": -95.0, "interest_expense": 12.0, "pretax_income": -101.0,
      "tax": 4.0, "net_income": -105.0,
      "operating_cash_flow": 40.0, "capital_spend": 85.0, "depreciation_and_amortization": 60.0,
      "total_assets": 3100.0, "current_assets": 1900.0, "current_liabilities": 700.0,
      "stockholders_equity": 1800.0, "cash": 1400.0, "receivables": 260.0, "payables": 90.0,
      "goodwill": 150.0, "long_term_debt": 300.0, "current_debt": 25.0
    },
    {"fiscal_year_end": "2024-12-31", "revenue": 900.0, "net_income": -160.0, "...": "every line the filing gives"},
    {"fiscal_year_end": "2023-12-31", "revenue": 610.0, "net_income": -210.0, "...": "every line the filing gives"}
  ]
}
```

Rules for the file:

- **Lines** are exactly the `StatementRow` names: `revenue`,
  `cost_of_revenue`, `gross_profit`, `operating_income`,
  `interest_expense`, `pretax_income`, `tax`, `net_income`,
  `operating_cash_flow`, `capital_spend`, `depreciation_and_amortization`,
  `total_assets`, `current_assets`, `current_liabilities`,
  `stockholders_equity`, `cash`, `inventory`, `receivables`, `payables`,
  `goodwill`, `long_term_debt`, `current_debt`. (Drop the `"..."` keys
  in the example — they only mark where the real lines go.)
- **Every fiscal year the filing gives** — at least three if it has them.
  A 3-year growth rate needs four year-ends; with three, growth is left
  unknown, not guessed. Add a year from "selected financial data" only if
  it is audited, and say so in `notes`.
- `fiscal_year_end` is an ISO date. Losses and negative equity are
  negative numbers; `capital_spend` is a **positive** payment.
- `amounts_in` is the multiplier the tables use (1000 for "in thousands",
  1000000 for "in millions"). Per-share figures are not transcribed.
- `transcribed_at` is the real current time; `source_url` the document
  you read.
- **Pro forma:** when preferred stock converts at listing, use the
  prospectus's pro forma balance sheet for the latest year (the as-reported
  one shows the preferred outside equity) and say so in `notes`. Never mix
  pro forma and as-reported figures without a note. When the only pro
  forma balance sheet is at an interim date, keep the audited year-end
  figures in the file — an interim balance sheet beside full-year flows
  breaks "one period per ratio" — and put the pro forma equity in `notes`
  and in your answer: the negative-equity score and signal are then an
  artefact of the preferred stock, and you must say so.
- **Leave a line out rather than guess.** A missing line stays unknown and
  drops out of the score; a guessed one is a fabricated number.
- **`offering`** only from the prospectus: the range on the cover once it
  is set (`price_low`, `price_high`; one price for a priced deal), and
  `shares_after_offering` — every class outstanding after the offering
  (the "shares outstanding after this offering" line in "The offering",
  all classes). Leave `offering` empty while the cover has no range.

## Step 3 — Score the transcribed figures

```bash
python3 -m fundamental_engine ipo "$ARGUMENTS"
```

The report is the ordinary stock report, under a note that the figures
were transcribed. Check: the fiscal years and currency it shows match
the filing; the valuation basis line (range midpoint, `--price`, or none);
the fundamentals evidence. If a figure looks wrong, fix the file — never
the score.

## Step 4 — Judge leadership, prospects and news

Write `data_cache/judgments/<TICKER>.json` (the `ticker` in the
statements file) in the standard format from `/analyse-stock` step 11 —
leadership mode and rubric (step 7–8), prospects (step 9, including the
`Position risk:` line), news (step 10), `anchors_applied`, the real
`updated_at`. Then add the IPO-specific anchors below, each answered from
the prospectus:

**Leadership**

- **Who is selling.** New shares (the company raising capital) or
  existing holders cashing out? A large secondary component — insiders or
  funds selling into the listing — lowers capital allocation and
  integrity unless the filing gives a reason that holds up.
- **Use of proceeds.** Specific (capacity, repaying named debt) or
  "general corporate purposes"? Proceeds used to pay insiders, repay
  related parties or fund distributions to pre-IPO holders → capital
  allocation ≤ 50.
- **Control after the offering.** Share classes and voting power. A
  founder or holder keeping majority votes on a minority of the equity is
  control without accountability → governance ≤ 50 (anchor 5 in step 8).
- **Lock-ups.** The dates and the share counts they release. Say when
  they expire.
- **Related-party dealings.** Leases, loans, supply or services with
  insiders or their companies; an unexplained one lowers integrity.
- **Short or restated audited history.** A restatement, a material
  weakness, an auditor's resignation or a qualified opinion is the
  integrity veto (step 8, anchor 1) exactly as for a listed company. A
  change of auditor before listing is not by itself the veto: read the
  filing's "change in accountants" section — reported disagreements or
  reportable events make it one; none reported is a point to name in
  `red_flags`, not a gate. Two audited years is a thin record:
  say so, score what exists (step 7.5).

**Prospects**

- **Customer and supplier concentration.** The prospectus must name
  customers above 10% of revenue and key or single-source suppliers. One
  customer or one supplier carrying the business → the position-risk
  anchor applies.
- **Cash burn against cash raised.** For a loss-maker: months of runway
  = (cash after the offering) ÷ (yearly operating cash burn + capex +
  purchase commitments due in the next twelve months, from the
  contractual-obligations table) × 12. Past burn alone understates what
  a company with large committed spend will consume.
  Under 24 months, the listing is funding survival, not growth → prospects
  ≤ 55 unless the path to positive cash flow is in the audited trend.
- **Revenue quality.** One-off contracts, revenue from related parties,
  revenue funded by the company's own credits, investments or discounts to
  customers, or from investors who are also customers — name it and count
  it out of the tailwind.
- **Risk factors that are specific, not boilerplate.** A factor that
  names this company's own dependency, dispute or regulator is evidence;
  "we may face competition" is not. Quote the specific ones.
- **Growth not in the audited statements is not evidence.** Bookings,
  "annualised run-rate", pipeline and TAM figures are the seller's case.
- **Position risk** against the industry leader, as in `/analyse-stock`
  step 9 — scale, supply, customers, out-spending, bundling, runway.

**News**

- Coverage of the listing itself — the roadshow, the demand, the
  valuation talk — is neutral (55), not bullish. Only material items
  (governance, regulatory action, litigation, credit ratings) move it.
- Fame is not evidence, in any pillar.

Re-run `python3 -m fundamental_engine ipo "$ARGUMENTS"` to blend the
judgment in.

## Step 5 — Reward against risk at the offer price

Valuation is scored at the range midpoint. To find where it would turn
reasonable, try prices:

```bash
python3 -m fundamental_engine ipo "$ARGUMENTS" --price <P> --json
```

Read `scored.valuation` at a few prices (the low end, the high end, and
lower) and report the price at which it reaches about 50 — or say that no
price makes it reasonable because the earnings are negative.

## Step 6 — Answer

1. **Stage and timeline** — filed when, how many amendments, reserved
   ticker, whether terms are set — and **what is still unknown** (no
   trading history, the final price, figures not in the filing).
2. **The scores** — fundamentals, valuation (or why there is none),
   composite and verdict — with the measured / unknown risk: no price
   record, the `risk_discount` it implies, loss years on record.
3. **Reward against risk at the offer price** — the valuation score at
   the midpoint, and the price at which it would turn reasonable.
4. **The IPO anchors that fired**, by name: who is selling, control,
   lock-up dates, concentration, runway, revenue quality.
5. **What would change the view** — a lower price, a profitable year in
   the audited numbers, a lock-up passing without heavy selling, a
   customer diversifying.
6. State plainly that **a first-day price has nothing to do with these
   numbers**, and that this is analysis, not a recommendation to
   subscribe.
