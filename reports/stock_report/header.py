"""Header panel — ticker, price, valuation, analyst bundle, data-source tag.

First thing the reader sees. Also the only place where we surface
valuation (P/E, P/B, div yield) and the yfinance analyst consensus
(target price and Strong Buy/Buy/Hold/Sell/Strong Sell label).
"""

from __future__ import annotations

from rich.panel import Panel

from data_sources.fundamentals_source import BalanceSheetSnapshot, is_bank_snapshot
from reports.formatting import format_money, format_price
from scorers.base import StockScore
from scorers.risk_profile import risk_discount, risk_profile
from scorers.valuation_scorer import sustainable_earnings


def header_panel(snap: BalanceSheetSnapshot, score: StockScore) -> Panel:
    name = snap.company_name or snap.ticker
    listing = " · ".join(part for part in (snap.exchange, snap.country) if part)
    sector = f"{snap.sector or '—'} / {snap.industry or '—'}"
    price = format_price(snap.current_price, snap.currency)
    hi = format_price(snap.high_52w, snap.currency)
    lo = format_price(snap.low_52w, snap.currency)
    mcap = format_money(snap.market_cap, snap.market_cap_currency)

    low_conf_tag = " [yellow](LOW CONFIDENCE)[/yellow]" if score.low_confidence else ""
    source_tag = snap.source or "live"

    lines = [
        f"[bold]{score.ticker}[/bold] — {name}",
        f"[dim]{sector}{f'  ·  {listing}' if listing else ''}[/dim]",
        f"Price: {price}  ·  52w: {lo} – {hi}  ·  Market cap: {mcap}",
    ]

    valuation_parts = valuation_parts_for(snap)
    if valuation_parts:
        lines.append("Valuation: " + "  ·  ".join(valuation_parts))

    risk_parts = risk_parts_for(snap)
    discount = risk_discount(risk_profile(snap))
    lines.append(
        "Risk, measured: " + ("  ·  ".join(risk_parts) or "nothing measurable yet")
        + f"  ·  {discount:.1f} points off when ranked against others"
    )

    analyst_parts = analyst_parts_for(snap)
    if analyst_parts:
        lines.append("Analyst bundle: " + "  ·  ".join(analyst_parts))

    lines.append(
        f"Data completeness: {score.data_completeness_pct:.0f}%  ·  "
        f"Source: {source_tag}{low_conf_tag}"
    )
    return Panel("\n".join(lines), title="Stock Analysis", title_align="left")


# ── Helpers (also consumed by the markdown writer) ───────────────────────────


def valuation_parts_for(snap: BalanceSheetSnapshot) -> list[str]:
    """P/E (with the P/E on repeatable earnings when they differ), P/B, yields."""
    parts: list[str] = []
    if snap.pe_ratio is not None:
        parts.append(_pe_part(snap, snap.pe_ratio))
    pb = snap.price_to_book if snap.price_to_book is not None else _price_to_book(snap)
    if pb is not None:
        parts.append(f"P/B {pb:.2f}")
    # A lender's cash flows are its loan book moving, not free cash flow.
    if snap.fcf_yield is not None and not is_bank_snapshot(snap):
        window = f" ({snap.cash_flow_window.value})" if snap.cash_flow_window else ""
        parts.append(f"FCF yield {snap.fcf_yield:.2f}%{window}")
    if snap.dividend_yield is not None:
        parts.append(f"Div yield {snap.dividend_yield:.2f}%")
    return parts


def risk_parts_for(snap: BalanceSheetSnapshot) -> list[str]:
    """Price swings, the deepest recent fall, what trades a day, and the record's loss years."""
    risk = risk_profile(snap)
    parts: list[str] = []
    if risk.volatility_1y_pct is not None:
        parts.append(f"1y volatility {risk.volatility_1y_pct:.0f}%")
    if risk.worst_fall_3y_pct is not None:
        parts.append(f"worst fall in 3y {risk.worst_fall_3y_pct:.0f}%")
    if risk.avg_daily_traded_value is not None:
        traded = format_money(risk.avg_daily_traded_value, snap.market_cap_currency)
        share = f" ({risk.daily_turnover_pct:.2f}% of the company)" if risk.daily_turnover_pct else ""
        parts.append(f"trades {traded} a day{share}")
    if risk.fiscal_years:
        parts.append(f"{risk.loss_years} loss year(s) in {risk.fiscal_years}")
    return parts


def _pe_part(snap: BalanceSheetSnapshot, pe: float) -> str:
    """'P/E 14.9 (core 26.7)' — the reported multiple, and the one the valuation uses."""
    reported = f"P/E {pe:.1f}"
    earnings = sustainable_earnings(snap)
    if earnings.ratio >= 1:
        return reported
    if earnings.ratio <= 0:
        return f"{reported} (no repeatable earnings)"
    return f"{reported} ({earnings.label} {pe / earnings.ratio:.1f})"


def analyst_parts_for(snap: BalanceSheetSnapshot) -> list[str]:
    """Render the yfinance consensus target + recommendation.

    Yahoo's `recommendationMean` is a 1-5 scale:
      1 = Strong Buy, 2 = Buy, 3 = Hold, 4 = Sell, 5 = Strong Sell.
    """
    parts: list[str] = []
    if snap.analyst_target_mean is not None:
        target = f"target {format_price(snap.analyst_target_mean, snap.currency)}"
        if snap.current_price and snap.current_price > 0:
            upside = (
                (snap.analyst_target_mean - snap.current_price)
                / snap.current_price
                * 100
            )
            target += f"  ({upside:+.1f}% upside)"
        parts.append(target)
    if snap.analyst_recommendation_mean is not None:
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
        count_tag = (
            f" ({snap.analyst_opinion_count} analysts)"
            if snap.analyst_opinion_count
            else ""
        )
        parts.append(f"consensus {label} ({rec:.2f}){count_tag}")
    return parts


def _price_to_book(snap: BalanceSheetSnapshot) -> float | None:
    if snap.current_price is None or snap.book_value_per_share is None:
        return None
    if snap.book_value_per_share <= 0:
        return None
    return snap.current_price / snap.book_value_per_share
