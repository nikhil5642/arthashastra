# Global Fundamental Analyst — Migration Plan

## Status — updated 2026-11-08 (end of session)

**Done:** Phase 0 (reliability), Phase 1 (global identity, currency,
market-independent pipeline), SEC EDGAR history for US filers (pulled
forward from Phase 3a, used for backtests), and the core of Phase 2:

- **Valuation pillar + composite v2** (§4.6 / §4.7):
  `0.35·fundamentals + 0.15·valuation + 0.25·leadership + 0.15·prospects
  + 0.10·news`. Valuation = earnings yield + FCF yield (P/B vs ROE for
  banks) + P/E vs its own history, on universal curves; loss-makers score
  0 on earnings yield; the value-trap guard caps slow growers at 70.
  Shipped directly rather than in the shadow mode §8 proposed — see the
  backtest result below.
- **Per-share basis fixed:** closes are split-adjusted but not
  dividend-adjusted, and SEC's first-filed EPS is rebased by Yahoo's split
  history (without it, Apple's 2013 P/E computed as 0.4).
- **`backtests/`** at the project root, like `tests/`: one suite per
  scoring claim, `python3 -m backtests [suite …]`, judged PASS / WEAK PASS
  / WEAK FAIL / FAIL / NO DATA (|t| ≥ 2 to PASS or FAIL; ranking claims
  need 10+ tickers on a date).

39 listings across 13 markets run through one pipeline; 453 tests, ~96%
coverage.

**Backtest result (39 listings, 30y requested; fundamentals history
reaches 2008 for US filers, 2015 for India, 2023 elsewhere):** nothing
is significant in either direction. Yearly: fundamentals rank IC −0.03
(t −0.5), valuation −0.06 (t −0.8), adding valuation −0.04 (t −1.1).
Quarterly: fundamentals +0.01 (t +0.2), valuation −0.04 (t −1.0). The
closest call is against the dip gate: quality-dip signals trailed
same-date peers by 3.8% a quarter (t −1.7, n = 43). The first backtest's
"+0.11 IC" came from 2008–2014 dates with only 5–7 US survivors; a 10-name
floor per date removes it. This universe — today's survivors, at most 39
names a date — cannot validate or refute the scoring; statistical power
needs more names per date (more US filers: SEC gives each ~18 years).

**Decision taken — market independence (user, mid-session):** there is
no per-market anything. This **supersedes**:

- §3.2's routing table. Sources now select themselves by coverage
  (screener answers only for `.NS` / `.BO`), and the news edition and
  regulator keywords are one universal set.
- §4.2 / §4.3's country inputs (cost of capital, CPI). Ratios are
  currency-free and use one set of curves; growth inflation is handled
  next by measuring growth in the user's base currency, which needs only
  FX history.
- Lot sizes: dropped. Share counts are indicative.

**Next:** widen the backtest universe (more US filers, more banks — the
bank-rubric suite needs 10+ banks on a date); base-currency growth;
insurer / REIT rubrics; SEC data in the live snapshot (today it feeds
backtests only); deeper history for Korea / Japan / EU (OpenDART, EDINET,
ESEF — each needs a free API key), since those markets backtest on
Yahoo's 4 years.

---

**Date:** 2026-11-08 · **Status:** original proposal (see Status above) ·
**Scope (from the user):** every world market (US, China, …); transform the
approach, tools, skills, *and* the scoring formula; make the data more
reliable and comprehensive.

India stays a first-class market (screener.in remains authoritative for
it). "Global" means India becomes one market among many, not that it is
removed.

---

## 1. TL;DR

- The code is India-locked in ~30 source/skill files. The hardest lock is
  in the data layer: `_yf_symbol` appends `.NS` to every symbol, so
  `AAPL` → `AAPL.NS` and every non-Indian listing silently returns zero
  data (verified, §2.3).
- Once that lock is bypassed, **yfinance alone fills 11–12 of the 14 scored
  fields in every market tested** (US, HK, China A, Japan, Germany, ADR,
  India). So a usable global baseline is close. The hard parts are
  identity, currency, scoring calibration, and data trust, not coverage.
- The scoring formula has India-calibrated constants that break globally:
  the promoter-holding curve (Apple's 1.7% insider stake → ~3/100), bank
  NIM curve (NIM ≥ 4% = 100; Japanese banks run < 1%), and nominal growth
  thresholds (a Turkish 40% nominal grower is shrinking in real terms but
  scores 100).
- Three formula weaknesses exist **even for India today**: pledge data is
  fabricated (always 0.0); the integrity veto can't stop a HOLD; and
  valuation is not scored at all, so a great company at 100× P/E can get
  STRONG_BUY.
- Proposed path: **Phase 0** reliability fixes (India, behaviour-honest) →
  **Phase 1** global identity + currency + Yahoo baseline → **Phase 2**
  scoring v2 in shadow mode, then switch → **Phase 3** authoritative
  per-market sources (US first, then China/HK, …) → **Phase 4** filing
  depth for Claude.
- **Blocker:** `.claude/settings.json` denies Edit/Write on every source
  and test directory. Implementation can't start until that lock is
  lifted (§7, decision 1).

---

## 2. What the read-through found

### 2.1 Architecture recap (unchanged by this plan)

Python fetches and scores deterministically. Claude scores the qualitative
pillars inside slash commands and writes `data_cache/judgments/<ID>.json`.
The layering is strict: `fundamental_engine → reports/screener → scorers →
data_sources → signals`. There is no LLM in Python. Test baseline:
**152 passed in 2.39 s**, all hermetic. All of this survives the migration.

### 2.2 India-coupling inventory

| Area | India assumption | Where | Global replacement |
|---|---|---|---|
| Identity | Bare tickers; `canonical()` strips `.NS/.BO`; adapter forces `.NS` | `fundamental_engine/ticker.py:20`, `yfinance_adapter.py:43-55` | Exchange-qualified Yahoo symbol as the ID (§3.1) |
| Resolver | Keeps only `NSI`/`BSE` exchanges | `resolver.py:21-23,129` | All exchanges; rank by primary listing; surface ADR vs local |
| Authoritative source | screener.in only | `source.py:47-121` (`_SCREENER_WINS`) | Per-market precedence chain (§3.4) |
| Units | `*_cr` fields (crore), `₹` literals | `snapshot.py:101,131-136`, 20+ display sites in `reports/`, `pick.py` | Amount + currency + unit; per-currency formatter |
| Market cap | Only screener fills `market_cap_cr` | `screener_adapter.py:162-165` | Yahoo `marketCap` + FX → local and USD |
| Budget | K / Lakh / Crore, INR | `budget_parser.py` | Currency-tagged budgets (`$10k`, `¥1m`, `1L`) |
| Picks | 10-share minimum, ₹, sector cap only | `pick.py:38-43` | Lot-size rules (JP/CN 100, HK per-stock), FX, country cap |
| Ownership | "Promoter" holding curve, FII/DII flows | `industrial.py:361-399`, `trends.py:89-165` | Ownership & alignment v2 (§4.4); FII/DII stays an India-only section |
| Pledge | India concept, scored + veto | `industrial.py:103-118` | Encumbrance, scored only when a real source exists |
| Bank rubric | NIM/ROE/CD curves tuned to Indian banks | `bank.py:100-238` | Cost-of-equity-relative returns, asset quality, NIM as context |
| Sector routing | `"financial services"` keyword → bank rubric | `snapshot.py:47-62` | Structural router (§4.1) |
| Growth veto | Nominal 3/5/7% thresholds | `industrial.py:55-59` | Inflation-floored thresholds, never weaker than today (§4.3) |
| News | Google News `en-IN`; ET/Moneycontrol; SEBI/ED keywords | `news_source.py:58-100` | Per-market edition + language + regulator keyword sets |
| Research | English Wikipedia only | `research_source.py:109-111` | Local-language Wikipedia + Brave country param |
| Leadership modes | FIRST_GEN needs listed < 10y; promoter ≥ 25% | `leader_mode_classifier.py:39-65` | FOUNDER_LED / FAMILY_CONTROLLED / STATE_CONTROLLED / PROFESSIONAL |
| Skills | NSE picks, SEBI veto, FII/DII anchors, ₹ examples | all 7 `SKILL.md` | Market-aware rewrite (§5) |
| Docs/primer | "Indian equities" everywhere | `CLAUDE.md`, READMEs, `launch.sh`, `__main__.py:42,80,135` | Rewrite |

### 2.3 Data-reliability findings

**Verified live this session:**

| # | Finding | Evidence | Impact |
|---|---|---|---|
| V1 | Every non-Indian symbol returns empty | Probe: `AAPL` → requested as `AAPL.NS` → 404; `0700.HK` → `0700.HK.NS` → 404 | Global is impossible without fixing identity |
| V2 | **Pledge is fabricated.** Screener's shareholding tables (`#quarterly-shp`, `#yearly-shp`) have no pledge row, and "pledge" appears nowhere in the page. `_parse_shareholding` then writes `pledge_pct = 0.0` | RELIANCE page; all 10 cached reports that show pledge show `0.0% → 100/100` (or 95 for banks) | 7.5% of industrial fundamentals is a constant 100; the pledge veto can never fire. Violates anti-optimism and live-data rules |
| V3 | Screener **does** expose bank asset quality: `Gross NPA %` and `Net NPA %` rows in the quarterly table | HDFCBANK page | Already captured in `raw_quarters` but unscored; report, banner, and docs wrongly say "missing" |
| V4 | Screener 52w High/Low never parses: `.number` holds only `1,612`, so `split("/")` fails | RELIANCE top-ratios | Dead code; yfinance silently fills |
| V5 | yfinance fills 11–12/14 scored fields per market; `promoter_holding` and `pledge_pct` are missing everywhere; `heldPercentInsiders` is present everywhere | Probe on AAPL, 0700.HK, 600519.SS, 7203.T, SAP.DE, BABA, RELIANCE.NS | Yahoo is a viable global baseline |
| V6 | Trading currency ≠ reporting currency | 0700.HK trades HKD, reports CNY; BABA trades USD, reports CNY | Any price-vs-fundamentals ratio is wrong without FX (ADR P/B could be off ~57×) |
| V7 | Low-base CAGR distortion | SAATVIKGL PAT 3y CAGR 229.9% (cached); SAP.DE 46.4% (probe) | Growth scores and vetoes misfire on base effects |

**Found by reading the code (not observed in data yet):**

- **R1 NaN leak.** `_compute_cagr` (`yfinance_adapter.py:501-529`) has no
  NaN guard. A NaN CAGR scores growth 0/100 (`industrial.py:300-309` falls
  through to `else`) and bypasses the growth veto (`NaN < x` is False). It
  was not seen in the 7-symbol probe, but it becomes likely once Yahoo is
  the primary source for most markets.
- **R2 Financials misrouted.** Any Yahoo "Financial Services" company goes
  to the bank rubric: Visa/Mastercard ("Credit Services"), exchanges, AMCs,
  insurers, brokers. Balance-sheet and earnings-quality axes are then
  skipped, and bank ROA curves (≥ 1.8% = 100) are trivially maxed by
  asset-light firms.
- **R3 Integrity veto can't stop HOLD.** The veto clamps the leadership
  pillar only. Fundamentals 90 + leadership 30 + prospects 70 + news 35 →
  **60.75 = HOLD**. Fraud cases have excellent *reported* fundamentals by
  construction (Satyam, Wirecard, Luckin).
- **R4 No valuation axis.** P/E and P/B are displayed but never scored.
- **R5 Mixed definitions.** Interest coverage is EBITDA/interest on the
  screener path (operating profit is pre-depreciation) but EBIT/interest
  on the yfinance path. That is lenient for D&A-heavy companies, and the
  data-sources README contract ("requires EBIT > 0") is unenforced on the
  screener path.
- **R6 Non-atomic persistence.** `JsonStore.upsert`, `SignalBus.emit`, and
  `JudgmentStore.save` read-modify-write without temp-file+rename or a
  lock, so concurrent engine and CLI runs can lose writes.
  `Signal.from_dict` raises on an unknown enum value, so one bad row breaks
  `read_all()`.
- **R7 Declared but unused deps.** `aiolimiter` (so no rate limiting
  exists), `orjson`, `python-dateutil`. `ticker.yf_symbol` is dead code
  (tests only). `research_source.py:60-64` reads `BRAVE_API_KEY` from env
  directly, against the config rule.
- **R8 News category precedence.** `classify_material` returns the first
  matching category in dict order, with EARNINGS before GOVERNANCE, so
  "SEBI probes Q4 results" is tagged *earnings*.

### 2.4 yfinance probe detail (suffix bypassed inside the probe only)

| Listing | Scored | Missing | Trade / report ccy | Insiders % | 3y rev / PAT CAGR |
|---|---|---|---|---|---|
| AAPL | 11/14 | interest_coverage, promoter, pledge | USD / USD | 1.7 | 7.7 / 10.9 |
| 0700.HK | 12/14 | promoter, pledge (2 quarters only) | HKD / CNY | 31.7 | 10.7 / 6.1 |
| 600519.SS | 12/14 | promoter, pledge | CNY / CNY | 61.6 (state parent) | 10.5 / 9.5 |
| 7203.T | 12/14 | promoter, pledge | JPY / JPY | 9.0 | 10.9 / 16.2 |
| SAP.DE | 11/14 | quick_ratio, promoter, pledge | EUR / EUR | 15.6 | 7.6 / **46.4** |
| BABA | 12/14 | promoter, pledge | USD / CNY | 1.8 | 5.6 / 12.5 |
| RELIANCE.NS | 12/14 | promoter, pledge | INR / INR | 51.8 (≈ promoter 50.0) | 6.4 / 6.6 |

Caveats: Yahoo gives **4 annual periods**, so 3y CAGR is its maximum window
and long-history valuation bands aren't possible from Yahoo alone. HK
quarterly coverage is thin. "Insiders %" semantics vary: it counts
corporate parents and state holders.

---

## 3. Target design — data & identity

### 3.1 Identity

- **Canonical ID = Yahoo symbol with exchange suffix**: `RELIANCE.NS`,
  `AAPL`, `0700.HK`, `600519.SS`, `7203.T`, `SAP.DE`. It is unique across
  exchanges, file-safe, and already the resolver's output.
  `canonical()` stops stripping suffixes.
- Bare input is ambiguous (`BHP` is listed on both the ASX and the NYSE) →
  resolve via search, filtered by a user-preference `DEFAULT_MARKET` env
  var (e.g. `IN`) to keep today's ergonomics. This is a preference, not
  company data.
- Market attributes come **live** from Yahoo `.info` (`market`,
  `exchange`, `currency`, `financialCurrency`, `country`). They are not
  inferred from a static suffix table.
- Deferred to Phase 4: issuer-vs-listing linking (A/H shares, ADRs) via
  OpenFIGI, so one judgment is shared across listings of the same issuer.

### 3.2 Market source-routing table (needs your sign-off, §7 decision 2)

A small static table: market → fundamentals provider chain, Google News
edition(s) + language(s), regulator keyword set (local language
included), lot-size rule. This is **source-routing config, not company
data**. It extends today's allowed list (API URLs, `MATERIAL_KEYWORDS`,
`MARKET_RSS_FEEDS`, CSS selectors). No tickers, aliases, or company facts
go in it.

### 3.3 Currency & units

- Every monetary field carries `reporting_currency`; prices carry
  `trading_currency`. `*_cr` fields become plain amounts plus currency.
- **Valuation ratios come from aggregates in one currency**: market cap
  (trading ccy → FX) vs net income / book / FCF (reporting ccy → FX). This
  sidesteps ADR ratios and share-class issues entirely.
- FX is live (Yahoo `USDJPY=X`-style pairs; it's already a dependency).
- Display: a per-currency formatter. INR keeps ₹/Cr/L; others use K/M/B/T.

### 3.4 Data layer v2 — reliability built in

- **Provider chain per market** (protocol: `fetch(id) → fields +
  provenance`). India: screener → Yahoo. US: SEC EDGAR → Yahoo (Phase 3).
  Everywhere else starts as Yahoo-only. `_SCREENER_WINS` generalises to
  per-market, per-field precedence.
- **Provenance on every field**: source, fiscal period/as-of, fetched-at,
  definition tag (e.g. `interest_coverage: ebit_basis`). Today only the 14
  scored fields are attributed, with no period.
- **Reconciliation**: when two providers report the same field and differ
  beyond tolerance, record a `DataConflict` and keep precedence. Surface
  it in evidence and lower confidence instead of choosing silently.
- **Validation at one choke point** (the merge): drop NaN/inf, enforce
  sign and range sanity (e.g. tax rate 0–100, D/E ≥ 0), flag stale periods
  (latest annual > 15 months old), and flag low-base CAGRs.
- **No silent defaults**: missing is `None`, never `0.0` or a neutral-good
  value. The pledge fix (V2) is the first instance.
- **One definition per metric globally**: e.g. interest coverage = EBIT /
  interest everywhere, and debt excludes lease liabilities wherever the
  source separates them (Yahoo `Total Debt − Capital Lease Obligations`).
- **Rate limiting + retry/backoff** per host (actually use `aiolimiter`).
  SEC requires ≤ 10 req/s and a declared User-Agent.
- **Data-quality score** separate from completeness: agreement + freshness
  + source tier. It feeds confidence and a verdict gate (§4.7).
- **Atomic persistence** (temp file + `os.replace`, plus a file lock);
  tolerant `Signal.from_dict`.

---

## 4. Target design — scoring v2

### 4.1 Rubric router (structural first, sector strings second)

| Rubric | Detection | Notes |
|---|---|---|
| BANK | Deposits on balance sheet / Yahoo industry `Banks—*` | Asset quality added (§4.5) |
| LENDER (NBFC) | Loans/receivables dominate assets, no deposits | Bank rubric minus CD ratio |
| INSURER | Yahoo `Insurance—*` | Generic profitability/growth/quality, leverage axes skipped, banner: Claude verifies combined ratio and solvency |
| REIT | Yahoo `REIT—*` (+ InvITs, J-REITs, S-REITs) | Same degraded approach; Claude verifies FFO and occupancy |
| INDUSTRIAL | Everything else, **including** exchanges, AMCs, brokers, payment networks, rating agencies | Fixes R2 |

### 4.2 Returns vs local cost of capital

Absolute ROE/ROCE curves don't travel: 8% ROE is decent in Japan (10y
yield ~1.6%) and value-destroying in Turkey (10y ~30%). Score the spread
**ROE − CoE_local**, where CoE = local 10y yield + country equity risk
premium (both fetched live, §6). Calibrate the curve so India's current
scores are roughly preserved, and verify that in shadow mode.

### 4.3 Growth — never weaker than today

- Veto thresholds become `max((3, 5, 7), CPI_local_3y + (−2, 0, +2))`.
  For low-inflation markets (India, US, Japan, EU) they stay exactly
  3/5/7. High-inflation markets get stricter (Turkey ~50% CPI →
  48/50/52).
- The growth *score* subtracts excess inflation above the ~5% environment
  the curves were tuned in. Below that, no adjustment.
- This is a **deliberate asymmetry**: Japan gets India's nominal bar, not
  a real-terms discount. It honours "never weaken the veto". Confirm in
  §7 decision 4.
- Base-effect guard: if the CAGR base year's margin is < ½ the window
  median, the PAT CAGR is flagged low-base and excluded. The veto then
  falls back to revenue CAGR alone, so a low base can't be used to escape
  it.

### 4.4 Ownership & alignment (replaces PROMOTER, 15%)

The current curve rewards *more* promoter stake (60%+ = 100, < 25% ≈
near 0). That punishes every widely-held company: Apple, Microsoft,
Infosys (14.5% → 23/100, currently masked by the fake pledge = 100). New
sub-scores:

- **Encumbrance**: pledge %, scored only from a real source (India
  disclosures, China via AKShare, Taiwan via MOPS), else `None`.
- **Insider net change, 1y**: promoter Δ (screener), Form 4 (US), 减持
  announcements (CN).
- **Dilution / buybacks**: 3y net share-count change. Universal, from
  Yahoo share counts.
- **Holding level, neutral-centred**: a low insider stake in a
  widely-held company is neutral (~65), not bad.
- Control structures (dual-class, state control, VIE) are judged by
  Claude in leadership, not here.

### 4.5 Bank rubric v2

Returns: ROE − CoE + ROA. **Asset quality (new)**: GNPA/NNPA level and
trend (India from `raw_quarters` today; US from FDIC later). NIM becomes
context, not scored, because it isn't comparable across rate regimes.
CD/LDR stress curve: keep for IN/US/CN; context-only for the EU and
Nordics, where LDR > 100% is structural. CET1 is not available from
Yahoo or screener, so Claude verifies it.

### 4.6 New deterministic pillar: VALUATION

- Earnings yield and FCF yield vs the local 10y yield (comparable across
  rate regimes).
- Multiple vs its own history (EV/EBITDA or P/E percentile). Full depth
  only where long history exists (India via screener, US via SEC); Yahoo
  allows 4 years.
- Banks: P/B vs justified P/B ≈ (ROE − g)/(CoE − g).
- Value-trap guard: if the growth veto fired, valuation is capped at 70.

### 4.7 Composite v2 + gates (recommended; tune in §7 decision 3)

```
composite = 0.35·fundamentals + 0.25·leadership + 0.15·valuation
          + 0.15·prospects   + 0.10·news
```

Gates applied after the blend:

1. **Integrity veto → composite ≤ 54** (AVOID ceiling). Fixes R3.
2. **Uninvestable market** (sanctions, foreign holders blocked) →
   RED_FLAG, excluded from picks.
3. **Low data quality** → verdict capped at HOLD, labelled "LOW DATA
   QUALITY". Unknown ≠ good.

Rationale: valuation is the missing axis. Leadership 30 → 25 because
Claude's knowledge depth varies by market, and integrity now acts through
a gate, which is stronger than weight. News 15 → 10 because multilingual
coverage will be uneven. Judgments gain a per-pillar `confidence` written
by Claude (today it is fixed at 0.85, `analyse_pipeline.py:114`).

### 4.8 Dip detector

Keep the gates. Add a volatility check: drawdown must also be ≥ 1× the 1y
realized volatility, so a 20% move in a 60%-vol small-cap isn't a "dip".
Pick adds a country cap next to `MAX_PER_SECTOR`.

---

## 5. Target design — Claude side (skills & qualitative rubric)

| Skill | Changes |
|---|---|
| `resolve-ticker` | All exchanges; show market/currency; ADR-vs-local disambiguation ("Alibaba" → BABA or 9988.HK?); `DEFAULT_MARKET` preference |
| `analyse-stock` | Market-aware steps: ownership semantics per market (promoter / insider / state / chaebol), FII/DII anchors India-only, global regulator list, accounting-standard caveats (US GAAP / IFRS / Ind AS / CAS / J-GAAP), currency in all figures, native-language news read directly, new jurisdiction anchor, `confidence` per pillar, judgment schema v2 |
| `research-founder` | Modes v2; local-language Wikipedia; regulator list per market |
| `pick-stocks` | Currency-tagged budget, lot sizes, FX exposure note, country concentration |
| `market-brief` | Per-market and global sections; per-market feeds |
| `refresh-now` / `explain-score` | Exchange-qualified IDs; explain the v2 formula and gates |

**Leadership modes v2:**

| Mode | Replaces / covers |
|---|---|
| FOUNDER_LED | Replaces FIRST_GEN; founder still running or controlling, regardless of listing age (NVIDIA, Meta, BYD) |
| FAMILY_CONTROLLED | Generalises MULTI_GEN_FAMILY: promoter families, chaebol, HK tycoons, European family firms |
| STATE_CONTROLLED | **New**: Chinese SOEs, Indian PSUs, Gulf state firms, Singapore GLCs. Rubric: policy-alignment risk, minority treatment, payout discipline, capital allocation |
| PROFESSIONAL | Widely held |

**Integrity veto v2** (all modes, ≤ 30 *and* composite gate): enforcement
by SEC/DOJ, CSRC, SFC, FCA/SFO, BaFin, AMF, JFSA/SESC, FSS (Korea),
SEBI/ED/SFIO, ASIC, OSC, CVM. Also: auditor resignation or qualified
opinion, restatement, SOX-404 material weakness, and a substantiated fraud
short-report.

**New prospects anchor — jurisdiction cap (≤ 55):** sanctions or
export-control exposure, VIE structure, capital controls, ADR delisting
risk. Same treatment as the structural-disruption cap.

**News v2:** per-market Google News edition and language. Category
precedence: governance > rating > insider/promoter > leadership >
corporate action > earnings (fixes R8). Quiet window stays neutral 55.

---

## 6. Data sources by market (proposed; ✓ = verified this session)

| Market | Yahoo suffix | Authoritative fundamentals | Access | Ownership / pledge | Notes |
|---|---|---|---|---|---|
| India | `.NS` `.BO` | screener.in ✓ | scrape | screener shareholding ✓; pledge source TBD (NSE/BSE disclosures, access unverified) | GNPA/NNPA in quarters ✓ |
| US | none | SEC EDGAR XBRL `companyfacts` | free, declared User-Agent, ≤ 10 req/s | Form 4, 13F, DEF 14A | FDIC BankFind for banks |
| China A | `.SS` `.SZ` | AKShare / BaoStock (optional extra) | scraper-based libs | AKShare pledge ratio, controlling holder | CSRC; tier-2 reliability, reconcile vs Yahoo |
| Hong Kong | `.HK` | Yahoo ✓ + HKEXnews (PDF) | scrape/PDF | HKEX disclosure of interests | board lot varies per stock |
| Japan | `.T` | EDINET API v2 | free, API key | large-holding reports | 100-share units |
| Korea | `.KS` `.KQ` | OpenDART | free, API key | major-shareholder filings | chaebol |
| Taiwan | `.TW` `.TWO` | TWSE OpenAPI / MOPS | free | MOPS director holdings + pledges | 1,000-share board lot |
| EU | `.DE` ✓ `.PA` `.AS` … | filings.xbrl.org (ESEF) | free | national registers | LDR norms differ |
| UK | `.L` | Companies House API | free key | PSC register | |
| Brazil | `.SA` | CVM open data | free | CVM | high-rate regime |
| Rest | various | Yahoo only | — | — | labelled tier-2, lower confidence |

**Macro inputs (all live, none hardcoded):**

- CPI: World Bank `FP.CPI.TOTL.ZG` or IMF DataMapper.
- 10y yields: FRED/OECD series (non-OECD coverage unverified).
- Equity risk premium: Damodaran country-risk dataset.
- FX: Yahoo pairs.
- Exposed via a new `macro <MARKET>` CLI command as an audit trail.

---

## 7. Decisions needed from you

1. **Unlock source editing (blocker).** `.claude/settings.json` denies
   Edit/Write on `fundamental_engine/`, `data_sources/`, `scorers/`,
   `screener/`, `reports/`, `signals/`, `tests/`. Proposal: remove those
   14 deny entries for the migration and keep every destructive-op deny
   (`rm -rf`, force-push, reset --hard, …). I will not route around the
   lock via shell writes.
2. **Market routing table (§3.2) as allowed static config?** It holds
   source routing, news editions, regulator keywords, and lot rules. No
   company data.
3. **Composite v2 (§4.7):** accept 35/25/15/15/10 + three gates, or
   adjust. At minimum I'd push for the integrity gate, which fixes a live
   weakness.
4. **Growth asymmetry (§4.3):** confirm "never weaker than today"
   (stricter only under high inflation) over a pure real-growth model.
5. **Later, for Phase 3:** a SEC User-Agent contact string (SEC policy
   requires a name/email; I won't use yours without you saying so), plus
   free API keys for EDINET, OpenDART, and FRED.

Assumed unless you say otherwise: India stays first-class; the ID format
is Yahoo symbols; the existing cache is migrated (backed up first), not
wiped; the US is the first authoritative source after the Yahoo baseline,
then China/HK.

---

## 8. Phased roadmap

Each phase updates the skills and docs it touches, so slash commands never
break mid-migration. Sizes are relative.

### Phase 0 — Reliability fixes on today's pipeline (S–M)

- Pledge → `None` + ownership v2 (§4.4) land together, so removing the
  fake 100 doesn't punish professionally run companies (INFY).
- NaN/inf guard at the merge boundary + in `_compute_cagr` (R1).
- Score GNPA/NNPA from `raw_quarters`; fix the banner, report, and docs (V3).
- Structural financial-sector router (R2).
- Integrity gate at composite level; judgment schema field
  `integrity_veto` (R3).
- Atomic writes + locks; tolerant `Signal.from_dict`; `StrEnum` in signals (R6).
- Provenance + `DataConflict` + data-quality panel in `analyse`.
- Fix High/Low parse (V4); one interest-coverage definition (R5); remove
  dead code and unused deps or put them to use (R7); news precedence (R8).
- **Exit:** tests green; a before/after score diff for the cached Indian
  universe, reviewed with you.

### Phase 1 — Global identity, currency, Yahoo baseline (L)

- Yahoo-symbol IDs; global resolver; live market detection; routing table.
- Currency model + FX; market cap local + USD; per-currency display.
- Budget parsing with currency; lot sizes; country cap in `pick`.
- `migrate-cache` command: backup → re-key `scores.json`, judgments, news,
  `prices.db`, signals.
- Market-aware news and research.
- All 7 skills, `launch.sh` primer, READMEs, and `CLAUDE.md` rules v2
  updated.
- **Exit:** `/analyse-stock` end-to-end for AAPL, 0700.HK, 600519.SS,
  7203.T, SAP.DE, BABA, RELIANCE.NS. India unchanged except Phase 0
  intent.

### Phase 2 — Scoring v2 in shadow mode, then switch (M–L)

- `macro_source` (CPI, yields, ERP, FX).
- Rubric router incl. INSURER/REIT; CoE-relative returns; growth floor +
  base-effect guard.
- Valuation pillar; composite v2 + gates; dip volatility check;
  judgment schema v2.
- Run v1 and v2 side by side and produce a diff report. You sign off,
  then v2 becomes default.

### Phase 3 — Authoritative sources, one market at a time (XL, incremental)

- 3a US: SEC EDGAR + Form 4 + FDIC.
- 3b China/HK: AKShare optional extra.
- 3c Japan/Korea/Taiwan: EDINET/DART/TWSE.
- 3d EU/UK: ESEF / Companies House.
- 3e Brazil and others.
- Each adapter ships with provenance, reconciliation against Yahoo, and
  hermetic tests.

### Phase 4 — Depth for Claude (M)

- Size-capped filing excerpts (risk factors, MD&A, governance/proxy,
  related-party notes).
- Cross-listing linking.
- Optional new skills: `/compare` (cross-market peers) and `/screen` (live
  Yahoo screener by region/sector; the universe stays live, no lists).

---

## 9. Testing strategy

- Stays hermetic: monkeypatch `_fetch_*` per provider; payloads built
  inline; no fixtures.
- Property tests:
  - NaN/inf never survives the merge.
  - Growth thresholds are never below (3, 5, 7) for any CPI.
  - Valuation ratios are invariant to the reporting/trading currency pair.
  - The integrity gate holds for any pillar mix.
- Shadow-diff harness (live, opt-in, outside pytest) for Phase 0 and
  Phase 2 sign-offs.

---

## 10. Adversarial review — where this plan could be wrong

- **Unverified API claims.** SEC `companyfacts` coverage for banks and
  insurers; EDINET/DART XBRL mapping effort (taxonomies differ per
  market — the biggest hidden cost); AKShare stability (scraper-based,
  breaks often); FRED yields for non-OECD markets; NSE pledge-disclosure
  access (NSE blocks bots); HK board-lot source. Each needs a probe before
  its phase starts, like the yfinance probe here.
- **Yahoo depth.** 4 annual periods limits long-window growth and
  own-history valuation outside India/US. Tier-2 markets will carry lower
  confidence. That's honest, but it means verdicts are less comparable
  across markets than the uniform 0–100 scale suggests.
- **Model risk in CoE.** ERP estimates are themselves opinions. Mitigation:
  show the CoE inputs in every report and verify India calibration in
  shadow mode before switching.
- **Anti-optimism regression.** Any recalibration can quietly loosen
  scores. Guard: the Phase 2 diff flags every ticker whose v2 composite
  exceeds v1 by > 3 points, each with an explanation.
- **Scope creep.** "Every market" at authoritative depth is unbounded.
  The plan bounds it: every Yahoo market at baseline, official adapters
  only where free structured data exists.
- **Rule tension.** The routing table (§3.2) stretches "nothing
  hardcoded". If you reject it, per-market source routing would have to
  come from live metadata alone, which can't express regulator keywords or
  news editions.
