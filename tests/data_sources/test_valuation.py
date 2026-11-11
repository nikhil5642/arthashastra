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


# ── Which twelve months each ratio covers ────────────────────────────────────


def test_source_pe_is_trailing_and_a_derived_one_is_the_fiscal_year():
    from data_sources.fundamentals_source.snapshot import StatementWindow

    published = msft(pe_ratio=35.0)
    apply_valuation(published, PRICES)
    assert published.earnings_window is StatementWindow.TTM

    derived = msft()
    apply_valuation(derived, PRICES)
    assert derived.earnings_window is StatementWindow.FISCAL_YEAR


def test_fcf_yield_uses_the_trailing_twelve_months_when_the_quarters_are_in():
    from data_sources.fundamentals_source.snapshot import StatementWindow

    # FCF fell from 90 (fiscal year) to 30 (trailing): today's price buys today's cash.
    snap = msft(ttm_period_end="2025-09-30", ttm_free_cash_flow=30.0)
    apply_valuation(snap, PRICES)
    assert snap.fcf_yield == round(30 / (10 * 420) * 100, 2)
    assert snap.cash_flow_window is StatementWindow.TTM

    # A trailing window older than the fiscal year adds nothing — the year wins.
    stale = msft(ttm_period_end="2025-03-31", ttm_free_cash_flow=30.0)
    apply_valuation(stale, PRICES)
    assert stale.fcf_yield == round(9 / 420 * 100, 2)
    assert stale.cash_flow_window is StatementWindow.FISCAL_YEAR


def test_core_earnings_ratio_follows_the_pe_window():
    snap = msft(pe_ratio=35.0, ttm_period_end="2025-09-30",
                core_earnings_ratio_ttm=0.56, core_earnings_ratio_fy=0.85)
    apply_valuation(snap, PRICES)
    assert snap.core_earnings_ratio == 0.56  # a trailing P/E → the trailing ratio

    fiscal = msft(ttm_period_end="2025-09-30", core_earnings_ratio_ttm=0.56, core_earnings_ratio_fy=0.85)
    apply_valuation(fiscal, PRICES)
    assert fiscal.core_earnings_ratio == 0.85  # a fiscal-year P/E → the fiscal-year ratio

    no_quarters = msft(pe_ratio=35.0, core_earnings_ratio_fy=0.85)
    apply_valuation(no_quarters, PRICES)
    assert no_quarters.core_earnings_ratio == 0.85  # the freshest ratio there is


def test_market_cap_stands_in_when_the_share_count_cannot_be_derived():
    snap = msft(latest_annual_period="2026-06-30", market_cap=4200.0)  # no EPS for the newest year
    apply_valuation(snap, PRICES)
    assert snap.fcf_yield == round(90 / 4200 * 100, 2)
    assert snap.price_to_book == round(4200 / 300, 2)


# ── Listings that trade in one currency and report in another ────────────────


def tencent(**overrides: object) -> BalanceSheetSnapshot:
    """HKD price, CNY statements: market cap HK$4,000 for 220 of profit."""
    return msft(ticker="0700.HK", currency="HKD", financial_currency="CNY", pe_ratio=16.5,
                market_cap=4000.0, **overrides)


def test_spot_fx_gives_a_cross_currency_listing_its_fcf_yield_and_book_multiple():
    snap = tencent()
    apply_valuation(snap, PRICES, fx_to_reporting=0.9)  # HK$1 = CN¥0.90
    assert snap.fcf_yield == round(90 / (4000 * 0.9) * 100, 2)
    assert snap.price_to_book == round(4000 * 0.9 / 300, 2)
    assert snap.pe_history == []  # each past year-end would need its own FX rate
    assert not snap.quote_unreliable


def test_published_book_value_is_ignored_across_currencies():
    snap = tencent(book_value_per_share=42.0)  # CNY per share against an HKD price
    apply_valuation(snap, PRICES, fx_to_reporting=0.9)
    assert snap.price_to_book == round(4000 * 0.9 / 300, 2)


def test_a_cross_currency_quote_below_one_and_a_half_times_earnings_is_a_receipt():
    """A CEDEAR: Yahoo's P/E of 0.01 divides one instrument's price by another's earnings."""
    cedear = msft(ticker="GOOGL.BA", currency="ARS", financial_currency="USD", pe_ratio=0.01,
                  market_cap=3.5e12)
    apply_valuation(cedear, PRICES, fx_to_reporting=0.0007)
    assert cedear.quote_unreliable
    assert (cedear.pe_ratio, cedear.earnings_yield, cedear.fcf_yield, cedear.price_to_book) == (
        None, None, None, None
    )
    assert cedear.earnings_window is None and cedear.pe_history == []


def test_a_receipt_is_also_caught_by_its_fx_converted_market_cap():
    # P/E looks plausible, but the "market cap" is the receipt programme's:
    # 150 of value against 120 of annual profit is 1.25× earnings.
    receipt = msft(ticker="TSLA.BA", currency="ARS", financial_currency="USD", pe_ratio=9.0,
                   market_cap=150_000.0)
    apply_valuation(receipt, PRICES, fx_to_reporting=0.001)
    assert receipt.quote_unreliable and receipt.pe_ratio is None


def test_a_mislabelled_statement_currency_is_caught_by_the_same_reconciliation():
    """Yahoo tags some Hong Kong listings USD while the figures are in yuan (7× too big)."""
    newborn = msft(ticker="9911.HK", currency="HKD", financial_currency="USD", pe_ratio=10.0,
                   market_cap=12_000.0, net_income=900.0)  # 900 "USD" is really CNY
    apply_valuation(newborn, PRICES, fx_to_reporting=0.128)  # → 1,536 of value: 1.7× "earnings"
    assert newborn.quote_unreliable and newborn.fcf_yield is None and newborn.pe_ratio is None


def test_an_unverifiable_cross_currency_quote_keeps_its_pe_but_mixes_nothing():
    # No published P/E (or a loss): the FX basis can't be proven either way.
    loss_maker = msft(ticker="BABA", currency="USD", financial_currency="CNY", market_cap=4000.0,
                      net_income=-50.0, eps_history={"2025-06-30": -5.0})
    apply_valuation(loss_maker, PRICES, fx_to_reporting=7.1)
    assert not loss_maker.quote_unreliable
    assert (loss_maker.fcf_yield, loss_maker.price_to_book) == (None, None)


def test_a_tiny_same_currency_pe_is_a_real_number_not_a_receipt():
    # A one-off gain can make a real company's trailing P/E tiny; the
    # earnings-quality layer handles that, not the receipt rule.
    snap = msft(pe_ratio=1.2)
    apply_valuation(snap, PRICES)
    assert not snap.quote_unreliable and snap.pe_ratio == 1.2
