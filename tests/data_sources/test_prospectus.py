"""Prospectus-statement tests — a transcribed file built inline, scored like any listed company."""

from __future__ import annotations

from datetime import date

from data_sources.fundamentals_source.prospectus import (
    PROSPECTUS_SOURCE,
    Offering,
    ProspectusStatements,
    StatementRow,
    snapshot_from_prospectus,
)
from scorers.valuation_scorer import score_valuation

AS_OF = date(2026, 11, 12)


def _year(end: str, revenue: float, profit: float) -> dict[str, object]:
    """One fiscal year in millions, every line a prospectus normally gives."""
    return {
        "fiscal_year_end": end,
        "revenue": revenue, "cost_of_revenue": revenue * 0.4, "gross_profit": revenue * 0.6,
        "operating_income": profit * 1.3, "interest_expense": 5, "pretax_income": profit * 1.25,
        "tax": profit * 0.25, "net_income": profit, "operating_cash_flow": profit * 1.2,
        "capital_spend": 40, "depreciation_and_amortization": 30, "total_assets": 2000,
        "current_assets": 900, "current_liabilities": 400, "stockholders_equity": 1200,
        "cash": 500, "long_term_debt": 200, "current_debt": 20,
    }


def _file(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "company": "Fictional Widgets, Inc.",
        "ticker": "fwi",
        "currency": "usd",
        "amounts_in": 1_000_000,
        "source_url": "https://example.test/fwi-s1.htm",
        "transcribed_at": "2026-11-12T10:00:00",
        "sector": "Industrials",
        "notes": ["Balance sheet is pro forma for the preferred conversion.", "", 3],
        "offering": {"price_low": 18, "price_high": 22, "shares_after_offering": 100_000_000},
        "fiscal_years": [
            _year("2023-12-31", 600, 70), _year("2025-12-31", 1000, 150), _year("2024-12-31", 800, 110),
        ],
    }
    data.update(overrides)
    return data


def _statements(**overrides: object) -> ProspectusStatements:
    statements = ProspectusStatements.from_dict(_file(**overrides))
    assert statements is not None
    return statements


def test_the_file_is_read_newest_year_first_in_full_units():
    statements = _statements()
    assert (statements.company, statements.ticker, statements.currency) == (
        "Fictional Widgets, Inc.", "FWI", "USD")
    assert [year.end for year in statements.fiscal_years] == [
        date(2025, 12, 31), date(2024, 12, 31), date(2023, 12, 31)]
    newest = statements.fiscal_years[0].values
    assert newest[StatementRow.REVENUE] == 1_000_000_000  # amounts_in applied
    assert newest[StatementRow.CAPITAL_SPEND] == 40_000_000
    assert statements.notes == ["Balance sheet is pro forma for the preferred conversion."]
    assert statements.offering.price == 20.0


def test_round_trip_keeps_every_figure_in_units():
    statements = _statements()
    again = ProspectusStatements.from_dict(statements.to_dict())
    assert again is not None
    assert again.to_dict() == statements.to_dict()
    assert statements.to_dict()["amounts_in"] == 1


def test_an_unreadable_file_is_none():
    assert ProspectusStatements.from_dict({}) is None
    assert ProspectusStatements.from_dict(_file(company="")) is None
    assert ProspectusStatements.from_dict(_file(currency=None)) is None
    assert ProspectusStatements.from_dict(_file(fiscal_years="lots")) is None
    bad_years = [{"fiscal_year_end": "FY25", "revenue": 1}, {"fiscal_year_end": "2025-12-31"}, "x",
                 {"fiscal_year_end": "2024-12-31", "revenue": "1,000", "net_income": float("nan")}]
    assert ProspectusStatements.from_dict(_file(fiscal_years=bad_years)) is None


def test_offering_terms_narrow_to_positive_numbers():
    assert Offering().price is None
    assert Offering(price_high=25.0).price == 25.0
    statements = _statements(offering={"price_low": -1, "price_high": True, "shares_after_offering": "many"})
    assert statements.offering == Offering()
    assert _statements(offering="tbd").offering == Offering()


def test_a_priced_offering_is_valued_on_the_shares_after_it():
    snap = snapshot_from_prospectus(_statements(), as_of=AS_OF)
    assert snap is not None
    assert snap.source == PROSPECTUS_SOURCE and snap.ticker == "FWI"
    assert snap.market_cap == 20.0 * 100_000_000
    assert snap.current_price == 20.0
    assert snap.pe_ratio is not None and round(snap.pe_ratio, 2) == 13.33  # 2B / 150M
    # Three fiscal years are two years of growth: a 3-year CAGR needs a fourth.
    assert (snap.rev_3y_cagr, snap.growth_years) == (29.1, 2)  # three years on file: a 2-year rate
    four = _statements(fiscal_years=[*_file()["fiscal_years"], _year("2022-12-31", 400, 40)])  # type: ignore[misc]
    assert snapshot_from_prospectus(four, as_of=AS_OF).rev_3y_cagr is not None  # type: ignore[union-attr]
    assert snap.price_history_years is None and snap.volatility_1y_pct is None  # unknown, not calm


def test_without_terms_there_is_no_valuation():
    snap = snapshot_from_prospectus(_statements(offering={}), as_of=AS_OF)
    assert snap is not None
    assert snap.market_cap is None and snap.pe_ratio is None
    assert score_valuation(snap).confidence == 0.0

    # A price with no share count is no market value either.
    no_shares = snapshot_from_prospectus(
        _statements(offering={"price_low": 18, "price_high": 22}), as_of=AS_OF
    )
    assert no_shares is not None and no_shares.market_cap is None


def test_a_higher_price_lowers_the_valuation_score():
    statements = _statements()
    scores = []
    for price in (10.0, 20.0, 40.0, 80.0):
        snap = snapshot_from_prospectus(statements, as_of=AS_OF, offer_price=price)
        assert snap is not None and snap.market_cap == price * 100_000_000
        scores.append(score_valuation(snap).score)
    assert scores == sorted(scores, reverse=True) and scores[0] > scores[-1]

    # --price works even when the prospectus has no range yet.
    unpriced = _statements(offering={"shares_after_offering": 100_000_000})
    assert snapshot_from_prospectus(unpriced, as_of=AS_OF).market_cap is None  # type: ignore[union-attr]
    assert snapshot_from_prospectus(unpriced, as_of=AS_OF, offer_price=30.0).market_cap == 3e9  # type: ignore[union-attr]


def test_a_line_left_out_stays_unknown():
    lines = {"revenue": 1000, "net_income": 150, "total_assets": 2000, "stockholders_equity": 1200}
    years = [{"fiscal_year_end": "2025-12-31", **lines}, {"fiscal_year_end": "2024-12-31", **lines}]
    snap = snapshot_from_prospectus(_statements(fiscal_years=years), as_of=AS_OF)
    assert snap is not None and snap.roe is not None
    assert snap.debt_to_equity is None and snap.current_ratio is None and snap.ocf_to_pat is None
    assert snap.interest_coverage is None


def test_lines_that_score_nothing_make_no_snapshot():
    years = [{"fiscal_year_end": "2025-12-31", "revenue": 1000, "net_income": 150}]
    assert snapshot_from_prospectus(_statements(fiscal_years=years), as_of=AS_OF) is None
