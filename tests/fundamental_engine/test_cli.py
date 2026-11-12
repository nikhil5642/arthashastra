"""CLI handler tests — every subcommand through `main(argv)`, network faked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_sources.fundamentals_source import BalanceSheetSnapshot
from data_sources.research_source import ResearchBundle
from fundamental_engine.__main__ import main
from fundamental_engine.judgments import Judgment, JudgmentStore, PillarJudgment


@pytest.fixture
def cache(tmp_path: Path, monkeypatch) -> Path:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("DATA_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("BASE_CURRENCY", "INR")
    return cache_dir


def _msft_snapshot() -> BalanceSheetSnapshot:
    return BalanceSheetSnapshot(
        ticker="MSFT", as_of_date="2026-11-08", roe=30.2, roce=26.3, debt_to_equity=0.13,
        rev_3y_cagr=16.1, pat_3y_cagr=22.7, current_price=530.61, currency="USD",
        financial_currency="USD", company_name="Microsoft Corporation", sector="Technology",
        source="yfinance",
    )


def test_hello_prints_resolved_config(cache, capsys):
    assert main(["--hello"]) == 0
    assert '"base_currency": "INR"' in capsys.readouterr().out
    assert main([]) == 0  # no subcommand → same config dump


def test_resolve_json_table_and_no_match(cache, capsys, monkeypatch):
    from fundamental_engine import resolver
    from fundamental_engine.resolver import TickerMatch

    async def fake_resolve(query: str, *, limit: int):
        if query == "nothing":
            return []
        return [TickerMatch("005930.KS", "KSE", "SAMSUNG ELEC", "Samsung Electronics Co., Ltd.", 160.0)]

    monkeypatch.setattr(resolver, "resolve_name", fake_resolve)
    assert main(["resolve", "Samsung", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["ticker"] == "005930.KS"
    assert main(["resolve", "Samsung"]) == 0
    assert "005930.KS" in capsys.readouterr().out
    assert main(["resolve", "nothing"]) == 3


def test_refresh_one_all_and_rejudge(cache, capsys, monkeypatch):
    from fundamental_engine import engine as engine_module
    from fundamental_engine.engine import RefreshResult, RefreshStatus

    class FakeEngine:
        async def refresh_one(self, ticker: str) -> RefreshResult:
            return RefreshResult(ticker=ticker, status=RefreshStatus.OK, composite=65.3, verdict="HOLD")

        async def refresh_all(self) -> list[RefreshResult]:
            return [
                RefreshResult(ticker="MSFT", status=RefreshStatus.OK, composite=65.3, verdict="HOLD"),
                RefreshResult(ticker="NOPE", status=RefreshStatus.NO_DATA),
            ]

    monkeypatch.setattr(engine_module.Engine, "from_env", classmethod(lambda cls: FakeEngine()))
    JudgmentStore(cache).save(Judgment(ticker="MSFT", leadership=PillarJudgment(score=80)))

    assert main(["refresh", "msft", "--rejudge"]) == 0
    out = capsys.readouterr().out
    assert "rejudge: cleared MSFT judgment" in out and "MSFT: ok" in out
    assert main(["refresh", "MSFT", "--rejudge"]) == 0
    assert "no prior judgment for MSFT" in capsys.readouterr().out

    JudgmentStore(cache).save(Judgment(ticker="GOOGL"))
    assert main(["refresh", "all", "--rejudge"]) == 0
    out = capsys.readouterr().out
    assert "cleared GOOGL judgment" in out and "Refreshed: 1 ok, 1 no_data, 0 errors" in out


def test_analyse_caches_score_snapshot_and_fx(cache, capsys, monkeypatch):
    from data_sources import fx_source
    from fundamental_engine import analyse_pipeline

    async def fake_analyse(symbol: str, *, data_cache_dir: Path, sec_user_agent: str = ""):
        return analyse_pipeline.score_snapshot(
            _msft_snapshot(), data_cache_dir=data_cache_dir, price_history=[("2026-11-05", 530.61)]
        )

    async def fake_rate(currency: str, base: str) -> float:
        return 88.0

    monkeypatch.setattr(analyse_pipeline, "analyse_ticker", fake_analyse)
    monkeypatch.setattr(fx_source, "conversion_rate", fake_rate)

    assert main(["analyse", "msft"]) == 0
    out = capsys.readouterr().out
    assert "awaiting Claude judgment" in out
    scores = json.loads((cache / "scores.json").read_text())
    assert scores["MSFT"]["fx_rate_to_base"] == 88.0
    assert (cache / "snapshots" / "MSFT.json").exists()
    assert list((cache / "reports").glob("MSFT_*.md"))

    JudgmentStore(cache).save(Judgment(
        ticker="MSFT", leadership=PillarJudgment(score=80), updated_at="2026-01-01T00:00:00",
    ))
    assert main(["analyse", "MSFT"]) == 0
    assert "[warn] Blended with a" in capsys.readouterr().out


def test_analyse_reports_missing_data(cache, capsys, monkeypatch):
    from fundamental_engine import analyse_pipeline

    async def no_data(symbol: str, *, data_cache_dir: Path, sec_user_agent: str = ""):
        raise analyse_pipeline.NoDataError("No fundamentals data for NOPE")

    monkeypatch.setattr(analyse_pipeline, "analyse_ticker", no_data)
    assert main(["analyse", "NOPE"]) == 3
    assert "resolve <NAME>" in capsys.readouterr().out


def test_research_json_rendered_and_empty(cache, capsys, monkeypatch):
    from data_sources import research_source

    bundles = {
        "Satya Nadella": ResearchBundle(
            name="Satya Nadella", company_ticker="MSFT", wikipedia_summary="CEO of Microsoft.",
            wikipedia_url="https://en.wikipedia.org/wiki/Satya_Nadella",
            web_hits=[{"title": "Interview", "url": "https://x/1", "snippet": "On AI"}],
            sources=["wikipedia", "brave_search"],
        ),
    }

    async def fake_research(self, name: str, ticker: str = "") -> ResearchBundle:
        return bundles.get(name, ResearchBundle(name=name))

    monkeypatch.setattr(research_source.ResearchSource, "research", fake_research)
    assert main(["research", "Satya Nadella", "--ticker", "MSFT", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["company_ticker"] == "MSFT"
    assert (cache / "research" / "satya_nadella.json").exists()
    assert main(["research", "Satya Nadella"]) == 0
    assert "Interview" in capsys.readouterr().out
    assert main(["research", "Unknown Person"]) == 0
    assert "No research data found" in capsys.readouterr().out


def test_news_uses_cached_name_and_needs_one(cache, capsys, monkeypatch):
    from data_sources import news_source
    from data_sources.news_source import NewsItem

    async def fake_fetch(self, company_name: str, *, ticker: str, within_days: int):
        assert company_name == "Microsoft Corporation"
        return [NewsItem(ticker=ticker, source="Google News", headline="Microsoft Q1 results beat",
                         url="https://x", published_at="2026-11-05T10:00:00", is_material=True,
                         material_category="earnings")]

    monkeypatch.setattr(news_source.NewsSource, "fetch_for_company", fake_fetch)
    assert main(["news", "MSFT"]) == 3  # nothing cached yet, no --company
    assert "No company name known for MSFT" in capsys.readouterr().out

    (cache / "scores.json").write_text(json.dumps(
        {"MSFT": {"composite": 65.0, "verdict": "HOLD", "company_name": "Microsoft Corporation"}}
    ))
    assert main(["news", "MSFT", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    [story] = payload["stories"]
    assert story["material_category"] == "earnings"
    assert payload["ticker"] == "MSFT" and payload["radar"]["window_days"] == 7
    assert main(["news", "MSFT"]) == 0
    assert "Microsoft Q1 results beat" in capsys.readouterr().out
    assert (cache / "news" / "MSFT.json").exists()


def test_engine_stops_cleanly_on_interrupt(cache, capsys, monkeypatch):
    from fundamental_engine import engine as engine_module

    async def interrupted(self, *, interval_seconds: int = 0) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(engine_module.Engine, "run_forever", interrupted)
    assert main(["engine"]) == 0
    assert "engine stopped" in capsys.readouterr().out


def test_brief_pick_and_migrate_dispatch(cache):
    assert main(["brief"]) == 0
    assert main(["pick", "--amount", "5L"]) == 0
    assert main(["pick", "--amount", "5L", "--tickers", "msft", "0700.HK"]) == 0
    assert main(["migrate-ids", "--dry-run"]) == 0


def test_screen_flags_become_a_typed_request(cache, capsys, monkeypatch):
    from fundamental_engine.commands import screen as screen_module
    from fundamental_engine.commands.screen import ScreenRequest, ScreenSort
    from fundamental_engine.scorecard import DEFAULT_REUSE_HOURS, RankBy

    seen: list[tuple[ScreenRequest, bool]] = []

    def fake_run(request: ScreenRequest, *, as_json: bool) -> int:
        seen.append((request, as_json))
        return 0

    monkeypatch.setattr(screen_module, "run_screen_command", fake_run)
    argv = [
        "screen", "--market", "US", "in", "--sector", "tech", "--min-mcap", "10B",
        "--max-pe", "25", "--min-roe", "15", "--max-de", "0.5", "--min-drawdown", "20",
        "--sort", "drawdown", "--score", "3", "--rank", "fundamentals", "--reuse-hours", "0",
        "--json",
    ]
    assert main(argv) == 0
    request, as_json = seen[0]
    # Names arrive as typed; `run_screen_command` puts them in Yahoo's spelling.
    assert request.markets == ("US", "in") and request.sectors == ("tech",)
    assert (request.min_market_cap, request.max_market_cap) == (1e10, None)
    assert (request.max_pe, request.min_roe_pct, request.max_debt_to_equity) == (25, 15, 0.5)
    assert (request.sort, request.score, as_json) == (ScreenSort.DRAWDOWN, 3, True)
    assert (request.rank_by, request.reuse_hours) == (RankBy.FUNDAMENTALS, 0.0)

    assert main(["screen"]) == 0
    defaults, _ = seen[1]
    assert (defaults.rank_by, defaults.reuse_hours) == (RankBy.RISK_ADJUSTED, DEFAULT_REUSE_HOURS)

    assert main(["screen", "--max-mcap", "lots"]) == 2
    assert "Invalid --max-mcap" in capsys.readouterr().out


def test_compare_passes_the_tickers_and_how_to_rank_them(cache, monkeypatch):
    from fundamental_engine.commands import compare as compare_module
    from fundamental_engine.scorecard import RankBy

    seen: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(tickers: list[str], **options: object) -> int:
        seen.append((tickers, options))
        return 0

    monkeypatch.setattr(compare_module, "run_compare_command", fake_run)
    assert main(["compare", "NVDA", "amd", "--rank", "valuation", "--json"]) == 0
    assert seen == [
        (["NVDA", "amd"], {"rank_by": RankBy.VALUATION, "reuse_hours": 12.0, "as_json": True})
    ]


def test_ipo_joins_the_name_and_passes_the_price(cache, monkeypatch):
    from fundamental_engine.commands import ipo as ipo_module

    seen: list[tuple[str, dict[str, object]]] = []

    def fake_run(name: str, **options: object) -> int:
        seen.append((name, options))
        return 0

    monkeypatch.setattr(ipo_module, "run_ipo_command", fake_run)
    assert main(["ipo", "cerebras", "systems", "--price", "28.5", "--json"]) == 0
    assert main(["ipo", "anthropic"]) == 0
    assert seen == [
        ("cerebras systems", {"offer_price": 28.5, "as_json": True}),
        ("anthropic", {"offer_price": None, "as_json": False}),
    ]
