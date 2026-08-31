"""Anthropic's posts, read out of the payload its own pages already ship.

Anthropic publishes no feed and does not advertise one, and thirteen guesses at
a feed URL all answered 404. The first attempt at closing that hole read the
sitemap, and a sitemap turned out to be the wrong document for the one question
this server asks — *when was this published*. `lastmod` is when the CMS last
touched the page, so a week's window returned seventy-seven Anthropic items for
a week in which Anthropic published five: *Toy models of superposition*, from
September 2022, served as news. Marking every date approximate did not help,
because it removed no items — 77 stayed 77 — and a listing of seventy-two false
positives does not become useful for admitting it is unreliable. It displaces
the sources that are telling the truth, out of the same token budget.

The pages themselves carry the real dates. Anthropic's site is a Next.js app,
and the App Router ships its server-rendered data inline as a series of
`self.__next_f.push([1,"..."])` chunks. Concatenated and decoded, those hold one
record per post: `publishedOn`, `slug`, `title`, and often `summary` — the CMS
fields, which is what a feed would have carried if there were one.

Measured against the live site: two requests, ~1.6s, 270 posts from /news back
to 2021 and 160 from /research. Fourteen sampled at random were checked against
the articles' own pages: 14 of 14 exact on both the date and the headline.

Two things this is not:

* **It is not a published interface.** `self.__next_f` is Next.js's internal
  streaming format. It has no contract, no version, and can change on any
  deploy of anthropic.com. That is what `fragile` is for, and it is why the
  source is marked so: a shape change comes back as `parsed-empty`, which the
  poller reports as a broken source rather than as a quiet week.
* **It does not reach /engineering.** That section renders from a different
  template and its payload holds no post records — measured, 0 of 25. Those
  twenty-five pages are not covered by this, and their dates in the sitemap
  were wrong anyway.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .rss import Entry

__all__ = ["parse_next_payload"]

# Each chunk is a JSON string literal holding a slice of the payload. They are
# concatenated before matching because a record can straddle two of them.
_CHUNK = re.compile(rb'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', re.S)

# One post record. `mid` is tempered so it cannot cross into the next record:
# an untitled post would otherwise borrow the following one's headline, and the
# result would be a real date under somebody else's title, which is the one
# outcome worse than no entry at all.
_POST = re.compile(
    r'"publishedOn":"(?P<when>20\d\d-\d\d-\d\dT[^"]*)",'
    r'"slug":\{"_type":"slug","current":"(?P<slug>[^"]+)"\}'
    r'(?P<mid>(?:(?!"publishedOn")[\s\S]){0,4000}?)'
    r'"title":"(?P<title>(?:[^"\\]|\\.)*)"'
)
_SUMMARY = re.compile(r'"summary":"(?P<text>(?:[^"\\]|\\.)*)"')


def _unescape(raw: str) -> str:
    """The matched text is still JSON-escaped one level down."""
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def _when(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_next_payload(raw: bytes, *, base: str) -> list[Entry]:
    """Entries for the posts one section page lists.

    `base` is the section URL the document came from, because the payload gives
    a slug and not a link. A page with no post records yields nothing, which the
    poller files as parsed-empty — a source that changed shape, not a quiet
    week. That is the correct reading for an undocumented format.
    """
    payload = "".join(
        json.loads(chunk.decode("utf-8", "replace")) for chunk in _CHUNK.findall(raw)
    )

    base = base.rstrip("/")
    entries: list[Entry] = []
    seen: set[str] = set()
    for post in _POST.finditer(payload):
        slug = post["slug"]
        # A featured post is listed twice, once in the hero block and once in
        # the body: 4 of 274 on /news, 5 of 165 on /research, same record both
        # times.
        if slug in seen:
            continue
        seen.add(slug)
        title = _unescape(post["title"]).strip()
        if not title:
            continue
        summary = _SUMMARY.search(post["mid"])
        body = _unescape(summary["text"]).strip() if summary else None
        entries.append(Entry(
            title=title,
            url=f"{base}/{slug}",
            published=_when(post["when"]),
            body=body or None,
            body_src="summary" if body else None,
        ))
    return entries
