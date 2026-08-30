"""Public Telegram channels through t.me/s/, which is HTML with no contract.

No account, no API key, no cookies: the preview view is public. Six Russian
channels where practitioners post about models days before anything is written
up in English — and the only source here whose format can change without a
version number to notice it by, which is why the parsing is narrow and explicit
rather than clever.

Parsed with the standard library. A CSS engine would be one dependency for one
source, against a promise the README makes; and what is needed here is three
attributes in a known shape, not a general selector language.

The traps, all found by running it rather than by reading:

* **The text needs both classes.** `tgme_widget_message_text` alone also matches
  the preview of a quoted reply, which sits BEFORE the real text in the DOM — so
  taking the first match archives the wrong message under the right id and the
  right date, and nothing about the result looks wrong.
* **The date is an attribute.** The visible time is local to the reader and
  carries no date at all.
* **t.me resets the connection on the sixth request in a row.** Not a 429: the
  socket closes, so a careless handler reads it as a dead channel. The poller
  spaces these out.
"""

from __future__ import annotations

import html as html_module
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from .rss import Entry

__all__ = ["parse_channel", "channel_url", "TELEGRAM_BASE"]

TELEGRAM_BASE = "https://t.me/s"
TITLE_LIMIT = 300  # a post is one block of text; this stands in for a headline

_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def channel_url(channel: str, before: int | None = None) -> str:
    """The preview view, optionally paging backwards.

    `?before=` takes the id of the oldest message on the page and returns the
    ones before it. Pages hold between 10 and 20 messages, not a fixed number,
    because albums swallow consecutive ids.
    """
    url = f"{TELEGRAM_BASE}/{channel}"
    return f"{url}?before={before}" if before else url


class _ChannelParser(HTMLParser):
    """Pulls out (post_id, iso_datetime, text) for each message on the page.

    A small state machine rather than a DOM: the three things needed are an
    attribute on the message div, an attribute on a nested time element, and the
    text of one specific div. Depth is tracked so a nested div cannot end the
    capture early.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[dict] = []
        self._current: dict | None = None
        self._depth = 0
        self._text_depth: int | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())

        if tag == "div":
            self._depth += 1
            if "tgme_widget_message" in classes and attributes.get("data-post"):
                self._current = {"post": attributes["data-post"], "when": None}
                self._depth = 1
                self._text_depth = None
                self._chunks = []
            # Both classes, always. js-message_text is what separates the real
            # text from the quoted preview of a reply.
            elif (self._current and self._text_depth is None
                  and {"tgme_widget_message_text", "js-message_text"} <= classes):
                self._text_depth = self._depth

        elif tag == "time" and self._current and not self._current["when"]:
            if when := attributes.get("datetime"):
                self._current["when"] = when

        elif tag == "br" and self._text_depth is not None:
            self._chunks.append("\n")

        elif tag == "a" and self._text_depth is not None and self._current is not None:
            href = (attributes.get("href") or "").strip()
            if href.startswith("http") and "t.me/" not in href:
                self._current.setdefault("links", []).append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._text_depth is not None and self._depth == self._text_depth:
            self._text_depth = None
        self._depth -= 1
        if self._current and self._depth <= 0:
            self._current["text"] = "".join(self._chunks)
            self.messages.append(self._current)
            self._current = None
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._text_depth is not None:
            self._chunks.append(data)

    def close(self) -> None:  # a truncated page still yields what it held
        super().close()
        if self._current is not None:
            self._current["text"] = "".join(self._chunks)
            self.messages.append(self._current)
            self._current = None


def _clean(text: str) -> str:
    text = html_module.unescape(text)
    text = _SPACES.sub(" ", text)
    return _BLANKS.sub("\n\n", text).strip()


def parse_channel(page: str, *, channel: str) -> list[Entry]:
    """Entries for one channel page. An unrelated page yields nothing, not an error."""
    parser = _ChannelParser()
    parser.feed(page)
    parser.close()

    entries: list[Entry] = []
    for message in parser.messages:
        text = _clean(message.get("text") or "")
        when = message.get("when")
        if not text or not when:
            # Two different things produce an empty message — a poll, which the
            # preview cannot render, and a photo with no caption. Neither has a
            # headline, and inventing one would fill the archive with rows that
            # say nothing.
            continue

        post_id = message["post"].rsplit("/", 1)[-1]
        # One link, the first. A post's headline describes one subject, so
        # attaching it to three articles would assert three things of which at
        # most one is true. Links back into t.me are the same ecosystem quoting
        # itself, not an independent source carrying the story.
        links = tuple(message.get("links", [])[:1])
        # A post is one block of text with no title field, so the first line
        # stands in for one. The whole text stays as the body: cutting a
        # headline out of a paragraph would lose the paragraph.
        first_line = text.split("\n", 1)[0].strip()
        title = first_line[:TITLE_LIMIT] if first_line else text[:TITLE_LIMIT]

        entries.append(Entry(
            title=title,
            url=f"https://t.me/{channel}/{post_id}",
            # astimezone() with no argument converts to the machine's zone, so
            # the stored timestamp would depend on where the poller runs. The
            # attribute is already UTC; this makes that explicit and portable.
            published=datetime.fromisoformat(when).astimezone(timezone.utc),
            body=text,
            body_src="message",
            links=links,
        ))
    return entries
