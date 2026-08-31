"""Hacker News through the Algolia index. No key, no auth, 10,000 requests an hour.

It earns its place twice over. It is the only source that can be searched at
its origin instead of in the local archive, and it is the only one that links
out — so the same URL arriving here and on a Chinese feed is one story seen
twice, which is where the cross-source count comes from.

Two properties decide the code below, both measured against the live endpoint:

* **The `url` key is absent, not null.** In 30 of 30 Ask HN items the string
  "url" does not appear anywhere in the object. `hit["url"]` raises KeyError on
  every one of them, and Ask HN is where people say what they actually use.
* **1,000 results per query, whatever `nbHits` claims.** Past that the API
  returns `hits: []` with HTTP 200 and no error — so a window too wide comes
  back empty and looks like a quiet day. The window is filtered server-side and
  kept narrow.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from .rss import MAX_FIELD, Entry

__all__ = ["rows_returned", "parse_search", "search_url", "ALGOLIA", "MAX_ROWS"]

ALGOLIA = "https://hn.algolia.com/api/v1"
MAX_ROWS = 1000  # asking for more is capped in silence, not refused
THREAD = "https://news.ycombinator.com/item?id={}"

_TAGS = re.compile(r"<[^>]+>")


def search_url(*, since: int, query: str | None = None, rows: int = 100) -> str:
    """Build one query.

    `search_by_date` for a plain window and `search` for a term: relevance
    ordering answers a different question, and would return old stories for
    "what happened today".

    The window is a numericFilter, applied by the server. Filtering afterwards
    would be filtering what survived the 1,000-result ceiling, which is not the
    same thing and cannot be told apart from there being nothing more.
    """
    endpoint = "search" if query else "search_by_date"
    params = {
        "tags": "story",  # comments outnumber stories and would eat the ceiling
        "numericFilters": f"created_at_i>{int(since)}",
        "hitsPerPage": min(rows, MAX_ROWS),
    }
    if query:
        params["query"] = query
    return f"{ALGOLIA}/{endpoint}?{urlencode(params)}"


def _text(raw: str | None) -> str | None:
    """story_text is HTML with escaped entities: &#x27;, &quot;, &#x2F;, <p>."""
    if not raw:
        return None
    stripped = html.unescape(_TAGS.sub(" ", html.unescape(raw)))
    return " ".join(stripped.split())[:MAX_FIELD] or None


def rows_returned(payload: dict) -> int:
    """Hits the endpoint sent, which is not how many become entries.

    The ceiling means "it returned all it could", so it has to be measured
    before filtering: one Ask HN with no objectID took the count from 1000 to
    999 and the marker stopped firing for the whole pass.
    """
    hits = payload.get("hits") if isinstance(payload, dict) else None
    return len(hits) if isinstance(hits, list) else 0


def parse_search(payload: dict) -> list[Entry]:
    """Turn one Algolia response into entries.

    An empty `hits` is a normal outcome — it is also what the ceiling returns —
    so it is not an error. A response with no `hits` key at all is: that shape
    means the schema moved, and it must not read as no news.
    """
    if not isinstance(payload, dict) or "hits" not in payload:
        raise ValueError("Hacker News response carries no `hits`; the schema changed")

    entries: list[Entry] = []
    for hit in payload["hits"]:
        title = (hit.get("title") or "").strip()
        stamp = hit.get("created_at_i")
        object_id = hit.get("objectID")
        # `is None` for the numbers: an id of 0 or a timestamp at the epoch are
        # values, and testing for truth would drop them silently.
        if not title or stamp is None or object_id is None:
            continue

        # .get, never [..]: the key is missing entirely on every Ask HN, and
        # dropping those would remove the part of this source worth having.
        # The external URL is preferred because it is what makes one story
        # visible across several feeds.
        entries.append(Entry(
            title=title[:MAX_FIELD],
            url=(hit.get("url") or "").strip() or THREAD.format(object_id),
            published=datetime.fromtimestamp(int(stamp), timezone.utc),
            body=_text(hit.get("story_text")),
            body_src="story_text" if hit.get("story_text") else None,
        ))
    return entries
