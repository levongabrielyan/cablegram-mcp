"""URL normalisation and stable item identity.

Every item is keyed by ``item_id(url)``. That id appears in tool output
and in every reply, so this module's behaviour is frozen: changing it reassigns
every id ever issued.

Normalisation is pure. It performs no network access and no clock reads, so the
same URL always yields the same id — offline, years later, on any machine.
Shortener expansion belongs in the fetcher, before a URL ever reaches here.

Two rules drive the details below, and both come from the same asymmetry:

* **Merging two articles is unrecoverable.** ``url_norm`` is UNIQUE, so the
  second one never reaches the reply and nothing reports it.
* **Splitting one article is a duplicate**, which is recoverable — though not
  harmless: every split hides that two sources carried one story, so a story
  six feeds ran names fewer carriers than it had, and nothing reports the gap.

Both are bad; only one is permanent. So every judgement call here errs towards
keeping URLs apart.
"""

from __future__ import annotations

import hashlib
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["normalise", "item_id", "ID_LENGTH", "IDENTITY", "IDENTITY_RECIPE",
           "NORMALISE_VERSION", "id_recipe"]

# 12 hex = 48 bits. A 50% chance of collision arrives at ~19.7 million items;
# the feeds run to ~200k items a year. At 8 hex that point was 77k items — five
# months — and a collision means a real article silently rejected by the PRIMARY
# KEY, in the one part of the system that cannot be rebuilt.
ID_LENGTH = 12

# Bumped only when a change in this module reassigns ids that were already
# issued. Adding a tracking key to the denylist does not qualify: it leaves
# essentially every existing id alone. Rewriting how the path or host is treated
# does, and an archive stamped with the old value then refuses to open rather
# than silently archiving everything it already holds a second time.
NORMALISE_VERSION = 1

# The algorithm, as one string. It used to be stamped into an archive file and
# checked on open; there is no file now, so nothing checks it at runtime. It is
# kept because ids appear in every reply and a caller may hold one across
# calls: a change here reassigns every id it has in hand, and the version in
# this string is where that change has to become visible.
IDENTITY = f"sha1[:{ID_LENGTH}]/v{NORMALISE_VERSION}"

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
})
# Deliberately NOT here: 't'. It is a tracking timestamp on some sites, but it
# is also the thread id in phpBB and vBulletin — and Hacker News links out to
# arbitrary sites, forums included. Dropping it globally would merge two threads
# into one, reproducing the very ?sid= bug the denylist exists to prevent.

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

    Without the second half, ``example.com`` and ``example.com/`` are stored as
    two items — which an earlier test asserted as correct, having been written after
    the code rather than before it.
    """
    path = unicodedata.normalize("NFC", path)
    return path.rstrip("/")


def normalise(url: str) -> str:
    """Collapse the many spellings of one page into a single canonical form.

    ``m.36kr.com/p/123?utm_source=wechat`` and ``https://36kr.com/p/123/`` are
    the same article. Without this they are stored as two items and neither
    names the other's source among its carriers.
    """
    url = url.strip()
    if not url:
        return ""
    # Only pages. A feed carrying `javascript:void(0)`, `mailto:` or `data:`
    # in <link> was normalised to `https://javascript:void(0)` and stored as
    # an item, then served with "Open `url` for the text". A scheme that is
    # not http(s) names something a reader cannot open, so it is not an item.
    scheme_end = url.find(":")
    if scheme_end > 0 and "//" not in url[:scheme_end + 3] and \
            url[:scheme_end].replace("+", "").replace("-", "").replace(".", "").isalpha():
        return ""

    parts = urlsplit(url if "//" in url else f"//{url}", scheme="https")
    scheme = parts.scheme.lower() or "https"
    if scheme == "http":
        scheme = "https"
    if scheme != "https":
        return ""

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


def id_recipe() -> str:
    """A fingerprint of the lists that decide what survives normalisation.

    NORMALISE_VERSION is remembered by hand, and the denylist is the one part of
    this module designed to grow — new tracking parameters appear every month.
    So the change most likely to happen was also the one the version number
    would forget: adding a key changes the id of every URL carrying it, and
    those are stored again as duplicates with nothing to say so.

    This moves on its own. It is deliberately not part of IDENTITY: such a
    change touches a handful of ids, not all of them, so it is recorded rather
    than treated as a different archive.
    """
    recipe = repr((ID_LENGTH, sorted(_DROP_QUERY), _DROP_PREFIXES, _HOST_PREFIXES))
    return hashlib.sha1(recipe.encode("utf-8")).hexdigest()[:6]


#: Evaluated once at import; the lists are module constants.
IDENTITY_RECIPE = id_recipe()
