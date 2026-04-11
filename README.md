# Fundamental Analyst

A Claude-Code-native fundamental analyst for Indian equities (NSE/BSE).
You ask "analyse TCS" or "find solar stocks with strong fundamentals at
a good entry"; Python fetches live data from screener.in + yfinance
and computes deterministic scores, Claude (inside a slash command)
writes the qualitative leadership / prospects / news judgment, and
the report tells you BUY / HOLD / AVOID with the numbers to back it.

No Anthropic API key, no cost budget, no hardcoded ticker list. The
universe is emergent — whatever you've loaded.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# (Optional) set BRAVE_API_KEY for /research-founder web search.
```

## Run

```bash
./launch.sh                  # open Claude Code with the analyst primer
```

That opens an interactive Claude session. Ask natural-language
questions ("analyse HDFC Bank", "find solar stocks worth buying",
"why did TCS score HOLD?") and Claude drives the CLI + slash commands
under the hood.

If you prefer the CLI directly:

```bash
python -m fundamental_engine resolve "Reliance Industries"     # name → ticker
python -m fundamental_engine refresh RELIANCE                  # fetch + score
python -m fundamental_engine analyse RELIANCE                  # full report
python -m fundamental_engine pick --amount 1L                  # buy-the-dip picks
python -m fundamental_engine brief                             # market overview
```

## Slash commands (from inside Claude Code)

| Command | What it does |
|---|---|
| `/analyse-stock <TICKER>` | Full four-pillar deep dive; Claude writes leadership / prospects / news judgment |
| `/research-founder <name>` | Leadership research + mode-aware rubric |
| `/pick-stocks <amount>` | Buy-the-dip candidates from the cached universe |
| `/market-brief` | Overview of cached universe + recent signals |
| `/refresh-now <TICKER\|all>` | Force live data refresh |
| `/explain-score <TICKER>` | Audit trail for a cached score (no re-fetch) |
| `/resolve-ticker <name>` | Free-text name → NSE/BSE ticker |

## Scoring

```
composite = 0.40 × fundamentals + 0.30 × leadership + 0.15 × prospects + 0.15 × news
```

Fundamentals is deterministic (screener.in + yfinance). The other
three are scored by Claude inside slash commands and written to
`data_cache/judgments/<TICKER>.json`. Banks are routed to a
bank-calibrated rubric (NIM, ROA, CD ratio) instead of the industrial
one. Anti-optimism defaults: growth-deceleration veto caps slow
compounders, quiet news windows score neutral (55) not bullish,
integrity red flags clamp leadership ≤ 30.

## Tests

```bash
pytest
```

All hermetic — no network, no fixtures. Live adapters are monkeypatched.

---

**Internals:** see [`CLAUDE.md`](./CLAUDE.md) for the architecture,
layer discipline, typed-interfaces rules, and scoring rubric details.
Each source folder has its own focused `README.md`.
