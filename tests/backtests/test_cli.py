"""`python3 -m backtests` end to end — fetches monkeypatched, report written."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backtests import cli
from backtests.engine.runner import BacktestResult
from backtests.report import BacktestReport, SuiteRun, render_markdown
from backtests.suite import Outcome, SuiteResult
from backtests.suites import ALL, dip_signals, fundamentals_score
from tests.backtests.engine.test_runner import us_filer


@pytest.fixture
def cache(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path / "cache"


def test_list_prints_every_claim(capsys):
    assert cli.main(["--list"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert all(suite.name in out and suite.claim in out for suite in ALL)


def test_unknown_suite_and_missing_tickers(cache, capsys):
    assert cli.main(["no_such_suite", "--tickers", "MSFT"]) == cli.EXIT_UNKNOWN_SUITE
    assert "choose from" in capsys.readouterr().out
    assert cli.main([]) == cli.EXIT_NO_TICKERS  # 'all' on an empty cache


def test_select_suites_keeps_report_order():
    assert cli.select_suites([]) == list(ALL)
    assert cli.select_suites(["dip_signals", "fundamentals_score"]) == [
        fundamentals_score.SUITE, dip_signals.SUITE,
    ]
    assert cli.select_suites(["fundamentals_score", "typo"]) is None


def test_run_writes_a_report_and_exits_like_a_test_run(cache, monkeypatch, capsys):
    monkeypatch.setattr(cli, "FETCH_SPACING_SECONDS", 0)

    async def fake_fetch(symbol: str, *, sec_user_agent: str):
        return us_filer(symbol, margin={"MSFT": 0.35, "GOOGL": 0.25}.get(symbol, 0.15))

    class FixedDate(date):
        @classmethod
        def today(cls) -> "FixedDate":
            return cls(2026, 1, 15)

    monkeypatch.setattr(cli, "fetch_statement_history", fake_fetch)
    monkeypatch.setattr(cli, "date", FixedDate)

    rc = cli.main(["--tickers", "msft", "GOOGL", "AAPL", "--years", "5", "--step", "quarter"])

    [report] = (cache / "reports").glob("backtest_*.md")
    content = report.read_text()
    assert rc == (cli.EXIT_SUITE_FAILED if "| FAIL |" in content else cli.EXIT_OK)
    assert "3 tickers · 5y · quarterly rebalance · 3-month forward total return" in content
    assert "| MSFT | sec |" in content  # canonical symbols
    assert all(f"`{suite.name}`" in content for suite in ALL)
    assert "Survivorship" in content
    assert "fetched MSFT" in capsys.readouterr().out


def _listing(symbol: str, name: str, currency: str, reporting: str, closes: int):
    history = us_filer(symbol, margin=0.2)
    history.metadata = {"company_name": name, "currency": currency, "financial_currency": reporting}
    history.prices = history.prices[:closes]
    return history


def test_one_listing_per_company_prefers_the_home_currency_then_the_longest_history():
    histories = [
        _listing("GOOGL.BA", "Alphabet Inc.", "ARS", "USD", 900),  # a peso receipt, listed first
        _listing("GOOG", "Alphabet Inc.", "USD", "USD", 500),
        _listing("GOOGL", "Alphabet Inc.", "USD", "USD", 700),  # same currency, longer history
        _listing("9988.HK", "Alibaba Group Holding Limited", "HKD", "CNY", 300),
        _listing("BABA", "Alibaba Group Holding Limited", "USD", "CNY", 600),  # neither is home
        _listing("SHEL.L", "Shell plc", "GBp", "USD", 800),  # alone: kept whatever its currency
        _listing("NONAME", "", "USD", "USD", 100),
        _listing("NONAME2", "", "USD", "USD", 100),  # no name → never matched to another
    ]
    kept, left_out = cli.one_listing_per_company(histories)
    assert [h.symbol for h in kept] == ["GOOGL", "BABA", "SHEL.L", "NONAME", "NONAME2"]
    assert left_out == ["GOOGL.BA", "GOOG", "9988.HK"]
    assert cli.one_listing_per_company([]) == ([], [])


def test_all_leaves_out_cached_quotes_that_do_not_reconcile(cache, capsys):
    import json

    from data_sources.fundamentals_source import BalanceSheetSnapshot
    from fundamental_engine.config import AnalystConfig
    from fundamental_engine.snapshot_store import SnapshotStore

    cache.mkdir(parents=True)
    (cache / "scores.json").write_text(json.dumps({"MSFT": {}, "RIO.BA": {}, "NEW.L": {}}))
    store = SnapshotStore(cache)
    store.save(BalanceSheetSnapshot(ticker="MSFT", as_of_date="2026-11-11"))
    store.save(BalanceSheetSnapshot(ticker="RIO.BA", as_of_date="2026-11-11", quote_unreliable=True))

    cfg = AnalystConfig.from_env()
    assert cli.resolve_tickers(["all"], cfg) == ["MSFT", "NEW.L"]  # no snapshot yet → kept
    assert "RIO.BA" in capsys.readouterr().out
    # Naming a ticker is the user's call: it is never filtered.
    assert cli.resolve_tickers(["rio.ba"], cfg) == ["RIO.BA"]


def test_markdown_tally_and_suites_without_tables():
    report = BacktestReport(
        settings="none",
        result=BacktestResult(observations=[], coverage=[]),
        runs=[
            SuiteRun(fundamentals_score.SUITE, SuiteResult(Outcome.NO_DATA, "nothing to rank")),
            SuiteRun(dip_signals.SUITE, SuiteResult(Outcome.FAIL, "dips lagged")),
        ],
    )
    assert report.tally() == "1 fail · 1 no data"
    assert report.any_failed and report.observations_with_returns == 0
    content = render_markdown(report)
    assert "## NO DATA — `fundamentals_score`" in content and "nothing to rank" in content
