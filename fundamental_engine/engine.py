"""Background cache warmer — refreshes the emergent universe.

The universe is whatever the user has already loaded via
`refresh <ticker>` or `analyse <ticker>`. The engine walks that list at a
fixed interval, fetches live fundamentals, recomputes deterministic scores,
and persists them. Qualitative pillars come from judgments Claude writes —
the engine never calls an LLM itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass

from fundamental_engine.analyse_pipeline import NoDataError, analyse_ticker
from fundamental_engine.cache import JsonStore
from fundamental_engine.config import AnalystConfig
from fundamental_engine.market_calendar import MarketCalendar
from fundamental_engine.ticker import canonical
from fundamental_engine.universe import load_universe
from signals.signal_bus import Severity, Signal, SignalBus, SignalType

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 900


@dataclass(slots=True)
class RefreshResult:
    ticker: str
    status: str  # "ok" | "no_data" | "error"
    composite: float | None = None
    verdict: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class Engine:
    def __init__(self, cfg: AnalystConfig) -> None:
        self._cfg = cfg
        self._calendar = MarketCalendar()
        self._scores_store = JsonStore(cfg.data_cache_dir / "scores.json")
        self._signal_bus = SignalBus(path=cfg.data_cache_dir / "signals.json")

    @classmethod
    def from_env(cls) -> "Engine":
        return cls(AnalystConfig.from_env())

    def universe(self) -> list[str]:
        return load_universe(self._cfg.data_cache_dir)

    async def refresh_one(self, ticker: str) -> RefreshResult:
        ticker = canonical(ticker)
        try:
            result = await analyse_ticker(
                ticker, data_cache_dir=self._cfg.data_cache_dir
            )
        except NoDataError as e:
            log.info("refresh %s: no_data", ticker)
            return RefreshResult(ticker=ticker, status="no_data", detail=str(e))
        except Exception as e:  # pragma: no cover
            log.exception("refresh %s failed", ticker)
            return RefreshResult(ticker=ticker, status="error", detail=str(e))

        snap = result.snapshot
        score = result.score
        extended = score.to_dict()
        extended.update(
            {
                "company_name": snap.company_name,
                "sector": snap.sector,
                "industry": snap.industry,
                "current_price": snap.current_price,
                "high_52w": snap.high_52w,
                "low_52w": snap.low_52w,
                "market_cap_cr": snap.market_cap_cr,
                "source": snap.source,
            }
        )
        self._scores_store.upsert(ticker, extended)

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
            status="ok",
            composite=score.composite,
            verdict=score.verdict,
        )

    async def refresh_all(
        self, tickers: list[str] | None = None
    ) -> list[RefreshResult]:
        targets = list(tickers) if tickers is not None else self.universe()
        results: list[RefreshResult] = []
        for t in targets:
            results.append(await self.refresh_one(t))
        ok = sum(1 for r in results if r.status == "ok")
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

    async def process_refresh_queue(self) -> int:
        queue_path = self._cfg.data_cache_dir / "refresh_queue.jsonl"
        if not queue_path.exists():
            return 0
        lines = queue_path.read_text().splitlines()
        queue_path.unlink()
        processed = 0
        for line in lines:
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            ticker = req.get("ticker")
            if ticker:
                await self.refresh_one(ticker)
                processed += 1
        return processed

    async def run_forever(
        self, *, interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    ) -> None:  # pragma: no cover — long-running loop
        log.info("engine started — interval %ds", interval_seconds)
        while True:
            log.info("engine tick")
            await self.refresh_all()
            await self.process_refresh_queue()
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                log.info("engine stopped")
                break


def enqueue_refresh(cache_dir, ticker: str) -> None:
    from datetime import datetime

    queue_path = cache_dir / "refresh_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with queue_path.open("a") as f:
        f.write(
            json.dumps(
                {"ticker": ticker, "ts": datetime.now().isoformat()}
            )
            + "\n"
        )
