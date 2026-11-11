"""Full screener.in page tests — inline HTML through the real parser, no network.

Builds a company page the way screener lays it out (top ratios, statement
tables, quarterly shareholding, peers, documents) and runs the whole
`_fetch_screener_in` pipeline over it.
"""

from __future__ import annotations

import httpx
import pytest
from selectolax.parser import HTMLParser

from data_sources.fundamentals_source import screener_adapter

CRORE = 1e7


def _table(section: str, headers: list[str], rows: dict[str, list[str]], wrapper: str = "") -> str:
    head = "".join(f"<th>{h}</th>" for h in ["", *headers])
    body = "".join(
        f"<tr><td>{label}</td>{''.join(f'<td>{cell}</td>' for cell in cells)}</tr>"
        for label, cells in rows.items()
    )
    table = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    if wrapper:
        table = f'<div id="{wrapper}">{table}</div>'
    return f'<section id="{section}">{table}</section>'


def _top_ratios(items: dict[str, str]) -> str:
    lis = "".join(
        f'<li><span class="name">{name}</span><span class="value">{value}</span></li>'
        for name, value in items.items()
    )
    return f'<ul id="top-ratios">{lis}</ul>'


def _number(text: str) -> str:
    return f'<span class="number">{text}</span>'


YEARS = ["Mar 2022", "Mar 2023", "Mar 2024", "Mar 2025"]
QUARTERS = ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026", "Sep 2026"]


def _industrial_page() -> str:
    return "".join([
        "<h1>Acme Industries Ltd</h1>",
        _top_ratios({
            "Market Cap": f"₹ {_number('1,00,000')} Cr.",
            "Current Price": f"₹ {_number('2,500')}",
            "High / Low": f"₹ {_number('3,000')} / {_number('2,000')}",
            "Stock P/E": _number("25.0"),
            "Book Value": f"₹ {_number('500')}",
            "Dividend Yield": f"{_number('1.20')} %",
            "ROCE": f"{_number('22.5')} %",
            "ROE": f"{_number('18.4')} %",
        }),
        _table("profit-loss", [*YEARS, "TTM"], {
            "Sales +": ["1,000", "1,200", "1,500", "1,800", "2,000"],
            "Expenses +": ["800", "950", "1,180", "1,400", "1,550"],
            "Operating Profit": ["200", "250", "320", "400", "450"],
            "Other Income +": ["10", "12", "15", "20", "22"],
            "Interest": ["20", "22", "25", "28", "30"],
            "Depreciation": ["40", "45", "50", "60", "60"],
            "Profit before tax": ["150", "195", "260", "332", "382"],
            "Tax %": ["25%", "25%", "25%", "25%", "25%"],
            "Net Profit +": ["112", "146", "195", "249", "286"],
            "EPS in Rs": ["11.20", "14.60", "19.50", "24.90", "28.60"],
        }),
        _table("balance-sheet", [*YEARS, "Sep 2025"], {
            "Equity Capital": ["100", "100", "100", "100", "100"],
            "Reserves": ["700", "800", "950", "1,100", "1,200"],
            "Borrowings +": ["300", "280", "250", "240", "230"],
            "Total Assets": ["1,500", "1,600", "1,800", "2,000", "2,100"],
        }),
        _table("cash-flow", YEARS, {
            "Cash from Operating Activity +": ["130", "170", "230", "300"],
            "Free Cash Flow": ["60", "90", "140", "200"],
        }),
        _table("ratios", YEARS, {
            "Debtor Days": ["45", "44", "42", "40"],
            "Inventory Days": ["60", "58", "55", "50"],
            "Days Payable": ["50", "50", "52", "55"],
            "Cash Conversion Cycle": ["55", "52", "45", "35"],
            "ROCE %": ["15%", "17%", "20%", "22%"],
        }),
        _table("quarters", QUARTERS, {
            "Sales +": ["430", "460", "480", "500", "560"],
            "Net Profit +": ["60", "65", "70", "72", "84"],
        }),
        _table("shareholding", QUARTERS, {
            "Promoters +": ["50.00%", "50.10%", "50.20%", "50.30%", "50.50%"],
            "FIIs +": ["20.00%", "19.50%", "19.00%", "18.50%", "17.80%"],
            "DIIs +": ["15.00%", "15.40%", "15.90%", "16.40%", "17.20%"],
            "Public +": ["15.00%", "15.00%", "14.90%", "14.80%", "14.50%"],
            "No. of Shareholders": ["1,00,000", "1,10,000", "1,20,000", "1,30,000", "1,50,000"],
        }, wrapper="quarterly-shp"),
        # Peer tables label every column (no blank row-label header).
        '<section id="peers"><table><thead><tr><th>S.No.</th><th>Name</th>'
        "<th>CMP Rs.</th><th>P/E</th></tr></thead><tbody><tr><td>1.</td>"
        "<td>Acme</td><td>2,500</td><td>25.0</td></tr></tbody></table></section>",
        '<section id="documents"><a href="#top">Top</a>'
        '<a href="https://example.com/ar2025.pdf">Annual Report 2025</a></section>',
        '<section id="analysis"><p>Pros: Debt reduced.</p><p>Cons: Low dividend.</p></section>',
    ])


def _bank_page(*, npa_cells: list[str]) -> str:
    return "".join([
        "<h1>Example Bank Ltd</h1>",
        _table("profit-loss", YEARS, {
            "Revenue +": ["800", "900", "1,050", "1,200"],
            "Interest": ["400", "450", "520", "600"],
            "Expenses +": ["200", "220", "250", "280"],
            "Financing Profit": ["200", "230", "280", "320"],
            "Other Income +": ["120", "140", "160", "180"],
            "Net Profit +": ["150", "175", "210", "240"],
        }),
        _table("balance-sheet", YEARS, {
            "Equity Capital": ["50", "50", "50", "50"],
            "Reserves": ["950", "1,050", "1,200", "1,350"],
            "Deposits": ["9,000", "10,000", "11,500", "13,000"],
            "Borrowing": ["1,000", "1,100", "1,200", "1,300"],
            "Investments": ["3,000", "3,200", "3,500", "3,800"],
            "Fixed Assets": ["100", "110", "120", "130"],
            "Total Assets": ["12,000", "13,500", "15,000", "16,500"],
        }),
        _table("quarters", QUARTERS, {
            "Revenue +": ["290", "300", "305", "310", "320"],
            "Net Profit +": ["58", "60", "61", "62", "65"],
            "Gross NPA %": npa_cells,
            "Net NPA %": npa_cells,
        }),
    ])


async def _parse(monkeypatch, page: str, *, consolidated: bool = True) -> dict:
    async def fake_tree(ticker: str):
        return HTMLParser(page), consolidated

    monkeypatch.setattr(screener_adapter, "_fetch_html_tree", fake_tree)
    return await screener_adapter._fetch_screener_in("ACME.NS")


async def test_industrial_page_end_to_end(monkeypatch):
    out = await _parse(monkeypatch, _industrial_page())

    # Identity and top ratios — crores multiplied out, both High/Low numbers read.
    assert out["company_name"] == "Acme Industries Ltd"
    assert out["currency"] == out["financial_currency"] == "INR"
    assert out["market_cap"] == 100_000 * CRORE
    assert (out["high_52w"], out["low_52w"]) == (3000.0, 2000.0)
    assert (out["roe"], out["roce"], out["pe_ratio"]) == (18.4, 22.5, 25.0)
    assert (out["book_value_per_share"], out["dividend_yield"]) == (500.0, 1.2)

    # Ownership — no pledge row on the page, so pledge stays unknown.
    assert out["insider_holding"] == 50.5
    assert "pledge_pct" not in out
    assert out["insider_delta_1y_pct_points"] == 0.5
    assert out["fii_delta_1y_pct_points"] == -2.2
    assert out["num_shareholders_delta_1y_pct"] == 50.0

    # Statements — aligned columns, core-EBIT coverage, TTM out of CAGRs.
    assert out["debt_to_equity"] == round(230 / 1300, 3)  # half-year BS column
    assert out["operating_margin"] == 22.5  # TTM 450 / 2000
    assert out["interest_coverage"] == 13.0  # (450 − 60) / 30
    assert out["rev_3y_cagr"] == 21.64
    assert out["latest_annual_period"] == "2025-03-31"
    assert out["ocf_to_pat"] == round(300 / 249, 3)
    assert out["roa"] == round(249 / 2000 * 100, 2)  # FY25 assets, not Sep-25
    assert out["cash_conversion_cycle_days"] == 35.0

    # Valuation inputs: the latest FISCAL year (never TTM), in rupees.
    assert out["net_income"] == 249 * CRORE
    assert out["eps_history"] == {
        "2022-03-31": 11.2, "2023-03-31": 14.6, "2024-03-31": 19.5, "2025-03-31": 24.9,
    }
    assert out["free_cash_flow"] == 200 * CRORE
    assert out["shareholders_equity"] == (100 + 1200) * CRORE  # latest BS column

    # Quarterly series in rupees, newest first, YoY by date.
    assert out["quarterly_revenue"][0] == 560 * CRORE
    assert out["latest_yoy_revenue_growth"] == round((560 - 430) / 430 * 100, 2)

    # Trends, raw dumps, peers, documents, analysis.
    assert out["roce_annual_trend"] == [15.0, 17.0, 20.0, 22.0]
    assert out["borrowings_delta_3y_pct"] == -20.0
    assert out["raw_profit_loss_headers"][-1] == "TTM"
    assert out["raw_shareholding_quarterly"]["promoters"][-1] == 50.5
    assert out["peers"][0]["p/e"] == 25.0
    assert out["documents"] == [{"label": "Annual Report 2025", "url": "https://example.com/ar2025.pdf"}]
    assert "Debt reduced" in out["screener_analysis_text"]
    assert "is_bank" not in out


async def test_bank_npa_falls_back_to_the_standalone_page(monkeypatch):
    """Consolidated bank quarters carry empty NPA cells; standalone has them."""
    async def fake_page(url: str):
        assert not url.endswith("/consolidated/")
        return HTMLParser(_bank_page(npa_cells=["1.80%", "1.70%", "1.60%", "1.50%", "1.40%"]))

    monkeypatch.setattr(screener_adapter, "_fetch_page", fake_page)
    out = await _parse(monkeypatch, _bank_page(npa_cells=[""] * 5))

    assert out["is_bank"] is True
    assert out["gross_npa_pct"] == 1.4
    assert out["gross_npa_delta_1y_pct_points"] == -0.4
    assert out["net_interest_income"] == (1200 - 600) * CRORE
    assert out["nim"] == round(600 / 16500 * 100, 2)
    assert out["non_interest_income_ratio"] == round(180 / (600 + 180) * 100, 2)
    assert out["credit_deposit_ratio"] == round((16500 - 3800 - 130) / 13000 * 100, 1)
    assert out["deposits"] == 13_000 * CRORE


async def test_unreachable_page_yields_nothing(monkeypatch):
    async def no_page(ticker: str):
        return None

    monkeypatch.setattr(screener_adapter, "_fetch_html_tree", no_page)
    assert await screener_adapter._fetch_screener_in("ACME.NS") == {}
    assert await screener_adapter.fetch_tables("ACME.NS") == {}


async def test_fetch_tables_returns_every_history_section(monkeypatch):
    async def fake_tree(ticker: str):
        return HTMLParser(_industrial_page()), True

    monkeypatch.setattr(screener_adapter, "_fetch_html_tree", fake_tree)
    tables = await screener_adapter.fetch_tables("ACME.NS")
    assert set(tables) == {*screener_adapter.HISTORY_SECTIONS, "shareholding"}
    headers, rows = tables["profit-loss"]
    assert headers[-1] == "TTM" and rows["net profit"][-1] == 286.0


@pytest.fixture
def mock_http(monkeypatch):
    """Route screener_adapter's httpx calls through a handler, no sockets."""
    def install(handler):
        real_client = httpx.AsyncClient

        class Client(real_client):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(screener_adapter.httpx, "AsyncClient", Client)
    return install


async def test_html_tree_prefers_consolidated_then_standalone(mock_http):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/consolidated/"):
            return httpx.Response(404)
        return httpx.Response(200, text="<h1>Standalone Co</h1>")

    mock_http(handler)
    tree, consolidated = await screener_adapter._fetch_html_tree("ACME")
    assert consolidated is False
    assert tree.css_first("h1").text() == "Standalone Co"
    assert [url.rsplit("/company/", 1)[1] for url in seen] == ["ACME/consolidated/", "ACME/"]


async def test_html_tree_is_none_when_both_pages_fail(mock_http):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    mock_http(handler)
    assert await screener_adapter._fetch_html_tree("ACME") is None


async def test_industrial_page_trailing_window_fiscal_amounts_and_trends(monkeypatch):
    out = await _parse(monkeypatch, _industrial_page())

    # The latest fiscal year's amounts, in rupees. Capex is the gap between
    # operating and free cash flow of that same year.
    assert out["revenue"] == 1800 * CRORE
    assert out["operating_cash_flow"] == 300 * CRORE
    assert out["capex"] == 100 * CRORE

    # Twelve months from the four newest quarters (Dec-25 … Sep-26).
    assert out["ttm_period_end"] == "2026-09-30"
    assert out["ttm_revenue"] == (460 + 480 + 500 + 560) * CRORE
    assert out["ttm_net_income"] == (65 + 70 + 72 + 84) * CRORE

    # Multi-year series: amounts in rupees like every other amount, margins in %.
    assert out["sales_annual_trend"] == [1000 * CRORE, 1200 * CRORE, 1500 * CRORE, 1800 * CRORE]
    assert out["net_profit_annual_trend"][-1] == 249 * CRORE
    assert out["borrowings_annual_trend"] == [300 * CRORE, 280 * CRORE, 250 * CRORE, 240 * CRORE]
    assert out["net_margin_annual_trend"] == [11.2, 12.17, 13.0, 13.83]
    assert out["operating_margin_annual_trend"] == [20.0, 20.83, 21.33, 22.22]

    # This page's quarterly table has no operating line: aligned, but unknown.
    assert out["quarterly_operating_margin"] == [None] * 5


def test_quarterly_operating_margin_is_after_depreciation():
    """Same definition as the yfinance path's Operating Income ÷ revenue."""
    out: dict = {}
    screener_adapter._apply_quarters(
        out,
        ["Jun 2026", "Sep 2026"],
        {
            "sales": [500.0, 560.0],
            "operating profit": [100.0, 126.0],
            "depreciation": [20.0, 14.0],
            "net profit": [72.0, 84.0],
        },
    )
    assert out["quarterly_dates"] == ["2026-09-30", "2026-06-30"]
    assert out["quarterly_operating_margin"] == [20.0, 16.0]  # (126 − 14) / 560, (100 − 20) / 500
    assert "ttm_revenue" not in out  # two quarters are not twelve months


def test_profit_that_became_a_loss_reaches_the_snapshot_as_minus_100():
    out: dict = {}
    screener_adapter._apply_profit_loss(
        out,
        YEARS,
        {"sales": [900.0, 950.0, 960.0, 940.0], "net profit": [80.0, 40.0, 5.0, -30.0]},
    )
    assert out["pat_3y_cagr"] == -100.0
    assert out["rev_3y_cagr"] == round(((940 / 900) ** (1 / 3) - 1) * 100, 2)
