"""Stock report render test — inline inputs, Markdown output."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from data_sources.fundamentals_source import BalanceSheetSnapshot
from reports.stock_report import render_stock_report
from scorers.composite_scorer import blend, pending_judgment
from scorers.fundamentals_scorer import score_fundamentals


def _quiet() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=120)


def _strong_snap() -> BalanceSheetSnapshot:
    return BalanceSheetSnapshot(
        ticker="RELIANCE",
        as_of_date="2026-04-11",
        debt_to_equity=0.42,
        interest_coverage=8.1,
        roe=12.5,
        roce=14.8,
        rev_3y_cagr=16.2,
        pat_3y_cagr=11.3,
        ocf_to_pat=1.12,
        promoter_holding=50.3,
        pledge_pct=0.0,
        current_price=1450.75,
        high_52w=1611.80,
        low_52w=1190.40,
        market_cap_cr=1827979.0,
        company_name="Reliance Industries Limited",
        sector="Energy",
        industry="Oil & Gas Refining & Marketing",
        source="yfinance+screener",
    )


def test_render_stock_report_writes_markdown(tmp_path: Path):
    snap = _strong_snap()
    fundamentals = score_fundamentals(snap)
    score = blend(
        ticker=snap.ticker,
        leadership=pending_judgment("leadership"),
        fundamentals=fundamentals,
        prospects=pending_judgment("prospects"),
        news=pending_judgment("news"),
    )
    fp = render_stock_report(snap, score, output_dir=tmp_path, console=_quiet())
    assert fp is not None
    assert fp.exists()
    content = fp.read_text()
    assert "RELIANCE" in content
    assert "Reliance Industries Limited" in content
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
        prospects=pending_judgment("prospects"),
        news=pending_judgment("news"),
    )
    fp = render_stock_report(snap, score, output_dir=tmp_path, console=_quiet())
    assert fp is not None
    content = fp.read_text()
    assert "SPARSE" in content
    assert "LOW CONFIDENCE" in content or score.low_confidence
