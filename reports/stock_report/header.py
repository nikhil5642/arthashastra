"""Header panel — ticker, price, valuation, analyst bundle, data-source tag.

First thing the reader sees. Also the only place where we surface
valuation (P/E, P/B, div yield) and the yfinance analyst consensus
(target price and Strong Buy/Buy/Hold/Sell/Strong Sell label).
"""

from __future__ import annotations

from rich.panel import Panel

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.base import StockScore


def header_panel(snap: BalanceSheetSnapshot, score: StockScore) -> Panel:
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

    valuation_parts = valuation_parts_for(snap)
    if valuation_parts:
        lines.append("Valuation: " + "  ·  ".join(valuation_parts))

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
    parts: list[str] = []
    if snap.pe_ratio is not None:
        parts.append(f"P/E {snap.pe_ratio:.1f}")
    pb = _price_to_book(snap)
    if pb is not None:
        parts.append(f"P/B {pb:.2f}")
    if snap.dividend_yield is not None:
        parts.append(f"Div yield {snap.dividend_yield:.2f}%")
    return parts


def analyst_parts_for(snap: BalanceSheetSnapshot) -> list[str]:
    """Render the yfinance consensus target + recommendation.

    Yahoo's `recommendationMean` is a 1-5 scale:
      1 = Strong Buy, 2 = Buy, 3 = Hold, 4 = Sell, 5 = Strong Sell.
    """
    parts: list[str] = []
    if snap.analyst_target_mean is not None:
        target = f"target ₹{snap.analyst_target_mean:,.0f}"
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
