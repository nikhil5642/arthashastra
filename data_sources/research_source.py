"""Leadership research source — live Wikipedia + optional Brave Search.

Python fetches the raw material (Wikipedia biography, web hit snippets) and
returns a ResearchBundle. Qualitative judgement — mode classification,
integrity veto, achievements — happens in Claude Code via the
`/research-founder` skill, not here.

No fixtures, no hardcoded data.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import TypedDict

import httpx

log = logging.getLogger(__name__)

BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
WIKIPEDIA_USER_AGENT = "fundamentalAnalyst/1.0 (research pipeline)"
HTTP_TIMEOUT_SECONDS = 15.0


class WebHit(TypedDict):
    """One Brave Search result, normalised to the three fields we use."""

    title: str
    url: str
    snippet: str


@dataclass(slots=True)
class ResearchBundle:
    name: str
    company_ticker: str = ""
    wikipedia_summary: str = ""
    wikipedia_url: str = ""
    web_hits: list[WebHit] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def excerpt_block(self, *, max_chars: int = 2000) -> str:
        blocks: list[str] = []
        if self.wikipedia_summary:
            blocks.append(f"[Wikipedia] {self.wikipedia_summary}")
        for hit in self.web_hits[:5]:
            blocks.append(f"[web] {hit['title']}: {hit['snippet']}")
        text = "\n\n".join(blocks)
        return text[:max_chars]


class ResearchSource:
    def __init__(self, *, brave_api_key: str | None = None) -> None:
        self._brave_api_key = (
            brave_api_key
            if brave_api_key is not None
            else os.environ.get("BRAVE_API_KEY", "")
        )

    async def research(
        self, name: str, company_ticker: str = ""
    ) -> ResearchBundle:
        log.info("research: live fetch for %s", name)
        loop = asyncio.get_running_loop()
        wiki_task = loop.run_in_executor(None, _fetch_wikipedia_sync, name)
        brave_task = _fetch_brave_search(name, company_ticker, self._brave_api_key)

        wiki_result, web_hits = await asyncio.gather(
            wiki_task, brave_task, return_exceptions=True
        )

        if isinstance(wiki_result, Exception):
            log.warning("wikipedia fetch failed for %s: %s", name, wiki_result)
            wiki_result = ("", "")
        if isinstance(web_hits, Exception):
            log.warning("brave search failed for %s: %s", name, web_hits)
            web_hits = []

        wiki_summary, wiki_url = wiki_result
        sources: list[str] = []
        if wiki_summary:
            sources.append("wikipedia")
        if web_hits:
            sources.append("brave_search")

        return ResearchBundle(
            name=name,
            company_ticker=company_ticker,
            wikipedia_summary=wiki_summary,
            wikipedia_url=wiki_url,
            web_hits=list(web_hits) if web_hits else [],
            sources=sources,
        )


def _fetch_wikipedia_sync(name: str) -> tuple[str, str]:
    try:
        import wikipediaapi
    except ImportError:
        log.warning("wikipedia-api not installed")
        return "", ""
    try:
        wiki = wikipediaapi.Wikipedia(
            user_agent=WIKIPEDIA_USER_AGENT, language="en"
        )
        page = wiki.page(name)
        if not page.exists():
            return "", ""
        return page.summary[:2500], page.fullurl
    except Exception as e:
        log.warning("wikipedia call failed for %s: %s", name, e)
        return "", ""


async def _fetch_brave_search(
    name: str, ticker: str, api_key: str
) -> list[WebHit]:
    if not api_key:
        log.debug("no BRAVE_API_KEY set — skipping web search")
        return []

    query_parts = [f'"{name}"']
    if ticker:
        query_parts.append(ticker)
    query_parts.append("CEO OR chairman OR founder")
    query = " ".join(query_parts)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                BRAVE_API_URL,
                params={"q": query, "count": 10},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("brave search HTTP call failed: %s", e)
        return []

    results = data.get("web", {}).get("results", [])[:10]
    return [
        WebHit(
            title=r.get("title", "") or "",
            url=r.get("url", "") or "",
            snippet=r.get("description", "") or "",
        )
        for r in results
    ]
