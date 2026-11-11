"""Data-quality section — statement freshness + source disagreements.

The "how much should you trust these numbers" layer of the report:

  - Latest annual period behind the ratios, with its age, flagged STALE
    once it is older than a normal reporting cycle.
  - The trailing-twelve-month window, when the source publishes quarters —
    or a plain statement that it doesn't, so fiscal-year cash flow and
    earnings are the freshest there is.
  - A quote whose price and statements don't reconcile — a depositary
    receipt or a mislabelled statement currency (no price-based ratio is
    shown for it).
  - Every scored field where yfinance and screener both answered but
    disagreed materially (`snap.data_conflicts`), showing which value the
    merge kept and which it overrode.
"""

from __future__ import annotations

from rich.panel import Panel

from data_sources.fundamentals_source import BalanceSheetSnapshot
from scorers.early_signals import STALE_ANNUAL_MONTHS, months_since


def data_quality_rows(snap: BalanceSheetSnapshot) -> list[str]:
    rows: list[str] = []
    period_line = _period_line(snap)
    if period_line:
        rows.append(period_line)
    rows.append(_trailing_line(snap))
    if snap.quote_unreliable:
        rows.append(
            "⚠ Price and statements don't reconcile (a depositary receipt, or a mislabelled "
            "statement currency) — valuation is left unscored. Check the primary listing."
        )
    for c in snap.data_conflicts:
        rows.append(
            f"Source conflict — {c.field_name.replace('_', ' ')}: kept "
            f"{c.chosen_source} {c.chosen_value:,.2f}, overrode "
            f"{c.other_source} {c.other_value:,.2f} ({c.rel_diff_pct:.0f}% apart)"
        )
    return rows


def data_quality_summary(snap: BalanceSheetSnapshot) -> str:
    """One-line version for the analysis-context banner."""
    n = len(snap.data_conflicts)
    conflicts = f"{n} source conflict{'s' if n != 1 else ''}"
    period = _period_line(snap) or "latest annual period unknown"
    return f"{conflicts}  ·  {period[0].lower()}{period[1:]}"


def data_quality_panel(snap: BalanceSheetSnapshot) -> Panel | None:
    rows = data_quality_rows(snap)
    if not rows:
        return None
    attention = bool(snap.data_conflicts) or snap.quote_unreliable
    return Panel(
        "\n".join(f"• {row}" for row in rows),
        title="Data quality — freshness and source conflicts",
        title_align="left",
        border_style="yellow" if attention else "dim",
    )


def _period_line(snap: BalanceSheetSnapshot) -> str:
    if not snap.latest_annual_period:
        return ""
    months = months_since(snap.latest_annual_period)
    if months is None:
        return f"Latest annual period: {snap.latest_annual_period}"
    stale = "  ⚠ STALE" if months > STALE_ANNUAL_MONTHS else ""
    return (
        f"Latest annual period: {snap.latest_annual_period} "
        f"({months} months old){stale}"
    )


def _trailing_line(snap: BalanceSheetSnapshot) -> str:
    if snap.ttm_is_current:
        return (
            f"Trailing twelve months: to {snap.ttm_period_end} — "
            f"valuation and early signals use them where published"
        )
    return (
        "Trailing twelve months: not available (fewer than four consecutive quarters "
        "published) — fiscal-year figures are the freshest there are"
    )
