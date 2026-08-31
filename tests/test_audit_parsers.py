"""Assertions for the parsers a mutation walked straight through.

The shared failure mode here is not a crash: every one of these produces a
well-formed entry carrying a wrong fact — a real date under somebody else's
headline, a summary belonging to another post, a channel reported dead because
one message had no timestamp.
"""

from datetime import datetime, timedelta, timezone

import pytest

from cablegram.cls import parse_response
from cablegram.hn import parse_search, rows_returned as hn_rows
from cablegram.hub import parse_models
from cablegram.nextjs import parse_next_payload
from cablegram.rss import MAX_FIELD
from cablegram.telegram import parse_channel
from cablegram.urls import item_id, normalise


def chunk(payload: str) -> bytes:
    """One `self.__next_f.push` chunk holding `payload`, the way the page ships it."""
    import json

    return (b'<script>self.__next_f.push([1,'
            + json.dumps(payload).encode() + b'])</script>')


# ── anthropic, through Next.js ──────────────────────────────────────────────

def test_an_untitled_post_does_not_borrow_the_next_one_s_headline():
    """The one outcome worse than no entry at all: a real date, a real slug and
    somebody else's headline.

    `mid` is tempered so it cannot cross a `publishedOn`. Untempered, a record
    whose own title is missing runs on into the following record and takes its
    title — and the result reads as a correctly parsed post in every respect a
    reader can check.
    """
    payload = (
        '"publishedOn":"2026-08-01T00:00:00Z","slug":{"_type":"slug","current":"first"},'
        '"publishedOn":"2026-08-20T00:00:00Z","slug":{"_type":"slug","current":"second"},'
        '"title":"Claude 5 is available today"'
    )
    entries = parse_next_payload(chunk(payload), base="https://www.anthropic.com/news")

    titles = {e.url.rsplit("/", 1)[-1]: e.title for e in entries}
    assert titles.get("second") == "Claude 5 is available today"
    assert "first" not in titles, (
        f"the post dated 2026-08-01 has no title of its own and was given "
        f"{titles.get('first')!r}, which belongs to the post below it")


def test_each_post_gets_its_own_summary_and_not_the_first_one_in_the_page():
    """`summary` is searched inside the record, not across the document. Widened
    to the whole payload every post on the page carries the first post's
    summary: 270 dispatches on /news, each a correct headline over the wrong
    body, with nothing in the reply that could disagree."""
    payload = (
        '"publishedOn":"2026-08-01T00:00:00Z","slug":{"_type":"slug","current":"first"},'
        '"summary":"About the first post","title":"First post",'
        '"publishedOn":"2026-08-20T00:00:00Z","slug":{"_type":"slug","current":"second"},'
        '"title":"Second post"'
    )
    entries = {e.title: e.body for e in
               parse_next_payload(chunk(payload),
                                  base="https://www.anthropic.com/news")}
    assert entries["First post"] == "About the first post"
    assert entries["Second post"] is None, (
        f"the second post ships no summary and was given "
        f"{entries['Second post']!r}, which describes the first")


def test_a_post_whose_date_will_not_parse_is_left_undated_not_dated_today():
    """This module exists because a sitemap's `lastmod` put a 2022 paper into a
    one-week window. Inventing a timestamp does the same thing from the other
    end: an unparseable date becomes the current instant, so the post lands
    inside every window anyone asks for, marked exact."""
    payload = ('"publishedOn":"2026-13-45T99:99:99Z",'
               '"slug":{"_type":"slug","current":"broken"},"title":"A post"')
    entries = parse_next_payload(chunk(payload),
                                 base="https://www.anthropic.com/news")
    assert entries and entries[0].published is None, (
        f"an unreadable date became {entries[0].published}, which is inside "
        f"every window this server asks about")


def test_the_url_a_post_gets_is_the_one_its_own_page_has():
    """The id is a pure function of the URL, so a doubled slash is a different
    article for the rest of its life: the same post archived from /news and
    from /news/ never meets itself, and the cross-source count — the strongest
    signal here — silently drops by one every time."""
    payload = ('"publishedOn":"2026-08-20T00:00:00Z",'
               '"slug":{"_type":"slug","current":"claude-5"},"title":"Claude 5"')
    trailing = parse_next_payload(chunk(payload),
                                  base="https://www.anthropic.com/news/")[0]
    plain = parse_next_payload(chunk(payload),
                               base="https://www.anthropic.com/news")[0]
    assert item_id(trailing.url) == item_id(plain.url), (
        f"{trailing.url} and {plain.url} archive as two different articles")


# ── the model hub ───────────────────────────────────────────────────────────

def test_a_response_without_the_ordering_figure_says_so_instead_of_printing_none():
    """The body is the only place the ordering travels, and the description
    tells the model to judge the listing by it. "trend None" reads as a score of
    None — a real value, low — for a field the response simply did not carry."""
    entries = parse_models([{"id": "org/model", "likes": 10, "downloads": 20,
                             "createdAt": "2026-08-30T10:00:00.000Z"}])
    assert "None" not in entries[0].body, (
        f"a missing figure is printed as a value: {entries[0].body!r}")
    # The property, not the sentence. This used to assert the exact wording, and
    # that wording was removed for being false: an author listing is ordered by
    # date, so calling the trend "the ordering" and then reporting it missing
    # made two false claims in one line. What has to hold is that an absent
    # figure is never presented as a value, and that nothing claims a ranking
    # that did not happen.
    assert "trend" not in entries[0].body, (
        f"nothing ordered this by trend: {entries[0].body!r}")
    assert "not ranked" in entries[0].body


def test_a_repo_identified_only_by_modelId_is_still_read():
    """`id` and `modelId` are the same field under two names, and the filter
    that drops third-party requantisations already reads both. Reading only one
    in the line that builds the entry drops every row of a response that uses
    the other — a whole source going quiet with the endpoint answering 200."""
    entries = parse_models([{"modelId": "deepseek-ai/DeepSeek-V4",
                             "createdAt": "2026-08-30T10:00:00.000Z"}])
    assert [e.title for e in entries] == ["deepseek-ai/DeepSeek-V4"]


# ── telegram ────────────────────────────────────────────────────────────────

CHANNEL = """
<div class="tgme_widget_message" data-post="ai_newz/1">
  <div class="tgme_widget_message_text js-message_text">Первый пост</div>
</div>
<div class="tgme_widget_message" data-post="ai_newz/2">
  <time datetime="2026-08-30T08:00:00+00:00">x</time>
  <div class="tgme_widget_message_text js-message_text">GLM-5 вышла</div>
</div>
"""


def test_one_message_with_no_timestamp_does_not_take_the_channel_with_it():
    """A preview page carries whatever Telegram rendered, and a message with no
    `time` element is ordinary. Passed on to `fromisoformat` it raises, the
    poller files the whole channel as `unparseable`, and six channels of
    practitioners go dark because one post was a poll."""
    entries = parse_channel(CHANNEL, channel="ai_newz")
    assert [e.title for e in entries] == ["GLM-5 вышла"], (
        "the dated message survives and the undated one is dropped, not the "
        "other way round")


def test_a_very_long_post_does_not_become_a_very_long_headline():
    """The adapters build their `Entry` by hand, so nothing else caps them. A
    24,000-character post reaches `item.title`, `sighting.title` and the trigram
    index untouched — and the listing prints it as one dispatch line, which is
    the whole payload."""
    long_post = "А" * 30_000
    page = ('<div class="tgme_widget_message" data-post="ai_newz/3">'
            '<time datetime="2026-08-30T08:00:00+00:00">x</time>'
            f'<div class="tgme_widget_message_text js-message_text">{long_post}</div>'
            '</div>')
    entry = parse_channel(page, channel="ai_newz")[0]
    assert len(entry.title) <= 300
    assert len(entry.body) <= MAX_FIELD


# ── cls.cn ──────────────────────────────────────────────────────────────────

def test_a_refusal_that_is_not_a_number_is_still_a_refusal():
    """`errno` is 0 as an int on success and a string on failure, and the whole
    check exists because this endpoint answers a rejected request with HTTP 200.
    Coerced with `int()` a non-numeric code raises ValueError from the
    comparison rather than from the refusal — the same class the poller files as
    `unparseable`, so a signing scheme that changed is reported as a document
    that would not parse, and the message says so."""
    with pytest.raises(ValueError) as raised:
        parse_response({"errno": "AUTH_FAILED", "msg": "sign error", "data": []})
    assert "errno=AUTH_FAILED" in str(raised.value), (
        f"the refusal has to be reported as a refusal, naming the code the "
        f"envelope carried: {raised.value}")
    assert "signing scheme" in str(raised.value), (
        "and the diagnosis, which is the reason this check exists")


# ── hacker news ─────────────────────────────────────────────────────────────

def test_an_ask_hn_with_no_url_key_at_all_still_becomes_an_entry():
    """In 30 of 30 Ask HN items the string "url" does not appear anywhere in the
    object — not null, absent. Subscripting raises on every one of them, and Ask
    HN is where people say what they actually use."""
    entries = parse_search({"hits": [
        {"title": "Ask HN: what are you running locally?",
         "created_at_i": 1_756_000_000, "objectID": "44444444"}]})
    assert entries and entries[0].url.endswith("id=44444444")


def test_the_ceiling_is_measured_on_what_arrived_not_on_what_survived():
    """One Ask HN with no objectID took the count from 1000 to 999 and the
    marker stopped firing for the whole pass. It means "the endpoint returned
    all it could", so it is a fact about the response — measured after filtering
    it can only ever be an undercount, in the direction that hides the warning.
    """
    payload = {"hits": [{"title": "story", "created_at_i": 1, "objectID": str(i)}
                        for i in range(9)] + [{"title": "no id", "created_at_i": 1}]}
    assert hn_rows(payload) == 10
    assert len(parse_search(payload)) == 9


def test_a_story_posted_at_the_epoch_is_not_dropped_for_being_falsy():
    """`created_at_i` of 0 and an objectID of "0" are values. Tested for truth
    they are dropped silently, and a filter that removes an item for holding a
    legitimate zero removes it from a listing whose header counts what survived.
    """
    entries = parse_search({"hits": [
        {"title": "The first story", "created_at_i": 0, "objectID": 0}]})
    assert len(entries) == 1


# ── url identity ────────────────────────────────────────────────────────────

def test_a_post_with_no_headline_of_its_own_is_dropped_rather_than_named():
    """An entry whose headline is a placeholder is a dispatch line that says
    nothing, occupying a slot in a per-source limit a real one would have had —
    and the block heading above it counts it as something the source carried."""
    payload = ('"publishedOn":"2026-08-20T00:00:00Z",'
               '"slug":{"_type":"slug","current":"blank"},"title":""')
    assert parse_next_payload(chunk(payload),
                              base="https://www.anthropic.com/news") == []


def test_a_two_label_host_keeps_its_own_name():
    """`m.` and `amp.` are mobile prefixes on `m.36kr.com`. Stripped from a
    two-label host, `m.io` becomes `io` and `www.ai` becomes `ai` — two
    unrelated sites merged into one, and `url_norm` is UNIQUE, so the second
    article never reaches a reply and nothing reports it."""
    assert normalise("https://m.io/post/1") == "https://m.io/post/1"
    assert normalise("https://m.36kr.com/p/9") == "https://36kr.com/p/9"


def test_a_parameter_present_and_empty_is_not_the_same_as_absent():
    """`?page=` and no query at all are two URLs a site can serve differently,
    and dropping the blank one merges them. Everything in this module errs
    towards keeping URLs apart, because merging is the unrecoverable direction:
    `url_norm` is UNIQUE and the loser is never seen again."""
    assert normalise("https://x.com/a?page=") != normalise("https://x.com/a")


def test_a_default_port_does_not_split_a_site_from_itself():
    """`https://x.com:443/a` and `https://x.com/a` are the same page, and a feed
    that spells one and a link that spells the other archive as two items —
    which halves a cross-source count without any of the counts looking wrong."""
    assert normalise("https://x.com:443/a") == normalise("https://x.com/a")
    assert normalise("https://x.com:8080/a") != normalise("https://x.com/a")


def test_the_recipe_moves_when_the_lists_that_decide_identity_move():
    """`NORMALISE_VERSION` is remembered by hand and the denylist is the part of
    this module designed to grow. Adding one key changes the id of every URL
    carrying it, and those re-archive as duplicates with nothing to say so — so
    the fingerprint has to be taken over the lists themselves, not over the
    number somebody has to remember to bump."""
    import cablegram.urls as urls

    before = urls.id_recipe()
    original = urls._DROP_QUERY
    urls._DROP_QUERY = frozenset(original | {"a_new_tracking_key"})
    try:
        assert urls.id_recipe() != before, (
            "a key was added to the denylist and the fingerprint did not move")
    finally:
        urls._DROP_QUERY = original
