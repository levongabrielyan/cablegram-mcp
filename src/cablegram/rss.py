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
import xml.parsers.expat as expat
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
    # The element the body came from, verbatim. Not "full" or "teaser": a feed
    # is free to put a whole article in <description> or two sentences in
    # <atom:content>, and both happen. Naming the element is a fact; deciding
    # how much of the article it holds is a property of the source, checked
    # against its real feed, and belongs where that check lives.
    body_src: str | None = None


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


_ENTITY_REF = re.compile(r"&([A-Za-z_][\w.-]*);")
_PREDEFINED = frozenset({"lt", "gt", "amp", "apos", "quot"})


class _PrologueRead(Exception):
    """Raised at the root element to stop before any content is expanded."""


def _reject_entity_bombs(raw: bytes) -> None:
    """Refuse entity declarations that expand into other entities.

    Custom XML entities nest geometrically — a few hundred bytes become
    gigabytes — and feeds come from third parties, so this input is hostile by
    default.

    Two earlier versions scanned the bytes for <!DOCTYPE and its internal
    subset. Both were wrong in both directions: a comment containing
    `<!DOCTYPE fake [ ]` moved the pointers and let a real bomb through, while
    an ordinary WordPress feed declaring `<!ENTITY nbsp "&#160;">` was thrown
    out whole. Reading bytes cannot tell a declaration from a mention of one.

    So expat reads the prologue properly and stops at the root element, before
    a single reference is expanded. What gets refused is the structural
    property that makes a bomb: an entity whose replacement text names another
    entity. `&#160;` is a character reference, expands once, and is fine.
    """
    parser = expat.ParserCreate()

    def on_entity_decl(name, is_parameter, value, base, system_id, public_id, notation):
        if system_id or public_id:
            raise ValueError(
                f"feed declares external entity {name!r}; refusing to fetch on its behalf"
            )
        nested = {ref for ref in _ENTITY_REF.findall(value or "")} - _PREDEFINED
        if nested:
            raise ValueError(
                f"feed declares entity {name!r} expanding into {sorted(nested)[:3]}; "
                "refusing to expand it"
            )

    def on_start_element(name, attrs):
        raise _PrologueRead

    parser.EntityDeclHandler = on_entity_decl
    parser.StartElementHandler = on_start_element
    try:
        parser.Parse(raw, True)
    except _PrologueRead:
        pass
    except expat.ExpatError:
        return  # malformed: let ElementTree raise the error callers already expect


# Searched in this order — a feed carrying both usually puts more in the first.
# The name recorded is the readable one, not the namespaced path.
_BODY_ELEMENTS = (
    (f"{_CONTENT}encoded", "content:encoded"),
    (f"{_ATOM}content", "atom:content"),
    ("description", "description"),
    (f"{_RSS1}description", "description"),
    (f"{_ATOM}summary", "atom:summary"),
)


def _body(item: ET.Element) -> tuple[str | None, str | None]:
    for path, name in _BODY_ELEMENTS:
        if text := _strip_html(_text(item.find(path))):
            return text, name
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

        body, body_src = _body(item)
        published = _parse_date(
            _first(item, "pubDate", f"{_DC}date", f"{_ATOM}published", f"{_ATOM}updated")
        )
        entries.append(Entry(title, url, published, body, body_src))

    return entries
