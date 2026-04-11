"""Trend rows shared between the industrial and bank Financial Profile.

Three distinct trend groups (all rendered into the same 3-column rows
structure the Financial Profile table uses):

  - Multi-year ratio / leverage trends (ROCE, op margin, borrowings)
  - Quarterly shareholding pattern (Promoter / FII / DII / Public deltas)
  - Quarterly revenue + PAT with date-aware QoQ and YoY deltas
"""

from __future__ import annotations

from datetime import date as _date

from data_sources.fundamentals_source import BalanceSheetSnapshot


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
    if snap.borrowings_delta_3y_pct is not None:
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
    if delta == float("inf"):
        return "+∞% (went from zero to debt)  ·  ↑ debt-expanding"
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
    """Smart-money / domestic-money tape — the classic re-rating tell.

    FII ≤ −2pts YoY + DII ≥ +2pts YoY is the classic "smart money
    exiting, domestic money accumulating" pattern (HDFCBANK post-merger).
    """
    if not snap.shareholding_dates:
        return

    sh_rows: list[tuple[str, str]] = []

    latest_promoter = _latest_pct(snap.promoter_holding_trend)
    if latest_promoter is not None:
        sh_rows.append(
            (
                "Promoter",
                f"{latest_promoter}{_delta_pts_string(snap.promoter_delta_1y_pct_points)}",
            )
        )

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
    )
    _append_quarterly_series(
        rows,
        section_label="Quarterly PAT",
        values=snap.quarterly_pat,
        dates=snap.quarterly_dates,
    )


def _append_quarterly_series(
    rows: list[tuple[str, str, str]],
    *,
    section_label: str,
    values: list[float],
    dates: list[str],
) -> None:
    """Append up to 4 quarter rows with QoQ + YoY annotations.

    `values` and `dates` are most-recent-first (index 0 = latest quarter).
    QoQ and YoY lookups are *date-aware* so a gap in the series (yfinance
    occasionally drops a middle quarter for recent IPOs) doesn't misalign
    the comparison: for each quarter we look up the entry whose date is
    ~90 days earlier (QoQ) or ~365 days earlier (YoY) and show an em-dash
    when the counterpart is missing.
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

    Returns None when no counterpart falls within ±`tolerance` days of
    the target, when either endpoint is missing, or when the past value
    is zero or negative.
    """
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
