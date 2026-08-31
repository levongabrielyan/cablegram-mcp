"""The sources, as data.

The set is fixed on purpose. A configurable aggregator is a different product:
this one is tuned for one question — what is happening in tech and AI today,
including the parts that reach English three days late.

Every entry was verified live. `tags` group them for filtering; `lang` tells the
reader which language to expect, since headlines are never translated.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Source", "SOURCES", "RETIRED", "by_id", "resolve"]


@dataclass(frozen=True, slots=True)
class Source:
    id: str
    name: str
    kind: str  # rss | hn | telegram | cls | hub | nextjs
    url: str
    lang: str  # en | zh | ru
    tags: tuple[str, ...] = ()
    note: str = ""
    aggregator: bool = False  # links out; show the destination host
    # For a `hub` source: one organisation's namespace instead of the global
    # trending listing. Empty means the global one.
    author: str = ""
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
        "anthropic", "Anthropic — news", "nextjs",
        "https://www.anthropic.com/news", "en",
        ("lab", "official"),
        "No feed exists, so this reads the data its own page ships inline: the "
        "CMS fields, with the real publication date and the headline Anthropic "
        "wrote. An internal Next.js format, so it can change without notice — "
        "which comes back as a broken source, not as a quiet week.",
        fragile=True,
    ),
    Source(
        "anthropic_research", "Anthropic — research", "nextjs",
        "https://www.anthropic.com/research", "en",
        ("lab", "official", "technical"),
        "The research section, which is a separate page and a separate request. "
        "Same reader as the news section, same fragility.",
        fragile=True,
    ),
    Source(
        "hub", "Hugging Face hub", "hub",
        "https://huggingface.co/api/models", "en",
        ("lab", "launches"),
        "Where open weights land, as opposed to the blog about them. Ordered by "
        "trend and not by date, because both date orderings return the firehose "
        "of every repo anyone touched. Somebody else's ranking, accepted "
        "knowingly: the score it was ordered on travels with each entry, beside "
        "the all-time like count, which is a different number and not sorted. "
        "A popularity list, so the six lab sources below cover what it misses.",
    ),

    # ── The labs, asked directly ──────────────────────────────────────────────
    #
    # Six organisations that publish weights and no feed of any kind, each read
    # from its own namespace on the hub, newest first. Not the trending list:
    # that one is a popularity list, and a release appears on it only if enough
    # people like it fast enough. Measured over seven days — these six published
    # thirteen models and the global top fifty carried five. Invisible to it:
    # tencent/ContextPilot in three sizes, both BF16 builds of GLM-5.3, and
    # tencent's smaller WeMM embeddings.
    #
    # There is no firehose to rank away inside one organisation's namespace, so
    # nothing here is ranked at all and the deviation the `hub` note declares
    # does not apply to these. Six requests, 2.6s together, and they run in
    # parallel with everything that is not Telegram.
    #
    # lang is `en`: the headline is a repository name in ASCII. These are
    # Chinese labs and their releases are not translated because there is
    # nothing to translate.
    Source(
        "deepseek", "DeepSeek", "hub",
        "https://huggingface.co/deepseek-ai", "en",
        ("lab", "launches", "weights"),
        "The lab whose Tuesday release started this module: "
        "V4-Flash-Vision-Exp reached the trending list the same day and "
        "would have been invisible if it had taken two.",
        author="deepseek-ai",
    ),
    Source(
        "qwen", "Qwen (Alibaba)", "hub",
        "https://huggingface.co/Qwen", "en",
        ("lab", "launches", "weights"),
        author="Qwen",
    ),
    Source(
        "zhipu", "Zhipu (Z.ai)", "hub",
        "https://huggingface.co/zai-org", "en",
        ("lab", "launches", "weights"),
        "Its BF16 builds of GLM-5.3 never reached the trending list.",
        author="zai-org",
    ),
    Source(
        "moonshot", "Moonshot AI", "hub",
        "https://huggingface.co/moonshotai", "en",
        ("lab", "launches", "weights"),
        author="moonshotai",
    ),
    Source(
        "minimax", "MiniMax", "hub",
        "https://huggingface.co/MiniMaxAI", "en",
        ("lab", "launches", "weights"),
        author="MiniMaxAI",
    ),
    Source(
        "tencent", "Tencent Hunyuan", "hub",
        "https://huggingface.co/tencent", "en",
        ("lab", "launches", "weights"),
        "Eight releases in seven days, three of which the trending list "
        "carried.",
        author="tencent",
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

# Dropped from the catalogue and still present in every archive already on disk.
# An archive is not rewritten when the catalogue changes — that is the point of
# it — so the renderer keeps meeting items from sources it can no longer look
# up, and printed them as `## vcru ??  1/17`: a Russian headline with no
# language, from something wire_sources does not list and the model cannot ask
# about. `??` breaks the one promise the server opens with, that every headline
# carries its language.
#
# Never returned by resolve(). These cannot be selected, only rendered.
RETIRED: tuple[Source, ...] = (
    Source(
        "vcru", "vc.ru", "rss",
        "https://vc.ru/rss", "ru",
        ("startups", "retired"),
        "Dropped: of seventeen items archived, two touched AI. The /ai/ feed "
        "404s and the general one was being used instead.",
    ),
    Source(
        "productradar", "Product Radar", "rss",
        "https://productradar.ru/rss/", "ru",
        ("launches", "retired"),
        "Dropped: answered 200 and had not published in 25 days — ten items in "
        "720. Product Hunt covers the same ground and is alive.",
    ),
)

_BY_ID = {s.id: s for s in SOURCES + RETIRED}


def by_id(source_id: str) -> Source | None:
    """Includes retired sources, because this is the renderer's lookup.

    An archived item still has a language whether or not its source is still in
    the catalogue. resolve() never sees these.
    """
    return _BY_ID.get(source_id)


def resolve(selectors: list[str] | None) -> tuple[Source, ...]:
    """Accept ids and tags in the same list: ``["early", "hn"]``.

    Nobody remembers twenty-two ids. Asking for a theme is the common case, so both
    resolve through one argument instead of two.
    """
    if not selectors:
        return SOURCES
    wanted = {s.lower() for s in selectors}
    return tuple(
        s for s in SOURCES
        if s.id in wanted or wanted & set(s.tags) or s.lang in wanted
    )
