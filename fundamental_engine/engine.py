"""Background cache warmer — opt-in, for users who want a standing refresh.

**This is not part of the default cockpit.** The Claude-native workflow
is on-demand: each slash command refreshes the ticker it operates on
before scoring, so nothing has to be pre-warmed. The engine here exists
for the rare case where someone wants `/market-brief` to always show
fresh numbers without invoking `/refresh-now` first.

When run (via `./launch.sh --engine`), the engine walks the emergent
universe (whatever the user has loaded via `refresh` / `analyse`),
re-fetches live fundamentals + daily closes, recomputes deterministic
scores, and persists the extended StockScore, the full snapshot, and the
raw price history. Tickers are refreshed one at a time with a short pause
between them so a large universe doesn't hammer screener.in. Qualitative
pillars come from judgments Claude writes — the engine never calls an LLM
itself.

The default interval is 1 hour. 15-minute ticks (from the original
800-ticker scan-the-market plan) don't fit an emergent-universe model
and just hammer screener.in for no real benefit.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from enum import StrEnum

from data_sources.fundamentals_source import FundamentalsSource
from data_sources.fx_source import conversion_rate
from fundamental_engine.analyse_pipeline import NoDataError, score_snapshot
from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig
from fundamental_engine.extended_score import ExtendedScore
from fundamental_engine.snapshot_store import SnapshotStore
from fundamental_engine.ticker import canonical
from fundamental_engine.universe import load_universe
from screener.price_history import PriceHistoryStore
from signals.signal_bus import Severity, Signal, SignalBus, SignalType

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 3600  # 1 hour — Claude-native workflow is on-demand
REFRESH_SPACING_SECONDS = 1.0  # pause between tickers in refresh_all
PRICE_DB_FILENAME = "prices.db"


class RefreshStatus(StrEnum):
    OK = "ok"
    NO_DATA = "no_data"
    ERROR = "error"


@dataclass(slots=True)
class RefreshResult:
    ticker: str
    status: RefreshStatus
    composite: float | None = None
    verdict: str | None = None
    detail: str = ""
    price_rows: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class Engine:
    def __init__(self, cfg: AnalystConfig) -> None:
        self._cfg = cfg
        self._scores_store = JsonStore(cfg.data_cache_dir / "scores.json")
        self._signal_bus = SignalBus(path=cfg.data_cache_dir / "signals.json")
        self._snapshots = SnapshotStore(cfg.data_cache_dir)
        self._price_db_path = cfg.data_cache_dir / PRICE_DB_FILENAME

    @classmethod
    def from_env(cls) -> "Engine":
        return cls(AnalystConfig.from_env())

    def universe(self) -> list[str]:
        return load_universe(self._cfg.data_cache_dir)

    async def refresh_one(self, ticker: str) -> RefreshResult:
        ticker = canonical(ticker)
        try:
            fetch = await FundamentalsSource().fetch_full(ticker)
        except Exception as e:  # pragma: no cover
            log.exception("fetch %s failed", ticker)
            return RefreshResult(ticker=ticker, status=RefreshStatus.ERROR, detail=str(e))

        try:
            result = score_snapshot(
                fetch.snapshot,
                data_cache_dir=self._cfg.data_cache_dir,
                price_history=fetch.price_history,
            )
        except NoDataError as e:
            log.info("refresh %s: no_data", ticker)
            return RefreshResult(ticker=ticker, status=RefreshStatus.NO_DATA, detail=str(e))
        except Exception as e:  # pragma: no cover
            log.exception("score %s failed", ticker)
            return RefreshResult(ticker=ticker, status=RefreshStatus.ERROR, detail=str(e))

        fx_rate = await conversion_rate(result.snapshot.currency, self._cfg.base_currency)
        extended = ExtendedScore.from_analysis(result, fx_rate_to_base=fx_rate)
        self._scores_store.upsert(ticker, extended.to_dict())
        self._snapshots.save(result.snapshot)

        price_rows_written = 0
        if result.price_history:
            with PriceHistoryStore(self._price_db_path) as store:
                price_rows_written = store.load_rows(ticker, result.price_history)

        score = result.score
        self._signal_bus.emit(
            Signal(
                signal_type=SignalType.SCORE_UPDATED,
                severity=Severity.INFO,
                ticker=ticker,
                message=f"composite {score.composite} ({score.verdict})",
                data={"composite": score.composite, "verdict": score.verdict},
            )
        )
        return RefreshResult(
            ticker=ticker,
            status=RefreshStatus.OK,
            composite=score.composite,
            verdict=score.verdict,
            price_rows=price_rows_written,
        )

    async def refresh_all(
        self,
        tickers: list[str] | None = None,
        *,
        spacing_seconds: float = REFRESH_SPACING_SECONDS,
    ) -> list[RefreshResult]:
        targets = list(tickers) if tickers is not None else self.universe()
        results: list[RefreshResult] = []
        for i, t in enumerate(targets):
            if i and spacing_seconds > 0:
                await asyncio.sleep(spacing_seconds)
            results.append(await self.refresh_one(t))
        ok = sum(1 for r in results if r.status == RefreshStatus.OK)
        log.info("refresh_all: %d/%d ok", ok, len(results))
        if results:
            self._signal_bus.emit(
                Signal(
                    signal_type=SignalType.UNIVERSE_REFRESHED,
                    severity=Severity.INFO,
                    ticker="",
                    message=f"{ok}/{len(results)} tickers refreshed",
                    data={"ok": ok, "total": len(results)},
                )
            )
        return results

    async def run_forever(
        self, *, interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    ) -> None:  # pragma: no cover — long-running loop
        log.info("engine started — interval %ds", interval_seconds)
        while True:
            log.info("engine tick")
            await self.refresh_all()
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                log.info("engine stopped")
                break
