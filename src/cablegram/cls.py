"""cls.cn — 财联社, a Chinese financial wire, and the source with the most to lose.

Its reporters hear from suppliers and investors, so model launches surface here
days before the official English post. It is also the most fragile thing in the
project: an internal API, signed, undocumented, reverse-engineered. The README
says so, and wire_sources marks it fragile, because a source that can break
without notice should say so before it does.

Two properties shape this module:

* **rn=100 covers 3.34 days and there is no way to page backwards.** Eighteen
  pagination parameters were tried against the live endpoint and every one is
  ignored. Ask for more than three days and the rest is simply not there, and
  no endpoint anywhere will serve it later.
* **A rejected signature returns HTTP 200.** The error is inside the envelope,
  so anything built on the status code reads a broken source as a quiet one.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlencode

from .rss import MAX_FIELD, Entry

__all__ = ["rows_returned", "CLS_BASE", "AI_SUBJECT", "signed_url", "parse_response", "feed_url"]

CLS_BASE = "https://www.cls.cn"
AI_SUBJECT = 1321  # 人工智能. Verified: 100 of 100 items carry it in `subjects`.
MAX_ROWS = 100  # rn=150, 200, 500 and 1000 all return 100

# `sv` is not validated — 7.7.5, 8.4.6 and 9.9.9 all answer errno=0 — so the
# client version cannot expire underneath this.
_BASE_PARAMS = {"appName": "CailianpressWeb", "os": "web", "sv": "8.7.9"}


def signed_url(path: str, extra: dict | None = None) -> str:
    """Sign a request the way the site's own client does.

    md5 of the sha1 hexdigest *as text*, over the sorted query string. The
    signature covers the parameters only, not the path — verified by two
    different endpoints producing the same sign for the same parameters.
    """
    params = dict(_BASE_PARAMS)
    params.update({k: str(v) for k, v in (extra or {}).items() if v is not None})
    query = urlencode(sorted(params.items()))
    sign = hashlib.md5(hashlib.sha1(query.encode()).hexdigest().encode()).hexdigest()
    return f"{CLS_BASE}{path}?{query}&sign={sign}"


def feed_url(subject: int = AI_SUBJECT, rows: int = MAX_ROWS) -> str:
    return signed_url(f"/api/subject/{subject}/article",
                      {"Subject_Id": subject, "rn": min(rows, MAX_ROWS)})


def _headline_and_body(item: dict) -> tuple[str, str | None]:
    """Split the dispatch into its headline and the rest.

    `article_title` is not a title. For a telegram — 62 of 100 items — it holds
    the entire dispatch: median 241 characters, up to 617. The real headline is
    the part inside 【】, and extracting it matched the article's own title
    field in every case checked.

    Storing the raw field would put a body into item.title, into sighting.title
    and into the trigram index, and cost 60,000 tokens for a page where 3,600
    will do.
    """
    raw = (item.get("article_title") or "").strip()
    if raw.startswith("【") and "】" in raw:
        head, _, rest = raw[1:].partition("】")
        body = rest.strip()[:MAX_FIELD] or None
        return head.strip()[:MAX_FIELD], body

    # The six telegrams without brackets are short, and their whole text is the
    # title on the article's own page too.
    brief = (item.get("article_brief") or "").strip()
    return raw[:MAX_FIELD], brief[:MAX_FIELD] or None


def rows_returned(payload: dict) -> int:
    """Articles the envelope carried, before any are dropped.

    Measured before filtering on purpose, and it matters most here: cls.cn
    cannot page backwards at all, so the AT CEILING marker is the only warning
    that something past its hundred is gone for good. One article without an
    `article_time` took the count to 99 and the warning never appeared.
    """
    if not isinstance(payload, dict):
        return 0
    data = payload.get("data") or []
    if isinstance(data, dict):
        data = data.get("roll_data") or data.get("depth_list") or []
    return len(data) if isinstance(data, list) else 0


def parse_response(payload: dict) -> list[Entry]:
    """Turn one API response into entries. Raises on an error envelope.

    `errno` is 0 as an int on success and '10012' as a string on failure, so
    int(errno) raises in exactly the case this check exists to catch. It is
    compared as text.
    """
    if not isinstance(payload, dict) or "errno" not in payload:
        raise ValueError("cls.cn response has no errno envelope; the schema changed")

    errno = str(payload.get("errno"))
    if errno != "0":
        msg = payload.get("msg") or ""
        raise ValueError(
            f"cls.cn refused the request: errno={errno} {msg}. "
            f"errno=10012 is a rejected signature, which arrives as HTTP 200 — "
            f"the signing scheme has changed."
        )

    data = payload.get("data") or []
    if isinstance(data, dict):
        # The subject endpoint returns a list; /api/cache and the roll list put
        # it under roll_data. One adapter, both shapes.
        data = data.get("roll_data") or data.get("depth_list") or []

    entries: list[Entry] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        article_id = item.get("article_id")
        # `ctime` on every endpoint but the subject one, which says article_time.
        stamp = item.get("article_time")
        if stamp is None:
            stamp = item.get("ctime")
        # `is None`, not falsy: an id of 0 or a timestamp at the epoch are
        # values, and testing for truth would drop them without a word.
        if article_id is None or stamp is None:
            # No fallback to capture time here. Ordering by timestamp is the only
            # pagination this source has, so an undated item breaks the
            # incremental stop as well as its own placement.
            continue

        title, body = _headline_and_body(item)
        if not title:
            continue

        entries.append(Entry(
            title=title,
            # No field holds a usable address: jump_url is an app scheme and
            # share_url is a share landing page. This one opens.
            url=f"{CLS_BASE}/detail/{article_id}",
            published=datetime.fromtimestamp(int(stamp), timezone.utc),
            body=body,
            body_src="article_title" if body else None,
        ))
    return entries
