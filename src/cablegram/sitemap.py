"""Sitemaps, for the labs that publish no feed at all.

Anthropic has no RSS and does not advertise one. Of 4,299 archived items,
exactly one pointed at anthropic.com — and it arrived in Russian, through a
Telegram channel, because a person there linked it. Everything else the archive
knew about Anthropic was press: lawsuits, leaks, Hacker News. It knew Anthropic
was being sued and not what Anthropic published.

A sitemap is a published, standard artefact rather than a scrape, and it dates
every entry. Two things it is not:

* **It carries no titles.** The headline here is derived from the slug, which
  reads well — `claude-for-teachers` — but is not what the page calls itself.
  Marked in the source note, because a derived title presented as the real one
  is the kind of small lie this project spends its output budget avoiding.
* **Its `lastmod` is often the site build time.** On anthropic.com twenty-five
  URLs share one timestamp to the millisecond, which is a deploy and not a
  publication. Measured, none of them is under the three prefixes this reads,
  so the dates it takes are real; the guard stays anyway, because that is a
  property of one site on one day.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

from .rss import Entry

__all__ = ["ARTICLE_PATHS", "parse_sitemap"]

_SITEMAP = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# Where a lab puts things it wrote, as opposed to careers pages and pricing.
ARTICLE_PATHS = ("/news/", "/research/", "/engineering/", "/blog/")

_WORD = re.compile(r"[-_]+")


def _title(url: str) -> str:
    """A headline from the slug. Legible, and not the page's own words."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    words = _WORD.sub(" ", slug).strip()
    return words[:1].upper() + words[1:] if words else ""


def _when(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).astimezone(
            timezone.utc)
    except ValueError:
        return None


def parse_sitemap(raw: bytes, *, paths: tuple[str, ...] = ARTICLE_PATHS) -> list[Entry]:
    """Entries for the article-like URLs of one sitemap.

    Malformed XML raises, as it does for a feed. A sitemap with no matching
    path yields nothing, which the poller reports as parsed-empty rather than
    as a quiet day.
    """
    root = ET.fromstring(raw)
    urls = root.findall(f".//{_SITEMAP}url") or root.findall(".//url")

    found: list[tuple[str, str]] = []
    for node in urls:
        loc = node.findtext(f"{_SITEMAP}loc") or node.findtext("loc") or ""
        mod = node.findtext(f"{_SITEMAP}lastmod") or node.findtext("lastmod") or ""
        loc = loc.strip()
        if loc and any(p in loc for p in paths):
            found.append((loc, mod.strip()))

    # One timestamp shared by many URLs is a deploy, not a publication. Those
    # entries keep their place and lose their date, so the reader sees the '~'
    # instead of a whole section filed under the day the site was rebuilt.
    repeated = {stamp for stamp, n in Counter(m for _, m in found).items()
                if stamp and n >= 5}

    entries: list[Entry] = []
    for loc, mod in found:
        title = _title(loc)
        if not title:
            continue
        when = _when(mod)
        entries.append(Entry(
            title=title,
            url=loc,
            published=when,
            body=None,
            body_src=None,
            # A shared timestamp is still roughly when those pages were touched,
            # and it is the only date there is. Dropping it would leave the
            # capture time, and an item dated "now" outranks every real one.
            date_exact=when is not None and mod not in repeated,
        ))
    return entries
