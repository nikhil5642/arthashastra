"""Financial Profile panel — industrial + bank variants.

Dispatches based on `snap.is_bank`:

  - Industrial: margin stack, cost mix, working-capital days, asset mix,
    multi-year trend, shareholding trend, quarterly rows.
  - Bank / NBFC: spread & fees (NIM, NII, non-interest income),
    asset quality (gross / net NPA with YoY change), profitability
    (ROE/ROA), balance sheet (deposits / borrowings / CD ratio),
    multi-year trend, shareholding trend, quarterly rows, and a
    "Missing" note for metrics the sources don't expose.

Both variants share the trend + quarterly rows defined in `trends.py`
so that downstream rendering is consistent.
"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table

from data_sources.fundamentals_source import BalanceSheetSnapshot
from reports.formatting import format_money
from reports.stock_report.trends import (
    append_multi_year_trend_rows,
    append_quarter_rows,
    append_shareholding_trend_rows,
)


def financial_profile_panel(snap: BalanceSheetSnapshot) -> Panel | None:
    """Return the Rich panel, or None if we have nothing to show."""
    rows = _profile_rows(snap)
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


def profile_rows(snap: BalanceSheetSnapshot) -> list[tuple[str, str, str]]:
    """Public: used by the markdown writer to bullet the same sections."""
    return _profile_rows(snap)


# ── Dispatch ─────────────────────────────────────────────────────────────────


def _profile_rows(snap: BalanceSheetSnapshot) -> list[tuple[str, str, str]]:
    if snap.is_bank:
        return _bank_rows(snap)
    return _industrial_rows(snap)


# ── Industrial variant ───────────────────────────────────────────────────────


def _industrial_rows(snap: BalanceSheetSnapshot) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []

    _append_margin_stack(rows, snap)
    _append_cost_structure(rows, snap)
    _append_working_capital(rows, snap)
    _append_asset_composition(rows, snap)
    append_multi_year_trend_rows(rows, snap)
    append_shareholding_trend_rows(rows, snap)
    append_quarter_rows(rows, snap)
    return rows


def _append_margin_stack(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    inner: list[tuple[str, str]] = []
    if snap.gross_margin is not None:
        inner.append(("Gross margin", f"{snap.gross_margin:.1f}%"))
    if snap.operating_margin is not None:
        inner.append(("Operating margin", f"{snap.operating_margin:.1f}%"))
    if snap.net_margin is not None:
        inner.append(("Net margin", f"{snap.net_margin:.1f}%"))
    for i, (label, val) in enumerate(inner):
        rows.append(("Margins" if i == 0 else "", label, val))


def _append_cost_structure(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    inner: list[tuple[str, str]] = []
    if snap.cogs_to_revenue is not None:
        inner.append(("COGS / revenue", f"{snap.cogs_to_revenue:.1f}%"))
    if snap.sga_to_revenue is not None:
        inner.append(("SG&A / revenue", f"{snap.sga_to_revenue:.1f}%"))
    if snap.tax_rate is not None:
        inner.append(("Effective tax rate", f"{snap.tax_rate:.1f}%"))
    for i, (label, val) in enumerate(inner):
        rows.append(("Cost mix" if i == 0 else "", label, val))


def _append_working_capital(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    inner: list[tuple[str, str]] = []
    if snap.debtor_days is not None:
        inner.append(("Debtor days", f"{snap.debtor_days:.0f}"))
    if snap.inventory_days is not None:
        inner.append(("Inventory days", f"{snap.inventory_days:.0f}"))
    if snap.payable_days is not None:
        inner.append(("Payable days", f"{snap.payable_days:.0f}"))
    if snap.cash_conversion_cycle_days is not None:
        inner.append(
            ("Cash conversion cycle", f"{snap.cash_conversion_cycle_days:.0f} days")
        )
    for i, (label, val) in enumerate(inner):
        rows.append(("Working capital" if i == 0 else "", label, val))


def _append_asset_composition(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    inner: list[tuple[str, str]] = []
    if snap.tangible_book_ratio is not None:
        inner.append(("Tangible book / equity", f"{snap.tangible_book_ratio:.2f}"))
    if snap.goodwill_to_equity is not None:
        inner.append(("Goodwill / equity", f"{snap.goodwill_to_equity:.2f}"))
    for i, (label, val) in enumerate(inner):
        rows.append(("Asset mix" if i == 0 else "", label, val))


# ── Bank variant ─────────────────────────────────────────────────────────────


def _bank_rows(snap: BalanceSheetSnapshot) -> list[tuple[str, str, str]]:
    """Bank-specific profile: NIM, NII split, CD ratio, deposit/borrow mix."""
    rows: list[tuple[str, str, str]] = []

    _append_spread_and_fees(rows, snap)
    _append_asset_quality(rows, snap)
    _append_bank_profitability(rows, snap)
    _append_bank_balance_sheet(rows, snap)
    append_multi_year_trend_rows(rows, snap)
    append_shareholding_trend_rows(rows, snap)
    append_quarter_rows(rows, snap)

    if snap.gross_npa_pct is None and snap.net_npa_pct is None:
        rows.append(
            ("Missing", "GNPA / NNPA", "no NPA data for this lender — verify manually")
        )
    rows.append(
        ("Missing", "CASA / CRAR", "not exposed by the data sources — verify manually")
    )
    return rows


def _append_asset_quality(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    inner: list[tuple[str, str]] = []
    if snap.gross_npa_pct is not None:
        inner.append(
            (
                "Gross NPA",
                f"{snap.gross_npa_pct:.2f}%{_yoy_pts(snap.gross_npa_delta_1y_pct_points)}",
            )
        )
    if snap.net_npa_pct is not None:
        inner.append(
            (
                "Net NPA",
                f"{snap.net_npa_pct:.2f}%{_yoy_pts(snap.net_npa_delta_1y_pct_points)}",
            )
        )
    for i, (label, val) in enumerate(inner):
        rows.append(("Asset quality" if i == 0 else "", label, val))


def _yoy_pts(delta: float | None) -> str:
    return "" if delta is None else f"  ({delta:+.2f}pts YoY)"


def _append_spread_and_fees(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    inner: list[tuple[str, str]] = []
    if snap.nim is not None:
        inner.append(("Net interest margin (NIM)", f"{snap.nim:.2f}%"))
    if snap.net_interest_income is not None:
        inner.append(
            (
                "Net interest income",
                format_money(snap.net_interest_income, snap.financial_currency),
            )
        )
    if snap.non_interest_income is not None:
        inner.append(
            (
                "Non-interest income (fee/treasury)",
                format_money(snap.non_interest_income, snap.financial_currency),
            )
        )
    if snap.non_interest_income_ratio is not None:
        inner.append(
            ("Non-interest income ratio", f"{snap.non_interest_income_ratio:.1f}%")
        )
    for i, (label, val) in enumerate(inner):
        rows.append(("Spread & fees" if i == 0 else "", label, val))


def _append_bank_profitability(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    inner: list[tuple[str, str]] = []
    if snap.roe is not None:
        inner.append(("ROE", f"{snap.roe:.1f}%"))
    if snap.roa is not None:
        inner.append(("ROA", f"{snap.roa:.2f}%"))
    if snap.tax_rate is not None:
        inner.append(("Effective tax rate", f"{snap.tax_rate:.1f}%"))
    for i, (label, val) in enumerate(inner):
        rows.append(("Profitability" if i == 0 else "", label, val))


def _append_bank_balance_sheet(
    rows: list[tuple[str, str, str]], snap: BalanceSheetSnapshot
) -> None:
    inner: list[tuple[str, str]] = []
    if snap.deposits is not None:
        inner.append(("Deposits", format_money(snap.deposits, snap.financial_currency)))
    if snap.borrowings is not None:
        inner.append(
            (
                "Borrowings (ex-deposits)",
                format_money(snap.borrowings, snap.financial_currency),
            )
        )
    if snap.credit_deposit_ratio is not None:
        label = "Credit-Deposit ratio"
        val = f"{snap.credit_deposit_ratio:.1f}%"
        if snap.credit_deposit_ratio > 100:
            val += "  (high — lending > funding)"
        inner.append((label, val))
    for i, (label, val) in enumerate(inner):
        rows.append(("Balance sheet" if i == 0 else "", label, val))
