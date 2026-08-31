"""Sitemaps, for the labs that publish no feed.

Of 4,299 archived items, exactly one pointed at anthropic.com — and it arrived
in Russian, through a Telegram channel, because somebody there linked it. The
archive knew Anthropic was being sued and not what Anthropic published.
"""

import pathlib

import pytest
import xml.etree.ElementTree as ET

from cablegram.sitemap import parse_sitemap

SAMPLE = (pathlib.Path(__file__).parent / "samples" / "anthropic_sitemap.xml").read_bytes()


def test_an_article_url_becomes_an_entry():
    entries = {e.url: e for e in parse_sitemap(SAMPLE)}
    assert "https://www.anthropic.com/news/100k-context-windows" in entries


def test_the_headline_is_read_out_of_the_slug():
    """Not the page's own words — a sitemap carries none — but legible, and
    declared as derived in the source note rather than passed off as the real
    title."""
    entry = next(e for e in parse_sitemap(SAMPLE) if e.url.endswith("100k-context-windows"))
    assert entry.title == "100k context windows"


def test_pages_that_are_not_articles_are_left_out():
    """A careers page is not a dispatch. Without the filter the whole site —
    pricing, jobs, legal — arrives as news."""
    assert not [e for e in parse_sitemap(SAMPLE) if "/careers" in e.url]


def test_a_timestamp_shared_by_many_urls_is_a_deploy_and_loses_its_date():
    """Twenty-five URLs on anthropic.com carry one timestamp to the millisecond.
    That is when the site was rebuilt, not when anything was published, and
    taking it would file a whole section under the day of a deploy.

    Built synthetically on purpose: on anthropic.com today the stamp lands only
    on pages this adapter already filters out — measured, none of the 437 URLs
    under /news/, /research/ or /engineering/ carries it. That is a property of
    one site on one day, not a guarantee, and the next sitemap will not be as
    tidy. The entries keep their place and lose their date, so the reader gets
    the approximate mark instead of a confident lie.
    """
    stamp = "2026-08-31T12:37:46.006Z"
    doc = ('<?xml version="1.0"?><urlset '
           'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(f"<url><loc>https://e.example/news/post-{i}</loc>"
                     f"<lastmod>{stamp}</lastmod></url>" for i in range(6))
           + "<url><loc>https://e.example/news/real</loc>"
             "<lastmod>2026-08-26T18:11:29.000Z</lastmod></url></urlset>").encode()

    entries = {e.url.rsplit("/", 1)[-1]: e for e in parse_sitemap(doc)}
    assert len(entries) == 7, "nothing is dropped for having a bad date"
    # The date is kept — it is the only one there is, and discarding it leaves
    # the capture time, which outranks every real date in the archive — and
    # marked, so the reader gets the approximate mark rather than a claim.
    assert all(entries[f"post-{i}"].date_exact is False for i in range(6))
    assert all(entries[f"post-{i}"].published is not None for i in range(6))
    assert entries["real"].date_exact is True


def test_the_real_sitemap_carries_real_dates():
    """The counterpart to the test above, against the live document: every
    article URL in it has a date of its own."""
    entries = parse_sitemap(SAMPLE)
    assert entries and all(e.published is not None for e in entries)
    assert all(e.date_exact for e in entries)


def test_a_real_lastmod_is_kept_in_utc():
    entry = next(e for e in parse_sitemap(SAMPLE) if e.url.endswith("100k-context-windows"))
    assert entry.published.isoformat().startswith("2026-08-26")
    assert entry.published.tzinfo is not None


def test_a_sitemap_with_no_articles_yields_nothing_rather_than_raising():
    """The poller files that as parsed-empty, which is a source that changed
    shape — not a quiet day."""
    empty = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' \
            b"<url><loc>https://e.example/pricing</loc></url></urlset>"
    assert parse_sitemap(empty) == []


def test_malformed_xml_raises_like_a_feed_does():
    with pytest.raises(ET.ParseError):
        parse_sitemap(b"<urlset><url>")
