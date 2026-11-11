"""Engine (background warmer) tests — mock FundamentalsSource + no LLM."""

from __future__ import annotations

import json
from pathlib import Path

import fundamental_engine.engine as engine_module
from data_sources.fundamentals_source import BalanceSheetSnapshot, FetchResult
from data_sources import fundamentals_source as fs_module
from fundamental_engine.engine import Engine
from screener.price_history import PriceHistoryStore


def _snap(ticker: str, *, complete: bool = True) -> BalanceSheetSnapshot:
    if not complete:
        return BalanceSheetSnapshot(
            ticker=ticker, as_of_date="2026-04-11", source="empty"
        )
    return BalanceSheetSnapshot(
        ticker=ticker,
        as_of_date="2026-04-11",
        debt_to_equity=0.4,
        interest_coverage=10.0,
        roe=18.0,
        roce=22.0,
        rev_3y_cagr=14.0,
        pat_3y_cagr=12.0,
        ocf_to_pat=1.1,
        insider_holding=50.0,
        pledge_pct=0.0,
        current_price=419.8,
        high_52w=654.38,
        low_52w=375.2,
        market_cap=3.78e12,
        currency="HKD",
        financial_currency="CNY",
        company_name=f"{ticker} Holdings Limited",
        sector="Test",
        industry="Test Industry",
        source="yfinance+screener",
    )


def _install_source(
    monkeypatch, ticker_to_result: dict[str, FetchResult]
) -> None:
    default = FetchResult(
        snapshot=BalanceSheetSnapshot(
            ticker="", as_of_date="2026-04-11", source="empty"
        ),
        price_history=[],
    )

    class FakeSource:
        async def fetch_full(self, ticker: str) -> FetchResult:
            return ticker_to_result.get(ticker, default)

    # Engine imports FundamentalsSource from fundamentals_source.
    monkeypatch.setattr(fs_module, "FundamentalsSource", lambda **_: FakeSource())
    monkeypatch.setattr(engine_module, "FundamentalsSource", lambda **_: FakeSource())
    monkeypatch.setattr(engine_module, "conversion_rate", _fake_rate)


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("BASE_CURRENCY", "INR")


async def _fake_rate(currency: str, base: str) -> float | None:
    return {"HKD": 11.3, "USD": 88.0}.get(currency)


def _history(days: int = 20, start: float = 1400.0) -> list[tuple[str, float]]:
    return [(f"2026-03-{i + 1:02d}", start + i * 5) for i in range(days)]


async def test_refresh_one_success(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(
        monkeypatch,
        {
            "0700.HK": FetchResult(
                snapshot=_snap("0700.HK"), price_history=_history()
            )
        },
    )

    engine = Engine.from_env()
    result = await engine.refresh_one("0700.HK")

    assert result.status == "ok"
    assert result.composite is not None
    assert result.verdict in {"STRONG_BUY", "BUY", "HOLD", "AVOID", "RED_FLAG"}
    assert result.price_rows == 20

    scores = json.loads(
        (tmp_path / "cache" / "scores.json").read_text()
    )
    assert "0700.HK" in scores
    assert scores["0700.HK"]["company_name"] == "0700.HK Holdings Limited"
    assert scores["0700.HK"]["currency"] == "HKD"
    assert scores["0700.HK"]["fx_rate_to_base"] == 11.3  # HKD → INR, cached for `pick`

    # Price history persisted to the SQLite store.
    with PriceHistoryStore(tmp_path / "cache" / "prices.db") as store:
        assert store.latest("0700.HK") is not None

    # The full snapshot is cached so `inspect` can answer line-item questions.
    snapshot = json.loads(
        (tmp_path / "cache" / "snapshots" / "0700.HK.json").read_text()
    )
    assert snapshot["roe"] == 18.0
    assert snapshot["financial_currency"] == "CNY"


async def test_refresh_one_no_data(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(monkeypatch, {})

    engine = Engine.from_env()
    result = await engine.refresh_one("NOPE")
    assert result.status == "no_data"


async def test_refresh_all_walks_emergent_universe(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(
        monkeypatch,
        {
            "MSFT": FetchResult(
                snapshot=_snap("MSFT"), price_history=_history()
            ),
            "RELIANCE.NS": FetchResult(
                snapshot=_snap("RELIANCE.NS"), price_history=_history()
            ),
        },
    )

    # Seed cache so the universe is non-empty.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "scores.json").write_text(
        json.dumps({"MSFT": {}, "RELIANCE.NS": {}})
    )

    engine = Engine.from_env()
    results = await engine.refresh_all(spacing_seconds=0)
    statuses = {r.ticker: r.status for r in results}
    assert statuses == {"MSFT": "ok", "RELIANCE.NS": "ok"}


async def test_refresh_emits_signals(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(
        monkeypatch,
        {
            "NVDA": FetchResult(
                snapshot=_snap("NVDA"), price_history=_history()
            )
        },
    )

    engine = Engine.from_env()
    await engine.refresh_one("NVDA")

    signals = json.loads(
        (tmp_path / "cache" / "signals.json").read_text()
    )
    assert any(
        s["signal_type"] == "SCORE_UPDATED" and s["ticker"] == "NVDA"
        for s in signals
    )


async def test_refresh_without_history_still_succeeds(tmp_path: Path, monkeypatch):
    _env(monkeypatch, tmp_path)
    _install_source(
        monkeypatch,
        {"005930.KS": FetchResult(snapshot=_snap("005930.KS"), price_history=[])},
    )

    engine = Engine.from_env()
    result = await engine.refresh_one("005930.KS")
    assert result.status == "ok"
    assert result.price_rows == 0
