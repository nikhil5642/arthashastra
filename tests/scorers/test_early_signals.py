"""Early-signal tests — every detector on a snapshot built to trip it, and one built not to."""

from __future__ import annotations

from datetime import date

import pytest

from data_sources.fundamentals_source import BalanceSheetSnapshot, StatementWindow
from data_sources.news_source import CategoryDigest, NewsCategory, NewsDigest
from scorers.early_signals import (
    EarlySignal,
    EarlySignalKind,
    Tone,
    detect_early_signals,
    months_since,
    of_tone,
    tally,
)
from signals.signal_bus import Severity

TODAY = date(2026, 11, 10)
QUARTERS = ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]


def snap(**overrides: object) -> BalanceSheetSnapshot:
    fields: dict[str, object] = {
        "ticker": "TEST", "as_of_date": "2026-11-10", "sector": "Technology",
        "latest_annual_period": "2025-12-31",
    }
    fields.update(overrides)
    return BalanceSheetSnapshot(**fields)  # type: ignore[arg-type]


def kinds(snapshot: BalanceSheetSnapshot, news: NewsDigest | None = None) -> dict[EarlySignalKind, EarlySignal]:
    return {s.kind: s for s in detect_early_signals(snapshot, news=news, today=TODAY)}


def only(snapshot: BalanceSheetSnapshot, kind: EarlySignalKind) -> EarlySignal:
    found = kinds(snapshot)
    assert kind in found, f"{kind} did not fire; got {sorted(found)}"
    return found[kind]


def test_a_bare_snapshot_raises_nothing():
    """Unknown is not good — and it is not a concern either."""
    assert detect_early_signals(snap(), today=TODAY) == []


# ── Earnings quality ─────────────────────────────────────────────────────────


def test_profit_padded_by_one_offs():
    padded = snap(pe_ratio=14.9, earnings_window=StatementWindow.TTM, ttm_period_end="2026-09-30",
                  core_earnings_ratio_ttm=0.557)
    signal = only(padded, EarlySignalKind.ONE_OFF_PROFIT)
    assert (signal.tone, signal.severity) == (Tone.CONCERN, Severity.URGENT)
    assert "44% of the profit over the trailing twelve months" in signal.message
    assert "the P/E is 26.8, not 14.9" in signal.message

    mild = only(snap(core_earnings_ratio_fy=0.8), EarlySignalKind.ONE_OFF_PROFIT)
    assert mild.severity is Severity.ALERT
    assert "latest fiscal year" in mild.message and "overstates" in mild.message  # no P/E to restate

    assert EarlySignalKind.ONE_OFF_PROFIT not in kinds(snap(core_earnings_ratio_fy=0.9))
    assert EarlySignalKind.ONE_OFF_PROFIT not in kinds(snap(core_earnings_ratio_fy=1.4))  # charges, not gains


def test_margin_at_a_cyclical_peak():
    peak = snap(pe_ratio=9.6, net_margin=63.8, net_margin_annual_trend=[21.2, 28.2, -37.5, 3.1, 22.8, 63.8])
    signal = only(peak, EarlySignalKind.PEAK_MARGIN)
    assert signal.severity is Severity.ALERT  # 2.9×: under the 3× urgent line
    assert "2.9× its 6-year median (22.0%)" in signal.message
    assert "on mid-cycle earnings the P/E is 18.6, not 9.6" in signal.message

    extreme = snap(net_margin=40.0, net_margin_annual_trend=[8.0, 10.0, 12.0, 40.0])
    assert only(extreme, EarlySignalKind.PEAK_MARGIN).severity is Severity.URGENT

    unproven = snap(net_margin=25.0, net_margin_annual_trend=[-24.0, -11.0, -37.0, 25.0])
    assert "typically lost money" in only(unproven, EarlySignalKind.PEAK_MARGIN).message

    steady = snap(net_margin=30.0, net_margin_annual_trend=[22.0, 24.0, 26.0, 30.0])
    assert EarlySignalKind.PEAK_MARGIN not in kinds(steady)
    # Just over the allowance: the valuation trims 3% of earnings — not worth a flag.
    marginal = snap(net_margin=9.7, net_margin_annual_trend=[5.5, 6.2, 6.4, 9.7])
    assert EarlySignalKind.PEAK_MARGIN not in kinds(marginal)


def test_paper_gains_are_not_mistaken_for_a_cyclical_peak():
    """A 50% reported margin that is 28% on core earnings is one-offs, not a cycle."""
    inflated = snap(net_margin=50.0, core_earnings_ratio_fy=0.557,
                    net_margin_annual_trend=[21.2, 24.0, 28.6, 32.8])
    found = kinds(inflated)
    assert EarlySignalKind.ONE_OFF_PROFIT in found and EarlySignalKind.PEAK_MARGIN not in found


# ── Cash ─────────────────────────────────────────────────────────────────────


def _cash(ttm: float | None, fiscal: float | None, **extra: object) -> BalanceSheetSnapshot:
    return snap(ttm_period_end="2026-09-30", ttm_free_cash_flow=ttm, free_cash_flow=fiscal, **extra)


def test_free_cash_flow_against_the_latest_fiscal_year():
    falling = only(_cash(19.75, 73.27), EarlySignalKind.FREE_CASH_FLOW_FALLING)
    assert falling.severity is Severity.ALERT and "73% below" in falling.message

    turned = only(_cash(-5.0, 20.0), EarlySignalKind.FREE_CASH_FLOW_FALLING)
    assert turned.severity is Severity.URGENT and "turned negative" in turned.message

    burning = only(_cash(-5.0, -8.0), EarlySignalKind.FREE_CASH_FLOW_FALLING)
    assert burning.severity is Severity.ALERT and "stayed negative" in burning.message

    rising = only(_cash(150.0, 100.0), EarlySignalKind.FREE_CASH_FLOW_RISING)
    assert rising.tone is Tone.STRENGTH and "50% above" in rising.message
    assert "turned positive" in only(_cash(5.0, -8.0), EarlySignalKind.FREE_CASH_FLOW_RISING).message

    assert kinds(_cash(90.0, 100.0)) == {}  # within ±40%


def test_free_cash_flow_needs_quarters_newer_than_the_fiscal_year():
    same_window = snap(ttm_period_end="2025-12-31", ttm_free_cash_flow=10.0, free_cash_flow=100.0)
    assert kinds(same_window) == {}  # the trailing year IS the fiscal year: no news
    assert kinds(_cash(None, 100.0)) == {}
    assert kinds(_cash(10.0, 100.0, is_bank=True)) == {}  # a lender's cash flow is its loan book


def test_capex_outrunning_operating_cash_flow():
    heavy = snap(ttm_period_end="2026-09-30", ttm_capex=159.8, ttm_operating_cash_flow=179.55,
                 capex=91.0, operating_cash_flow=165.0)
    signal = only(heavy, EarlySignalKind.CAPEX_OUTRUNS_CASH_FLOW)
    assert "89% of operating cash flow over the trailing twelve months" in signal.message

    fiscal_only = snap(capex=90.0, operating_cash_flow=100.0)
    assert "latest fiscal year" in only(fiscal_only, EarlySignalKind.CAPEX_OUTRUNS_CASH_FLOW).message

    assert kinds(snap(capex=50.0, operating_cash_flow=100.0)) == {}
    # One trailing figure without the other falls back to the fiscal-year pair.
    mixed = snap(ttm_period_end="2026-09-30", ttm_capex=95.0, capex=30.0, operating_cash_flow=100.0)
    assert kinds(mixed) == {}


def test_heavy_stock_based_pay():
    rubrik = snap(ttm_period_end="2026-07-31", latest_annual_period="2026-01-31",
                  ttm_stock_based_compensation=340.0, ttm_revenue=1540.0, ttm_free_cash_flow=290.0)
    signal = only(rubrik, EarlySignalKind.STOCK_PAY_HEAVY)
    assert signal.severity is Severity.URGENT
    assert "22% of revenue" in signal.message and "exceeds free cash flow" in signal.message

    moderate = snap(stock_based_compensation=12.0, revenue=100.0, free_cash_flow=40.0)
    assert only(moderate, EarlySignalKind.STOCK_PAY_HEAVY).severity is Severity.ALERT
    assert kinds(snap(stock_based_compensation=6.0, revenue=100.0)) == {}


# ── Momentum ─────────────────────────────────────────────────────────────────


def test_revenue_momentum():
    shrinking = only(snap(latest_yoy_revenue_growth=-12.0), EarlySignalKind.REVENUE_SHRINKING)
    assert shrinking.severity is Severity.URGENT
    assert only(snap(latest_yoy_revenue_growth=-4.0), EarlySignalKind.REVENUE_SHRINKING).severity is Severity.ALERT

    slowing = only(snap(latest_yoy_revenue_growth=9.0, rev_3y_cagr=27.0), EarlySignalKind.REVENUE_DECELERATING)
    assert "slowed to +9%" in slowing.message and "27% three-year pace" in slowing.message

    faster = only(snap(latest_yoy_revenue_growth=24.65, rev_3y_cagr=12.51), EarlySignalKind.REVENUE_ACCELERATING)
    assert faster.tone is Tone.STRENGTH

    assert kinds(snap(latest_yoy_revenue_growth=11.0, rev_3y_cagr=12.0)) == {}
    # A slow grower slowing a little more is the growth veto's business, not a deceleration.
    assert kinds(snap(latest_yoy_revenue_growth=0.0, rev_3y_cagr=6.0)) == {}
    # A lender's revenue is interest income — the bank rubric leaves it out, and so does this.
    assert kinds(snap(latest_yoy_revenue_growth=9.0, rev_3y_cagr=27.0, is_bank=True)) == {}


def test_operating_margin_shift_is_matched_by_date():
    compressing = snap(quarterly_dates=QUARTERS, quarterly_operating_margin=[20.0, 22.0, 23.0, 25.0, 26.5])
    signal = only(compressing, EarlySignalKind.MARGIN_COMPRESSING)
    assert "fell to 20.0% in the latest quarter from 26.5% a year earlier" in signal.message
    assert signal.severity is Severity.ALERT

    collapsing = snap(quarterly_dates=QUARTERS, quarterly_operating_margin=[10.0, 0, 0, 0, 26.5])
    assert only(collapsing, EarlySignalKind.MARGIN_COMPRESSING).severity is Severity.URGENT

    expanding = snap(quarterly_dates=QUARTERS, quarterly_operating_margin=[80.7, 80.4, 67.6, 45.0, 32.6])
    assert only(expanding, EarlySignalKind.MARGIN_EXPANDING).tone is Tone.STRENGTH

    # No year-ago quarter, or the year-ago margin unknown → nothing.
    assert kinds(snap(quarterly_dates=QUARTERS[:4], quarterly_operating_margin=[20.0, 22.0, 23.0, 30.0])) == {}
    assert kinds(snap(quarterly_dates=QUARTERS, quarterly_operating_margin=[20.0, 22.0, 23.0, 25.0, None])) == {}


def test_profit_against_sales():
    falling = only(snap(latest_yoy_pat_growth=-24.3, latest_yoy_revenue_growth=24.7),
                   EarlySignalKind.PROFIT_FALLING_AS_SALES_GROW)
    assert "fell 24% year on year" in falling.message and "revenue grew 25%" in falling.message

    def jump(margins: list[float | None] | None, **extra: object) -> BalanceSheetSnapshot:
        quarters = {"quarterly_dates": QUARTERS, "quarterly_operating_margin": margins} if margins else {}
        return snap(latest_yoy_pat_growth=40.0, latest_yoy_revenue_growth=18.0, **quarters, **extra)

    widening = [31.0, 30.0, 29.0, 28.0, 27.0]  # newest first: 31% now, 27% a year earlier
    leverage = only(jump(widening), EarlySignalKind.PROFIT_OUTPACING_SALES)
    assert (leverage.tone, leverage.severity) == (Tone.STRENGTH, Severity.INFO)

    # A profit jump beside a margin that halved is last year's one-off charge, not leverage.
    halved = jump([20.1, 30.0, 35.0, 38.0, 40.1])
    assert EarlySignalKind.PROFIT_OUTPACING_SALES not in kinds(halved)
    assert EarlySignalKind.MARGIN_COMPRESSING in kinds(halved)
    # Without the operating margin there is nothing to confirm it with: no strength.
    assert EarlySignalKind.PROFIT_OUTPACING_SALES not in kinds(jump(None))
    # A profit jump made of one-offs is not operating leverage.
    assert EarlySignalKind.PROFIT_OUTPACING_SALES not in kinds(jump(widening, core_earnings_ratio_fy=0.6))
    assert kinds(snap(latest_yoy_pat_growth=-24.0, latest_yoy_revenue_growth=-3.0)).keys() == {
        EarlySignalKind.REVENUE_SHRINKING
    }


# ── Balance sheet ────────────────────────────────────────────────────────────


def test_debt_shift_needs_a_material_level():
    rising = only(snap(debt_change_1y_pct=321.0, latest_debt_to_equity=0.215, debt_to_equity_1y_ago=0.087),
                  EarlySignalKind.DEBT_RISING)
    assert rising.severity is Severity.ALERT
    assert "rose 321% in a year (debt / equity 0.09 → 0.21)" in rising.message

    heavy = snap(debt_change_1y_pct=120.0, latest_debt_to_equity=0.9, debt_to_equity_1y_ago=0.4)
    assert only(heavy, EarlySignalKind.DEBT_RISING).severity is Severity.URGENT

    falling = only(snap(debt_change_1y_pct=-64.5, latest_debt_to_equity=0.037, debt_to_equity_1y_ago=0.269),
                   EarlySignalKind.DEBT_FALLING)
    assert falling.tone is Tone.STRENGTH

    # Tripling a rounding error is not a debt problem.
    assert kinds(snap(debt_change_1y_pct=200.0, latest_debt_to_equity=0.03, debt_to_equity_1y_ago=0.01)) == {}
    assert kinds(snap(debt_change_1y_pct=321.0, latest_debt_to_equity=0.9, debt_to_equity_1y_ago=0.4,
                      is_bank=True)) == {}


def test_negative_equity():
    assert only(snap(shareholders_equity=-520.0), EarlySignalKind.NEGATIVE_EQUITY).severity is Severity.ALERT
    losing = snap(shareholders_equity=-520.0, net_income=-300.0)
    signal = only(losing, EarlySignalKind.NEGATIVE_EQUITY)
    assert signal.severity is Severity.URGENT and "loss-making" in signal.message
    assert kinds(snap(shareholders_equity=100.0)) == {}


def _working_capital(**overrides: object) -> BalanceSheetSnapshot:
    fields: dict[str, object] = {
        "balance_sheet_date": "2026-09-30", "quarterly_dates": QUARTERS,
        "latest_yoy_revenue_growth": 10.0, "debtor_days": 60.0, "inventory_days": 45.0,
    }
    fields.update(overrides)
    return snap(**fields)


def test_receivables_and_inventory_outpacing_sales():
    receivables = only(_working_capital(receivables_change_1y_pct=45.0),
                       EarlySignalKind.RECEIVABLES_OUTPACING_SALES)
    assert "grew 45% in a year against +10% revenue growth" in receivables.message
    inventory = only(_working_capital(inventory_change_1y_pct=58.0), EarlySignalKind.INVENTORY_OUTPACING_SALES)
    assert "building faster than it sells" in inventory.message

    # Growing with sales is not a signal.
    assert kinds(_working_capital(receivables_change_1y_pct=25.0, inventory_change_1y_pct=22.0)) == {}
    # It is the days of sales that count, not the gap in points: 127% against
    # 106% sales growth is days +10%; 25% against 5% is days +19%.
    hyper_growth = _working_capital(receivables_change_1y_pct=127.0, latest_yoy_revenue_growth=106.0)
    assert EarlySignalKind.RECEIVABLES_OUTPACING_SALES not in kinds(hyper_growth)
    slow_growth = _working_capital(receivables_change_1y_pct=25.0, latest_yoy_revenue_growth=5.0)
    assert EarlySignalKind.RECEIVABLES_OUTPACING_SALES in kinds(slow_growth)
    # A software company's stock room: too small for its growth to mean anything.
    assert kinds(_working_capital(inventory_change_1y_pct=58.0, inventory_days=0.7)) == {}
    assert kinds(_working_capital(receivables_change_1y_pct=45.0, debtor_days=None)) == {}
    # The balance sheet must be the latest quarter's, or the two growth rates describe different spans.
    assert kinds(_working_capital(receivables_change_1y_pct=45.0, balance_sheet_date="2026-03-31")) == {}


def test_goodwill_jump():
    signal = only(snap(goodwill_change_1y_pct=101.2, latest_goodwill_to_equity=0.101), EarlySignalKind.GOODWILL_JUMP)
    assert signal.severity is Severity.INFO and "to 10% of equity" in signal.message
    big = snap(goodwill_change_1y_pct=80.0, latest_goodwill_to_equity=0.45)
    assert only(big, EarlySignalKind.GOODWILL_JUMP).severity is Severity.ALERT
    assert kinds(snap(goodwill_change_1y_pct=300.0, latest_goodwill_to_equity=0.01)) == {}


# ── Ownership ────────────────────────────────────────────────────────────────


def test_share_count_shift():
    assert only(snap(share_count_change_1y_pct=5.0), EarlySignalKind.SHARE_DILUTION).severity is Severity.ALERT
    assert only(snap(share_count_change_1y_pct=14.0), EarlySignalKind.SHARE_DILUTION).severity is Severity.URGENT
    buyback = only(snap(share_count_change_1y_pct=-9.6), EarlySignalKind.SHARE_BUYBACK)
    assert buyback.tone is Tone.STRENGTH and "fell 9.6%" in buyback.message
    assert kinds(snap(share_count_change_1y_pct=1.8)) == {}


def test_insider_and_foreign_institution_flows():
    assert only(snap(insider_delta_1y_pct_points=-1.2), EarlySignalKind.INSIDER_SELLING).severity is Severity.ALERT
    assert only(snap(insider_delta_1y_pct_points=-4.0), EarlySignalKind.INSIDER_SELLING).severity is Severity.URGENT
    assert only(snap(insider_delta_1y_pct_points=2.0), EarlySignalKind.INSIDER_BUYING).tone is Tone.STRENGTH
    assert kinds(snap(insider_delta_1y_pct_points=0.4)) == {}

    assert only(snap(fii_delta_1y_pct_points=-2.2), EarlySignalKind.FOREIGN_INSTITUTIONS_SELLING).severity is Severity.INFO
    assert only(snap(fii_delta_1y_pct_points=-8.99), EarlySignalKind.FOREIGN_INSTITUTIONS_SELLING).severity is Severity.ALERT
    assert only(snap(fii_delta_1y_pct_points=3.0), EarlySignalKind.FOREIGN_INSTITUTIONS_BUYING).tone is Tone.STRENGTH


def test_retail_crowding_in_while_institutions_leave():
    crowding = snap(num_shareholders_delta_1y_pct=30.0, fii_delta_1y_pct_points=-8.99)
    assert "up 30% in a year" in only(crowding, EarlySignalKind.RETAIL_CROWDING).message
    assert EarlySignalKind.RETAIL_CROWDING not in kinds(snap(num_shareholders_delta_1y_pct=30.0, fii_delta_1y_pct_points=1.0))
    assert EarlySignalKind.RETAIL_CROWDING not in kinds(snap(num_shareholders_delta_1y_pct=30.0))


# ── Lenders ──────────────────────────────────────────────────────────────────


def test_asset_quality_shift():
    worse = only(snap(is_bank=True, gross_npa_pct=3.4, gross_npa_delta_1y_pct_points=0.6),
                 EarlySignalKind.ASSET_QUALITY_WORSENING)
    assert worse.severity is Severity.ALERT and "to 3.40% of advances" in worse.message
    assert only(snap(gross_npa_pct=5.0, gross_npa_delta_1y_pct_points=1.4),
                EarlySignalKind.ASSET_QUALITY_WORSENING).severity is Severity.URGENT
    assert only(snap(gross_npa_pct=1.1, gross_npa_delta_1y_pct_points=-0.7),
                EarlySignalKind.ASSET_QUALITY_IMPROVING).tone is Tone.STRENGTH
    assert kinds(snap(gross_npa_pct=1.11, gross_npa_delta_1y_pct_points=-0.13)) == {}


# ── News radar ───────────────────────────────────────────────────────────────


def _news(stories: int, **categories: tuple[int, int]) -> NewsDigest:
    return NewsDigest(
        window_days=7,
        stories=stories,
        categories={
            NewsCategory(name): CategoryDigest(stories=count, widest_headline=f"{name} headline", widest_coverage=coverage)
            for name, (count, coverage) in categories.items()
        },
    )


def test_a_governance_burst_needs_a_real_share_of_the_weeks_news():
    burst = kinds(snap(), _news(99, governance=(48, 1)))[EarlySignalKind.GOVERNANCE_NEWS]
    assert burst.severity is Severity.URGENT
    assert "48 of the last 7 days' 99 news stories are governance-tagged" in burst.message
    assert "“governance headline”" in burst.message

    small_company = kinds(snap(), _news(6, governance=(3, 1)))[EarlySignalKind.GOVERNANCE_NEWS]
    assert small_company.severity is Severity.ALERT

    # A megacap always has a few "antitrust" / "probe" headlines among 76 stories.
    assert kinds(snap(), _news(76, governance=(3, 1))) == {}
    # …unless one of them is carried everywhere.
    assert EarlySignalKind.GOVERNANCE_NEWS in kinds(snap(), _news(76, governance=(1, 6)))
    assert kinds(snap(), None) == {}  # the feed wasn't read


def test_the_governance_radar_works_for_a_company_few_outlets_cover():
    """Story counts scale with fame; the share of the week's stories does not."""
    # Two of a small company's three stories: the same event as 48 of a megacap's 99.
    half = kinds(snap(), _news(3, governance=(2, 1)))[EarlySignalKind.GOVERNANCE_NEWS]
    assert half.severity is Severity.ALERT
    assert "2 of the last 7 days' 3 news stories are governance-tagged" in half.message
    # Most of the little that is written — and enough stories not to be one stray headline.
    assert kinds(snap(), _news(5, governance=(4, 1)))[EarlySignalKind.GOVERNANCE_NEWS].severity is Severity.URGENT
    assert kinds(snap(), _news(5, governance=(3, 1)))[EarlySignalKind.GOVERNANCE_NEWS].severity is Severity.ALERT
    # One story is one story, however thin the coverage; and two in ten is not a takeover.
    assert kinds(snap(), _news(1, governance=(1, 1))) == {}
    assert kinds(snap(), _news(10, governance=(2, 1))) == {}


def test_credit_rating_news_is_flagged_for_reading():
    signal = kinds(snap(), _news(20, rating=(1, 1)))[EarlySignalKind.CREDIT_RATING_NEWS]
    assert signal.severity is Severity.ALERT and "“rating headline”" in signal.message


# ── Tape and data ────────────────────────────────────────────────────────────


def test_price_drawdown():
    assert only(snap(current_price=690.0, high_52w=1255.0), EarlySignalKind.PRICE_DRAWDOWN).severity is Severity.INFO
    assert only(snap(current_price=40.0, high_52w=100.0), EarlySignalKind.PRICE_DRAWDOWN).severity is Severity.ALERT
    assert kinds(snap(current_price=90.0, high_52w=100.0)) == {}


def test_stale_statements_and_receipts():
    stale = only(snap(latest_annual_period="2025-03-31"), EarlySignalKind.STALE_STATEMENTS)
    assert "19 months old" in stale.message
    assert kinds(snap(latest_annual_period="2025-09-30")) == {}  # 13 months: within a reporting cycle

    unreliable = only(snap(quote_unreliable=True), EarlySignalKind.UNRELIABLE_QUOTE)
    assert unreliable.severity is Severity.URGENT and "don't reconcile" in unreliable.message


def test_a_listing_under_a_year_old_is_named():
    recent = only(snap(price_history_years=0.42), EarlySignalKind.RECENTLY_LISTED)
    assert (recent.tone, recent.severity) == (Tone.CONCERN, Severity.ALERT)
    assert "Only 5 months of trading on record for this listing" in recent.message
    assert "no P/E history" in recent.message and "lock-up" in recent.message
    # A long-listed company whose symbol the price source barely covers: no claim of a new listing.
    gap = only(snap(price_history_years=0.0), EarlySignalKind.RECENTLY_LISTED).message
    assert gap.startswith("Under a month of trading on record") and "another symbol" in gap
    assert "Listed about" not in gap
    assert kinds(snap(price_history_years=1.0)) == {}
    assert kinds(snap(price_history_years=None)) == {}  # unknown is not a signal


# ── Strengths paid for out of profit that may not repeat ─────────────────────


def test_a_strength_paid_for_out_of_peak_profit_is_context_not_material():
    """A refiner paying down debt at the top of its cycle: the debt fact stays, as INFO."""
    deleveraging = {"debt_change_1y_pct": -64.5, "latest_debt_to_equity": 0.037, "debt_to_equity_1y_ago": 0.269}
    at_peak = {"net_margin": 40.0, "net_margin_annual_trend": [8.0, 10.0, 12.0, 40.0]}

    normal = only(snap(**deleveraging), EarlySignalKind.DEBT_FALLING)
    assert normal.severity is Severity.ALERT and "may not repeat" not in normal.message

    found = kinds(snap(**deleveraging, **at_peak))
    assert EarlySignalKind.PEAK_MARGIN in found
    discounted = found[EarlySignalKind.DEBT_FALLING]
    assert (discounted.tone, discounted.severity) == (Tone.STRENGTH, Severity.INFO)
    assert discounted.message.startswith("Total debt fell 64% in a year")
    assert discounted.message.endswith("it is paid for out of profit that may not repeat.")


def test_one_offs_discount_the_same_strengths_but_not_the_ones_profit_does_not_pay_for():
    padded = snap(
        core_earnings_ratio_fy=0.5,  # half the profit is unusual items
        ttm_period_end="2026-09-30", ttm_free_cash_flow=150.0, free_cash_flow=100.0,  # FCF +50%
        share_count_change_1y_pct=-5.0,  # a buyback is a fact about the share count
        latest_yoy_revenue_growth=30.0, rev_3y_cagr=12.0,  # and demand is a fact about customers
    )
    found = kinds(padded)
    assert found[EarlySignalKind.ONE_OFF_PROFIT].severity is Severity.URGENT
    assert found[EarlySignalKind.FREE_CASH_FLOW_RISING].severity is Severity.INFO
    assert found[EarlySignalKind.SHARE_BUYBACK].severity is Severity.ALERT
    assert found[EarlySignalKind.REVENUE_ACCELERATING].severity is Severity.ALERT
    assert tally(list(found.values())) == "1 concern (1 urgent) · 3 strengths"


# ── Ordering, tallies, persistence ───────────────────────────────────────────


def test_concerns_come_first_and_the_most_severe_leads():
    mixed = snap(
        share_count_change_1y_pct=-5.0,  # strength
        goodwill_change_1y_pct=101.0, latest_goodwill_to_equity=0.1,  # concern, INFO
        debt_change_1y_pct=321.0, latest_debt_to_equity=0.215, debt_to_equity_1y_ago=0.087,  # concern, ALERT
        core_earnings_ratio_fy=0.5,  # concern, URGENT
    )
    signals = detect_early_signals(mixed, today=TODAY)
    assert [(s.tone, s.severity) for s in signals] == [
        (Tone.CONCERN, Severity.URGENT), (Tone.CONCERN, Severity.ALERT),
        (Tone.CONCERN, Severity.INFO), (Tone.STRENGTH, Severity.ALERT),
    ]
    assert tally(signals) == "3 concerns (1 urgent) · 1 strength"
    assert tally(of_tone(signals, Tone.STRENGTH)) == "1 strength"
    assert tally([]) == ""


def test_signal_round_trips_through_json_and_rejects_rows_that_do_not_fit():
    signal = EarlySignal(EarlySignalKind.DEBT_RISING, Tone.CONCERN, Severity.ALERT, "Total debt rose 321%.")
    assert EarlySignal.from_dict(signal.to_dict()) == signal
    assert EarlySignal.from_dict({**signal.to_dict(), "kind": "a_kind_from_the_future"}) is None
    assert EarlySignal.from_dict({**signal.to_dict(), "message": ""}) is None
    assert EarlySignal.from_dict({}) is None


@pytest.mark.parametrize(
    ("iso", "expected"), [("2025-12-31", 10), ("2026-11-10", 0), ("not a date", None), ("", None)]
)
def test_months_since(iso, expected):
    assert months_since(iso, today=TODAY) == expected


def test_a_profit_priced_at_a_few_times_earnings_is_a_question_not_a_bargain():
    from data_sources.fundamentals_source import BalanceSheetSnapshot
    from scorers.early_signals import EarlySignalKind, detect_early_signals

    def kinds(pe: float | None) -> set[EarlySignalKind]:
        snap = BalanceSheetSnapshot(ticker="CHEAP", as_of_date="2026-11-13", pe_ratio=pe)
        return {signal.kind for signal in detect_early_signals(snap)}

    assert EarlySignalKind.PRICED_FOR_DOUBT in kinds(2.6)
    assert EarlySignalKind.PRICED_FOR_DOUBT not in kinds(12.0)
    assert EarlySignalKind.PRICED_FOR_DOUBT not in kinds(None)  # unknown says nothing
    assert EarlySignalKind.PRICED_FOR_DOUBT not in kinds(-4.0)  # a loss is flagged elsewhere


def test_a_profit_after_mostly_loss_years_is_flagged_as_unproven():
    from data_sources.fundamentals_source import BalanceSheetSnapshot
    from scorers.early_signals import EarlySignalKind, detect_early_signals

    def flagged(margins: list[float], profit: float | None = 1e9) -> bool:
        snap = BalanceSheetSnapshot(ticker="TURN", as_of_date="2026-11-13", net_income=profit,
                                    net_margin_annual_trend=margins)
        return EarlySignalKind.UNPROVEN_PROFIT in {s.kind for s in detect_early_signals(snap)}

    assert flagged([-30.0, -12.0, -4.0, 2.0, 16.0])  # three of five years in loss
    assert not flagged([8.0, 9.0, -2.0, 11.0, 12.0])  # one bad year is not a pattern
    assert not flagged([-30.0, -12.0, 16.0])  # too short a record to call
    assert not flagged([-30.0, -12.0, -4.0, 2.0, -6.0], profit=-1e8)  # still losing: said elsewhere
    assert not flagged([-30.0, -12.0, -4.0, 2.0, 16.0], profit=None)  # unknown says nothing



def test_the_doubt_alert_uses_the_caps_own_test_not_the_raw_pe():
    """4× reported, but 60% of it one-off: 10× repeatable — no cap, so no alert claiming one."""
    from data_sources.fundamentals_source import BalanceSheetSnapshot
    from scorers.early_signals import EarlySignalKind, detect_early_signals

    padded = BalanceSheetSnapshot(ticker="PAD", as_of_date="2026-11-13", pe_ratio=4.0,
                                  earnings_yield=25.0, core_earnings_ratio_fy=0.4)
    assert EarlySignalKind.PRICED_FOR_DOUBT not in {s.kind for s in detect_early_signals(padded)}
