"""Preview — how many of the rubric's marks does each listing in a screen clear?

A screen can match hundreds of companies and only a few dozen can be
scored in full. The preview decides which, without looking at size: for
every mark (`scorers.fundamentals_scorer.marks`) it asks Yahoo three
set-membership questions about the screen's listings —

    which have a value at all?   which clear `good`?   which clear `excellent`?

— and gives each listing the rubric-weighted share of the marks it clears,
0 to 100, over the marks Yahoo knows a value for. An unknown is left out,
not counted as a miss: small companies have more gaps in Yahoo's data, and
a gap is not a weakness.

It is not a strength either. `coverage` says how much of the marks' weight
the score rests on, and a preview resting on less than `MIN_COVERAGE` is
`thin`: a month-old listing with one known ratio — its cash-rich current
ratio — would otherwise outrank everything on a perfect 100.

Three questions per mark is a couple of dozen small requests for a whole
screen, against ten per company to score one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace

from data_sources import universe_source
from data_sources.universe_source import Bound, Criteria
from scorers.fundamentals_scorer.marks import Mark

MAX_CONCURRENT_QUESTIONS = 4
# Below this share of the marks' weight a preview is too thin to rank on —
# the same line the scorers draw for a low-confidence score.
MIN_COVERAGE = 0.6
_GOOD_LEVEL, _EXCELLENT_LEVEL = 0.5, 1.0  # the share of a mark's weight each level earns


@dataclass(frozen=True, slots=True)
class Preview:
    score: float  # share of the known marks' weight cleared, 0–100
    coverage: float  # share of all the marks' weight Yahoo has a value for, 0–1

    @property
    def thin(self) -> bool:
        """Too little is known for the score to be compared with a well-covered one."""
        return self.coverage < MIN_COVERAGE


async def preview(
    criteria: Criteria, symbols: Collection[str], marks: Sequence[Mark]
) -> dict[str, Preview] | None:
    """Each symbol's preview; None when Yahoo did not answer every question.

    A symbol Yahoo knows no mark for is left out of the result.
    """
    gate = asyncio.Semaphore(MAX_CONCURRENT_QUESTIONS)

    async def ask(bound: Bound) -> set[str] | None:
        async with gate:
            return await universe_source.fetch_symbols(
                replace(criteria, bounds=(*criteria.bounds, bound))
            )

    answers = await asyncio.gather(
        *(ask(bound) for mark in marks for bound in _questions(mark))
    )
    if any(answer is None for answer in answers):
        return None

    earned = dict.fromkeys(symbols, 0.0)
    possible = dict.fromkeys(symbols, 0.0)
    for i, mark in enumerate(marks):
        known, good, excellent = (answers[3 * i + k] or set() for k in range(3))
        for symbol in symbols:
            if symbol not in known:
                continue
            possible[symbol] += mark.weight
            if symbol in excellent:
                earned[symbol] += mark.weight * _EXCELLENT_LEVEL
            elif symbol in good:
                earned[symbol] += mark.weight * _GOOD_LEVEL
    total = sum(mark.weight for mark in marks)
    return {
        symbol: Preview(
            score=round(earned[symbol] / possible[symbol] * 100, 1),
            coverage=round(possible[symbol] / total, 2),
        )
        for symbol in symbols
        if possible[symbol] > 0
    }


def _questions(mark: Mark) -> tuple[Bound, Bound, Bound]:
    """Known at all · clears `good` · clears `excellent`."""
    if mark.higher_is_better:
        return (
            Bound(mark.measure),
            Bound(mark.measure, at_least=mark.good),
            Bound(mark.measure, at_least=mark.excellent),
        )
    return (
        Bound(mark.measure),
        Bound(mark.measure, at_most=mark.good),
        Bound(mark.measure, at_most=mark.excellent),
    )
