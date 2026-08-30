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
