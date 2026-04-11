---
name: research-founder
description: Deep-dive leadership research — fetch Wikipedia + web hits for a founder/CEO, classify the mode, produce a leadership judgment.
argument-hint: <person name> [ticker or company]
---

Research a founder or CEO and produce a leadership judgment that the
analyst can blend into a stock score.

## Step 1 — Resolve the ticker context

Parse `$ARGUMENTS`. It may be `"<Person Name>"` alone, or
`"<Person Name>" <ticker-or-company>`.

If no ticker was provided, infer the company from your knowledge and
resolve it:

```bash
python3 -m fundamental_engine resolve "<company name>" --json
```

Capture `TICKER` for later steps. If you can't work out which
company the person leads, ask the user before continuing.

## Step 2 — Fetch the raw research

```bash
python3 -m fundamental_engine research "<Leader Name>" --ticker <TICKER> --json
```

The command:
- Hits live Wikipedia via `wikipedia-api`
- Hits Brave Search (if `BRAVE_API_KEY` is set)
- Saves `data_cache/research/<name_slug>.json`
- Prints the bundle as JSON on stdout

Read the JSON carefully.

## Step 3 — Classify the mode

- **FIRST_GEN** — listed < 10 years, original founder still active
- **MULTI_GEN_FAMILY** — promoter holding ≥ 25%, 2nd+ gen at the helm
- **PROFESSIONAL** — institutional / low-promoter / professional CEO

## Step 4 — Apply the mode-specific rubric

- **FIRST_GEN**: integrity 35% + track record 30% + reputation 20% +
  first-time bonus 15%
- **MULTI_GEN_FAMILY**: governance 30% + pledge clean 25% +
  succession quality 20% + related-party clean 15% + family reputation 10%
- **PROFESSIONAL**: CEO track record 35% + board independence 20% +
  capital allocation 20% + governance 15% + tenure stability 10%

**Integrity veto**: ANY mention of fraud, SEBI action, auditor
resignation, or material integrity failure → clamp to ≤ 30.

## Step 5 — Update the judgment file

Read the existing `data_cache/judgments/<TICKER>.json` if it exists
(use the Read tool). Preserve the `prospects` and `news` blocks if
they're already populated; only overwrite the `leadership` block.
Then write the file back via the Write tool.

If the judgment file doesn't exist yet, create it with just the
leadership block filled in and the other two set to `null`. A
full `/analyse-stock <TICKER>` pass will fill them in later.

## Step 6 — Present the leader profile

Tell the user:
- Mode + why
- Leadership score + integrity score
- 3–5 key achievements
- Red flags (if any)
- What would change your mind
