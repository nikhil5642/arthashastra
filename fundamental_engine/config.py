"""Analyst environment configuration.

Loaded from `.env.{ANALYST_ENV}` via python-dotenv. Claude-Code-native,
so there is no LLM API key / cost budget / mock mode — the analyst does
not call an LLM from Python. The only optional secret is `BRAVE_API_KEY`
for leadership web research; everything else runs fine without it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

VALID_ENVS = {"sandbox", "live"}


@dataclass(slots=True)
class AnalystConfig:
    analyst_env: str
    data_cache_dir: Path
    brave_api_key: str

    @classmethod
    def from_env(cls) -> "AnalystConfig":
        env = os.environ.get("ANALYST_ENV", "sandbox").strip().lower()
        if env not in VALID_ENVS:
            raise ValueError(
                f"ANALYST_ENV must be one of {sorted(VALID_ENVS)}, got {env!r}"
            )

        env_file = PROJECT_ROOT / f".env.{env}"
        if env_file.exists():
            load_dotenv(env_file, override=False)
        else:
            fallback = PROJECT_ROOT / ".env.example"
            if fallback.exists():
                load_dotenv(fallback, override=False)

        cache_dir = Path(os.environ.get("DATA_CACHE_DIR", "data_cache"))
        if not cache_dir.is_absolute():
            cache_dir = PROJECT_ROOT / cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            analyst_env=env,
            data_cache_dir=cache_dir,
            brave_api_key=os.environ.get("BRAVE_API_KEY", ""),
        )

    def redacted(self) -> dict:
        return {
            "analyst_env": self.analyst_env,
            "data_cache_dir": str(self.data_cache_dir),
            "brave_api_key": _mask(self.brave_api_key),
        }


def _mask(secret: str) -> str:
    if not secret:
        return "(not set)"
    if len(secret) <= 8:
        return "****"
    return f"{secret[:4]}…{secret[-4:]}"
