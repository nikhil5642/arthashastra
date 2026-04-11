"""Simple JSON-backed cache for analyst state.

Writers: the engine (cache warmer) writes aggregate files.
Readers: CLI report generators read them.

Used for `scores.json`, `dip_candidates.json`, and similar key-keyed blobs.
Time-series data (daily closes) lives in `PriceHistoryStore`, not here.

The API is deliberately typed as `dict[str, object]` rather than
`dict[str, Any]`. `object` is the correct type for polymorphic JSON
values — callers have to narrow with `isinstance` or pass through a
typed `from_dict` constructor (see `fundamental_engine.extended_score`
and `fundamental_engine.dip_candidate`) before using the values. This
makes the file-boundary → typed-domain-object transition impossible
to skip by accident.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Alias for "a row of arbitrary JSON" — what sits inside `scores.json`
# under each ticker key. Public so callers can be explicit about the
# shape they're handling at the untyped boundary.
JsonRow = dict[str, object]
JsonBlob = dict[str, object]


class JsonStore:
    """File-backed key → value store for small JSON blobs.

    This is the **bottom-of-stack persistence primitive**. Any place
    that needs a typed view of the data should layer a typed record
    (ExtendedScore / DipCandidateRecord / Judgment) on top of this.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> JsonBlob:
        if not self._path.exists():
            return {}
        try:
            parsed = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("cache file %s corrupt — returning empty", self._path)
            return {}
        if not isinstance(parsed, dict):
            log.warning(
                "cache file %s top-level is %s, not a dict — returning empty",
                self._path,
                type(parsed).__name__,
            )
            return {}
        return parsed

    def write(self, data: JsonBlob) -> None:
        self._path.write_text(json.dumps(data, indent=2, default=str))

    def upsert(self, key: str, value: object) -> None:
        data = self.read()
        data[key] = value
        self.write(data)

    def get(self, key: str, default: object = None) -> object:
        return self.read().get(key, default)

    def delete(self, key: str) -> None:
        data = self.read()
        data.pop(key, None)
        self.write(data)
