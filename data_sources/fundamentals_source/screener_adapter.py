"""screener.in adapter — authoritative source for Indian listings.

Only NSE / BSE listings (`.NS` / `.BO` Yahoo symbols) are fetched; for any
other security this adapter returns nothing without making a request, so
the pipeline stays market-independent and screener simply adds depth
where it has coverage. Screener publishes amounts in crores; every
monetary field is converted to plain rupees here.

The screener.in HTML has a fixed set of tables per company::

    #top-ratios       — P/E, P/B, ROE, ROCE, market cap, div yield, D/E
    #profit-loss      — annual P&L (Sales/Revenue, Interest, NP, Tax%, ...)
    #balance-sheet    — annual BS (Borrowings, Reserves, Total Assets, ...)
    #cash-flow        — annual CF (CFO, CFI, CFF, FCF, CFO/OP)
    #ratios           — 10-year Debtor/Inventory/Payable days + ROCE%
    #quarters         — 13 quarters of P&L rows
    #shareholding     — quarterly + yearly Promoter/FII/DII/Public trend
    #peers            — peer comparison (JS-loaded, static HTML is empty)
    #documents        — annual reports / credit ratings / investor PPTs
    #analysis         — screener's own narrative block

`_fetch_screener_in` is the orchestrator. It parses each table once into
`(headers, rows)` tuples, then fans them out to `_apply_*` functions that
mutate a shared `out` dict. The raw tables are also stored verbatim on
the snapshot via `_apply_raw_dumps` so future questions can be answered
without re-parsing the page.

Tests monkeypatch `_fetch_screener_in` on this module directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from data_sources.fundamentals_source.primitives import (
    HTTP_TIMEOUT_SECONDS,
    SCREENER_BASE_URL,
    SCREENER_USER_AGENT,
    _annual_headers,
    _drop_ttm,
    _latest,
    _latest_aligned,
    _latest_and_yoy_delta,
    _latest_with_label,
    _parse_float,
    _parse_pct,
    _screener_cagr,
    _screener_month_to_iso,
    _value_at_label,
    latest_yoy_growth,
    trailing_twelve_months,
)
from data_sources.fundamentals_source.snapshot import (
    DocumentRecord,
    PeerRecord,
    SnapshotFields,
)

if TYPE_CHECKING:
    # Forward references — only the type checker sees these.
    # Zero runtime import cost thanks to `from __future__ import annotations`.
    from selectolax.parser import HTMLParser

log = logging.getLogger(__name__)

# Screener covers Indian listings only; these are their Yahoo suffixes.
_SCREENER_SUFFIXES: tuple[str, ...] = (".NS", ".BO")
RUPEES_PER_CRORE = 1e7

# Screener tables come out as (headers, {row_label: [v0, …, vN | None]})
# where None marks a missing / non-numeric cell. Callers preserve column
# alignment by using positional indexing into the values list.
SectionRows = dict[str, list[float | None]]


# ── Public entry point ───────────────────────────────────────────────────────


def covers(symbol: str) -> bool:
    """True for NSE / BSE listings — the only securities screener knows."""
    return symbol.strip().upper().endswith(_SCREENER_SUFFIXES)


def screener_code(symbol: str) -> str:
    """'RELIANCE.NS' → 'RELIANCE' (screener's URL slug)."""
    return symbol.strip().upper().rsplit(".", 1)[0]


async def _fetch_screener_in(symbol: str) -> SnapshotFields:
    if not covers(symbol):
        return SnapshotFields()
    ticker = screener_code(symbol)
    page = await _fetch_html_tree(ticker)
    if page is None:
        return SnapshotFields()
    tree, is_consolidated = page

    out: SnapshotFields = SnapshotFields(currency="INR", financial_currency="INR")

    _parse_top_ratios(tree, out)
    _parse_h1(tree, out)
    _parse_shareholding(tree, out)

    # Parse each table exactly once and fan out. (headers, rows) tuples
    # preserve column alignment so _drop_ttm can trim by position.
    bs_headers, bs_rows = _parse_section(tree, "balance-sheet")
    pl_headers, pl_rows = _parse_section(tree, "profit-loss")
    cf_headers, cf_rows = _parse_section(tree, "cash-flow")
    r_headers, r_rows = _parse_section(tree, "ratios")
    q_headers, q_rows = _parse_section(tree, "quarters")
    sh_headers, sh_rows = _parse_shareholding_table(tree)

    _apply_balance_sheet(out, bs_headers, bs_rows)
    _apply_profit_loss(out, pl_headers, pl_rows)
    _apply_cash_flow(out, cf_headers, cf_rows, pl_headers, pl_rows)
    _apply_ratios(out, r_rows)
    _apply_quarters(out, q_headers, q_rows)
    _apply_asset_quality(out, q_rows)
    _apply_derived(out, bs_headers, bs_rows, pl_headers, pl_rows)
    _apply_bank_metrics(out, bs_headers, bs_rows, pl_headers, pl_rows)
    _apply_multi_year_trends(out, bs_headers, bs_rows, pl_headers, pl_rows, r_rows)
    _apply_shareholding_trend(out, sh_headers, sh_rows)
    _apply_raw_dumps(
        out,
        pl_headers, pl_rows,
        bs_headers, bs_rows,
        cf_headers, cf_rows,
        r_headers, r_rows,
        q_headers, q_rows,
        sh_headers, sh_rows,
    )
    _apply_peers(tree, out)
    _apply_documents(tree, out)
    _apply_analysis_text(tree, out)

    # Banks report NPAs on a standalone basis only: the consolidated
    # quarterly table carries the rows with every cell empty.
    if out.get("is_bank") and "gross_npa_pct" not in out and is_consolidated:
        standalone = await _fetch_page(f"{SCREENER_BASE_URL}/{ticker.upper()}/")
        if standalone is not None:
            _, standalone_quarters = _parse_section(standalone, "quarters")
            _apply_asset_quality(out, standalone_quarters)

    return out


# A table as parsed: (column headers, {row label: values aligned to headers}).
Table = tuple[list[str], dict[str, list[float | None]]]

HISTORY_SECTIONS: tuple[str, ...] = (
    "profit-loss",
    "balance-sheet",
    "cash-flow",
    "ratios",
    "quarters",
)


async def fetch_tables(symbol: str) -> dict[str, Table]:
    """Every table on the company page, for point-in-time backtests.

    Keys are the section ids in HISTORY_SECTIONS plus "shareholding".
    Empty for listings screener doesn't cover.
    """
    if not covers(symbol):
        return {}
    page = await _fetch_html_tree(screener_code(symbol))
    if page is None:
        return {}
    tree, _ = page
    tables = {section: _parse_section(tree, section) for section in HISTORY_SECTIONS}
    tables["shareholding"] = _parse_shareholding_table(tree)
    return tables


async def _fetch_html_tree(ticker: str) -> "tuple[HTMLParser, bool] | None":
    """Fetch the company page → (parsed tree, is_consolidated), or None.

    The consolidated view is preferred; companies without consolidated
    accounts fall back to the standalone page.
    """
    consolidated = await _fetch_page(f"{SCREENER_BASE_URL}/{ticker.upper()}/consolidated/")
    if consolidated is not None:
        return consolidated, True
    standalone = await _fetch_page(f"{SCREENER_BASE_URL}/{ticker.upper()}/")
    if standalone is not None:
        return standalone, False
    log.warning("screener.in has no page for %s", ticker)
    return None


async def _fetch_page(url: str) -> "HTMLParser | None":
    """GET one screener page and parse it; None on any failure."""
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers={"User-Agent": SCREENER_USER_AGENT})
    except Exception as e:
        log.warning("screener.in fetch error (%s): %s", url, e)
        return None
    if resp.status_code != 200:
        return None
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        log.warning("selectolax not installed — screener parsing skipped")
        return None
    return HTMLParser(resp.text)


# ── Top-of-page parsers (single-value fields) ────────────────────────────────


def _parse_top_ratios(tree: "HTMLParser", out: SnapshotFields) -> None:
    """Extract the `ul#top-ratios` list (P/E, P/B, ROE, ROCE, mcap, etc.)."""
    for item in tree.css("ul#top-ratios li"):
        name_el = item.css_first(".name")
        val_el = item.css_first(".number")
        if not (name_el and val_el):
            continue
        name = name_el.text(strip=True).lower()
        raw = val_el.text(strip=True)
        if "roce" in name:
            v = _parse_pct(raw)
            if v is not None:
                out["roce"] = v
        elif "roe" in name:
            v = _parse_pct(raw)
            if v is not None:
                out["roe"] = v
        elif "market cap" in name:
            v = _parse_float(raw)  # published in ₹ crore
            if v is not None:
                out["market_cap"] = v * RUPEES_PER_CRORE
        elif "current price" in name:
            v = _parse_float(raw)
            if v is not None:
                out["current_price"] = v
        elif "stock p/e" in name or name == "p/e":
            v = _parse_float(raw)
            if v is not None and v > 0:
                out["pe_ratio"] = v
        elif "book value" in name:
            v = _parse_float(raw)
            if v is not None:
                out["book_value_per_share"] = v
        elif "dividend yield" in name:
            v = _parse_pct(raw)
            if v is not None:
                out["dividend_yield"] = v
        elif "debt to equity" in name or name.replace(" ", "") == "d/e":
            v = _parse_float(raw)
            if v is not None and v >= 0:
                out["debt_to_equity"] = v
        elif "high / low" in name:
            # Two `.number` spans ("1,612" / "1,152"); `val_el` is only the
            # first, so read them both explicitly.
            numbers = item.css(".number")
            if len(numbers) >= 2:
                hi = _parse_float(numbers[0].text(strip=True))
                lo = _parse_float(numbers[1].text(strip=True))
                if hi is not None:
                    out["high_52w"] = hi
                if lo is not None:
                    out["low_52w"] = lo


def _parse_h1(tree: "HTMLParser", out: SnapshotFields) -> None:
    h1 = tree.css_first("h1")
    if h1:
        text = h1.text(strip=True)
        if text:
            out["company_name"] = text


def _parse_shareholding(tree: "HTMLParser", out: SnapshotFields) -> None:
    """Pull latest promoter holding + pledge % (the quarterly-SHP trend
    is captured separately by `_apply_shareholding_trend`)."""
    sh = tree.css_first("#shareholding")
    if sh is None:
        return
    for row in sh.css("table tbody tr"):
        cells = row.css("td")
        if not cells:
            continue
        label = cells[0].text(strip=True).lower().rstrip("+").strip()
        vals = [c.text(strip=True) for c in cells[1:]]
        if not vals:
            continue
        latest = _parse_pct(vals[-1])
        if latest is None:
            continue
        if label.startswith("promoter") and "pledge" not in label:
            out["insider_holding"] = latest  # the promoter group is the insider bloc
        elif "pledge" in label:
            out["pledge_pct"] = latest
    # No pledge default. Screener's shareholding tables carry no pledge row
    # (verified 2026-11-08), so absence means "unknown", not "0% pledged".
    # Defaulting to 0.0 used to hand every company a clean-pledge score and
    # made the pledge veto impossible to trigger.


# ── Generic section parser ───────────────────────────────────────────────────


def _parse_section(
    tree: "HTMLParser", section_id: str
) -> tuple[list[str], dict[str, list[float | None]]]:
    """Return (headers, {row_label: [v0, ..., vN]}) for a screener table.

    Headers exclude the empty first cell. Row values are positional: each
    index corresponds to the same index in `headers`, with `None` for
    missing / non-numeric cells. Trailing `+` markers on labels are
    stripped and labels lowercased. This lets callers drop a TTM column
    (or any other named column) without shifting the CAGR window.
    """
    el = tree.css_first(f"#{section_id}")
    if el is None:
        return [], {}
    tbl = el.css_first("table")
    if tbl is None:
        return [], {}

    raw_headers = [th.text(strip=True) for th in tbl.css("thead th")]
    headers = [h for h in raw_headers if h]  # drop empty label column

    rows: dict[str, list[float | None]] = {}
    for row in tbl.css("tbody tr"):
        cells = row.css("td")
        if not cells:
            continue
        raw_label = cells[0].text(strip=True)
        if not raw_label:
            continue
        label = raw_label.lower().rstrip("+").strip()
        values: list[float | None] = []
        for c in cells[1:]:
            text = c.text(strip=True)
            if not text or text == "--":
                values.append(None)
                continue
            parsed = _parse_float(text.rstrip("%"))
            values.append(parsed)
        if any(v is not None for v in values):
            rows[label] = values
    return headers, rows


# ── Per-table appliers (compute typed fields from raw rows) ──────────────────


def _apply_balance_sheet(
    out: SnapshotFields,
    headers: list[str],
    rows: dict[str, list[float | None]],
) -> None:
    """D/E = borrowings / (equity capital + reserves), all from one column."""
    # Screener uses "Borrowings+" for most companies and bare "Borrowing"
    # for banks / NBFCs. Also fall back to "total debt" for good measure.
    borrowings_row = (
        rows.get("borrowings") or rows.get("borrowing") or rows.get("total debt")
    )
    equity_parts = _latest_aligned(rows.get("equity capital"), rows.get("reserves"))
    if equity_parts is not None:
        out["shareholders_equity"] = sum(equity_parts) * RUPEES_PER_CRORE
    aligned = _latest_aligned(
        borrowings_row, rows.get("equity capital"), rows.get("reserves")
    )
    if aligned is None:
        return
    borrowings, equity_capital, reserves = aligned
    equity = equity_capital + reserves
    if equity > 0:
        out["debt_to_equity"] = round(borrowings / equity, 3)


def _apply_profit_loss(
    out: SnapshotFields,
    headers: list[str],
    rows: dict[str, list[float | None]],
) -> None:
    """Margins, interest coverage, tax rate, 3y CAGRs, latest fiscal year.

    Latest-value metrics use the newest column (TTM when present), with
    numerator and denominator always taken from that same column. CAGRs
    drop TTM first so the 3-year window spans whole fiscal years.
    """
    if not rows:
        return

    sales = rows.get("sales") or rows.get("revenue")
    net_profit = rows.get("net profit")

    operating_margin = _pct_of(rows.get("operating profit"), sales)
    if operating_margin is not None:
        out["operating_margin"] = operating_margin
    net_margin = _pct_of(net_profit, sales)
    if net_margin is not None:
        out["net_margin"] = net_margin
    expense_ratio = _pct_of(rows.get("expenses"), sales)
    if expense_ratio is not None:
        out["cogs_to_revenue"] = expense_ratio

    # Interest coverage on CORE operating EBIT — (operating profit −
    # depreciation) / interest — the same definition as the yfinance path
    # (Operating Income / interest expense). Screener's operating profit
    # alone is pre-depreciation and flatters D&A-heavy businesses; PBT
    # includes "other income", where one-off gains land (TMPV's FY26
    # demerger gain turned 5.7× into 27×). A negative value is kept: a
    # core operating loss is known-bad coverage, not missing data.
    aligned = _latest_aligned(
        rows.get("operating profit"), rows.get("depreciation"), rows.get("interest")
    )
    if aligned is not None:
        operating_profit, depreciation, interest = aligned
        if interest > 0:
            core_ebit = operating_profit - depreciation
            out["interest_coverage"] = round(core_ebit / interest, 2)

    latest_tax = _latest(rows.get("tax %"))
    if latest_tax is not None and 0 <= latest_tax <= 100:
        out["tax_rate"] = latest_tax

    annual = _annual_headers(headers)
    if annual:
        period = _screener_month_to_iso(annual[-1])
        if period:
            out["latest_annual_period"] = period
        # The latest fiscal year's profit, never TTM: valuation pairs it with
        # that same year's EPS to get the share count.
        annual_profit = _value_at_label(headers, net_profit, annual[-1])
        if annual_profit is not None:
            out["net_income"] = annual_profit * RUPEES_PER_CRORE
        annual_sales = _value_at_label(headers, sales, annual[-1])
        if annual_sales is not None and annual_sales > 0:
            out["revenue"] = annual_sales * RUPEES_PER_CRORE
    eps_history = _annual_eps(annual, _drop_ttm(headers, rows.get("eps in rs")))
    if eps_history:
        out["eps_history"] = eps_history

    cagr = _screener_cagr(_drop_ttm(headers, sales), years=3)
    if cagr is not None:
        out["rev_3y_cagr"] = cagr
    # A profit that became a loss is the worst growth outcome, not a gap in
    # the data — see primitives.PROFIT_WIPED_OUT_CAGR.
    cagr = _screener_cagr(_drop_ttm(headers, net_profit), years=3, wiped_out_counts=True)
    if cagr is not None:
        out["pat_3y_cagr"] = cagr


def _annual_eps(
    annual_headers: list[str], eps_row: list[float | None]
) -> dict[str, float]:
    """{fiscal-year end (ISO): EPS} — screener's EPS row is already per share, in ₹."""
    history: dict[str, float] = {}
    for header, eps in zip(annual_headers, eps_row):
        period = _screener_month_to_iso(header)
        if period is not None and eps is not None:
            history[period] = eps
    return history


def _pct_of(
    part: list[float | None] | None, whole: list[float | None] | None
) -> float | None:
    """`part / whole × 100` from the newest column where both exist."""
    aligned = _latest_aligned(part, whole)
    if aligned is None:
        return None
    numerator, denominator = aligned
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 2)


def _apply_cash_flow(
    out: SnapshotFields,
    cf_headers: list[str],
    cf_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
) -> None:
    """OCF/PAT and FCF/PAT, each against net profit of the SAME fiscal year.

    Matching by column label (not "latest of each table") matters right
    after results season, when the P&L can already show a fiscal year the
    cash-flow table hasn't caught up with.
    """
    if not cf_rows:
        return
    annual_headers = _annual_headers(pl_headers)
    annual_np = _drop_ttm(pl_headers, pl_rows.get("net profit"))

    ocf = _cash_to_profit(
        cf_headers, cf_rows.get("cash from operating activity"), annual_headers, annual_np
    )
    if ocf is not None:
        out["ocf_to_pat"] = ocf
    fcf = _cash_to_profit(
        cf_headers, cf_rows.get("free cash flow"), annual_headers, annual_np
    )
    if fcf is not None:
        out["fcf_to_pat"] = fcf
    latest_fcf = _latest_with_label(cf_headers, cf_rows.get("free cash flow"))
    if latest_fcf is not None:
        out["free_cash_flow"] = latest_fcf[1] * RUPEES_PER_CRORE
    # Screener's free cash flow is operating cash flow less capex, so the two
    # rows of one fiscal year give that year's capex.
    cash_pair = _latest_aligned(
        cf_rows.get("cash from operating activity"), cf_rows.get("free cash flow")
    )
    if cash_pair is not None:
        operating_cash_flow, free_cash_flow = cash_pair
        out["operating_cash_flow"] = operating_cash_flow * RUPEES_PER_CRORE
        if operating_cash_flow >= free_cash_flow:
            out["capex"] = (operating_cash_flow - free_cash_flow) * RUPEES_PER_CRORE


def _cash_to_profit(
    cf_headers: list[str],
    cash_row: list[float | None] | None,
    pl_headers: list[str],
    net_profit_row: list[float | None],
) -> float | None:
    latest = _latest_with_label(cf_headers, cash_row)
    if latest is None:
        return None
    label, cash = latest
    profit = _value_at_label(pl_headers, net_profit_row, label)
    if profit is None or profit <= 0:
        return None
    return round(cash / profit, 3)


def _apply_ratios(
    out: SnapshotFields,
    rows: dict[str, list[float | None]],
) -> None:
    if not rows:
        return
    dd = _latest(rows.get("debtor days"))
    idx = _latest(rows.get("inventory days"))
    dp = _latest(rows.get("days payable"))
    ccc = _latest(rows.get("cash conversion cycle"))
    if dd is not None:
        out["debtor_days"] = dd
    if idx is not None:
        out["inventory_days"] = idx
    if dp is not None:
        out["payable_days"] = dp
    if ccc is not None:
        out["cash_conversion_cycle_days"] = ccc


def _apply_quarters(
    out: SnapshotFields,
    headers: list[str],
    rows: dict[str, list[float | None]],
) -> None:
    """Quarterly revenue, PAT and operating margin (newest-first), with what they add up to.

    A quarter is kept only when every available series has a value for
    it, so `quarterly_revenue[i]`, `quarterly_pat[i]` and
    `quarterly_dates[i]` always describe the same quarter. The operating
    margin is aligned to the same dates, None where it can't be computed.
    """
    sales = rows.get("sales") or rows.get("revenue")
    net_profit = rows.get("net profit")
    if not sales and not net_profit:
        return

    revenue: list[float] = []
    pat: list[float] = []
    margins: list[float | None] = []
    dates: list[str] = []
    for i, header in enumerate(headers):
        quarter_end = _screener_month_to_iso(header)
        if quarter_end is None:
            continue
        sale = _cell(sales, i)
        profit = _cell(net_profit, i)
        if (sales and sale is None) or (net_profit and profit is None):
            continue
        dates.append(quarter_end)
        margins.append(_core_operating_margin(rows, i))
        if sale is not None:
            revenue.append(sale * RUPEES_PER_CRORE)
        if profit is not None:
            pat.append(profit * RUPEES_PER_CRORE)

    # Screener lists oldest → newest; the snapshot stores newest-first.
    for series in (revenue, pat, margins, dates):
        series.reverse()

    if dates:
        out["quarterly_dates"] = dates
        out["quarterly_operating_margin"] = margins
    if revenue:
        out["quarterly_revenue"] = revenue
        growth = latest_yoy_growth(revenue, dates)
        if growth is not None:
            out["latest_yoy_revenue_growth"] = growth
    if pat:
        out["quarterly_pat"] = pat
        growth = latest_yoy_growth(pat, dates)
        if growth is not None:
            out["latest_yoy_pat_growth"] = growth
    _apply_trailing_twelve_months(out, revenue, pat, dates)


def _core_operating_margin(rows: dict[str, list[float | None]], i: int) -> float | None:
    """(Operating profit − depreciation) ÷ sales for column `i`, in percent.

    Depreciation comes off because screener's operating profit is pre-
    depreciation, while the yfinance path's Operating Income is after it —
    one definition of the operating margin, whatever the source.
    """
    sale = _cell(rows.get("sales") or rows.get("revenue"), i)
    operating_profit = _cell(rows.get("operating profit"), i)
    depreciation = _cell(rows.get("depreciation"), i)
    if not sale or sale <= 0 or operating_profit is None or depreciation is None:
        return None
    return round((operating_profit - depreciation) / sale * 100, 2)


def _apply_trailing_twelve_months(
    out: SnapshotFields, revenue: list[float], pat: list[float], dates: list[str]
) -> None:
    """Twelve-month revenue and profit from the four newest consecutive quarters."""
    ttm_revenue = trailing_twelve_months(revenue, dates) if len(revenue) == len(dates) else None
    ttm_profit = trailing_twelve_months(pat, dates) if len(pat) == len(dates) else None
    if ttm_revenue is None and ttm_profit is None:
        return
    out["ttm_period_end"] = dates[0]
    if ttm_revenue is not None:
        out["ttm_revenue"] = ttm_revenue
    if ttm_profit is not None:
        out["ttm_net_income"] = ttm_profit


def _cell(row: list[float | None] | None, i: int) -> float | None:
    return row[i] if row and i < len(row) else None


def _apply_asset_quality(
    out: SnapshotFields,
    q_rows: dict[str, list[float | None]],
) -> None:
    """Bank asset quality from the quarterly table's NPA rows.

    Screener's bank quarterly results carry `Gross NPA %` and `Net NPA %`
    rows (verified on HDFCBANK, 2026-11-08). Industrials have neither, so
    this is a no-op for them.
    """
    gross, gross_delta = _latest_and_yoy_delta(q_rows.get("gross npa %"))
    net, net_delta = _latest_and_yoy_delta(q_rows.get("net npa %"))
    if gross is not None:
        out["gross_npa_pct"] = gross
        if gross_delta is not None:
            out["gross_npa_delta_1y_pct_points"] = gross_delta
    if net is not None:
        out["net_npa_pct"] = net
        if net_delta is not None:
            out["net_npa_delta_1y_pct_points"] = net_delta


def _apply_derived(
    out: SnapshotFields,
    bs_headers: list[str],
    bs_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
) -> None:
    """ROA = latest annual net profit / total assets at that same year-end.

    The balance sheet often carries a newer half-year column (e.g. "Sep
    2025"), so "latest of each table" would divide FY profit by mid-year
    assets. Matching on the fiscal-year label avoids that.
    """
    latest = _latest_with_label(
        _annual_headers(pl_headers), _drop_ttm(pl_headers, pl_rows.get("net profit"))
    )
    if latest is None:
        return
    label, net_profit = latest
    total_assets = _value_at_label(bs_headers, bs_rows.get("total assets"), label)
    if total_assets is not None and total_assets > 0:
        out["roa"] = round(net_profit / total_assets * 100, 2)


def _apply_bank_metrics(
    out: SnapshotFields,
    bs_headers: list[str],
    bs_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
) -> None:
    """Bank-specific metrics: NIM, NII, non-interest income, CD ratio.

    Triggers when the screener P&L has the bank shape (a `revenue` row
    for interest income, an `interest` row for interest expense, and
    either a `financing profit` line or a `deposits` row on the BS).
    Industrials use `sales` / `operating profit` instead, so this is a
    safe discriminator and sets `is_bank=True` for downstream dispatch.

    Screener's `expenses` row on bank P&Ls conflates operating expenses
    with loan-loss provisions, so a formal Cost-to-Income cannot be
    derived cleanly from standard tables — we surface NII, non-interest
    income, and CD ratio which *are* directly computable.
    """
    if not (
        "revenue" in pl_rows
        and "interest" in pl_rows
        and ("financing profit" in pl_rows or "deposits" in bs_rows)
    ):
        return

    out["is_bank"] = True

    deposits = _latest(bs_rows.get("deposits"))
    borrowings = _latest(
        bs_rows.get("borrowings")
        or bs_rows.get("borrowing")
        or bs_rows.get("total debt")
    )
    if deposits is not None:
        out["deposits"] = deposits * RUPEES_PER_CRORE
    if borrowings is not None:
        out["borrowings"] = borrowings * RUPEES_PER_CRORE

    _apply_bank_spread(out, bs_headers, bs_rows, pl_headers, pl_rows)
    _apply_credit_deposit_ratio(out, bs_rows)


def _apply_bank_spread(
    out: SnapshotFields,
    bs_headers: list[str],
    bs_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
) -> None:
    """NII, NIM, and non-interest income for the latest full fiscal year."""
    annual_headers = _annual_headers(pl_headers)
    latest = _latest_with_label(
        annual_headers, _drop_ttm(pl_headers, pl_rows.get("revenue"))
    )
    if latest is None:
        return
    label, interest_earned = latest
    interest_expended = _value_at_label(
        annual_headers, _drop_ttm(pl_headers, pl_rows.get("interest")), label
    )
    if interest_expended is None:
        return
    nii = interest_earned - interest_expended
    out["net_interest_income"] = nii * RUPEES_PER_CRORE

    # NIM uses Total Assets as the denominator — a real bank analyst uses
    # average interest-earning assets, but screener doesn't split those.
    total_assets = _value_at_label(bs_headers, bs_rows.get("total assets"), label)
    if total_assets is not None and total_assets > 0:
        out["nim"] = round(nii / total_assets * 100, 2)

    # Non-interest income: only meaningful for banks where fees / treasury
    # are a real franchise axis. NBFCs book fee income inside the "revenue"
    # row, so screener's "other income" is essentially a rounding line
    # (often negligible or negative). Skip the ratio when structurally absent.
    other = _value_at_label(
        annual_headers, _drop_ttm(pl_headers, pl_rows.get("other income")), label
    )
    if other is not None and nii > 0:
        out["non_interest_income"] = other * RUPEES_PER_CRORE
        if (nii + other) > 0 and abs(other) / nii >= 0.02:
            out["non_interest_income_ratio"] = round(other / (nii + other) * 100, 2)


def _apply_credit_deposit_ratio(
    out: SnapshotFields, bs_rows: dict[str, list[float | None]]
) -> None:
    """CD ratio ≈ (Total Assets − Investments − Fixed Assets) / Deposits.

    Not surgical (includes some non-loan assets such as cash) but close
    enough to flag the >100% CD-ratio stress that matters. All inputs come
    from the same balance-sheet column.
    """
    total_assets = bs_rows.get("total assets")
    investments = bs_rows.get("investments")
    deposits = bs_rows.get("deposits")
    fixed_assets = bs_rows.get("fixed assets")

    with_fixed = _latest_aligned(total_assets, investments, deposits, fixed_assets)
    if with_fixed is not None:
        assets, invested, deposit_base, fixed = with_fixed
        net_loans = assets - invested - fixed
    else:
        without_fixed = _latest_aligned(total_assets, investments, deposits)
        if without_fixed is None:
            return
        assets, invested, deposit_base = without_fixed
        net_loans = assets - invested
    if deposit_base > 0:
        out["credit_deposit_ratio"] = round(net_loans / deposit_base * 100, 1)


def _apply_multi_year_trends(
    out: SnapshotFields,
    bs_headers: list[str],
    bs_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
    r_rows: dict[str, list[float | None]],
) -> None:
    """Expose raw multi-year series so the report can show direction.

    Latest-only scoring misses the story: a ROE of 15% that was 25% three
    years ago signals degrading capital efficiency, which matters for the
    prospects judgment even if today's snapshot still looks healthy.
    """
    # ROCE % from the screener #ratios section (annual, oldest→newest).
    roce_series = r_rows.get("roce %")
    if roce_series:
        out["roce_annual_trend"] = [v for v in roce_series if v is not None]

    # Sales, profit and margins by fiscal year, from the annual P&L rows.
    sales = _drop_ttm(pl_headers, pl_rows.get("sales") or pl_rows.get("revenue"))
    op_profit = _drop_ttm(pl_headers, pl_rows.get("operating profit"))
    net_profit = _drop_ttm(pl_headers, pl_rows.get("net profit"))

    if sales:
        out["sales_annual_trend"] = [v * RUPEES_PER_CRORE for v in sales if v is not None]
    if net_profit:
        out["net_profit_annual_trend"] = [
            v * RUPEES_PER_CRORE for v in net_profit if v is not None
        ]

    operating_margins = _margin_trend(op_profit, sales)
    if operating_margins:
        out["operating_margin_annual_trend"] = operating_margins
    net_margins = _margin_trend(net_profit, sales)
    if net_margins:
        out["net_margin_annual_trend"] = net_margins

    # Borrowings trajectory — is debt expanding or shrinking? Fiscal-year
    # columns only: a trailing half-year balance sheet ("Sep 2025") would
    # turn the "3-year" change into a 2.5-year one.
    borrowings_series = _fiscal_year_values(
        bs_headers,
        bs_rows.get("borrowings") or bs_rows.get("borrowing") or bs_rows.get("total debt"),
        fiscal_years=set(_annual_headers(pl_headers)),
    )
    if borrowings_series:
        clean = [v for v in borrowings_series if v is not None]
        if clean:
            out["borrowings_annual_trend"] = [v * RUPEES_PER_CRORE for v in clean]
            if len(clean) >= 4:
                latest = clean[-1]
                three_years_ago = clean[-4]
                if three_years_ago > 0:
                    out["borrowings_delta_3y_pct"] = round(
                        (latest - three_years_ago) / three_years_ago * 100, 1
                    )
                elif latest > 0:
                    # A % change from zero is undefined — flag it instead.
                    out["borrowings_went_from_zero"] = True
                else:
                    out["borrowings_delta_3y_pct"] = 0.0


def _margin_trend(
    part: list[float | None], sales: list[float | None]
) -> list[float]:
    """`part / sales × 100` for every fiscal year where both exist, oldest → newest."""
    return [
        round(numerator / sale * 100, 2)
        for numerator, sale in zip(part, sales)
        if numerator is not None and sale is not None and sale > 0
    ]


def _fiscal_year_values(
    headers: list[str],
    values: list[float | None] | None,
    *,
    fiscal_years: set[str],
) -> list[float | None]:
    """Values under fiscal-year column labels only (all of them if unknown)."""
    if not values:
        return []
    if not fiscal_years:
        return values
    return [v for header, v in zip(headers, values) if header in fiscal_years]


def _parse_shareholding_table(
    tree: "HTMLParser",
) -> tuple[list[str], dict[str, list[float | None]]]:
    """Parse `#shareholding > #quarterly-shp > table` → (headers, rows).

    Same positional contract as `_parse_section`. Cells are "N.NN%" except
    the number-of-shareholders row, which is a plain comma-separated count.
    """
    sh_el = tree.css_first("#shareholding")
    q_el = sh_el.css_first("#quarterly-shp") if sh_el is not None else None
    tbl = q_el.css_first("table") if q_el is not None else None
    if tbl is None:
        return [], {}

    raw_headers = [th.text(strip=True) for th in tbl.css("thead th")]
    headers = [h for h in raw_headers if h]

    rows: dict[str, list[float | None]] = {}
    for row in tbl.css("tbody tr"):
        cells = row.css("td")
        if not cells:
            continue
        raw_label = cells[0].text(strip=True)
        if not raw_label:
            continue
        label = raw_label.lower().rstrip("+").strip()
        values: list[float | None] = []
        for c in cells[1:]:
            text = c.text(strip=True)
            if not text or text == "--":
                values.append(None)
                continue
            parsed = _parse_pct(text)
            if parsed is None:
                parsed = _parse_float(text)
            values.append(parsed)
        if any(v is not None for v in values):
            rows[label] = values
    return headers, rows


def _apply_shareholding_trend(
    out: SnapshotFields,
    headers: list[str],
    rows: dict[str, list[float | None]],
) -> None:
    """Derive the quarterly shareholding trend + 1y deltas.

    Up to 12 quarters of Promoter / FII / DII / Public / Government /
    Number-of-shareholders rows. The YoY deltas are the cleanest "smart
    money vs domestic money" tape the standard screener page offers —
    watch for FII exit + DII accumulation (classic re-rating warning) and
    promoter buying/selling (insider conviction signal).
    """
    if not headers:
        return

    iso_dates: list[str] = []
    for h in headers:
        iso = _screener_month_to_iso(h)
        iso_dates.append(iso if iso else h)

    def _reverse_clean(series: list[float | None] | None) -> list[float]:
        if not series:
            return []
        return list(reversed([v for v in series if v is not None]))

    # Dates aligned with the raw (oldest→newest) row; reverse to newest-first.
    if iso_dates:
        out["shareholding_dates"] = list(reversed(iso_dates))

    out["insider_holding_trend"] = _reverse_clean(rows.get("promoters"))
    out["fii_holding_trend"] = _reverse_clean(rows.get("fiis"))
    out["dii_holding_trend"] = _reverse_clean(rows.get("diis"))
    out["public_holding_trend"] = _reverse_clean(rows.get("public"))
    out["num_shareholders_trend"] = _reverse_clean(rows.get("no. of shareholders"))

    # 1y deltas — most-recent minus 4-quarters-back. For % series the
    # delta is in percentage points; for counts it's a % change.
    def _delta_1y_pts(series: list[float]) -> float | None:
        if len(series) < 5:
            return None
        return round(series[0] - series[4], 2)

    def _delta_1y_pct(series: list[float]) -> float | None:
        if len(series) < 5 or series[4] == 0:
            return None
        return round((series[0] - series[4]) / series[4] * 100, 1)

    out["fii_delta_1y_pct_points"] = _delta_1y_pts(out["fii_holding_trend"])
    out["dii_delta_1y_pct_points"] = _delta_1y_pts(out["dii_holding_trend"])
    out["insider_delta_1y_pct_points"] = _delta_1y_pts(
        out["insider_holding_trend"]
    )
    out["num_shareholders_delta_1y_pct"] = _delta_1y_pct(
        out["num_shareholders_trend"]
    )


# ── Raw dump / peers / documents / analysis text ─────────────────────────────


def _apply_raw_dumps(
    out: SnapshotFields,
    pl_headers: list[str], pl_rows: dict[str, list[float | None]],
    bs_headers: list[str], bs_rows: dict[str, list[float | None]],
    cf_headers: list[str], cf_rows: dict[str, list[float | None]],
    r_headers: list[str], r_rows: dict[str, list[float | None]],
    q_headers: list[str], q_rows: dict[str, list[float | None]],
    sh_headers: list[str], sh_rows: dict[str, list[float | None]],
) -> None:
    """Store every parsed row verbatim so future questions don't re-scrape.

    Downstream code (scorers, reports, Claude skills) can look up any
    line item by its lowercased label without the parser having to learn
    it first — this is the "capture all of it" layer.
    """
    out["raw_profit_loss"] = pl_rows
    out["raw_profit_loss_headers"] = pl_headers
    out["raw_balance_sheet"] = bs_rows
    out["raw_balance_sheet_headers"] = bs_headers
    out["raw_cash_flow"] = cf_rows
    out["raw_cash_flow_headers"] = cf_headers
    out["raw_ratios"] = r_rows
    out["raw_ratios_headers"] = r_headers
    out["raw_quarters"] = q_rows
    out["raw_quarters_headers"] = q_headers
    if sh_headers or sh_rows:
        out["raw_shareholding_headers"] = sh_headers
        out["raw_shareholding_quarterly"] = sh_rows


def _apply_peers(tree: "HTMLParser", out: SnapshotFields) -> None:
    """Extract the peer-comparison table (relative valuation vs industry).

    Screener loads peers via JS into a placeholder, so the static HTML
    may contain either a real table or only `#peers-table-placeholder`.
    We return silently when the data isn't there.
    """
    peers_el = tree.css_first("#peers")
    if peers_el is None:
        return
    tbl = peers_el.css_first("table")
    if tbl is None:
        return
    header_cells = [th.text(strip=True) for th in tbl.css("thead th")]
    if not header_cells:
        return
    records: list[PeerRecord] = []
    for row in tbl.css("tbody tr"):
        cells = row.css("td")
        if len(cells) < 2:
            continue
        rec: PeerRecord = {}
        for i, cell in enumerate(cells):
            key = header_cells[i].lower() if i < len(header_cells) else f"col{i}"
            text = cell.text(strip=True)
            if not text:
                continue
            parsed = _parse_float(text.rstrip("%").replace(",", ""))
            rec[key] = parsed if parsed is not None else text
        if rec:
            records.append(rec)
    if records:
        out["peers"] = records


def _apply_documents(tree: "HTMLParser", out: SnapshotFields) -> None:
    """Extract links to annual reports, credit ratings, investor PPTs."""
    docs_el = tree.css_first("#documents")
    if docs_el is None:
        return
    records: list[DocumentRecord] = []
    for a in docs_el.css("a[href]")[:40]:  # cap to avoid ballooning
        href = a.attributes.get("href", "") or ""
        label = a.text(strip=True)
        if not href or not label:
            continue
        if href.startswith("#"):  # skip in-page navigation
            continue
        records.append({"label": label[:200], "url": href})
    if records:
        out["documents"] = records


def _apply_analysis_text(tree: "HTMLParser", out: SnapshotFields) -> None:
    """Capture screener's own `#analysis` narrative block, if present."""
    el = tree.css_first("#analysis")
    if el is None:
        return
    text = el.text(separator=" ", strip=True)
    if text:
        # Cap to a reasonable length — anything longer is boilerplate.
        out["screener_analysis_text"] = text[:3000]
