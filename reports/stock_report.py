"""`analyse <TICKER>` — full deep-dive terminal report + Markdown save.

Renders a BalanceSheetSnapshot (live data) + StockScore (deterministic
fundamentals + Claude-written judgments). Pillars that don't yet have a
judgment are shown with a clear "pending Claude judgment" marker so the
reader knows the verdict is incomplete.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from data_sources.fundamentals_source import BalanceSheetSnapshot
from reports.formatting import (
    evidence_table,
    make_console,
    section_panel,
    verdict_text,
)
from scorers.base import StockScore

PILLAR_WEIGHTS_PCT: tuple[tuple[str, int], ...] = (
    ("fundamentals", 40),
    ("leadership", 30),
    ("prospects", 15),
    ("news", 15),
)


# ── Public entry point ────────────────────────────────────────────────────────


def render_stock_report(
    snap: BalanceSheetSnapshot,
    score: StockScore,
    *,
    output_dir: Path | None = None,
    console: Console | None = None,
) -> Path | None:
    console = console or make_console()

    console.print(_header_panel(snap, score))
    console.print()

    if snap.business_summary:
        console.print(
            Panel(
                snap.business_summary,
                title="Business — what the company actually does",
                title_align="left",
                border_style="dim",
            )
        )
        console.print()

    profile_panel = _financial_profile_panel(snap)
    if profile_panel is not None:
        console.print(profile_panel)

    for pillar, weight in PILLAR_WEIGHTS_PCT:
        evidence = score.evidence.get(pillar, [])
        score_val = getattr(score, f"{pillar}_score")
        console.print(
            section_panel(
                title=pillar.title(),
                weight_pct=weight,
                score=score_val,
                body=evidence_table(evidence),
            )
        )

    console.print()
    console.print(
        Panel(
            verdict_text(score.verdict, score.confidence),
            title=f"Composite {score.composite}/100",
            title_align="left",
            border_style="bold",
        )
    )

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        fp = output_dir / f"{score.ticker}_{datetime.now().strftime('%Y-%m-%d')}.md"
        fp.write_text(_markdown(snap, score))
        return fp
    return None


# ── Financial Profile panel (industrial + bank variants) ─────────────────────


def _financial_profile_panel(snap: BalanceSheetSnapshot) -> Panel | None:
    """Non-scored snapshot: margins, cost mix, working capital, quarterly trend."""
    rows = _financial_profile_rows(snap)
    if not rows:
        return None

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    table.add_column("Section", style="dim")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for section, metric, value in rows:
        table.add_row(section, metric, value)

    return Panel(
        table,
        title="Financial Profile — where the money comes from and goes to",
        title_align="left",
        border_style="cyan",
    )


def _financial_profile_rows(
    snap: BalanceSheetSnapshot,
) -> list[tuple[str, str, str]]:
    if snap.is_bank:
        return _bank_financial_profile_rows(snap)
    return _industrial_financial_profile_rows(snap)


def _industrial_financial_profile_rows(
    snap: BalanceSheetSnapshot,
) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []

    # Margin stack
    margin_rows: list[tuple[str, str]] = []
    if snap.gross_margin is not None:
        margin_rows.append(("Gross margin", f"{snap.gross_margin:.1f}%"))
    if snap.operating_margin is not None:
        margin_rows.append(("Operating margin", f"{snap.operating_margin:.1f}%"))
    if snap.net_margin is not None:
        margin_rows.append(("Net margin", f"{snap.net_margin:.1f}%"))
    for i, (label, val) in enumerate(margin_rows):
        rows.append(("Margins" if i == 0 else "", label, val))

    # Cost structure
    cost_rows: list[tuple[str, str]] = []
    if snap.cogs_to_revenue is not None:
        cost_rows.append(("COGS / revenue", f"{snap.cogs_to_revenue:.1f}%"))
    if snap.sga_to_revenue is not None:
        cost_rows.append(("SG&A / revenue", f"{snap.sga_to_revenue:.1f}%"))
    if snap.tax_rate is not None:
        cost_rows.append(("Effective tax rate", f"{snap.tax_rate:.1f}%"))
    for i, (label, val) in enumerate(cost_rows):
        rows.append(("Cost mix" if i == 0 else "", label, val))

    # Working capital
    wc_rows: list[tuple[str, str]] = []
    if snap.debtor_days is not None:
        wc_rows.append(("Debtor days", f"{snap.debtor_days:.0f}"))
    if snap.inventory_days is not None:
        wc_rows.append(("Inventory days", f"{snap.inventory_days:.0f}"))
    if snap.payable_days is not None:
        wc_rows.append(("Payable days", f"{snap.payable_days:.0f}"))
    if snap.cash_conversion_cycle_days is not None:
        wc_rows.append(
            ("Cash conversion cycle", f"{snap.cash_conversion_cycle_days:.0f} days")
        )
    for i, (label, val) in enumerate(wc_rows):
        rows.append(("Working capital" if i == 0 else "", label, val))

    # Asset composition
    asset_rows: list[tuple[str, str]] = []
    if snap.tangible_book_ratio is not None:
        asset_rows.append(("Tangible book / equity", f"{snap.tangible_book_ratio:.2f}"))
    if snap.goodwill_to_equity is not None:
        asset_rows.append(("Goodwill / equity", f"{snap.goodwill_to_equity:.2f}"))
    for i, (label, val) in enumerate(asset_rows):
        rows.append(("Asset mix" if i == 0 else "", label, val))

    _append_multi_year_trend_rows(rows, snap)
    _append_shareholding_trend_rows(rows, snap)
    _append_quarter_rows(rows, snap)
    return rows


def _append_multi_year_trend_rows(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    """Render 'is the business improving or degrading?' in one section."""
    trend_rows: list[tuple[str, str]] = []
    if snap.roce_annual_trend and len(snap.roce_annual_trend) >= 3:
        series = snap.roce_annual_trend
        trend_rows.append(
            (
                "ROCE 5y trend",
                _trend_summary(series[-5:], unit="%", precision=0),
            )
        )
    if (
        snap.operating_margin_annual_trend
        and len(snap.operating_margin_annual_trend) >= 3
    ):
        series = snap.operating_margin_annual_trend
        trend_rows.append(
            (
                "Operating margin 5y trend",
                _trend_summary(series[-5:], unit="%", precision=1),
            )
        )
    if snap.borrowings_delta_3y_pct is not None:
        delta = snap.borrowings_delta_3y_pct
        if delta == float("inf"):
            delta_str = "+∞% (went from zero to debt)"
        else:
            delta_str = f"{delta:+.1f}% over 3 years"
        arrow = "↑ debt-expanding" if delta > 10 else "↓ deleveraging" if delta < -10 else "→ stable"
        trend_rows.append(("Borrowings 3y change", f"{delta_str}  ·  {arrow}"))
    for i, (label, val) in enumerate(trend_rows):
        rows.append(("Multi-year trend" if i == 0 else "", label, val))


def _append_shareholding_trend_rows(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    """Smart-money / domestic-money tape — the classic re-rating tell."""
    if not snap.shareholding_dates:
        return

    sh_rows: list[tuple[str, str]] = []

    def _latest(series: list[float]) -> str | None:
        if not series:
            return None
        return f"{series[0]:.2f}%"

    def _delta_str(delta: float | None) -> str:
        if delta is None:
            return ""
        if delta == 0:
            return "  (flat YoY)"
        return f"  ({delta:+.2f}pts YoY)"

    latest_promoter = _latest(snap.promoter_holding_trend)
    if latest_promoter is not None:
        sh_rows.append(
            (
                "Promoter",
                f"{latest_promoter}{_delta_str(snap.promoter_delta_1y_pct_points)}",
            )
        )

    latest_fii = _latest(snap.fii_holding_trend)
    if latest_fii is not None:
        delta = snap.fii_delta_1y_pct_points
        flag = ""
        if delta is not None and delta <= -2:
            flag = "  ⚠ FII exit"
        elif delta is not None and delta >= 2:
            flag = "  ✓ FII accumulating"
        sh_rows.append(("FII", f"{latest_fii}{_delta_str(delta)}{flag}"))

    latest_dii = _latest(snap.dii_holding_trend)
    if latest_dii is not None:
        delta = snap.dii_delta_1y_pct_points
        flag = ""
        if delta is not None and delta >= 2:
            flag = "  ✓ DII accumulating"
        elif delta is not None and delta <= -2:
            flag = "  ⚠ DII exit"
        sh_rows.append(("DII", f"{latest_dii}{_delta_str(delta)}{flag}"))

    latest_public = _latest(snap.public_holding_trend)
    if latest_public is not None:
        sh_rows.append(("Public / retail", f"{latest_public}"))

    if snap.num_shareholders_trend and snap.num_shareholders_delta_1y_pct is not None:
        latest_count = snap.num_shareholders_trend[0]
        delta_pct = snap.num_shareholders_delta_1y_pct
        sh_rows.append(
            (
                "Retail shareholders",
                f"{latest_count:,.0f}  ({delta_pct:+.1f}% YoY)",
            )
        )

    for i, (label, val) in enumerate(sh_rows):
        rows.append(("Shareholding" if i == 0 else "", label, val))


def _trend_summary(series: list[float], *, unit: str, precision: int) -> str:
    """Render a short series as 'first → ... → last  ·  direction'."""
    if not series:
        return ""
    if len(series) == 1:
        return f"{series[0]:.{precision}f}{unit}"
    first = series[0]
    last = series[-1]
    middle = " → ".join(f"{v:.{precision}f}{unit}" for v in series)
    change = last - first
    if change > 0.5:
        arrow = "↑ improving"
    elif change < -0.5:
        arrow = "↓ degrading"
    else:
        arrow = "→ stable"
    return f"{middle}  ·  {arrow}"


def _bank_financial_profile_rows(
    snap: BalanceSheetSnapshot,
) -> list[tuple[str, str, str]]:
    """Bank-specific profile: NIM, NII split, CD ratio, deposit/borrow mix."""
    rows: list[tuple[str, str, str]] = []

    spread_rows: list[tuple[str, str]] = []
    if snap.nim is not None:
        spread_rows.append(("Net interest margin (NIM)", f"{snap.nim:.2f}%"))
    if snap.net_interest_income_cr is not None:
        spread_rows.append(
            ("Net interest income", f"₹{snap.net_interest_income_cr:,.0f} Cr")
        )
    if snap.non_interest_income_cr is not None:
        spread_rows.append(
            ("Non-interest income (fee/treasury)",
             f"₹{snap.non_interest_income_cr:,.0f} Cr")
        )
    if snap.non_interest_income_ratio is not None:
        spread_rows.append(
            ("Non-interest income ratio", f"{snap.non_interest_income_ratio:.1f}%")
        )
    for i, (label, val) in enumerate(spread_rows):
        rows.append(("Spread & fees" if i == 0 else "", label, val))

    profit_rows: list[tuple[str, str]] = []
    if snap.roe is not None:
        profit_rows.append(("ROE", f"{snap.roe:.1f}%"))
    if snap.roa is not None:
        profit_rows.append(("ROA", f"{snap.roa:.2f}%"))
    if snap.tax_rate is not None:
        profit_rows.append(("Effective tax rate", f"{snap.tax_rate:.1f}%"))
    for i, (label, val) in enumerate(profit_rows):
        rows.append(("Profitability" if i == 0 else "", label, val))

    bs_rows: list[tuple[str, str]] = []
    if snap.deposits_cr is not None:
        bs_rows.append(("Deposits", f"₹{snap.deposits_cr:,.0f} Cr"))
    if snap.borrowings_cr is not None:
        bs_rows.append(("Borrowings (ex-deposits)", f"₹{snap.borrowings_cr:,.0f} Cr"))
    if snap.credit_deposit_ratio is not None:
        label = "Credit-Deposit ratio"
        val = f"{snap.credit_deposit_ratio:.1f}%"
        if snap.credit_deposit_ratio > 100:
            val += "  (high — lending > funding)"
        bs_rows.append((label, val))
    for i, (label, val) in enumerate(bs_rows):
        rows.append(("Balance sheet" if i == 0 else "", label, val))

    _append_multi_year_trend_rows(rows, snap)
    _append_shareholding_trend_rows(rows, snap)
    _append_quarter_rows(rows, snap)

    rows.append(
        (
            "Missing",
            "GNPA / NNPA / CASA / CRAR",
            "screener does not expose — assess in Claude leadership pillar",
        )
    )
    return rows


# ── Quarterly trend rows (shared by both profile variants) ───────────────────


def _append_quarter_rows(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    _append_quarterly_rows(
        rows,
        section_label="Quarterly revenue",
        values=snap.quarterly_revenue,
        dates=snap.quarterly_dates,
    )
    _append_quarterly_rows(
        rows,
        section_label="Quarterly PAT",
        values=snap.quarterly_pat,
        dates=snap.quarterly_dates,
    )


def _append_quarterly_rows(
    rows: list[tuple[str, str, str]],
    *,
    section_label: str,
    values: list[float],
    dates: list[str],
) -> None:
    """Append up to 4 quarter rows with QoQ + YoY annotations.

    `values` and `dates` are most-recent-first (index 0 = latest quarter).
    QoQ and YoY lookups are *date-aware* so a gap in the quarterly series
    (yfinance occasionally drops a middle quarter for recent IPOs) doesn't
    misalign the comparison: for each quarter we look up the entry whose
    date is ~90 days earlier (QoQ) or ~365 days earlier (YoY) and show
    an em-dash when the counterpart is missing.
    """
    if not values or not dates:
        return
    n = min(4, len(values), len(dates))
    for i in range(n):
        section = section_label if i == 0 else ""
        cr_value = values[i] / 1e7
        qoq = _pct_change_by_date(
            values, dates, current_idx=i, days_ago=90, tolerance=30
        )
        yoy = _pct_change_by_date(
            values, dates, current_idx=i, days_ago=365, tolerance=45
        )
        qoq_str = f"{qoq:+.1f}%" if qoq is not None else "—"
        yoy_str = f"{yoy:+.1f}%" if yoy is not None else "—"
        value_str = f"₹{cr_value:,.0f} Cr  ·  QoQ {qoq_str}  ·  YoY {yoy_str}"
        rows.append((section, dates[i], value_str))


def _pct_change_by_date(
    values: list[float],
    dates: list[str],
    *,
    current_idx: int,
    days_ago: int,
    tolerance: int,
) -> float | None:
    """Find the entry whose date is roughly `days_ago` before `dates[current_idx]`.

    Returns None when no counterpart falls within ±`tolerance` days of the
    target, when either endpoint is missing, or when the past value is zero
    or negative.
    """
    from datetime import date as _date

    if current_idx >= len(values) or current_idx >= len(dates):
        return None
    current = values[current_idx]
    if current is None:
        return None
    try:
        current_date = _date.fromisoformat(dates[current_idx])
    except (TypeError, ValueError):
        return None

    best_idx: int | None = None
    best_gap: int = tolerance + 1
    for j in range(current_idx + 1, len(dates)):
        try:
            past_date = _date.fromisoformat(dates[j])
        except (TypeError, ValueError):
            continue
        actual_delta = (current_date - past_date).days
        gap = abs(actual_delta - days_ago)
        if gap < best_gap:
            best_gap = gap
            best_idx = j
    if best_idx is None:
        return None
    past = values[best_idx]
    if past is None or past <= 0:
        return None
    return (current - past) / past * 100


# ── Header panel (ticker + price + valuation + analyst bundle) ──────────────


def _header_panel(snap: BalanceSheetSnapshot, score: StockScore) -> Panel:
    name = snap.company_name or snap.ticker
    sector = f"{snap.sector or '—'} / {snap.industry or '—'}"
    price = f"₹{snap.current_price:,.2f}" if snap.current_price else "—"
    hi = f"₹{snap.high_52w:,.2f}" if snap.high_52w else "—"
    lo = f"₹{snap.low_52w:,.2f}" if snap.low_52w else "—"
    mcap = f"₹{snap.market_cap_cr:,.0f} Cr" if snap.market_cap_cr else "—"

    low_conf_tag = " [yellow](LOW CONFIDENCE)[/yellow]" if score.low_confidence else ""
    source_tag = snap.source or "live"

    lines = [
        f"[bold]{score.ticker}[/bold] — {name}",
        f"[dim]{sector}[/dim]",
        f"Price: {price}  ·  52w: {lo} – {hi}  ·  Market cap: {mcap}",
    ]

    valuation_parts = _valuation_parts(snap)
    if valuation_parts:
        lines.append("Valuation: " + "  ·  ".join(valuation_parts))

    analyst_parts = _analyst_parts(snap)
    if analyst_parts:
        lines.append("Analyst bundle: " + "  ·  ".join(analyst_parts))

    lines.append(
        f"Data completeness: {score.data_completeness_pct:.0f}%  ·  "
        f"Source: {source_tag}{low_conf_tag}"
    )
    return Panel("\n".join(lines), title="Stock Analysis", title_align="left")


def _analyst_parts(snap: BalanceSheetSnapshot) -> list[str]:
    parts: list[str] = []
    if snap.analyst_target_mean is not None:
        target = f"target ₹{snap.analyst_target_mean:,.0f}"
        if snap.current_price and snap.current_price > 0:
            upside = (snap.analyst_target_mean - snap.current_price) / snap.current_price * 100
            target += f"  ({upside:+.1f}% upside)"
        parts.append(target)
    if snap.analyst_recommendation_mean is not None:
        # Yahoo scale: 1 = strong buy, 2 = buy, 3 = hold, 4 = sell, 5 = strong sell.
        rec = snap.analyst_recommendation_mean
        if rec <= 1.5:
            label = "Strong Buy"
        elif rec <= 2.5:
            label = "Buy"
        elif rec <= 3.5:
            label = "Hold"
        elif rec <= 4.5:
            label = "Sell"
        else:
            label = "Strong Sell"
        ana_count = f" ({snap.analyst_opinion_count} analysts)" if snap.analyst_opinion_count else ""
        parts.append(f"consensus {label} ({rec:.2f}){ana_count}")
    return parts


def _valuation_parts(snap: BalanceSheetSnapshot) -> list[str]:
    parts: list[str] = []
    if snap.pe_ratio is not None:
        parts.append(f"P/E {snap.pe_ratio:.1f}")
    pb = _price_to_book(snap)
    if pb is not None:
        parts.append(f"P/B {pb:.2f}")
    if snap.dividend_yield is not None:
        parts.append(f"Div yield {snap.dividend_yield:.2f}%")
    return parts


def _price_to_book(snap: BalanceSheetSnapshot) -> float | None:
    if snap.current_price is None or snap.book_value_per_share is None:
        return None
    if snap.book_value_per_share <= 0:
        return None
    return snap.current_price / snap.book_value_per_share


# ── Markdown output (saved to data_cache/reports/<TICKER>_<date>.md) ─────────


def _markdown(snap: BalanceSheetSnapshot, score: StockScore) -> str:
    lines: list[str] = [
        f"# {score.ticker} — {snap.company_name or ''}",
        "",
        f"**Verdict:** {score.verdict} (confidence {score.confidence:.0%})",
        f"**Composite:** {score.composite}/100 | "
        f"Data completeness: {score.data_completeness_pct:.0f}%"
        + (" | **LOW CONFIDENCE**" if score.low_confidence else ""),
        "",
    ]
    if snap.sector:
        lines.append(f"- Sector/Industry: {snap.sector} / {snap.industry}")
    if snap.current_price is not None:
        lines.append(f"- Current price: ₹{snap.current_price:,.2f}")
    if snap.high_52w is not None and snap.low_52w is not None:
        lines.append(
            f"- 52w range: ₹{snap.low_52w:,.2f} – ₹{snap.high_52w:,.2f}"
        )
    if snap.market_cap_cr is not None:
        lines.append(f"- Market cap: ₹{snap.market_cap_cr:,.0f} Cr")
    valuation_parts = _valuation_parts(snap)
    if valuation_parts:
        lines.append("- Valuation: " + "  ·  ".join(valuation_parts))
    lines.append(f"- Source: {snap.source}")
    lines.append("")

    profile_rows = _financial_profile_rows(snap)
    if profile_rows:
        lines.append("## Financial Profile")
        current_section = ""
        for section, metric, value in profile_rows:
            if section and section != current_section:
                lines.append("")
                lines.append(f"**{section}**")
                current_section = section
            lines.append(f"- {metric}: {value}")
        lines.append("")

    for pillar, weight in PILLAR_WEIGHTS_PCT:
        score_val = getattr(score, f"{pillar}_score")
        lines.append(f"## {pillar.title()} ({weight}%) — {score_val}/100")
        for bullet in score.evidence.get(pillar, []):
            lines.append(f"- {bullet}")
        lines.append("")

    lines.append(f"_Generated: {score.last_updated}_")
    return "\n".join(lines)
