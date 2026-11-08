"""Company-name normalisation shared by search-style data sources.

Search APIs match on how people write a company's name, not on its legal
name. Yahoo's search returns nothing for "reliance ltd", and an exact-
phrase news query for "Reliance Industries Limited" misses every headline
that just says "Reliance Industries". Both the ticker resolver and the
news source clean names through here so they agree on one rule.
"""

from __future__ import annotations

import re

# Legal-form suffixes that carry no search signal. Matched as whole
# trailing words, case-insensitively, after punctuation is stripped.
_SUFFIX_WORDS: frozenset[str] = frozenset(
    {
        "limited",
        "ltd",
        "corporation",
        "corp",
        "company",
        "co",
        "inc",
        "incorporated",
        "plc",
        "llp",
        "pvt",
        "private",
    }
)

# Bracketed qualifiers screener / Yahoo append, e.g. "(Consolidated)".
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")

# A cleaned name this short ("ITC", "SBI") is too ambiguous to search on
# by itself, so `news_query_name` keeps the legal suffix for precision.
_MIN_STANDALONE_LENGTH = 5


def clean_company_name(name: str) -> str:
    """Strip punctuation, bracketed qualifiers, and trailing legal suffixes."""
    text = _PARENTHETICAL_RE.sub(" ", name or "")
    text = re.sub(r"[^\w\s&-]", " ", text).strip()
    words = text.split()
    while words and words[-1].lower() in _SUFFIX_WORDS:
        words.pop()
    return " ".join(words)


def news_query_name(name: str) -> str:
    """The form of a company name to use as an exact-phrase news query."""
    cleaned = clean_company_name(name)
    if len(cleaned) < _MIN_STANDALONE_LENGTH:
        return _PARENTHETICAL_RE.sub(" ", name or "").strip() or cleaned
    return cleaned
