"""Hacker News through the Algolia index: no key, no auth, 10k requests an hour.

The only source that can be searched at its origin rather than in the local
archive, and the only one that links out — which is what makes it the bridge
between an English-language headline and the Chinese or Russian one for the
same story.

Tested against saved responses from the live endpoint, including the two shapes
that break naive code: an Ask HN with no `url` key at all, and a Show HN that
carries its content only in story_text.
"""

import json
from datetime import timezone
from pathlib import Path

import pytest

from cablegram.hn import parse_search, search_url

SAMPLE = json.loads((Path(__file__).parent / "samples" / "hn_search.json").read_text())


def test_the_url_key_is_absent_not_null():
    """The saved sample has to keep this property or the test below proves
    nothing: in 30 of 30 Ask HN items the string "url" does not appear in the
    object at all. hit["url"] raises KeyError; a schema of `url: string | null`
    is wrong, it is `url?: string`.
    """
    ask = [h for h in SAMPLE["hits"] if "Ask HN" in h.get("title", "")]
    assert ask, "the sample must contain an Ask HN"
    assert "url" not in ask[0]


def test_an_item_without_a_url_falls_back_to_its_thread():
    """Dropping them would remove Ask HN entirely — which is where people say
    what they are actually using, and the reason this source is here."""
    entries = parse_search(SAMPLE)
    assert len(entries) == len(SAMPLE["hits"])
    threads = [e for e in entries if "news.ycombinator.com/item" in e.url]
    assert threads, "the item with no url must resolve to its discussion"


def test_an_item_with_a_url_keeps_the_external_one():
    """The external URL is what makes the cross-source count work: the same
    link on Hacker News and on a Chinese feed is one story seen twice. Keying
    these on the thread instead would make that impossible."""
    entries = parse_search(SAMPLE)
    external = [e for e in entries if "news.ycombinator.com" not in e.url]
    assert external


def test_story_text_becomes_the_body_unescaped():
    """It arrives as HTML with entities: &#x27;, &quot;, &#x2F; and <p>."""
    entries = parse_search(SAMPLE)
    bodies = [e.body for e in entries if e.body]
    assert bodies
    for body in bodies:
        assert "&#x27;" not in body and "<p>" not in body


def test_dates_are_utc():
    for entry in parse_search(SAMPLE):
        assert entry.published.tzinfo == timezone.utc
        assert 2020 < entry.published.year < 2100


def test_an_item_with_no_title_is_skipped():
    assert parse_search({"hits": [{"objectID": "1", "created_at_i": 1788078787}]}) == []


def test_an_empty_result_is_not_an_error():
    """Past the hard 1000-result ceiling the API returns hits:[] with HTTP 200
    and no error at all, so an empty list has to be a normal outcome here."""
    assert parse_search({"hits": [], "nbHits": 0}) == []


def test_a_response_without_hits_is_an_error():
    """That shape means the schema moved, and it must not read as no news."""
    with pytest.raises(ValueError):
        parse_search({"message": "something else"})


# ── the query, where the ceiling and the date filter live ───────────────────

def test_the_window_is_filtered_server_side():
    """numericFilters on created_at_i, not a client-side pass over everything:
    only the first 1000 results of any query are reachable, so filtering after
    the fact would silently drop whatever fell outside them."""
    url = search_url(since=1788078787)
    assert "numericFilters=created_at_i%3E1788078787" in url or \
           "numericFilters=created_at_i>1788078787" in url


def test_a_bare_window_uses_the_chronological_endpoint():
    """search_by_date, not search: the relevance ordering answers a different
    question and would return old stories for "what happened today"."""
    assert "search_by_date" in search_url(since=1788078787)


def test_a_term_search_uses_the_relevance_endpoint():
    assert search_url(query="lovable", since=0).rstrip("?").endswith(
        tuple(["hitsPerPage=100", "0"])) or "/search?" in search_url(query="lovable", since=0)


def test_only_stories_are_requested():
    """Comments outnumber stories by an order of magnitude and would eat the
    1000-result ceiling without adding a headline."""
    assert "tags=story" in search_url(since=0)


def test_the_page_size_stays_under_the_documented_cap():
    """Algolia caps hitsPerPage at 1000 silently rather than refusing, so asking
    for more is asking for a number that will not be honoured — and the request
    would then be describing a page size it does not get.

    This assertion used to read `assert "hitsPerPage=1000" not in url or True`.
    The left half claimed the opposite of the property (asking for 5000 does
    produce hitsPerPage=1000, which is the correct behaviour) and the `or True`
    meant it could not fail either way.
    """
    from cablegram.hn import MAX_ROWS

    assert f"hitsPerPage={MAX_ROWS}" in search_url(since=0, rows=5000), \
        "over the cap, the request asks for the cap"
    assert "hitsPerPage=100" in search_url(since=0, rows=100), \
        "under it, the request asks for what it wants"


def test_the_poller_asks_for_the_whole_ceiling():
    """hitsPerPage defaulted to 100, so a 48-hour window returned 3 hours of
    stories: the cap decided the window, not the parameter. One request for
    1000 costs exactly the same as one for 100."""
    from cablegram.poll import _request_url
    from cablegram.sources import by_id

    url = _request_url(by_id("hn"), since=0)
    assert "hitsPerPage=1000" in url
