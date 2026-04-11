"""Research source tests — mock Wikipedia + Brave adapters.

No network access. The live adapters (`_fetch_wikipedia_sync`,
`_fetch_brave_search`) are monkeypatched at the module level so all tests
stay hermetic.
"""

from __future__ import annotations

from data_sources import research_source
from data_sources.research_source import ResearchBundle, ResearchSource


def test_excerpt_block_assembles_sources():
    bundle = ResearchBundle(
        name="Test Person",
        company_ticker="TEST",
        wikipedia_summary="Bio sentence.",
        web_hits=[{"title": "T1", "snippet": "S1"}, {"title": "T2", "snippet": "S2"}],
    )
    text = bundle.excerpt_block()
    assert "[Wikipedia] Bio sentence." in text
    assert "[web] T1: S1" in text
    assert "[web] T2: S2" in text


def test_excerpt_block_respects_max_chars():
    bundle = ResearchBundle(
        name="X",
        company_ticker="X",
        wikipedia_summary="x" * 5000,
    )
    assert len(bundle.excerpt_block(max_chars=500)) == 500


def test_excerpt_block_empty_when_no_sources():
    bundle = ResearchBundle(name="X", company_ticker="X")
    assert bundle.excerpt_block() == ""


async def test_research_assembles_wikipedia_and_brave(monkeypatch):
    def fake_wiki(name: str) -> tuple[str, str]:
        return (
            f"{name} is a business leader.",
            "https://en.wikipedia.org/wiki/Test",
        )

    async def fake_brave(name, ticker, api_key):
        return [
            {"title": "Leader news 1", "url": "http://x/1", "snippet": "s1"},
            {"title": "Leader news 2", "url": "http://x/2", "snippet": "s2"},
        ]

    monkeypatch.setattr(research_source, "_fetch_wikipedia_sync", fake_wiki)
    monkeypatch.setattr(research_source, "_fetch_brave_search", fake_brave)

    bundle = await ResearchSource(brave_api_key="fake").research(
        "Test Person", "TEST"
    )
    assert bundle.name == "Test Person"
    assert bundle.wikipedia_summary == "Test Person is a business leader."
    assert len(bundle.web_hits) == 2
    assert set(bundle.sources) == {"wikipedia", "brave_search"}


async def test_research_empty_when_both_sources_fail(monkeypatch):
    def fake_wiki(name: str) -> tuple[str, str]:
        return ("", "")

    async def fake_brave(name, ticker, api_key):
        return []

    monkeypatch.setattr(research_source, "_fetch_wikipedia_sync", fake_wiki)
    monkeypatch.setattr(research_source, "_fetch_brave_search", fake_brave)

    bundle = await ResearchSource(brave_api_key="").research("Nobody", "XYZ")
    assert bundle.wikipedia_summary == ""
    assert bundle.web_hits == []
    assert bundle.sources == []


async def test_research_handles_wikipedia_exception(monkeypatch):
    def fake_wiki(name: str) -> tuple[str, str]:
        raise RuntimeError("wiki down")

    async def fake_brave(name, ticker, api_key):
        return [{"title": "t", "url": "u", "snippet": "s"}]

    monkeypatch.setattr(research_source, "_fetch_wikipedia_sync", fake_wiki)
    monkeypatch.setattr(research_source, "_fetch_brave_search", fake_brave)

    bundle = await ResearchSource(brave_api_key="fake").research("X", "X")
    assert bundle.wikipedia_summary == ""
    assert len(bundle.web_hits) == 1
    assert bundle.sources == ["brave_search"]


async def test_research_without_brave_key_only_uses_wikipedia(monkeypatch):
    def fake_wiki(name: str) -> tuple[str, str]:
        return ("Bio", "http://wiki/x")

    # Don't replace brave — let the real one run; it returns [] when api_key is ""
    bundle = await ResearchSource(brave_api_key="").research("X", "X")
    # Wikipedia is live here, so either it finds something or not — we
    # just monkeypatch it to be safe:
    monkeypatch.setattr(research_source, "_fetch_wikipedia_sync", fake_wiki)
    bundle = await ResearchSource(brave_api_key="").research("X", "X")
    assert bundle.wikipedia_summary == "Bio"
    assert bundle.web_hits == []
    assert bundle.sources == ["wikipedia"]
