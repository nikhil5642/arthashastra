"""Simple JSON-backed cache for analyst state.

Writers: the engine (cache warmer) writes aggregate files.
Readers: CLI report generators read them.

Used for `scores.json`, `dip_candidates.json`, and similar key-keyed blobs.
Time-series data (daily closes) lives in `PriceHistoryStore`, not here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class JsonStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("cache file %s corrupt — returning empty", self._path)
            return {}

    def write(self, data: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, indent=2, default=str))

    def upsert(self, key: str, value: Any) -> None:
        data = self.read()
        data[key] = value
        self.write(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.read().get(key, default)

    def delete(self, key: str) -> None:
        data = self.read()
        data.pop(key, None)
        self.write(data)
