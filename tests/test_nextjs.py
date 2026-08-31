"""Anthropic's posts, read out of the payload its own pages ship.

The sitemap was tried first and dated seventy-seven items into a week in which
Anthropic published five, because `lastmod` is when the CMS last touched a page.
This reads the CMS fields instead, so the failures worth guarding against are
different: a record that borrows the next one's headline, a featured post
counted twice, and a shape change that has to look like a broken source rather
than a quiet week.
"""

import pathlib

import pytest

from cablegram.nextjs import parse_next_payload

SAMPLE = (pathlib.Path(__file__).parent / "samples" / "anthropic_news.html").read_bytes()
BASE = "https://www.anthropic.com/news"


def entries():
    return parse_next_payload(SAMPLE, base=BASE)


def test_a_post_becomes_an_entry_with_the_headline_its_author_wrote():
    """The whole reason for leaving the sitemap. A slug reads as
    `Model hardware standard research preview`; the page is called `Previewing
    the Model Hardware Standard`, and 17% of the slugs were two words or fewer —
    `Anthropic bcg`, `Projects`, `Australia MOU`."""
    first = entries()[0]
    assert first.title == "Previewing the Model Hardware Standard"
    assert first.url == f"{BASE}/model-hardware-standard-research-preview"


def test_the_date_is_the_one_the_publisher_stamped():
    """`lastmod` said 2026-08-26 for a paper published in 2022. `publishedOn` is
    the CMS field, and fourteen sampled entries matched the date printed on the
    article's own page, 14 of 14."""
    first = entries()[0]
    assert first.published.isoformat().startswith("2026-08-27")
    assert first.published.tzinfo is not None
    assert first.date_exact is True, "this date needs no tilde; it is the real one"


def test_every_entry_keeps_its_own_headline():
    """The regex walks from one record's `publishedOn` to the next `"title"`,
    so an untitled post could take the following one's headline — a real date
    under somebody else's words, which is worse than no entry at all. The match
    is tempered so it cannot cross into the next record.

    Compares the entries against each other rather than naming titles, so it
    holds as the page changes.
    """
    titles = [e.title for e in entries()]
    assert len(titles) == len(set(titles)), (
        f"two entries share a headline: {titles}. One of them borrowed it")


def test_a_post_listed_twice_is_archived_once():
    """The featured post appears in the hero block and again in the list — 4 of
    274 on /news, 5 of 165 on /research, the same record both times."""
    urls = [e.url for e in entries()]
    assert len(urls) == len(set(urls))


def test_the_summary_travels_when_the_page_carries_one():
    """A body the sitemap could never supply: it had none at all, for any item."""
    with_body = [e for e in entries() if e.body]
    assert with_body, "the sample has posts with summaries"
    assert all(e.body_src == "summary" for e in with_body)
    assert all(e.body_src is None for e in entries() if not e.body), (
        "an element name with no body behind it is a claim about text that is "
        "not there")


def test_a_record_split_across_two_chunks_is_still_read():
    """The payload arrives as a series of pushes and a record can straddle two
    of them, so they are concatenated before matching. The sample is deliberately
    cut down the middle."""
    assert SAMPLE.count(b"__next_f.push") >= 2
    assert len(entries()) >= 4


def test_a_page_with_no_records_yields_nothing_rather_than_raising():
    """Which the poller files as parsed-empty: a source that changed shape, not
    a quiet week. That is the correct reading for an undocumented format, and it
    is why this source is marked fragile.

    /engineering is exactly this case today — a different template, 0 records of
    25 pages — so it is not in the catalogue.
    """
    assert parse_next_payload(b"<html><body>nothing here</body></html>", base=BASE) == []


def test_a_chunk_that_will_not_decode_raises_rather_than_being_skipped():
    """A chunk in the right shape whose content does not decode means the
    escaping changed, and skipping it would return most of the posts and lose
    the rest with nothing said. The poller turns this into `unparseable` with
    the reason attached, which is a source reported broken — the same call
    parse_models makes when the hub's schema moves.
    """
    with pytest.raises(ValueError):
        parse_next_payload(rb'<script>self.__next_f.push([1,"bad \q escape"])</script>',
                           base=BASE)
