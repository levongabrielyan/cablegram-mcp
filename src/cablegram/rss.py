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


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    raw = html.unescape("".join(node.itertext()))
    return _SPACES.sub(" ", unicodedata.normalize("NFC", raw)).strip()


def _strip_html(raw: str) -> str:
    """Unescape after stripping: feeds double-encode, so &amp;nbsp; needs both passes."""
    return _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", raw))).strip()


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
    """Custom XML entities expand geometrically. A few hundred bytes become gigabytes.

    Feeds are served by third parties, so this input is hostile by default. No
    legitimate RSS feed declares its own entities, and refusing them costs
    nothing while keeping the standard library instead of a new dependency.
    """
    head = raw[:8192].upper()
    if b"<!ENTITY" in head:
        raise ValueError("feed declares custom XML entities; refusing to expand them")


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
        title = _first(item, "title", f"{_RSS1}title", f"{_ATOM}title")
        url = _link(item)
        if not title or not url:
            continue

        body = _first(
            item,
            f"{_CONTENT}encoded", "description", f"{_RSS1}description",
            f"{_ATOM}content", f"{_ATOM}summary",
        )
        published = _parse_date(
            _first(item, "pubDate", f"{_DC}date", f"{_ATOM}published", f"{_ATOM}updated")
        )
        entries.append(Entry(title, url, published, _strip_html(body) or None))

    return entries
