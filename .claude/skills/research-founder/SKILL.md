---
name: research-founder
description: Deep-dive leadership research for any listed company — fetch Wikipedia + web hits for a founder / CEO / controlling shareholder, classify the leadership mode, produce a leadership judgment.
argument-hint: <person name> [ticker or company]
---

Research a founder, CEO, or controlling shareholder and produce a
leadership judgment the analyst can blend into a stock score.

## Step 1 — Resolve the ticker context

Parse `$ARGUMENTS`: `"<Person Name>"` alone, or
`"<Person Name>" <ticker-or-company>`.

If no ticker was given, infer the company and resolve it:

```bash
python3 -m fundamental_engine resolve "<company name>" --json
```

Capture `TICKER` — the full Yahoo symbol of the primary listing
(`MSFT`, `RELIANCE.NS`, `0700.HK`). If you can't tell which company the
person leads, ask the user.

## Step 2 — Fetch the raw research

```bash
python3 -m fundamental_engine research "<Leader Name>" --ticker <TICKER> --json
```

The command hits live Wikipedia (English) and Brave Search when
`BRAVE_API_KEY` is set, saves `data_cache/research/<name_slug>.json`, and
prints the bundle as JSON. For leaders covered mostly in another language
(China, Japan, Korea, …), supplement it with your own web search in that
language — regulator records and annual-report governance sections first.

## Step 3 — Classify the mode

- **FOUNDER_LED** — the founder still runs or controls it, whatever its
  listing age
- **FAMILY_CONTROLLED** — a family / promoter group controls it (2nd+
  generation, or a family bloc ≥ 25%)
- **STATE_CONTROLLED** — a government or state entity controls it
- **PROFESSIONAL** — widely held, professional management

## Step 4 — Apply the mode-specific rubric

- **FOUNDER_LED**: integrity 35% + execution track record 30% + capital
  allocation 20% + key-person / succession risk 15%
- **FAMILY_CONTROLLED**: governance 30% + pledge / encumbrance clean 25% +
  succession quality 20% + related-party clean 15% + family reputation 10%
- **STATE_CONTROLLED**: minority-shareholder treatment 30% +
  capital-allocation discipline 25% + policy / political interference
  risk 20% + governance & disclosure 15% + management stability 10%
- **PROFESSIONAL**: CEO track record 35% + board independence 20% +
  capital allocation 20% + governance 15% + tenure stability 10%

**Integrity veto**: fraud, enforcement action by any securities regulator
or prosecutor (SEC / DOJ, SEBI / ED, CSRC, SFC, FCA / SFO, BaFin, JFSA, …),
auditor resignation, restatement, or a prior venture's bankruptcy → clamp
to ≤ 30 and set `"integrity_veto": true` (the composite is then capped at
54 / AVOID).

## Step 5 — Update the judgment file

Read `data_cache/judgments/<TICKER>.json` if it exists (Read tool).
Preserve the `prospects` and `news` blocks; overwrite only `leadership`.
Write it back with the Write tool.

If it doesn't exist, create it with the leadership block filled in and
the other two set to `null`. A full `/analyse-stock <TICKER>` pass fills
them later.

## Step 6 — Present the leader profile

- Mode + why
- Leadership score + integrity score (and whether the veto fired)
- 3–5 key achievements
- Red flags, if any
- What would change your mind
