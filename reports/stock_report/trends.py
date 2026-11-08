"""Trend rows shared between the industrial and bank Financial Profile.

Three distinct trend groups (all rendered into the same 3-column rows
structure the Financial Profile table uses):

  - Multi-year ratio / leverage trends (ROCE, op margin, borrowings)
  - Quarterly shareholding pattern (Promoter / FII / DII / Public deltas)
  - Quarterly revenue + PAT with date-aware QoQ and YoY deltas
"""

from __future__ import annotations

from data_sources.fundamentals_source import BalanceSheetSnapshot, pct_change_by_date
from reports.formatting import format_money


# ── Multi-year ratio trend (ROCE / OPM / Borrowings 3y Δ) ────────────────────


def append_multi_year_trend_rows(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    """Render 'is the business improving or degrading?' in one section."""
    trend_rows: list[tuple[str, str]] = []

    if snap.roce_annual_trend and len(snap.roce_annual_trend) >= 3:
        trend_rows.append(
            (
                "ROCE 5y trend",
                _trend_summary(snap.roce_annual_trend[-5:], unit="%", precision=0),
            )
        )
    if (
        snap.operating_margin_annual_trend
        and len(snap.operating_margin_annual_trend) >= 3
    ):
        trend_rows.append(
            (
                "Operating margin 5y trend",
                _trend_summary(
                    snap.operating_margin_annual_trend[-5:], unit="%", precision=1
                ),
            )
        )
    if snap.borrowings_went_from_zero:
        trend_rows.append(
            ("Borrowings 3y change", "from zero to debt  ·  ↑ debt-expanding")
        )
    elif snap.borrowings_delta_3y_pct is not None:
        trend_rows.append(
            ("Borrowings 3y change", _borrowings_delta_string(snap.borrowings_delta_3y_pct))
        )

    for i, (label, val) in enumerate(trend_rows):
        rows.append(("Multi-year trend" if i == 0 else "", label, val))


def _trend_summary(series: list[float], *, unit: str, precision: int) -> str:
    """Render a short series as 'first → ... → last  ·  direction'."""
    if not series:
        return ""
    if len(series) == 1:
        return f"{series[0]:.{precision}f}{unit}"
    first, last = series[0], series[-1]
    middle = " → ".join(f"{v:.{precision}f}{unit}" for v in series)
    change = last - first
    if change > 0.5:
        arrow = "↑ improving"
    elif change < -0.5:
        arrow = "↓ degrading"
    else:
        arrow = "→ stable"
    return f"{middle}  ·  {arrow}"


def _borrowings_delta_string(delta: float) -> str:
    delta_str = f"{delta:+.1f}% over 3 years"
    if delta > 10:
        arrow = "↑ debt-expanding"
    elif delta < -10:
        arrow = "↓ deleveraging"
    else:
        arrow = "→ stable"
    return f"{delta_str}  ·  {arrow}"


# ── Shareholding trend (Promoter / FII / DII / retail) ───────────────────────


def append_shareholding_trend_rows(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    """Ownership tape — insider stake, institutional flows, retail count.

    Rows appear only for the series a source actually published: screener
    (Indian listings) gives a quarterly promoter / FII / DII / public split;
    elsewhere only the latest insider and institutional percentages exist.
    FII ≤ −2pts YoY + DII ≥ +2pts YoY is the classic "foreign money exiting,
    domestic money accumulating" pattern (HDFCBANK post-merger).
    """
    sh_rows: list[tuple[str, str]] = []

    latest_insider = _latest_pct(snap.insider_holding_trend)
    if latest_insider is not None:
        sh_rows.append(
            (
                "Insider / promoter",
                f"{latest_insider}{_delta_pts_string(snap.insider_delta_1y_pct_points)}",
            )
        )
    elif snap.insider_holding is not None:
        sh_rows.append(("Insider / promoter", f"{snap.insider_holding:.2f}%"))
    if snap.institutional_holding is not None and not snap.fii_holding_trend:
        sh_rows.append(("Institutions", f"{snap.institutional_holding:.2f}%"))

    latest_fii = _latest_pct(snap.fii_holding_trend)
    if latest_fii is not None:
        delta = snap.fii_delta_1y_pct_points
        flag = _flow_flag(delta, bearish_tag="⚠ FII exit", bullish_tag="✓ FII accumulating")
        sh_rows.append(("FII", f"{latest_fii}{_delta_pts_string(delta)}{flag}"))

    latest_dii = _latest_pct(snap.dii_holding_trend)
    if latest_dii is not None:
        delta = snap.dii_delta_1y_pct_points
        flag = _flow_flag(delta, bullish_tag="✓ DII accumulating", bearish_tag="⚠ DII exit")
        sh_rows.append(("DII", f"{latest_dii}{_delta_pts_string(delta)}{flag}"))

    latest_public = _latest_pct(snap.public_holding_trend)
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


def _latest_pct(series: list[float]) -> str | None:
    if not series:
        return None
    return f"{series[0]:.2f}%"


def _delta_pts_string(delta: float | None) -> str:
    if delta is None:
        return ""
    if delta == 0:
        return "  (flat YoY)"
    return f"  ({delta:+.2f}pts YoY)"


def _flow_flag(
    delta: float | None, *, bullish_tag: str, bearish_tag: str
) -> str:
    if delta is None:
        return ""
    if delta >= 2:
        return f"  {bullish_tag}"
    if delta <= -2:
        return f"  {bearish_tag}"
    return ""


# ── Quarterly rows (revenue + PAT with QoQ + YoY deltas) ─────────────────────


def append_quarter_rows(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    _append_quarterly_series(
        rows,
        section_label="Quarterly revenue",
        values=snap.quarterly_revenue,
        dates=snap.quarterly_dates,
        currency=snap.financial_currency,
    )
    _append_quarterly_series(
        rows,
        section_label="Quarterly PAT",
        values=snap.quarterly_pat,
        dates=snap.quarterly_dates,
        currency=snap.financial_currency,
    )


def _append_quarterly_series(
    rows: list[tuple[str, str, str]],
    *,
    section_label: str,
    values: list[float],
    dates: list[str],
    currency: str,
) -> None:
    """Append up to 4 quarter rows with QoQ + YoY annotations.

    `values` and `dates` are most-recent-first (index 0 = latest quarter).
    QoQ and YoY lookups are *date-aware* (`pct_change_by_date`, shared
    with the adapters) so a gap in the series doesn't misalign the
    comparison: each quarter is compared with the entry dated ~90 days
    (QoQ) or ~365 days (YoY) earlier, with an em-dash when there is none.
    """
    if not values or not dates:
        return
    n = min(4, len(values), len(dates))
    for i in range(n):
        section = section_label if i == 0 else ""
        qoq = pct_change_by_date(
            values, dates, current_idx=i, days_ago=90, tolerance=30
        )
        yoy = pct_change_by_date(
            values, dates, current_idx=i, days_ago=365, tolerance=45
        )
        qoq_str = f"{qoq:+.1f}%" if qoq is not None else "—"
        yoy_str = f"{yoy:+.1f}%" if yoy is not None else "—"
        value_str = f"{format_money(values[i], currency)}  ·  QoQ {qoq_str}  ·  YoY {yoy_str}"
        rows.append((section, dates[i], value_str))
