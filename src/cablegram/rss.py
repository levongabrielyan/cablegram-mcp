"""RSS and Atom parsing, using only the standard library.

Written by hand rather than pulling in feedparser because the feed set is fixed
and known: eleven feeds, all verified. A dependency earns its place when it
encapsulates knowledge that shifts or that fails silently — neither applies to
eleven URLs that have to keep working anyway.

Parsing is pure: it takes bytes and returns entries. No network, no clock, so it
can be tested against saved samples forever.
"""

from __future__ import annotations

import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

__all__ = ["Entry", "parse_feed"]

_ATOM = "{http://www.w3.org/2005/Atom}"
_RSS1 = "{http://purl.org/rss/1.0/}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_DC = "{http://purl.org/dc/elements/1.1/}"

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Entry:
    title: str
    url: str
    published: datetime | None  # None when the feed gave nothing usable
    body: str | None
    body_kind: str | None = None  # 'full' | 'teaser' | None


def _text(node: ET.Element | None) -> str:
    """Raw text. Deliberately does NOT unescape.

    ElementTree already resolved the XML entities once. A second pass would
    expand HTML5 legacy entities that need no semicolon, so a link containing
    `&amp;copy=` would come out as `©` — a broken URL and a wrong id, in the
    part of the system that is frozen. Unescaping belongs to prose only.
    """
    if node is None:
        return ""
    raw = "".join(node.itertext())
    return _SPACES.sub(" ", unicodedata.normalize("NFC", raw)).strip()


def _prose(raw: str) -> str:
    """Same as _text but for human-readable fields, where entities must resolve."""
    return _SPACES.sub(" ", html.unescape(raw)).strip()


def _strip_html(raw: str) -> str:
    """Unescape, strip tags, unescape again: feeds double-encode routinely."""
    return _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", html.unescape(raw)))).strip()


def _parse_date(raw: str) -> datetime | None:
    """Feeds date things in at least four ways. Guessing wrong is worse than not knowing.

    A wrong timestamp puts an item in the wrong day silently; a missing one is
    marked and the reader knows not to trust it.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:  # RFC-822: "Sat, 30 Aug 2026 06:40:00 +0000" — the RSS 2.0 form
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:  # ISO-8601: "2026-08-30T06:40:00Z" — the Atom form
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _reject_entity_bombs(raw: bytes) -> None:
    """Custom XML entities expand geometrically: a few hundred bytes become gigabytes.

    Feeds come from third parties, so this input is hostile by default.

    The check reads the DOCTYPE's internal subset rather than a fixed window of
    bytes. A byte window is defeated by padding the prologue, and it also fires
    on any article that merely mentions <!ENTITY — taking a whole source down
    for the day. Both were real: the first was demonstrated with 8.3 KB of
    comment, the second is a plain WordPress feed declaring &nbsp;.
    """
    start = raw.upper().find(b"<!DOCTYPE")
    if start == -1:
        return
    opening = raw.find(b"[", start)
    if opening == -1:
        return  # external DTD only; ElementTree does not fetch it
    closing = raw.find(b"]", opening)
    subset = raw[opening : closing if closing != -1 else len(raw)]
    if b"<!ENTITY" in subset.upper():
        raise ValueError("feed declares custom XML entities; refusing to expand them")


# Where a feed puts the whole article, and where it puts the first paragraph.
# Only the parser can tell them apart: by the time an item is stored, "full" and
# "teaser" look alike, and guessing from length gets long teasers wrong.
_FULL_BODY = (f"{_CONTENT}encoded", f"{_ATOM}content")
_TEASER_BODY = ("description", f"{_RSS1}description", f"{_ATOM}summary")


def _body(item: ET.Element) -> tuple[str | None, str | None]:
    for paths, kind in ((_FULL_BODY, "full"), (_TEASER_BODY, "teaser")):
        for path in paths:
            if text := _strip_html(_text(item.find(path))):
                return text, kind
    return None, None


def _first(item: ET.Element, *paths: str) -> str:
    for path in paths:
        value = _text(item.find(path))
        if value:
            return value
    return ""


def _link(item: ET.Element) -> str:
    for path in ("link", f"{_RSS1}link"):
        if url := _text(item.find(path)):
            return url
    # Atom puts it in an attribute, and may list several rels.
    links = item.findall(f"{_ATOM}link")
    for rel in ("alternate", None):
        for node in links:
            if node.get("rel", "alternate") == (rel or node.get("rel", "alternate")):
                if href := node.get("href", "").strip():
                    return href
    return _text(item.find("guid"))


def parse_feed(raw: bytes) -> list[Entry]:
    """Return the entries of an RSS 2.0 or Atom document.

    Malformed XML raises; a malformed *entry* is skipped. One bad item in a feed
    of forty should cost that item, not the other thirty-nine.
    """
    _reject_entity_bombs(raw)
    root = ET.fromstring(raw)

    # RSS 1.0 keeps <item> inside a namespace, so the plain search misses it and
    # returns nothing at all — a source could switch format and go mute for
    # months without raising anything.
    items = (
        root.findall(".//item")
        or root.findall(f".//{_RSS1}item")
        or root.findall(f".//{_ATOM}entry")
    )

    entries: list[Entry] = []
    for item in items:
        title = _prose(_first(item, "title", f"{_RSS1}title", f"{_ATOM}title"))
        url = _link(item)
        if not title or not url:
            continue

        body, body_kind = _body(item)
        published = _parse_date(
            _first(item, "pubDate", f"{_DC}date", f"{_ATOM}published", f"{_ATOM}updated")
        )
        entries.append(Entry(title, url, published, body, body_kind))

    return entries
