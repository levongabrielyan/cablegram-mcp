"""URL normalisation and stable item identity.

Every archived item is keyed by ``item_id(url)``. That id appears in tool output
and in the archive, so this module's behaviour is frozen: changing it reassigns
every id ever issued.

Normalisation is pure. It performs no network access and no clock reads, so the
same URL always yields the same id — offline, years later, on any machine.
Shortener expansion belongs in the fetcher, before a URL ever reaches here.
"""

from __future__ import annotations

import hashlib
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["normalise", "item_id"]

_HOST_PREFIXES = ("www.", "m.", "amp.", "mobile.")

# Query keys that carry meaning: without them the URL points somewhere else.
# Everything not listed here is dropped, including every tracking parameter.
_KEEP_QUERY = frozenset({"id", "p", "v", "page", "q", "story_fbid", "t"})

_TRACKING_PREFIXES = ("utm_", "pk_", "mc_", "ga_")


def _clean_host(host: str) -> str:
    host = host.lower().rstrip(".")
    if host.startswith("["):  # IPv6 literal, leave untouched
        return host
    if ":" in host:
        host, _, port = host.partition(":")
        host = _strip_prefixes(host)
        return f"{host}:{port}" if port not in ("80", "443") else host
    return _strip_prefixes(host)


def _strip_prefixes(host: str) -> str:
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix) and host.count(".") >= 2:
            return host[len(prefix) :]
    return host


def _clean_query(query: str) -> str:
    if not query:
        return ""
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key.lower() in _KEEP_QUERY
        and not key.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlencode(sorted(kept))


def _clean_path(path: str) -> str:
    path = unicodedata.normalize("NFC", path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return path


def normalise(url: str) -> str:
    """Collapse the many spellings of one page into a single canonical form.

    ``m.36kr.com/p/123?utm_source=wechat`` and ``https://36kr.com/p/123/`` are
    the same article. Without this they archive as two items and the
    cross-source count — the strongest early signal available — never fires.
    """
    url = url.strip()
    if not url:
        return ""

    parts = urlsplit(url if "//" in url else f"//{url}", scheme="https")
    scheme = parts.scheme.lower() or "https"
    if scheme == "http":
        scheme = "https"

    host = _clean_host(parts.netloc)
    if not host:
        return url.lower()

    return urlunsplit((scheme, host, _clean_path(parts.path), _clean_query(parts.query), ""))


def item_id(url: str, length: int = 8) -> str:
    """Stable short id derived from the URL. Computed, never assigned.

    Because it is a pure function of the URL, two calls hours apart agree
    without the server holding any state between them.
    """
    digest = hashlib.sha1(normalise(url).encode("utf-8")).hexdigest()
    return digest[:length]
