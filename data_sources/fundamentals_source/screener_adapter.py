"""screener.in adapter — authoritative source for Indian-specific ratios.

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
from typing import Any

import httpx

from data_sources.fundamentals_source.primitives import (
    HTTP_TIMEOUT_SECONDS,
    SCREENER_BASE_URL,
    SCREENER_USER_AGENT,
    _drop_ttm,
    _latest,
    _parse_float,
    _parse_pct,
    _screener_cagr,
    _screener_month_to_iso,
)

log = logging.getLogger(__name__)


# ── Public entry point ───────────────────────────────────────────────────────


async def _fetch_screener_in(ticker: str) -> dict[str, Any]:
    tree = await _fetch_html_tree(ticker)
    if tree is None:
        return {}

    out: dict[str, Any] = {}

    _parse_top_ratios(tree, out)
    _parse_h1(tree, out)
    _parse_shareholding(tree, out)

    # Parse each numeric table once and fan out. (headers, rows) tuples
    # preserve column alignment so _drop_ttm can trim by position.
    bs_headers, bs_rows = _parse_section(tree, "balance-sheet")
    pl_headers, pl_rows = _parse_section(tree, "profit-loss")
    cf_headers, cf_rows = _parse_section(tree, "cash-flow")
    r_headers, r_rows = _parse_section(tree, "ratios")

    _apply_balance_sheet(out, bs_headers, bs_rows)
    _apply_profit_loss(out, pl_headers, pl_rows)
    _apply_cash_flow(out, cf_rows, pl_headers, pl_rows)
    _apply_ratios(out, r_rows)
    _apply_quarters(tree, out)
    _apply_derived(out, bs_rows, pl_headers, pl_rows)
    _apply_bank_metrics(out, bs_rows, pl_headers, pl_rows)
    _apply_multi_year_trends(out, bs_headers, bs_rows, pl_headers, pl_rows, r_rows)
    _apply_shareholding_trend(tree, out)
    _apply_raw_dumps(
        out,
        pl_headers, pl_rows,
        bs_headers, bs_rows,
        cf_headers, cf_rows,
        r_headers, r_rows,
        tree,
    )
    _apply_peers(tree, out)
    _apply_documents(tree, out)
    _apply_analysis_text(tree, out)

    return out


async def _fetch_html_tree(ticker: str):
    """Fetch the screener.in company page and return a parsed tree, or None."""
    urls = [
        f"{SCREENER_BASE_URL}/{ticker.upper()}/consolidated/",
        f"{SCREENER_BASE_URL}/{ticker.upper()}/",
    ]
    headers = {"User-Agent": SCREENER_USER_AGENT}

    resp = None
    for url in urls:
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True
            ) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                break
        except Exception as e:
            log.warning("screener.in fetch error (%s): %s", url, e)
            continue

    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp is not None else "no response"
        log.warning("screener.in returned %s for %s", status, ticker)
        return None

    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        log.warning("selectolax not installed — screener parsing skipped")
        return None

    return HTMLParser(resp.text)


# ── Top-of-page parsers (single-value fields) ────────────────────────────────


def _parse_top_ratios(tree, out: dict[str, Any]) -> None:
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
            v = _parse_float(raw)
            if v is not None:
                out["market_cap_cr"] = v
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
            parts = [p.strip() for p in raw.split("/")]
            if len(parts) == 2:
                hi = _parse_float(parts[0])
                lo = _parse_float(parts[1])
                if hi is not None:
                    out["high_52w"] = hi
                if lo is not None:
                    out["low_52w"] = lo


def _parse_h1(tree, out: dict[str, Any]) -> None:
    h1 = tree.css_first("h1")
    if h1:
        text = h1.text(strip=True)
        if text:
            out["company_name"] = text


def _parse_shareholding(tree, out: dict[str, Any]) -> None:
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
            out["promoter_holding"] = latest
        elif "pledge" in label:
            out["pledge_pct"] = latest
    if "promoter_holding" in out and "pledge_pct" not in out:
        out["pledge_pct"] = 0.0


# ── Generic section parser ───────────────────────────────────────────────────


def _parse_section(
    tree, section_id: str
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
    out: dict[str, Any],
    headers: list[str],
    rows: dict[str, list[float | None]],
) -> None:
    if not rows:
        return
    # Screener uses "Borrowings+" for most companies and bare "Borrowing"
    # for banks / NBFCs. Also fall back to "total debt" for good measure.
    borrowings = _latest(
        rows.get("borrowings")
        or rows.get("borrowing")
        or rows.get("total debt")
    )
    equity_capital = _latest(rows.get("equity capital"))
    reserves = _latest(rows.get("reserves"))

    equity = None
    if equity_capital is not None and reserves is not None:
        equity = equity_capital + reserves

    if borrowings is not None and equity is not None and equity > 0:
        out["debt_to_equity"] = round(borrowings / equity, 3)


def _apply_profit_loss(
    out: dict[str, Any],
    headers: list[str],
    rows: dict[str, list[float | None]],
) -> None:
    if not rows:
        return

    sales = rows.get("sales") or rows.get("revenue")
    operating_profit = rows.get("operating profit")
    interest = rows.get("interest")
    net_profit = rows.get("net profit")
    tax_pct = rows.get("tax %")
    expenses = rows.get("expenses")

    latest_sales = _latest(sales)
    latest_op = _latest(operating_profit)
    latest_int = _latest(interest)
    latest_np = _latest(net_profit)
    latest_tax = _latest(tax_pct)

    if latest_op is not None and latest_int is not None and latest_int > 0:
        out["interest_coverage"] = round(latest_op / latest_int, 2)

    if latest_sales is not None and latest_sales > 0:
        if latest_op is not None:
            out["operating_margin"] = round(latest_op / latest_sales * 100, 2)
        if latest_np is not None:
            out["net_margin"] = round(latest_np / latest_sales * 100, 2)
        if expenses:
            latest_exp = _latest(expenses)
            if latest_exp is not None:
                out.setdefault(
                    "cogs_to_revenue",
                    round(latest_exp / latest_sales * 100, 2),
                )

    if latest_tax is not None and 0 <= latest_tax <= 100:
        out["tax_rate"] = latest_tax

    # CAGRs: drop TTM column first so the 3y window uses actual fiscal years.
    cagr = _screener_cagr(_drop_ttm(headers, sales), years=3)
    if cagr is not None:
        out["rev_3y_cagr"] = cagr
    cagr = _screener_cagr(_drop_ttm(headers, net_profit), years=3)
    if cagr is not None:
        out["pat_3y_cagr"] = cagr


def _apply_cash_flow(
    out: dict[str, Any],
    cf_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
) -> None:
    if not cf_rows:
        return
    ocf = _latest(cf_rows.get("cash from operating activity"))
    fcf = _latest(cf_rows.get("free cash flow"))

    # Compare against the latest *annual* net profit, not TTM — cash-flow
    # rows are annual, so mixing periods would distort OCF/PAT and FCF/PAT.
    annual_np = _drop_ttm(pl_headers, pl_rows.get("net profit"))
    net_profit = _latest(annual_np)

    if ocf is not None and net_profit is not None and net_profit > 0:
        out["ocf_to_pat"] = round(ocf / net_profit, 3)
    if fcf is not None and net_profit is not None and net_profit > 0:
        out["fcf_to_pat"] = round(fcf / net_profit, 3)


def _apply_ratios(
    out: dict[str, Any],
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


def _apply_quarters(tree, out: dict[str, Any]) -> None:
    headers, rows = _parse_section(tree, "quarters")
    sales = rows.get("sales") or rows.get("revenue")
    net_profit = rows.get("net profit")
    if not sales and not net_profit:
        return

    # Screener publishes in crores, yfinance in rupees. Normalise both
    # upstream paths to rupees so downstream display code (which divides
    # by 1e7 for Cr) works identically regardless of which source won.
    def cr_to_inr(vals: list[float | None]) -> list[float]:
        return [(v * 1e7) for v in vals if v is not None]

    # Oldest → newest in screener, we want most-recent-first.
    if sales:
        out["quarterly_revenue"] = list(reversed(cr_to_inr(sales)))
    if net_profit:
        out["quarterly_pat"] = list(reversed(cr_to_inr(net_profit)))

    if headers:
        iso_dates = [d for d in (_screener_month_to_iso(h) for h in headers) if d]
        if iso_dates:
            n = max(
                len(out.get("quarterly_revenue", [])),
                len(out.get("quarterly_pat", [])),
            )
            out["quarterly_dates"] = list(reversed(iso_dates))[:n]

    rev = out.get("quarterly_revenue") or []
    if len(rev) >= 5 and rev[4] > 0:
        out["latest_yoy_revenue_growth"] = round(
            (rev[0] - rev[4]) / rev[4] * 100, 2
        )
    pat = out.get("quarterly_pat") or []
    if len(pat) >= 5 and pat[4] > 0:
        out["latest_yoy_pat_growth"] = round(
            (pat[0] - pat[4]) / pat[4] * 100, 2
        )


def _apply_derived(
    out: dict[str, Any],
    bs_rows: dict[str, list[float | None]],
    pl_headers: list[str],
    pl_rows: dict[str, list[float | None]],
) -> None:
    """Cross-section derivations that need both balance sheet and P&L."""
    total_assets = _latest(bs_rows.get("total assets"))
    annual_np = _drop_ttm(pl_headers, pl_rows.get("net profit"))
    latest_np = _latest(annual_np)
    if total_assets is not None and latest_np is not None and total_assets > 0:
        out["roa"] = round(latest_np / total_assets * 100, 2)


def _apply_bank_metrics(
    out: dict[str, Any],
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

    latest_revenue = _latest(_drop_ttm(pl_headers, pl_rows.get("revenue")))
    latest_interest = _latest(_drop_ttm(pl_headers, pl_rows.get("interest")))
    latest_other = _latest(_drop_ttm(pl_headers, pl_rows.get("other income")))

    total_assets = _latest(bs_rows.get("total assets"))
    deposits = _latest(bs_rows.get("deposits"))
    borrowings = _latest(
        bs_rows.get("borrowings")
        or bs_rows.get("borrowing")
        or bs_rows.get("total debt")
    )
    investments = _latest(bs_rows.get("investments"))
    fixed_assets = _latest(bs_rows.get("fixed assets"))

    if deposits is not None:
        out["deposits_cr"] = round(deposits, 0)
    if borrowings is not None:
        out["borrowings_cr"] = round(borrowings, 0)

    nii: float | None = None
    if latest_revenue is not None and latest_interest is not None:
        nii = latest_revenue - latest_interest
        out["net_interest_income_cr"] = round(nii, 0)

    # NIM uses Total Assets as the denominator — a real bank analyst uses
    # average interest-earning assets, but screener doesn't split those.
    if nii is not None and total_assets is not None and total_assets > 0:
        out["nim"] = round(nii / total_assets * 100, 2)

    # Non-interest income: only meaningful for banks where fees / treasury
    # are a real franchise axis. NBFCs book fee income inside the "revenue"
    # row, so screener's "other income" is essentially a rounding line
    # (often negligible or negative). Skip the ratio when structurally absent.
    if latest_other is not None and nii is not None and nii > 0:
        out["non_interest_income_cr"] = round(latest_other, 0)
        if (nii + latest_other) > 0 and abs(latest_other) / nii >= 0.02:
            out["non_interest_income_ratio"] = round(
                latest_other / (nii + latest_other) * 100, 2
            )

    # Credit-Deposit ratio ≈ (Total Assets − Investments − Fixed Assets) /
    # Deposits. Not surgical (includes some non-loan assets) but close
    # enough to flag the >100% CD ratio stress which is what matters.
    if (
        total_assets is not None
        and investments is not None
        and deposits is not None
        and deposits > 0
    ):
        net_loans = total_assets - investments
        if fixed_assets is not None:
            net_loans -= fixed_assets
        out["credit_deposit_ratio"] = round(net_loans / deposits * 100, 1)


def _apply_multi_year_trends(
    out: dict[str, Any],
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

    # Operating margin trend computed from P&L annual rows.
    sales = _drop_ttm(pl_headers, pl_rows.get("sales") or pl_rows.get("revenue"))
    op_profit = _drop_ttm(pl_headers, pl_rows.get("operating profit"))
    net_profit = _drop_ttm(pl_headers, pl_rows.get("net profit"))

    if sales:
        out["sales_annual_trend"] = [v for v in sales if v is not None]
    if net_profit:
        out["net_profit_annual_trend"] = [v for v in net_profit if v is not None]

    if sales and op_profit:
        trend: list[float] = []
        for s, op in zip(sales, op_profit):
            if s is None or op is None or s <= 0:
                continue
            trend.append(round(op / s * 100, 2))
        if trend:
            out["operating_margin_annual_trend"] = trend

    # Borrowings trajectory — is debt expanding or shrinking?
    borrowings_series = (
        bs_rows.get("borrowings")
        or bs_rows.get("borrowing")
        or bs_rows.get("total debt")
    )
    if borrowings_series:
        clean = [v for v in borrowings_series if v is not None]
        if clean:
            out["borrowings_annual_trend"] = clean
            if len(clean) >= 4:
                latest = clean[-1]
                three_years_ago = clean[-4]
                if three_years_ago > 0:
                    out["borrowings_delta_3y_pct"] = round(
                        (latest - three_years_ago) / three_years_ago * 100, 1
                    )
                elif latest > 0:
                    out["borrowings_delta_3y_pct"] = float("inf")
                else:
                    out["borrowings_delta_3y_pct"] = 0.0


def _apply_shareholding_trend(tree, out: dict[str, Any]) -> None:
    """Parse the quarterly shareholding pattern table.

    Screener's `#shareholding > #quarterly-shp > table` has up to 12
    quarters of Promoter / FII / DII / Public / Government /
    Number-of-shareholders rows. The YoY deltas in these lines are the
    cleanest "smart money vs domestic money" tape we get from the
    standard screener page — watch for FII exit + DII accumulation
    (classic re-rating warning) and promoter buying/selling (insider
    conviction signal).
    """
    sh_el = tree.css_first("#shareholding")
    if sh_el is None:
        return
    q_el = sh_el.css_first("#quarterly-shp")
    if q_el is None:
        return
    tbl = q_el.css_first("table")
    if tbl is None:
        return

    raw_headers = [th.text(strip=True) for th in tbl.css("thead th")]
    headers = [h for h in raw_headers if h]
    if not headers:
        return

    iso_dates: list[str] = []
    for h in headers:
        iso = _screener_month_to_iso(h)
        iso_dates.append(iso if iso else h)

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
            # Shareholding cells are "N.NN%"; number of shareholders is a
            # raw comma-separated integer.
            parsed = _parse_pct(text)
            if parsed is None:
                parsed = _parse_float(text)
            values.append(parsed)
        if any(v is not None for v in values):
            rows[label] = values

    def _reverse_clean(series: list[float | None] | None) -> list[float]:
        if not series:
            return []
        return list(reversed([v for v in series if v is not None]))

    # Dates aligned with the raw (oldest→newest) row; reverse to newest-first.
    if iso_dates:
        out["shareholding_dates"] = list(reversed(iso_dates))

    out["promoter_holding_trend"] = _reverse_clean(rows.get("promoters"))
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
    out["promoter_delta_1y_pct_points"] = _delta_1y_pts(
        out["promoter_holding_trend"]
    )
    out["num_shareholders_delta_1y_pct"] = _delta_1y_pct(
        out["num_shareholders_trend"]
    )


# ── Raw dump / peers / documents / analysis text ─────────────────────────────


def _apply_raw_dumps(
    out: dict[str, Any],
    pl_headers: list[str], pl_rows: dict[str, list[float | None]],
    bs_headers: list[str], bs_rows: dict[str, list[float | None]],
    cf_headers: list[str], cf_rows: dict[str, list[float | None]],
    r_headers: list[str], r_rows: dict[str, list[float | None]],
    tree,
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

    q_headers, q_rows = _parse_section(tree, "quarters")
    out["raw_quarters"] = q_rows
    out["raw_quarters_headers"] = q_headers

    sh_el = tree.css_first("#shareholding")
    if sh_el is None:
        return
    q_sh = sh_el.css_first("#quarterly-shp")
    if q_sh is None:
        return
    tbl = q_sh.css_first("table")
    if tbl is None:
        return

    raw_headers = [th.text(strip=True) for th in tbl.css("thead th")]
    headers = [h for h in raw_headers if h]
    out["raw_shareholding_headers"] = headers
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
            v = _parse_pct(text)
            if v is None:
                v = _parse_float(text)
            values.append(v)
        if any(v is not None for v in values):
            rows[label] = values
    out["raw_shareholding_quarterly"] = rows


def _apply_peers(tree, out: dict[str, Any]) -> None:
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
    records: list[dict[str, Any]] = []
    for row in tbl.css("tbody tr"):
        cells = row.css("td")
        if len(cells) < 2:
            continue
        rec: dict[str, Any] = {}
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


def _apply_documents(tree, out: dict[str, Any]) -> None:
    """Extract links to annual reports, credit ratings, investor PPTs."""
    docs_el = tree.css_first("#documents")
    if docs_el is None:
        return
    records: list[dict[str, str]] = []
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


def _apply_analysis_text(tree, out: dict[str, Any]) -> None:
    """Capture screener's own `#analysis` narrative block, if present."""
    el = tree.css_first("#analysis")
    if el is None:
        return
    text = el.text(separator=" ", strip=True)
    if text:
        # Cap to a reasonable length — anything longer is boilerplate.
        out["screener_analysis_text"] = text[:3000]
