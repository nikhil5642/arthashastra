"""Prospectus statements — the numbers of a company that has not listed yet.

Before its first quarterly report a company's statements exist only in its
prospectus. Claude reads that document in `/analyse-ipo` and transcribes
the annual statements into a `ProspectusStatements` file; this module
turns the file into the same yfinance-shaped frames the SEC adapter builds
and runs them through the same fillers, so a company that has not listed
is scored on the same rubric, by the same formulas, as one that has.

Three things differ from a listed company, and the snapshot says so:

  - **The figures were transcribed, not fetched.** `snap.source` reads
    "prospectus" and the source document is named, so every number can be
    checked against the filing.
  - **There is no market price.** Valuation is only computed when an offer
    price and the share count after the offering are known; the market cap
    is then price × those shares, and every per-share figure is put on that
    share count (the business will be owned by all of them).
  - **There is no price history and no quarterly feed**, so nothing can be
    said about volatility, P/E against the company's own past, or the
    newest quarters. Unknown stays unknown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from data_sources.fundamentals_source import sec_adapter
from data_sources.fundamentals_source.history import AnnualFrames, StatementHistory, snapshot_as_of
from data_sources.fundamentals_source.snapshot import BalanceSheetSnapshot, SnapshotFields

PROSPECTUS_SOURCE = "prospectus"


class StatementRow(StrEnum):
    """The statement lines a prospectus file may carry, one value per fiscal year."""

    REVENUE = "revenue"
    COST_OF_REVENUE = "cost_of_revenue"
    GROSS_PROFIT = "gross_profit"
    OPERATING_INCOME = "operating_income"
    INTEREST_EXPENSE = "interest_expense"
    PRETAX_INCOME = "pretax_income"
    TAX = "tax"
    NET_INCOME = "net_income"
    OPERATING_CASH_FLOW = "operating_cash_flow"
    CAPITAL_SPEND = "capital_spend"  # a positive payment
    DEPRECIATION = "depreciation_and_amortization"
    TOTAL_ASSETS = "total_assets"
    CURRENT_ASSETS = "current_assets"
    CURRENT_LIABILITIES = "current_liabilities"
    EQUITY = "stockholders_equity"
    CASH = "cash"
    INVENTORY = "inventory"
    RECEIVABLES = "receivables"
    PAYABLES = "payables"
    GOODWILL = "goodwill"
    LONG_TERM_DEBT = "long_term_debt"
    CURRENT_DEBT = "current_debt"


# Prospectus line → the yfinance row name the shared fillers read.
_FRAME_ROW: dict[StatementRow, str] = {
    StatementRow.REVENUE: "Total Revenue",
    StatementRow.COST_OF_REVENUE: "Cost Of Revenue",
    StatementRow.GROSS_PROFIT: "Gross Profit",
    StatementRow.OPERATING_INCOME: "Operating Income",
    StatementRow.INTEREST_EXPENSE: "Interest Expense",
    StatementRow.PRETAX_INCOME: "Pretax Income",
    StatementRow.TAX: "Tax Provision",
    StatementRow.NET_INCOME: "Net Income",
    StatementRow.OPERATING_CASH_FLOW: "Operating Cash Flow",
    StatementRow.CAPITAL_SPEND: "Capital Spend",
    StatementRow.DEPRECIATION: "Depreciation And Amortization",
    StatementRow.TOTAL_ASSETS: "Total Assets",
    StatementRow.CURRENT_ASSETS: "Current Assets",
    StatementRow.CURRENT_LIABILITIES: "Current Liabilities",
    StatementRow.EQUITY: "Stockholders Equity",
    StatementRow.CASH: "Cash And Cash Equivalents",
    StatementRow.INVENTORY: "Inventory",
    StatementRow.RECEIVABLES: "Accounts Receivable",
    StatementRow.PAYABLES: "Accounts Payable",
    StatementRow.GOODWILL: "Goodwill",
    StatementRow.LONG_TERM_DEBT: "Long Term Debt",
    StatementRow.CURRENT_DEBT: "Current Debt",
}
_INCOME = ("Total Revenue", "Cost Of Revenue", "Gross Profit", "Operating Income",
           "Interest Expense", "Pretax Income", "Tax Provision", "Net Income")
_BALANCE = ("Total Assets", "Current Assets", "Current Liabilities", "Stockholders Equity",
            "Cash And Cash Equivalents", "Inventory", "Accounts Receivable", "Accounts Payable",
            "Goodwill", "Long Term Debt", "Current Debt")


@dataclass(frozen=True, slots=True)
class FiscalYear:
    """One fiscal year's lines, in the reporting currency's own units (not thousands)."""

    end: date
    values: dict[StatementRow, float]


@dataclass(frozen=True, slots=True)
class Offering:
    """The terms of the sale, once the prospectus states them."""

    price_low: float | None = None
    price_high: float | None = None
    shares_after_offering: float | None = None  # every class, after the new shares are sold

    @property
    def price(self) -> float | None:
        """The middle of the range, or the one price given."""
        known = [p for p in (self.price_low, self.price_high) if p is not None]
        return sum(known) / len(known) if known else None

    def to_dict(self) -> dict[str, object]:
        return {
            "price_low": self.price_low,
            "price_high": self.price_high,
            "shares_after_offering": self.shares_after_offering,
        }


@dataclass(slots=True)
class ProspectusStatements:
    """A prospectus's annual statements, as transcribed by Claude."""

    company: str
    ticker: str  # the symbol the company has reserved, or a slug of its name
    currency: str
    source_url: str  # the prospectus the figures were read from
    transcribed_at: str
    fiscal_years: list[FiscalYear] = field(default_factory=list)
    offering: Offering = field(default_factory=Offering)
    sector: str = ""
    industry: str = ""
    country: str = ""
    business_summary: str = ""
    notes: list[str] = field(default_factory=list)  # e.g. "balance sheet is pro forma"

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ProspectusStatements | None:
        """None for a file without a company, a currency or a single readable fiscal year."""
        company, currency = _text(data.get("company")), _text(data.get("currency")).upper()
        multiplier = _number(data.get("amounts_in")) or 1.0
        raw_years = data.get("fiscal_years")
        years = [
            year
            for raw in (raw_years if isinstance(raw_years, list) else [])
            if (year := _fiscal_year(raw, multiplier)) is not None
        ]
        if not company or not currency or not years:
            return None
        raw_notes = data.get("notes")
        return cls(
            company=company,
            ticker=_text(data.get("ticker")).upper(),
            currency=currency,
            source_url=_text(data.get("source_url")),
            transcribed_at=_text(data.get("transcribed_at")),
            fiscal_years=sorted(years, key=lambda year: year.end, reverse=True),
            offering=_offering(data.get("offering")),
            sector=_text(data.get("sector")),
            industry=_text(data.get("industry")),
            country=_text(data.get("country")),
            business_summary=_text(data.get("business_summary")),
            notes=[n for n in map(_text, raw_notes if isinstance(raw_notes, list) else []) if n],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "company": self.company,
            "ticker": self.ticker,
            "currency": self.currency,
            "amounts_in": 1,
            "source_url": self.source_url,
            "transcribed_at": self.transcribed_at,
            "sector": self.sector,
            "industry": self.industry,
            "country": self.country,
            "business_summary": self.business_summary,
            "notes": list(self.notes),
            "offering": self.offering.to_dict(),
            "fiscal_years": [
                {"fiscal_year_end": year.end.isoformat(),
                 **{row.value: value for row, value in year.values.items()}}
                for year in self.fiscal_years
            ],
        }


def missing_lines(statements: ProspectusStatements) -> list[StatementRow]:
    """The statement lines the newest fiscal year does not carry."""
    newest = statements.fiscal_years[0].values if statements.fiscal_years else {}
    return [row for row in StatementRow if row not in newest]


def snapshot_from_prospectus(
    statements: ProspectusStatements, *, as_of: date, offer_price: float | None = None
) -> BalanceSheetSnapshot | None:
    """Score-ready snapshot of a company that has not listed; None when nothing is usable.

    `offer_price` overrides the prospectus's own range (a final price, or a
    "what if it prices at X"). Without a price and a share count the
    snapshot carries no valuation at all.
    """
    price = offer_price if offer_price is not None else statements.offering.price
    shares = statements.offering.shares_after_offering
    priced = bool(price and price > 0 and shares and shares > 0)

    history = StatementHistory(
        symbol=statements.ticker,
        prices=[(as_of.isoformat(), price)] if priced and price else [],
        frames=_frames(statements, shares if priced else None),
        metadata=SnapshotFields(
            company_name=statements.company,
            sector=statements.sector,
            industry=statements.industry,
            country=statements.country,
            currency=statements.currency,
            financial_currency=statements.currency,
        ),
    )
    snap = snapshot_as_of(history, as_of, reporting_lag_days=0)
    if snap is None:
        return None
    snap.business_summary = statements.business_summary
    snap.source = PROSPECTUS_SOURCE
    if priced and price and shares:
        snap.market_cap = price * shares
    return snap


def _frames(statements: ProspectusStatements, shares: float | None) -> AnnualFrames:
    """The transcribed lines as yfinance-shaped frames, newest fiscal year first."""
    ends = [year.end for year in statements.fiscal_years]
    values: dict[str, dict[date, float]] = {name: {} for name in _FRAME_ROW.values()}
    for year in statements.fiscal_years:
        for row, value in year.values.items():
            values[_FRAME_ROW[row]][year.end] = value
    derived = sec_adapter._derived_rows(values)

    income = {name: values[name] for name in _INCOME} | {"EBITDA": derived["EBITDA"]}
    if shares:
        # Per-share figures on the share count that will own the business after the sale.
        income["Diluted EPS"] = {end: profit / shares for end, profit in values["Net Income"].items()}
    return AnnualFrames(
        income=sec_adapter._frame(income, ends),
        balance=sec_adapter._frame(
            {name: values[name] for name in _BALANCE} | {"Total Debt": derived["Total Debt"]}, ends
        ),
        cashflow=sec_adapter._frame(
            {
                "Operating Cash Flow": values["Operating Cash Flow"],
                "Capital Expenditure": derived["Capital Expenditure"],
                "Free Cash Flow": derived["Free Cash Flow"],
            },
            ends,
        ),
        source=PROSPECTUS_SOURCE,
    )


# ── Narrowing untyped JSON ───────────────────────────────────────────────────


def _fiscal_year(raw: object, multiplier: float) -> FiscalYear | None:
    if not isinstance(raw, dict):
        return None
    try:
        end = date.fromisoformat(_text(raw.get("fiscal_year_end")))
    except ValueError:
        return None
    values = {
        row: number * multiplier
        for row in StatementRow
        if (number := _number(raw.get(row.value))) is not None
    }
    return FiscalYear(end, values) if values else None


def _offering(raw: object) -> Offering:
    if not isinstance(raw, dict):
        return Offering()
    return Offering(
        price_low=_positive(raw.get("price_low")),
        price_high=_positive(raw.get("price_high")),
        shares_after_offering=_positive(raw.get("shares_after_offering")),
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _positive(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
