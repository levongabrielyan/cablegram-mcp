"""The id is frozen: these tests are the contract, not a formality."""

import pytest

from cablegram.urls import item_id, normalise


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
    for junk in ("fbclid=abc", "utm_campaign=x", "spm=a.b.c", "share_token=zz", "from=timeline"):
        assert normalise(f"https://example.com/a?{junk}") == "https://example.com/a"


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
    assert len(ident) == 8
    assert all(c in "0123456789abcdef" for c in ident)


def test_id_is_deterministic_across_calls():
    """No state, no clock: hours apart must agree."""
    assert item_id("https://example.com/a") == item_id("https://example.com/a")


def test_root_and_empty_do_not_crash():
    assert normalise("https://example.com") == "https://example.com"
    assert normalise("https://example.com/") == "https://example.com/"
    assert normalise("") == ""
