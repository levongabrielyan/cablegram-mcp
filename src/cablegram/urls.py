"""URL normalisation and stable item identity.

Every archived item is keyed by ``item_id(url)``. That id appears in tool output
and in the archive, so this module's behaviour is frozen: changing it reassigns
every id ever issued.

Normalisation is pure. It performs no network access and no clock reads, so the
same URL always yields the same id — offline, years later, on any machine.
Shortener expansion belongs in the fetcher, before a URL ever reaches here.

Two rules drive the details below, and both come from the same asymmetry:

* **Merging two articles is unrecoverable.** ``url_norm`` is UNIQUE, so the
  second one never enters the archive and nothing reports it.
* **Splitting one article is a duplicate**, which is visible and harmless.

So every judgement call here errs towards keeping URLs apart.
"""

from __future__ import annotations

import hashlib
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["normalise", "item_id", "ID_LENGTH"]

# 12 hex = 48 bits. A 50% chance of collision arrives at ~19.7 million items;
# this archive grows by ~200k a year. At 8 hex that point was 77k items — five
# months — and a collision means a real article silently rejected by the PRIMARY
# KEY, in the one part of the system that cannot be rebuilt.
ID_LENGTH = 12

_HOST_PREFIXES = ("www.", "m.", "amp.", "mobile.")

# Deny, never allow. A whitelist drops any key it has not heard of, and an
# unlisted key that identifies the page (?sid=, ?story=, ?post=) merges distinct
# articles. This list only removes keys that are tracking wherever they appear.
_DROP_QUERY = frozenset({
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "twclid", "igshid",
    "yclid", "ysclid", "_openstat",           # Yandex, VK, Russian web
    "spm", "share_token", "share_source", "share_medium", "scene",
    "from_source", "from_channel", "wechat_redirect",  # Chinese web
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "vero_id", "oly_enc_id",
    "ref_src", "ref_url", "at_medium", "at_campaign", "cmpid",
    "s_kwcid", "trk", "trkCampaign", "sc_channel",
    "t",  # a tracking timestamp on much of the web; keeping it splits one story
})

# Whole families that are always instrumentation, whatever follows the prefix.
_DROP_PREFIXES = ("utm_", "pk_", "mtm_", "piwik_", "ga_", "hsa_", "at_custom")


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


def _is_tracking(key: str) -> bool:
    key = key.lower()
    return key in _DROP_QUERY or key.startswith(_DROP_PREFIXES)


def _clean_query(query: str) -> str:
    if not query:
        return ""
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if not _is_tracking(key)
    ]
    return urlencode(sorted(kept))


def _clean_path(path: str) -> str:
    """Trailing slashes never distinguish two pages, and the bare root has none.

    Without the second half, ``example.com`` and ``example.com/`` archive as two
    items — which an earlier test asserted as correct, having been written after
    the code rather than before it.
    """
    path = unicodedata.normalize("NFC", path)
    return path.rstrip("/")


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


def item_id(url: str, length: int = ID_LENGTH) -> str:
    """Stable short id derived from the URL. Computed, never assigned.

    Because it is a pure function of the URL, two calls hours apart agree
    without the server holding any state between them.
    """
    digest = hashlib.sha1(normalise(url).encode("utf-8")).hexdigest()
    return digest[:length]
