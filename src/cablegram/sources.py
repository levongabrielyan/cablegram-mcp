"""The nineteen sources, as data.

The set is fixed on purpose. A configurable aggregator is a different product:
this one is tuned for one question — what is happening in tech and AI today,
including the parts that reach English three days late.

Every entry was verified live. `tags` group them for filtering; `lang` tells the
reader which language to expect, since headlines are never translated.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Source", "SOURCES", "by_id", "resolve"]


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    name: str
    kind: str  # rss | hn | telegram | cls | hub | sitemap
    url: str
    lang: str  # en | zh | ru
    tags: tuple[str, ...] = ()
    note: str = ""
    aggregator: bool = False  # links out; show the destination host
    # Reverse-engineered rather than published, so it can break without notice.
    # Declared in the output before it happens rather than explained after.
    fragile: bool = False


SOURCES: tuple[Source, ...] = (
    # ── English ──────────────────────────────────────────────────────────────
    Source(
        "testingcatalog", "TestingCatalog", "rss",
        "https://www.testingcatalog.com/rss/", "en",
        ("early", "leaks"),
        "Unreleased features found in production builds, often before the announcement.",
    ),
    Source(
        "alternativeto", "AlternativeTo", "rss",
        "https://alternativeto.net/news/feed/", "en",
        ("launches",),
        "Product launches and changes.",
    ),
    Source(
        "hn", "Hacker News", "hn",
        "https://hn.algolia.com/api/v1/search_by_date", "en",
        ("community", "searchable"),
        "The only source that can be searched at its origin. 10k requests/hour, no key.",
        aggregator=True,
    ),
    Source(
        "openai", "OpenAI", "rss",
        "https://openai.com/news/rss.xml", "en",
        ("lab", "official"),
    ),
    Source(
        "deepmind", "Google DeepMind", "rss",
        "https://deepmind.google/blog/rss.xml", "en",
        ("lab", "official"),
    ),
    Source(
        "huggingface", "Hugging Face", "rss",
        "https://huggingface.co/blog/feed.xml", "en",
        ("lab", "community"),
        "Headlines only: the feed carries no description at all. Verified against "
        "the raw XML, so an empty body here is the source, not a parser bug.",
    ),
    Source(
        "n8n", "n8n blog", "rss",
        "https://blog.n8n.io/rss/", "en",
        ("automation", "official"),
    ),
    Source(
        "mcp", "Model Context Protocol", "rss",
        "https://blog.modelcontextprotocol.io/index.xml", "en",
        ("official", "technical"),
        "The protocol this server speaks. /rss.xml, /atom.xml and /feed.xml all "
        "404 — /index.xml is the one that answers.",
    ),
    Source(
        "producthunt", "Product Hunt", "rss",
        "https://www.producthunt.com/feed", "en",
        ("launches",),
        "What shipped and is being charged for, which no other source here "
        "covers. Not AI-only: most of a day is not about this.",
    ),
    Source(
        "anthropic", "Anthropic", "sitemap",
        "https://www.anthropic.com/sitemap.xml", "en",
        ("lab", "official"),
        "No feed exists, so this reads the sitemap. Headlines are derived from "
        "the URL slug and are not the page's own words.",
    ),
    Source(
        "hub", "Hugging Face hub", "hub",
        "https://huggingface.co/api/models", "en",
        ("lab", "launches"),
        "Where open weights land, as opposed to the blog about them. Ordered by "
        "trend and not by date, because both date orderings return the firehose "
        "of every repo anyone touched. Somebody else's ranking, accepted "
        "knowingly: the score it was ordered on travels with each entry, beside "
        "the all-time like count, which is a different number and not sorted.",
    ),

    # ── Chinese ──────────────────────────────────────────────────────────────
    Source(
        "cls", "财联社 Cailianpress", "cls",
        "https://www.cls.cn/api/subject/1321/article", "zh",
        ("early", "finance"),
        "A financial wire, not an AI outlet. Its reporters hear from suppliers and "
        "investors, so model launches surface here days before the official post. "
        "Undocumented internal API with a signed request: holds 3.34 days at most "
        "and cannot page backwards, so a gap in polling is permanent.",
        fragile=True,
    ),
    Source(
        "qbitai", "量子位 QbitAI", "rss",
        "https://www.qbitai.com/feed", "zh",
        ("community",),
        "Redirects from /feed/ — use the path without the trailing slash.",
    ),
    Source(
        "kr36", "36氪 36Kr", "rss",
        "https://www.36kr.com/feed", "zh",
        ("startups", "finance"),
    ),

    # ── Russian ──────────────────────────────────────────────────────────────
    Source(
        "habr", "Habr — AI hub", "rss",
        "https://habr.com/ru/rss/hub/artificial_intelligence/all/", "ru",
        ("community", "technical"),
    ),

    # ── Telegram: public HTML, no account, no API ─────────────────────────────
    Source(
        "ai_newz", "@ai_newz", "telegram",
        "https://t.me/s/ai_newz", "ru",
        ("telegram", "researcher"),
        "Artiom Sanakoev, ex-Meta GenAI.",
    ),
    Source(
        "denissexy", "@denissexy", "telegram",
        "https://t.me/s/denissexy", "ru",
        ("telegram",),
        "Hands-on model testing.",
    ),
    Source(
        "data_secrets", "@data_secrets", "telegram",
        "https://t.me/s/data_secrets", "ru",
        ("telegram",),
    ),
    Source(
        "seeallochnaya", "@seeallochnaya", "telegram",
        "https://t.me/s/seeallochnaya", "ru",
        ("telegram", "papers"),
    ),
    Source(
        "llm_under_hood", "@llm_under_hood", "telegram",
        "https://t.me/s/llm_under_hood", "ru",
        ("telegram", "technical"),
        "Agent architecture and running costs.",
    ),
    Source(
        "techsparks", "@techsparks", "telegram",
        "https://t.me/s/techsparks", "ru",
        ("telegram",),
    ),
)

_BY_ID = {s.id: s for s in SOURCES}


def by_id(source_id: str) -> Source | None:
    return _BY_ID.get(source_id)


def resolve(selectors: list[str] | None) -> tuple[Source, ...]:
    """Accept ids and tags in the same list: ``["early", "hn"]``.

    Nobody remembers nineteen ids. Asking for a theme is the common case, so both
    resolve through one argument instead of two.
    """
    if not selectors:
        return SOURCES
    wanted = {s.lower() for s in selectors}
    return tuple(
        s for s in SOURCES
        if s.id in wanted or wanted & set(s.tags) or s.lang in wanted
    )
