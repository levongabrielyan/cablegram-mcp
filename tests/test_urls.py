"""The id is frozen: these tests are the contract, not a formality."""

import pytest

from cablegram.urls import ID_LENGTH, item_id, normalise


@pytest.mark.parametrize(
    "variant",
    [
        "https://36kr.com/p/123",
        "https://36kr.com/p/123/",
        "http://36kr.com/p/123",
        "https://www.36kr.com/p/123",
        "https://m.36kr.com/p/123",
        "https://36kr.com/p/123?utm_source=wechat&utm_medium=social",
        "https://36kr.com/p/123#comments",
        "https://36KR.com/p/123",
        "  https://36kr.com/p/123  ",
    ],
)
def test_one_article_one_id(variant):
    """Nine spellings of the same page must collapse to one item.

    This is what makes the cross-source count possible: the same story seen in
    six feeds is recognised as one story, not six.
    """
    assert normalise(variant) == "https://36kr.com/p/123"
    assert item_id(variant) == item_id("https://36kr.com/p/123")


def test_meaningful_query_survives():
    """Hacker News keys its items on ?id= — dropping it would merge every story."""
    a = normalise("https://news.ycombinator.com/item?id=41234567")
    b = normalise("https://news.ycombinator.com/item?id=41234568")
    assert a != b
    assert "id=41234567" in a


def test_tracking_never_survives():
    """Only keys that are instrumentation wherever they appear are dropped."""
    for junk in ("fbclid=abc", "utm_campaign=x", "spm=a.b.c", "share_token=zz",
                 "gclid=1", "yclid=2", "igshid=3", "mc_cid=4", "_hsenc=5"):
        assert normalise(f"https://example.com/a?{junk}") == "https://example.com/a"


def test_ambiguous_keys_are_kept_on_purpose():
    """`from` is tracking on WeChat and a real parameter elsewhere.

    Keeping it costs a duplicate — visible and harmless. Dropping it risks
    merging two articles, which is unrecoverable. When unsure, keep.
    """
    assert normalise("https://example.com/a?from=timeline") == "https://example.com/a?from=timeline"


def test_query_order_does_not_matter():
    assert normalise("https://e.com/x?id=1&page=2") == normalise("https://e.com/x?page=2&id=1")


def test_distinct_pages_stay_distinct():
    urls = [
        "https://openai.com/index/gpt-5",
        "https://openai.com/index/gpt-4",
        "https://anthropic.com/index/gpt-5",
        "https://t.me/s/ai_newz/1234",
        "https://t.me/s/ai_newz/1235",
    ]
    assert len({item_id(u) for u in urls}) == len(urls)


def test_unicode_path_is_stable():
    """Chinese and Cyrillic paths must survive, and compose identically."""
    assert normalise("https://qbitai.com/2026/08/智谱.html").endswith("智谱.html")
    decomposed = "https://habr.com/ru/post/й"  # U+0439
    composed = "https://habr.com/ru/post/й"  # U+0438 + combining breve
    assert item_id(decomposed) == item_id(composed)


def test_id_shape():
    ident = item_id("https://example.com/a")
    assert len(ident) == ID_LENGTH
    assert all(c in "0123456789abcdef" for c in ident)


def test_id_is_deterministic_across_calls():
    """No state, no clock: hours apart must agree."""
    assert item_id("https://example.com/a") == item_id("https://example.com/a")


def test_root_and_empty_do_not_crash():
    assert normalise("https://example.com") == "https://example.com"
    assert normalise("https://example.com/") == "https://example.com"
    assert normalise("") == ""


# ── Regressions found by an external reviewer, 2026-08-30 ────────────────────
# Written before the fix, so they fail against the old behaviour. A test written
# after the code tends to confirm what the code does, not what it should do.

def test_id_is_wide_enough_to_outlast_the_archive():
    """8 hex is 32 bits: a 50% chance of collision after ~77k items, five months
    of feeds. `id` is the PRIMARY KEY, so a collision silently rejects a real
    article — in the one part of the system that cannot be regenerated.
    """
    assert len(item_id("https://e.com/a")) >= 12


def test_unknown_query_keys_must_not_merge_distinct_pages():
    """A whitelist fails destructively: an unlisted key that identifies the page
    merges two articles into one and the second never enters the archive.
    A blacklist fails benignly — it lets tracking through and creates a duplicate.
    """
    for key in ("sid", "story", "topic", "article", "post", "aid", "tid"):
        a = item_id(f"https://e.com/x?{key}=1")
        b = item_id(f"https://e.com/x?{key}=2")
        assert a != b, f"?{key}= identifies the page; merging it loses articles"


def test_ambiguous_t_is_kept_and_costs_a_duplicate():
    """'t' is a tracking timestamp on some sites and a forum thread id on others.

    Keeping it archives one story twice — recoverable. Dropping it merges two
    threads into one, and the second never enters the archive. See the note in
    urls.py: the asymmetry decides, not which case is more common.
    """
    assert item_id("https://e.com/x?t=1735689600") != item_id("https://e.com/x?t=1735689700")


def test_root_with_and_without_slash_is_one_page():
    assert item_id("https://example.com") == item_id("https://example.com/")


def test_forum_thread_ids_must_stay_apart():
    """?t= is the thread id in phpBB and vBulletin, the same shape as ?sid=.

    Dropping it globally reproduces the very bug the denylist was written to
    fix — and Hacker News links out to arbitrary sites, forums included.
    """
    a = item_id("https://forum.ex.com/viewtopic.php?t=123")
    b = item_id("https://forum.ex.com/viewtopic.php?t=456")
    assert a != b
