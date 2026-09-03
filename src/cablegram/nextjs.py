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
* **It is one reader for three sections that do not stamp dates alike.** /news
  and /research write a full timestamp; /engineering writes the date on its own.
  A first version required the time and so found nothing there, which was
  written up as "its payload holds no post records — measured, 0 of 25". The
  page had all 25. What had been measured was the expression, not the page, and
  the sentence explaining it away is the expensive part: it tells the next
  reader not to look.
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
    # The time is optional. /news and /research stamp a full timestamp;
    # /engineering publishes the date alone, `"publishedOn":"2026-05-25"`, and
    # requiring the T rejected all 25 of its records. That was written up as
    # "its payload holds no post records — measured, 0 of 25", which was a
    # measurement of this expression rather than of the page.
    r'"publishedOn":"(?P<when>20\d\d-\d\d-\d\d[^"]*)",'
    r'"slug":\{"_type":"slug","current":"(?P<slug>[^"]+)"\}'
    r'(?P<mid>(?:(?!"publishedOn")[\s\S]){0,4000}?)'
    r'"title":"(?P<title>(?:[^"\\]|\\.)*)"'
)
_SUMMARY = re.compile(r'"summary":"(?P<text>(?:[^"\\]|\\.)*)"')

# A featured announcement. /news carries a "Newsroom Featured Grid" whose items
# are not posts: the record is `featuredGridLink`, its date is a bare day, and
# its URL is site-relative and not under the section. That is where Anthropic
# put "Introducing Claude Fable 5.1 and Claude Mythos 5.1" on 2026-09-01, and
# a reader that only took `post` records served 272 items without it. Measured
# from the model's side: wire_search "Fable 5.1" over a week found it on Hacker
# News, Product Hunt and TestingCatalog and not on anthropic — under a COVER
# line vouching that anthropic had been searched back to 2021. The one outlet
# that published the week's biggest launch was the one that could not see it.
_FEATURED = re.compile(
    r'"_type":"featuredGridLink",'
    r'"date":"(?P<when>20\d\d-\d\d-\d\d[^"]*)",'
    r'(?P<mid>(?:(?!"_type").){0,2000}?)'
    r'"title":"(?P<title>(?:[^"\\]|\\.)*)",'
    r'"url":"(?P<url>[^"]+)"'
)


def _unescape(raw: str) -> str:
    """The matched text is still JSON-escaped one level down."""
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def _when(raw: str) -> datetime | None:
    """UTC when the field says nothing about the zone.

    /engineering publishes `2026-05-25` with no time and no offset, and
    astimezone() on a naive datetime reads it in the machine's local zone: in
    CEST that lands on 2026-05-24T22:00Z and files the post a day early. The
    site stamps its other two sections in UTC, so UTC is what the bare date
    means — and guessing the reader's zone is not a property of the post.
    """
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


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
    origin = base.split("/", 3)[0] + "//" + base.split("/", 3)[2]
    entries: list[Entry] = []
    seen: set[str] = set()
    for card in _FEATURED.finditer(payload):
        url = card["url"]
        url = url if url.startswith("http") else origin + "/" + url.lstrip("/")
        if url in seen:
            continue
        seen.add(url)
        title = _unescape(card["title"]).strip()
        if not title:
            continue
        summary = _SUMMARY.search(card["mid"])
        body = _unescape(summary["text"]).strip() if summary else None
        entries.append(Entry(
            title=title,
            url=url,
            published=_when(card["when"]),
            body=body or None,
            body_src="summary" if body else None,
        ))
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
