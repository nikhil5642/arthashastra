"""Data-quality section — statement freshness + source disagreements.

The "how much should you trust these numbers" layer of the report:

  - Latest annual period behind the ratios, with its age, flagged STALE
    once it is older than a normal reporting cycle.
  - Every scored field where yfinance and screener both answered but
    disagreed materially (`snap.data_conflicts`), showing which value the
    merge kept and which it overrode.
"""

from __future__ import annotations

from datetime import date

from rich.panel import Panel

from data_sources.fundamentals_source import BalanceSheetSnapshot

# A fiscal year's audited numbers normally land within ~3 months of
# year-end, so anything older than 12 + 3 months means a missed filing,
# a delisted / suspended company, or a source that stopped updating.
STALE_ANNUAL_MONTHS = 15


def data_quality_rows(snap: BalanceSheetSnapshot) -> list[str]:
    rows: list[str] = []
    period_line = _period_line(snap)
    if period_line:
        rows.append(period_line)
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
    return Panel(
        "\n".join(f"• {row}" for row in rows),
        title="Data quality — freshness and source conflicts",
        title_align="left",
        border_style="yellow" if snap.data_conflicts else "dim",
    )


def _period_line(snap: BalanceSheetSnapshot) -> str:
    if not snap.latest_annual_period:
        return ""
    months = _months_since(snap.latest_annual_period)
    if months is None:
        return f"Latest annual period: {snap.latest_annual_period}"
    stale = "  ⚠ STALE" if months > STALE_ANNUAL_MONTHS else ""
    return (
        f"Latest annual period: {snap.latest_annual_period} "
        f"({months} months old){stale}"
    )


def _months_since(iso_date: str) -> int | None:
    try:
        then = date.fromisoformat(iso_date)
    except ValueError:
        return None
    return max(0, round((date.today() - then).days / 30.44))
