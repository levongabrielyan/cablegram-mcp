"""Anthropic's posts, read out of the payload its own pages ship.

The sitemap was tried first and dated seventy-seven items into a week in which
Anthropic published five, because `lastmod` is when the CMS last touched a page.
This reads the CMS fields instead, so the failures worth guarding against are
different: a record that borrows the next one's headline, a featured post
counted twice, and a shape change that has to look like a broken source rather
than a quiet week.
"""

import json
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

    /engineering was believed to be this case — "a different template, 0 records
    of 25 pages" — and was not: the regex required a time and that section
    stamps a bare date. It is in the catalogue and serves its records. The
    sentence is kept as the record of a measurement of the expression rather
    than of the page.
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


def test_a_section_that_stamps_a_bare_date_is_still_read():
    """/news and /research write a full timestamp; /engineering writes the date
    alone. Requiring the T found nothing there, and that was written up as "its
    payload holds no post records — measured, 0 of 25". The page had all 25:
    what had been measured was the expression, not the page."""
    chunk = json.dumps('[{"_type":"post","publishedOn":"2026-05-25","slug":{"_type":"slug","current":"how-we-contain-claude"},"title":"How we contain Claude"}]')
    doc = f'<script>self.__next_f.push([1,{chunk}])</script>'.encode()
    entry = parse_next_payload(doc, base="https://www.anthropic.com/engineering")[0]
    assert entry.title == "How we contain Claude"
    # UTC, not the machine's zone: astimezone() on a naive datetime read it as
    # local time and filed the post a day early under CEST.
    assert entry.published.isoformat() == "2026-05-25T00:00:00+00:00"


def test_a_featured_announcement_that_is_not_a_post_is_still_read():
    """/news carries a "Newsroom Featured Grid" whose items are `featuredGridLink`
    records, not posts: a bare date, a site-relative URL outside the section.
    That is where "Introducing Claude Fable 5.1 and Claude Mythos 5.1" sat on
    2026-09-01, and a reader taking only `post` records served 272 items
    without it.

    From the model's side: wire_search "Fable 5.1" over a week found it on
    Hacker News, Product Hunt and TestingCatalog and not on anthropic, under a
    COVER line vouching that anthropic had been searched back to 2021. A model
    concludes Anthropic has not announced it. The fixture is 1,570 bytes cut
    from the live page that day: the card, then the post that follows it.
    """
    raw = (pathlib.Path(__file__).parent / "samples"
           / "anthropic_news_featured.html").read_bytes()
    entries = parse_next_payload(raw, base="https://www.anthropic.com/news")
    titles = [e.title for e in entries]
    assert "Introducing Claude Fable 5.1 and Claude Mythos 5.1" in titles, titles
    card = next(e for e in entries if e.title.startswith("Introducing Claude Fable"))
    assert card.url == "https://www.anthropic.com/claude-fable-and-mythos-5-1"
    assert card.published.isoformat().startswith("2026-09-01")
    assert card.body and "coding and knowledge work" in card.body
    assert "Previewing the Model Hardware Standard" in titles, "and the post beside it"


def test_a_bare_date_is_marked_as_not_the_hour():
    """/engineering stamps every post `2026-05-25`, and the featured cards on
    /news do the same; the reader files them at midnight UTC. Midnight is a
    fill. Unmarked, wire_read printed the Fable 5.1 announcement as
    `2026-09-01T00:00:00Z` with the same authority as a post stamped to the
    minute, and a model quoting the hour quoted one nobody wrote. Atom's
    <updated>-only entries already carry the mark; this is the same fact."""
    post = json.dumps('[{"_type":"post","publishedOn":"2026-05-25","slug":{"_type":"slug","current":"how-we-contain-claude"},"title":"How we contain Claude"}]')
    doc = f'<script>self.__next_f.push([1,{post}])</script>'.encode()
    entry = parse_next_payload(doc, base="https://www.anthropic.com/engineering")[0]
    assert entry.date_exact is False, "a bare date has no hour to be exact about"

    raw = (pathlib.Path(__file__).parent / "samples"
           / "anthropic_news_featured.html").read_bytes()
    entries = parse_next_payload(raw, base="https://www.anthropic.com/news")
    card = next(e for e in entries if e.title.startswith("Introducing Claude Fable"))
    assert card.date_exact is False, card
    post = next(e for e in entries if not e.title.startswith("Introducing Claude Fable"))
    assert post.date_exact is True, "the post beside it is stamped to the second"

