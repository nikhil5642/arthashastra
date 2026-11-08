"""Stock report render test — inline inputs, Markdown output."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from data_sources.fundamentals_source import BalanceSheetSnapshot
from reports.stock_report import render_stock_report
from scorers.composite_scorer import blend, pending_judgment
from scorers.fundamentals_scorer import score_fundamentals
from scorers.valuation_scorer import score_valuation


def _quiet() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=120)


def _strong_snap() -> BalanceSheetSnapshot:
    return BalanceSheetSnapshot(
        ticker="0700.HK",
        as_of_date="2026-11-08",
        debt_to_equity=0.35,
        interest_coverage=18.5,
        roe=21.0,
        roce=17.8,
        rev_3y_cagr=10.7,
        pat_3y_cagr=6.1,
        ocf_to_pat=1.4,
        insider_holding=31.7,
        current_price=419.8,
        high_52w=654.38,
        low_52w=375.2,
        market_cap=3.78e12,
        exchange="HKSE",
        country="China",
        currency="HKD",
        financial_currency="CNY",
        quarterly_revenue=[204.78e9, 184.5e9],
        quarterly_dates=["2026-06-30", "2025-06-30"],
        company_name="Tencent Holdings Limited",
        sector="Communication Services",
        industry="Internet Content & Information",
        source="yfinance",
    )


def test_render_stock_report_writes_markdown(tmp_path: Path):
    snap = _strong_snap()
    fundamentals = score_fundamentals(snap)
    score = blend(
        ticker=snap.ticker,
        leadership=pending_judgment("leadership"),
        fundamentals=fundamentals,
        valuation=score_valuation(snap),
        prospects=pending_judgment("prospects"),
        news=pending_judgment("news"),
    )
    fp = render_stock_report(snap, score, output_dir=tmp_path, console=_quiet())
    assert fp is not None
    assert fp.exists()
    content = fp.read_text()
    assert "0700.HK" in content
    assert "Tencent Holdings Limited" in content
    assert "Current price: HK$419.80" in content  # trading currency
    assert "Market cap: HK$3.78T" in content
    assert "CN¥204.78B" in content  # statements in the reporting currency
    assert "Listing: HKSE · China" in content
    assert "Verdict" in content
    assert "Fundamentals" in content
    assert "Leadership" in content
    assert "D/E" in content
    assert "ROE" in content
    assert "pending" in content.lower()  # Claude judgment pending markers


def test_render_sparse_snap_marks_low_confidence(tmp_path: Path):
    snap = BalanceSheetSnapshot(
        ticker="SPARSE",
        as_of_date="2026-04-11",
        debt_to_equity=0.5,
        roe=15.0,
        current_price=50.0,
        company_name="Sparse Data Co",
        source="yfinance",
    )
    fundamentals = score_fundamentals(snap)
    score = blend(
        ticker="SPARSE",
        leadership=pending_judgment("leadership"),
        fundamentals=fundamentals,
        valuation=score_valuation(snap),
        prospects=pending_judgment("prospects"),
        news=pending_judgment("news"),
    )
    fp = render_stock_report(snap, score, output_dir=tmp_path, console=_quiet())
    assert fp is not None
    content = fp.read_text()
    assert "SPARSE" in content
    assert "LOW CONFIDENCE" in content or score.low_confidence


def test_report_shows_data_quality_and_bank_asset_quality(tmp_path: Path):
    from data_sources.fundamentals_source import DataConflict

    snap = BalanceSheetSnapshot(
        ticker="BANKX",
        as_of_date="2026-11-08",
        is_bank=True,
        roe=15.0,
        roa=1.5,
        gross_npa_pct=1.4,
        net_npa_pct=0.4,
        gross_npa_delta_1y_pct_points=-0.2,
        latest_annual_period="2026-03-31",
        data_conflicts=[
            DataConflict("roe", "screener", 15.0, "yfinance", 21.0, 28.6)
        ],
        company_name="Bank X",
    )
    score = blend(
        ticker="BANKX",
        leadership=pending_judgment("leadership"),
        fundamentals=score_fundamentals(snap),
        valuation=score_valuation(snap),
        prospects=pending_judgment("prospects"),
        news=pending_judgment("news"),
    )
    fp = render_stock_report(snap, score, output_dir=tmp_path, console=_quiet())
    assert fp is not None
    content = fp.read_text()
    assert "## Data quality" in content
    assert "Latest annual period: 2026-03-31" in content
    assert "Source conflict — roe" in content
    assert "Gross NPA: 1.40%" in content
    assert "CASA / CRAR" in content


def _rich_industrial_snap() -> BalanceSheetSnapshot:
    """Toyota-style profile exercising every industrial section."""
    return BalanceSheetSnapshot(
        ticker="7203.T", as_of_date="2026-11-08", company_name="Toyota Motor Corporation",
        exchange="Tokyo", country="Japan", currency="JPY", financial_currency="JPY",
        current_price=2850.0, high_52w=3400.0, low_52w=2400.0, market_cap=4.4e13,
        pe_ratio=8.4, book_value_per_share=2600.0, dividend_yield=3.1,
        analyst_target_mean=3300.0, analyst_recommendation_mean=2.2, analyst_opinion_count=21,
        gross_margin=20.1, operating_margin=10.4, net_margin=9.6,
        cogs_to_revenue=79.9, sga_to_revenue=8.2, tax_rate=27.0,
        debtor_days=28, inventory_days=38, payable_days=45, cash_conversion_cycle_days=21,
        tangible_book_ratio=0.95, goodwill_to_equity=0.01,
        roce_annual_trend=[6.0, 7.0, 9.0, 10.0], operating_margin_annual_trend=[8.0, 8.5, 11.0, 10.4],
        borrowings_delta_3y_pct=18.0,
        shareholding_dates=["2026-09-30"], insider_holding_trend=[11.0], insider_delta_1y_pct_points=-1.2,
        fii_holding_trend=[22.0], fii_delta_1y_pct_points=-2.5, dii_holding_trend=[30.0],
        dii_delta_1y_pct_points=2.4, public_holding_trend=[37.0],
        num_shareholders_trend=[900000.0], num_shareholders_delta_1y_pct=12.0,
        quarterly_revenue=[12.4e12, 11.9e12, 11.8e12], quarterly_pat=[1.1e12, 1.0e12, 1.2e12],
        quarterly_dates=["2026-09-30", "2026-06-30", "2025-09-30"],
        debt_to_equity=1.1, roe=11.0, rev_3y_cagr=10.9, pat_3y_cagr=16.2,
    )


def test_industrial_report_renders_every_section(tmp_path: Path):
    snap = _rich_industrial_snap()
    score = blend(
        ticker=snap.ticker,
        leadership=pending_judgment("leadership"),
        fundamentals=score_fundamentals(snap),
        valuation=score_valuation(snap),
        prospects=pending_judgment("prospects"),
        news=pending_judgment("news"),
    )
    fp = render_stock_report(snap, score, output_dir=tmp_path, console=_quiet())
    content = fp.read_text()
    for expected in (
        "Current price: ¥2,850.00", "Market cap: ¥44.00T", "P/E 8.4", "P/B 1.10",
        "Gross margin: 20.1%", "SG&A / revenue: 8.2%", "Cash conversion cycle: 21 days",
        "Goodwill / equity: 0.01", "ROCE 5y trend: 6% → 7% → 9% → 10%  ·  ↑ improving",
        "Borrowings 3y change: +18.0% over 3 years  ·  ↑ debt-expanding",
        "Insider / promoter: 11.00%  (-1.20pts YoY)", "⚠ FII exit", "✓ DII accumulating",
        "Retail shareholders: 900,000  (+12.0% YoY)", "¥12.40T  ·  QoQ +4.2%  ·  YoY +5.1%",
    ):
        assert expected in content, expected


def test_header_analyst_bundle_labels(tmp_path: Path):
    from reports.stock_report.header import analyst_parts_for

    snap = _rich_industrial_snap()
    for mean, label in ((1.2, "Strong Buy"), (2.2, "Buy"), (3.0, "Hold"), (4.0, "Sell"), (4.8, "Strong Sell")):
        snap.analyst_recommendation_mean = mean
        parts = analyst_parts_for(snap)
        assert parts[0] == "target ¥3,300.00  (+15.8% upside)"
        assert parts[1].startswith(f"consensus {label}")


def test_bank_profile_rows():
    from reports.stock_report.financial_profile import profile_rows

    snap = BalanceSheetSnapshot(
        ticker="HDFCBANK.NS", as_of_date="2026-11-08", is_bank=True, financial_currency="INR",
        nim=3.4, net_interest_income=1.2e12, non_interest_income=4.0e11, non_interest_income_ratio=25.0,
        roe=14.0, roa=1.7, tax_rate=24.0, deposits=2.5e13, borrowings=6.0e12,
        credit_deposit_ratio=104.0, borrowings_went_from_zero=True,
    )
    rows = {(metric, value) for _, metric, value in profile_rows(snap)}
    assert ("Net interest income", "₹120,000 Cr") in rows
    assert ("Credit-Deposit ratio", "104.0%  (high — lending > funding)") in rows
    assert ("Borrowings 3y change", "from zero to debt  ·  ↑ debt-expanding") in rows
    assert ("GNPA / NNPA", "no NPA data for this lender — verify manually") in rows


def test_report_prints_the_integrity_gate(tmp_path: Path):
    snap = _strong_snap()
    score = blend(
        ticker=snap.ticker,
        leadership=pending_judgment("leadership"),
        fundamentals=score_fundamentals(snap),
        valuation=score_valuation(snap),
        prospects=pending_judgment("prospects"),
        news=pending_judgment("news"),
        integrity_veto=True,
    )
    content = render_stock_report(snap, score, output_dir=tmp_path, console=_quiet()).read_text()
    assert "GATE: integrity veto" in content
