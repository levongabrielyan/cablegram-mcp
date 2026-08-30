"""Public Telegram channels through t.me/s/, which is HTML with no contract.

No account, no API key, no cookies. Six Russian channels where researchers post
about models days before anything is written up in English, and the only source
here whose format can change without any version to notice it by.

Tested against a saved page from the live site, so the traps that only appear
in real HTML — a quoted reply, an album, a message with no text — are in the
fixture rather than imagined.
"""

from datetime import timezone
from pathlib import Path

import pytest

from cablegram.telegram import channel_url, parse_channel

SAMPLE = (Path(__file__).parent / "samples" / "telegram_channel.html").read_text()


def test_messages_are_found_by_their_post_id():
    entries = parse_channel(SAMPLE, channel="data_secrets")
    assert entries


def test_the_permalink_is_built_from_the_post_id():
    for entry in parse_channel(SAMPLE, channel="data_secrets"):
        assert entry.url.startswith("https://t.me/data_secrets/")
        assert entry.url.rsplit("/", 1)[1].isdigit()


def test_a_quoted_reply_is_not_taken_for_the_message():
    """The selector needs both classes. `.tgme_widget_message_text` alone also
    matches the preview of a quoted reply — which appears BEFORE the real text
    in the DOM, so taking the first match silently archives the wrong message,
    under the right id and the right date. Nothing about the result looks wrong.
    """
    if "js-message_reply_text" not in SAMPLE:
        pytest.skip("this fixture carries no reply")

    quoted = SAMPLE.split('js-message_reply_text"', 1)[1].split("</div>", 1)[0]
    quoted_text = quoted.split(">", 1)[1].strip()[:40]
    for entry in parse_channel(SAMPLE, channel="data_secrets"):
        assert not entry.title.startswith(quoted_text[:20] or "\0")


def test_dates_come_from_the_attribute_not_the_visible_text():
    """The visible time is local to the reader and has no date at all; the
    attribute is ISO-8601 and always UTC."""
    for entry in parse_channel(SAMPLE, channel="data_secrets"):
        assert entry.published is not None
        assert entry.published.tzinfo == timezone.utc
        assert 2020 < entry.published.year < 2100


def test_a_message_with_no_text_is_skipped_not_guessed():
    """Two different cases produce this — a poll, and a photo with no caption —
    and neither has a headline to archive. Inventing one from the channel name
    would fill the archive with rows that say nothing."""
    empty = ('<div class="tgme_widget_message" data-post="ai_newz/999">'
             '<a class="tgme_widget_message_date">'
             '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a></div>')
    assert parse_channel(empty, channel="ai_newz") == []


def test_the_headline_is_the_first_line_and_the_body_the_rest():
    """A Telegram post is one block of text, so there is no title field to read.
    The first line stands in for one, and the whole text is kept as the body —
    truncating a headline out of a paragraph would lose the paragraph."""
    entries = parse_channel(SAMPLE, channel="data_secrets")
    with_body = [e for e in entries if e.body]
    assert with_body
    for entry in with_body:
        assert len(entry.title) <= 300
        assert entry.body.startswith(entry.title[:30])


def test_html_inside_a_message_becomes_text():
    """Posts carry <b>, <a href>, <br> and emoji. The tags must not reach the
    archive, and a <br> is a line break rather than a word joined to the next."""
    html = ('<div class="tgme_widget_message" data-post="ai_newz/1">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            '<b>GLM-5</b> вышла<br/>Контекст 2M токенов &amp; дешевле'
            '</div></div>')
    entry = parse_channel(html, channel="ai_newz")[0]
    assert "<b>" not in entry.body and "&amp;" not in entry.body
    assert entry.title == "GLM-5 вышла"
    assert "2M" in entry.body


def test_an_unrelated_page_yields_nothing_rather_than_raising():
    """t.me serves a normal page for a channel with no public preview."""
    assert parse_channel("<html><body>no channel here</body></html>", channel="x") == []


def test_the_channel_url_asks_for_the_preview_view():
    assert channel_url("ai_newz") == "https://t.me/s/ai_newz"


def test_paging_asks_for_what_came_before_the_oldest_seen():
    assert channel_url("ai_newz", before=4710) == "https://t.me/s/ai_newz?before=4710"


# ── the link inside a post, which is what makes a channel able to cross ─────

def test_the_first_external_link_is_extracted():
    """A channel's own URL is its permalink, so without this six of nineteen
    sources can never appear in the cross-source count — and 34 of every 100
    posts carry a link, four of them to openai.com in a single sample."""
    html = ('<div class="tgme_widget_message" data-post="ai_newz/1">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            'GLM-5 вышла<br/>Читайте: <a href="https://openai.com/index/glm">тут</a>'
            '</div></div>')
    entry = parse_channel(html, channel="ai_newz")[0]
    assert entry.url == "https://t.me/ai_newz/1", "the post is still its own item"
    assert entry.links == ("https://openai.com/index/glm",)


def test_only_one_link_is_taken():
    """A post's headline describes one subject. Attaching it to three articles
    would assert three things, of which at most one is true."""
    html = ('<div class="tgme_widget_message" data-post="ai_newz/2">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            '<a href="https://a.example/one">a</a> и <a href="https://b.example/two">b</a>'
            '</div></div>')
    assert len(parse_channel(html, channel="ai_newz")[0].links) == 1


def test_a_link_back_into_telegram_is_not_a_crossing():
    """Channels quote each other constantly. Those are the same ecosystem, not
    an independent source carrying the same story."""
    html = ('<div class="tgme_widget_message" data-post="ai_newz/3">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            'Смотри <a href="https://t.me/denissexy/123">там</a>'
            '</div></div>')
    assert parse_channel(html, channel="ai_newz")[0].links == ()


def test_a_post_with_no_link_carries_none():
    for entry in parse_channel(SAMPLE, channel="data_secrets"):
        assert isinstance(entry.links, tuple)


def test_a_double_escaped_href_is_decoded():
    """Telegram escapes ampersands twice: &amp;amp; in the raw HTML. HTMLParser
    unescapes once, so what reaches normalise still carries &amp; — parse_qsl
    splits on & and the keys become `amp;utm_medium`, which do not start with
    utm_ and survive the denylist whole.

    The rubbish in the URL is not the problem. Two channels linking the same
    article with different campaigns then produce different ids, and the
    crossing this feature exists to create does not happen.
    """
    html = ('<div class="tgme_widget_message" data-post="ai_newz/1">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            '<a href="https://openai.com/x?a=1&amp;amp;utm_campaign=launch">x</a>'
            '</div></div>')
    link = parse_channel(html, channel="ai_newz")[0].links[0]
    assert "&amp;" not in link
    assert link == "https://openai.com/x?a=1&utm_campaign=launch"

    # and the point of it: the denylist can now see the tracking key it was
    # blind to, so two channels with different campaigns produce one id
    from cablegram.urls import item_id
    assert item_id(link) == item_id("https://openai.com/x?a=1")


def test_the_text_is_not_unescaped_twice():
    """HTMLParser(convert_charrefs=True) already converts once, and _clean called
    html.unescape again — so `R&amp;amp;D` in a post came out as `R&D` and an
    escaped `&amp;lt;script&amp;gt;` became a literal tag in the archive."""
    html = ('<div class="tgme_widget_message" data-post="ai_newz/1">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            'Цена: 5 &amp;lt;script&amp;gt; и R&amp;amp;D'
            '</div></div>')
    body = parse_channel(html, channel="ai_newz")[0].body
    assert "<script>" not in body
    assert "&lt;script&gt;" in body or "&amp;lt;" in body


def test_a_sponsored_link_is_not_taken_as_the_subject():
    """Russian channels open with the promo and put the real link further down,
    so "the first link" is positional, not semantic. utm_medium=telegram is the
    channel promoting something inside itself — one of 67 links in a live poll,
    cheap to exclude, and it would otherwise be the one link kept."""
    html = ('<div class="tgme_widget_message" data-post="ai_newz/1">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            '<a href="https://shop.example/x?utm_source=pr&amp;amp;utm_medium=telegram">ad</a>'
            ' а вот новость <a href="https://openai.com/index/glm">тут</a>'
            '</div></div>')
    assert parse_channel(html, channel="ai_newz")[0].links == \
        ("https://openai.com/index/glm",)


def test_a_link_carrying_an_ordinary_utm_is_kept():
    """utm_source=chatgpt.com or perplexity means somebody copied the link from
    there. Those are real articles — two of the three utm links in a live poll —
    and dropping every utm would throw them away."""
    html = ('<div class="tgme_widget_message" data-post="ai_newz/1">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            '<a href="https://edition.cnn.com/story?utm_source=perplexity">x</a>'
            '</div></div>')
    assert parse_channel(html, channel="ai_newz")[0].links


def test_a_very_long_post_is_capped_like_a_feed_entry():
    """MAX_FIELD was applied in the RSS parser only, and the three adapters
    build their Entry by hand — so a 24,000-character post was archived whole,
    into the title, the body and the trigram index."""
    from cablegram.rss import MAX_FIELD

    html = ('<div class="tgme_widget_message" data-post="ai_newz/1">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            + "x" * (MAX_FIELD * 4) + '</div></div>')
    entry = parse_channel(html, channel="ai_newz")[0]
    assert len(entry.body) <= MAX_FIELD and len(entry.title) <= MAX_FIELD
