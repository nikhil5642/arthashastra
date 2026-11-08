"""Valuation ratio tests — prices set against statements, one currency at a time."""

from __future__ import annotations

from datetime import date

from data_sources.fundamentals_source.snapshot import BalanceSheetSnapshot
from data_sources.fundamentals_source.valuation import apply_valuation
from data_sources.price_series import PriceSeries

# Microsoft-shaped, scaled: 10 shares, June fiscal years.
EPS = {"2022-06-30": 7.0, "2023-06-30": 8.0, "2024-06-30": 10.0, "2025-06-30": 12.0}
PRICES = PriceSeries.from_rows(
    [("2022-06-30", 210.0), ("2023-06-30", 280.0), ("2024-07-01", 300.0), ("2025-06-30", 360.0),
     ("2025-12-31", 420.0)]
)


def msft(**overrides: object) -> BalanceSheetSnapshot:
    fields: dict[str, object] = {
        "ticker": "MSFT", "as_of_date": "2025-12-31", "currency": "USD", "financial_currency": "USD", "current_price": 420.0,
        "latest_annual_period": "2025-06-30", "eps_history": dict(EPS), "net_income": 120.0,
        "free_cash_flow": 90.0, "shareholders_equity": 300.0,
    }
    fields.update(overrides)
    return BalanceSheetSnapshot(**fields)  # type: ignore[arg-type]


def test_live_ratios_from_price_and_statements():
    snap = msft(pe_ratio=35.0)  # the source's trailing P/E is kept
    apply_valuation(snap, PRICES)
    assert snap.pe_ratio == 35.0
    assert snap.earnings_yield == round(100 / 35, 2)
    # 120 / 12 = 10 shares → FCF 9 a share on a 420 price.
    assert snap.fcf_yield == round(9 / 420 * 100, 2)
    assert snap.price_to_book == round(420 / 30, 2)
    # Each fiscal year-end close over that year's EPS (2024-06-30 → next close, Jul 1).
    assert snap.pe_history == [30.0, 35.0, 30.0, 30.0]


def test_pe_is_derived_when_the_source_has_none():
    snap = msft()
    apply_valuation(snap, PRICES)
    assert snap.pe_ratio == 35.0  # 420 / FY25 EPS 12
    assert snap.earnings_yield == round(100 / 35, 2)


def test_published_book_value_per_share_wins():
    snap = msft(book_value_per_share=42.0)
    apply_valuation(snap, PRICES)
    assert snap.price_to_book == 10.0


def test_backtest_prices_on_the_as_of_date_and_sees_no_later_year_ends():
    snap = msft(latest_annual_period="2024-06-30", eps_history={k: v for k, v in EPS.items() if k < "2025"},
                net_income=100.0, current_price=None)
    apply_valuation(snap, PRICES, as_of=date(2024, 12, 31))
    assert snap.current_price is None  # last close (Jul 1) is months stale
    assert snap.pe_ratio is None and snap.fcf_yield is None

    snap = msft(latest_annual_period="2024-06-30", eps_history={k: v for k, v in EPS.items() if k < "2025"},
                net_income=100.0)
    apply_valuation(snap, PRICES, as_of=date(2025, 7, 2))
    assert snap.current_price == 360.0  # the close known that evening
    assert snap.pe_ratio == 36.0  # 360 / FY24 EPS 10
    assert snap.pe_history == [30.0, 35.0, 30.0]  # FY25's year-end is not yet in eps_history


def test_shares_need_eps_for_the_same_fiscal_year():
    snap = msft(latest_annual_period="2026-06-30")  # no EPS for the newest year yet
    apply_valuation(snap, PRICES)
    assert snap.pe_ratio is None and snap.fcf_yield is None and snap.price_to_book is None


def test_minor_unit_prices_are_converted_before_dividing():
    tesco = msft(ticker="TSCO.L", currency="GBp", financial_currency="GBP", current_price=42000.0)
    apply_valuation(tesco, PriceSeries.from_rows([(d.isoformat(), c * 100) for d, c in zip(PRICES.dates, PRICES.closes)]))
    assert tesco.pe_ratio == 35.0
    assert tesco.pe_history == [30.0, 35.0, 30.0, 30.0]


def test_adr_keeps_the_source_pe_but_mixes_no_currencies():
    alibaba = msft(ticker="BABA", currency="USD", financial_currency="CNY", pe_ratio=26.5)
    apply_valuation(alibaba, PRICES)
    assert alibaba.earnings_yield == round(100 / 26.5, 2)
    assert (alibaba.fcf_yield, alibaba.price_to_book, alibaba.pe_history) == (None, None, [])
    unknown = msft(financial_currency="")
    apply_valuation(unknown, PRICES)
    assert unknown.pe_ratio is None


def test_losses_have_no_pe_and_skip_history():
    snap = msft(eps_history={**EPS, "2025-06-30": -2.0, "2022-06-30": -1.0}, net_income=-20.0)
    apply_valuation(snap, PRICES)
    assert snap.pe_ratio is None and snap.earnings_yield is None
    assert snap.fcf_yield == round(9 / 420 * 100, 2)  # −20 / −2 is still 10 shares
    assert snap.pe_history == [35.0, 30.0]


def test_profit_and_eps_of_opposite_signs_give_no_share_count():
    snap = msft(net_income=50.0, eps_history={**EPS, "2025-06-30": -2.0})
    apply_valuation(snap, PRICES)
    assert snap.fcf_yield is None and snap.price_to_book is None


def test_no_price_means_no_ratios():
    snap = msft(current_price=None)
    apply_valuation(snap, PriceSeries())
    assert (snap.pe_ratio, snap.fcf_yield, snap.pe_history) == (None, None, [])


def test_zero_eps_never_divides():
    snap = msft(eps_history={"2025-06-30": 0.0})
    apply_valuation(snap, PRICES)
    assert snap.pe_ratio is None and snap.fcf_yield is None
