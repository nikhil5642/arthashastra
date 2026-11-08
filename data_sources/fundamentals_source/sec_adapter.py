"""SEC EDGAR adapter — deep, as-filed annual history for US filers.

`data.sec.gov` publishes every XBRL fact a company has filed (since 2009,
with comparatives back to ~2007), each tagged with its filing date. That
makes it the free source for honest US backtests:

  - Only annual 10-K facts are used: flows spanning ~a year, balances at
    the fiscal-year end. (10-Ks also carry quarterly breakdowns in their
    notes; the duration filter drops those.)
  - For every fiscal year the FIRST-filed value is kept, so a later
    restatement can't leak into a backtest of the past.
  - Concepts are merged in priority order per year, because filers switch
    tags over time (Revenues → RevenueFromContractWithCustomer… in 2018).

The facts are reshaped into yfinance-style annual frames — rows named
"Total Revenue", "Net Income", "Total Debt", … and one column per
fiscal-year end, newest first — so the existing yfinance fillers compute
every ratio. One set of formulas for every source.

Coverage is US-registered filers reporting in US GAAP; anything with an
exchange suffix is skipped without a request. SEC asks automated clients
to identify themselves: the caller passes `SEC_USER_AGENT` from config.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from pandas import DataFrame

log = logging.getLogger(__name__)

SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
HTTP_TIMEOUT_SECONDS = 30.0

# A fiscal-year flow spans ~365 days; quarterly notes inside 10-Ks don't.
_ANNUAL_DURATION_DAYS = range(330, 400)
_ANNUAL_FORMS = frozenset({"10-K", "10-K405", "10-KT"})

# yfinance row name → us-gaap concepts, in priority order.
_INCOME_ROWS: dict[str, tuple[str, ...]] = {
    "Total Revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
    "Cost Of Revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
    "Gross Profit": ("GrossProfit",),
    "Selling General And Administration": ("SellingGeneralAndAdministrativeExpense",),
    "Operating Income": ("OperatingIncomeLoss",),
    "Interest Expense": ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"),
    "Pretax Income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "Tax Provision": ("IncomeTaxExpenseBenefit",),
    "Net Income": ("NetIncomeLoss",),
}
_CASHFLOW_ROWS: dict[str, tuple[str, ...]] = {
    "Operating Cash Flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "Capital Spend": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "Depreciation And Amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ),
}
_BALANCE_ROWS: dict[str, tuple[str, ...]] = {
    "Total Assets": ("Assets",),
    "Current Assets": ("AssetsCurrent",),
    "Current Liabilities": ("LiabilitiesCurrent",),
    "Stockholders Equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "Cash And Cash Equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "Inventory": ("InventoryNet",),
    "Accounts Receivable": ("AccountsReceivableNetCurrent",),
    "Accounts Payable": ("AccountsPayableCurrent",),
    "Goodwill": ("Goodwill",),
    "Long Term Debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "Current Debt": ("LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"),
}

# Per-share rows live under the "USD/shares" unit, not "USD".
_PER_SHARE_ROWS: dict[str, tuple[str, ...]] = {
    "Diluted EPS": ("EarningsPerShareDiluted", "EarningsPerShareBasic"),
}

# fiscal-year end → (value, first filing date)
Series = dict[date, tuple[float, date]]


@dataclass(slots=True)
class SecAnnualFrames:
    """yfinance-shaped annual statements built from as-filed SEC facts."""

    income: "DataFrame"
    balance: "DataFrame"
    cashflow: "DataFrame"
    # Fiscal-year end → the date its EPS was first filed. That EPS is on the
    # share count of its filing day, before any later stock split.
    eps_filed: dict[date, date] = field(default_factory=dict)


# ── Public entry points ──────────────────────────────────────────────────────

_cik_by_ticker: dict[str, int] = {}


def covers(symbol: str) -> bool:
    """US listings only — Yahoo symbols with an exchange suffix are foreign."""
    return "." not in symbol.strip()


async def fetch_annual_frames(symbol: str, *, user_agent: str) -> SecAnnualFrames | None:
    """As-filed annual statements for a US filer; None when SEC has none."""
    if not covers(symbol):
        return None
    cik = await _lookup_cik(symbol.strip().upper(), user_agent)
    if cik is None:
        return None
    facts = await _get_json(SEC_COMPANY_FACTS_URL.format(cik=cik), user_agent)
    if not isinstance(facts, dict):
        return None
    return build_annual_frames(facts)


def build_annual_frames(company_facts: dict[str, object]) -> SecAnnualFrames | None:
    """Pure: SEC companyfacts JSON → yfinance-shaped annual frames."""
    gaap = _us_gaap(company_facts)
    if not gaap:
        return None

    income = {row: _flow_series(gaap, concepts) for row, concepts in _INCOME_ROWS.items()}
    per_share = {
        row: _flow_series(gaap, concepts, unit="USD/shares")
        for row, concepts in _PER_SHARE_ROWS.items()
    }
    cashflow = {row: _flow_series(gaap, concepts) for row, concepts in _CASHFLOW_ROWS.items()}
    fiscal_ends = sorted(set(income["Net Income"]) | set(income["Total Revenue"]), reverse=True)
    if not fiscal_ends:
        return None
    balance = {
        row: _instant_series(gaap, concepts, set(fiscal_ends))
        for row, concepts in _BALANCE_ROWS.items()
    }

    values = {
        row: _values(series)
        for row, series in {**income, **per_share, **cashflow, **balance}.items()
    }
    derived = _derived_rows(values)

    return SecAnnualFrames(
        income=_frame(
            {
                **{row: values[row] for row in (*_INCOME_ROWS, *_PER_SHARE_ROWS)},
                "EBITDA": derived["EBITDA"],
            },
            fiscal_ends,
        ),
        balance=_frame(
            {**{row: values[row] for row in _BALANCE_ROWS}, "Total Debt": derived["Total Debt"]},
            fiscal_ends,
        ),
        cashflow=_frame(
            {
                "Operating Cash Flow": values["Operating Cash Flow"],
                "Capital Expenditure": derived["Capital Expenditure"],
                "Free Cash Flow": derived["Free Cash Flow"],
            },
            fiscal_ends,
        ),
        eps_filed={end: filed for end, (_, filed) in per_share["Diluted EPS"].items()},
    )


# ── Fact selection ───────────────────────────────────────────────────────────


def _us_gaap(company_facts: dict[str, object]) -> dict[str, object]:
    facts = company_facts.get("facts")
    if not isinstance(facts, dict):
        return {}
    gaap = facts.get("us-gaap")
    return gaap if isinstance(gaap, dict) else {}


def _usd_facts(
    gaap: dict[str, object], concept: str, *, unit: str = "USD"
) -> list[dict[str, object]]:
    node = gaap.get(concept)
    if not isinstance(node, dict):
        return []
    units = node.get("units")
    if not isinstance(units, dict):
        return []
    facts = units.get(unit)
    return [f for f in facts if isinstance(f, dict)] if isinstance(facts, list) else []


def _flow_series(
    gaap: dict[str, object], concepts: tuple[str, ...], *, unit: str = "USD"
) -> Series:
    """Fiscal-year flows (income / cash-flow lines), merged across concepts."""
    return _merge_by_priority(
        _first_filed(
            _annual_fact(f, instant=False) for f in _usd_facts(gaap, concept, unit=unit)
        )
        for concept in concepts
    )


def _instant_series(
    gaap: dict[str, object], concepts: tuple[str, ...], fiscal_ends: set[date]
) -> Series:
    """Balances at each fiscal-year end, merged across concepts."""
    merged = _merge_by_priority(
        _first_filed(_annual_fact(f, instant=True) for f in _usd_facts(gaap, concept))
        for concept in concepts
    )
    return {end: entry for end, entry in merged.items() if end in fiscal_ends}


def _first_filed(facts: Iterable[tuple[date, float, date] | None]) -> Series:
    """One concept's facts → the earliest-filed value for each period end."""
    series: Series = {}
    for parsed in facts:
        if parsed is None:
            continue
        end, value, filed = parsed
        if end not in series or filed < series[end][1]:
            series[end] = (value, filed)
    return series


def _merge_by_priority(per_concept: Iterable[Series]) -> Series:
    """Higher-priority concepts win; lower ones only fill missing years."""
    merged: Series = {}
    for series in per_concept:
        for end, entry in series.items():
            merged.setdefault(end, entry)
    return merged


def _annual_fact(fact: dict[str, object], *, instant: bool) -> tuple[date, float, date] | None:
    """(period end, value, filed) for a 10-K fact of the right shape, else None."""
    if fact.get("form") not in _ANNUAL_FORMS:
        return None
    end = _as_date(fact.get("end"))
    filed = _as_date(fact.get("filed"))
    value = fact.get("val")
    if end is None or filed is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    start = _as_date(fact.get("start"))
    if instant:
        if start is not None:
            return None
    elif start is None or (end - start).days not in _ANNUAL_DURATION_DAYS:
        return None
    return end, float(value), filed


# ── Frame assembly ───────────────────────────────────────────────────────────


def _values(series: Series) -> dict[date, float]:
    return {end: value for end, (value, _) in series.items()}


def _derived_rows(values: dict[str, dict[date, float]]) -> dict[str, dict[date, float]]:
    """Rows yfinance publishes that SEC doesn't, built the same way."""
    debt: dict[date, float] = {}
    for end in set(values["Long Term Debt"]) | set(values["Current Debt"]):
        debt[end] = values["Long Term Debt"].get(end, 0.0) + values["Current Debt"].get(end, 0.0)

    ebitda = {
        end: operating + values["Depreciation And Amortization"][end]
        for end, operating in values["Operating Income"].items()
        if end in values["Depreciation And Amortization"]
    }
    # yfinance reports capex as a negative cash flow; SEC as a positive payment.
    capex = {end: -spend for end, spend in values["Capital Spend"].items()}
    free_cash_flow = {
        end: ocf - values["Capital Spend"][end]
        for end, ocf in values["Operating Cash Flow"].items()
        if end in values["Capital Spend"]
    }
    return {
        "Total Debt": debt,
        "EBITDA": ebitda,
        "Capital Expenditure": capex,
        "Free Cash Flow": free_cash_flow,
    }


def _frame(rows: dict[str, dict[date, float]], fiscal_ends: list[date]) -> "DataFrame":
    """Rows × fiscal-year columns (newest first), NaN where a year is missing."""
    import pandas as pd

    nan = float("nan")
    return pd.DataFrame(
        {pd.Timestamp(end): [rows[row].get(end, nan) for row in rows] for end in fiscal_ends},
        index=list(rows),
    )


def _as_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# ── Transport (tests monkeypatch `_get_json`) ────────────────────────────────


async def _lookup_cik(ticker: str, user_agent: str) -> int | None:
    if not _cik_by_ticker:
        mapping = await _get_json(SEC_TICKER_MAP_URL, user_agent)
        if isinstance(mapping, dict):
            for row in mapping.values():
                if isinstance(row, dict) and isinstance(row.get("ticker"), str):
                    cik = row.get("cik_str")
                    if isinstance(cik, int):
                        _cik_by_ticker[row["ticker"].upper()] = cik
    return _cik_by_ticker.get(ticker)


async def _get_json(url: str, user_agent: str) -> object | None:
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:
        log.warning("SEC request failed (%s): %s", url, e)
        return None
    if resp.status_code != 200:
        log.warning("SEC returned %s for %s", resp.status_code, url)
        return None
    try:
        return resp.json()
    except ValueError:
        return None
