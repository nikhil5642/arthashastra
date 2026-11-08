"""Analyst environment configuration.

This project is Claude-Code-native: there's **no LLM API key, no cost
budget, no sandbox/live split**. Everything that would ordinarily live
behind a feature flag is either deterministic (scoring) or done by
Claude in a slash command (qualitative pillars). The only optional
secret is `BRAVE_API_KEY` for leadership web research; everything else
runs fine without it.

Config comes from an optional `.env` file at the project root. Values
present in the process environment always win — `.env` only fills
unset keys via `load_dotenv(override=False)`.

`BASE_CURRENCY` is the investor's own currency: `pick` budgets are in
it, and every listing's price is converted into it with live FX.

`SEC_USER_AGENT` identifies this tool to SEC EDGAR (US filings history
for backtests). SEC asks automated clients to name themselves; add a
contact, e.g. "Your Name you@example.com", if you have one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


DEFAULT_BASE_CURRENCY = "INR"
DEFAULT_SEC_USER_AGENT = "Arthashastra fundamental-analyst/1.0 (personal research tool)"


@dataclass(slots=True)
class AnalystConfig:
    data_cache_dir: Path
    brave_api_key: str
    base_currency: str = DEFAULT_BASE_CURRENCY
    sec_user_agent: str = DEFAULT_SEC_USER_AGENT

    @classmethod
    def from_env(cls) -> "AnalystConfig":
        # Load `.env` (if present) without overriding anything already
        # set in the process environment. `.env.example` is only used
        # when the user hasn't created a real `.env` yet.
        env_file = PROJECT_ROOT / ".env"
        fallback = PROJECT_ROOT / ".env.example"
        if env_file.exists():
            load_dotenv(env_file, override=False)
        elif fallback.exists():
            load_dotenv(fallback, override=False)

        cache_dir = Path(os.environ.get("DATA_CACHE_DIR", "data_cache"))
        if not cache_dir.is_absolute():
            cache_dir = PROJECT_ROOT / cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        base_currency = os.environ.get("BASE_CURRENCY", "").strip().upper()
        return cls(
            data_cache_dir=cache_dir,
            brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
            base_currency=base_currency or DEFAULT_BASE_CURRENCY,
            sec_user_agent=os.environ.get("SEC_USER_AGENT", "").strip()
            or DEFAULT_SEC_USER_AGENT,
        )

    def redacted(self) -> dict[str, str]:
        return {
            "data_cache_dir": str(self.data_cache_dir),
            "brave_api_key": _mask(self.brave_api_key),
            "base_currency": self.base_currency,
            "sec_user_agent": self.sec_user_agent,
        }


def _mask(secret: str) -> str:
    if not secret:
        return "(not set)"
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}…{secret[-4:]}"
