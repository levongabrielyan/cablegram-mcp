"""The write path. Everything here is about what gets lost silently.

These tests used to open by calling the archive the one part of the server that
could not be rebuilt. There is no archive: every call builds a database in
memory, fills it, answers, and throws it away.

What that changes is the cost, not the care. A bug here no longer loses history
— there is none — it loses part of the answer being written right now, and it
does it without an error. A source that stored nine of eleven entries returns a
reply that looks exactly like a quiet day for the other two. That is why these
tests are heavier than the code they cover.
"""

from datetime import datetime, timezone

import pytest

from cablegram.schema import connect
from cablegram.fetch import Fetched
from cablegram.rss import Entry
from cablegram.sources import by_id
from cablegram.store import (StoreReport,
                             items_by_ids, latest_items,
                             record_attempt, search_items,
                             store_entries)
from cablegram.urls import item_id

NOW = "2026-08-30T12:00:00Z"
PUB = datetime(2026, 8, 30, 7, 12, tzinfo=timezone.utc)
GLM = "https://qbitai.com/2026/08/glm5.html"


def carriers(db, iid: str) -> int:
    """How many sources have a sighting of this item.

    Was `store.cross_count`, removed with the CROSS line that was its only
    caller: a reader who can see the same headline twice does not need the
    server to count it. The write path still has to record both sightings —
    that is what these tests are about — so the count moved here, where it is
    an assertion rather than a claim in a reply.
    """
    return db.execute("SELECT COUNT(*) FROM sighting WHERE item_id = ?",
                      (iid,)).fetchone()[0]


@pytest.fixture
def db():
    conn = connect()
    yield conn
    conn.close()


def entry(url=GLM, title="智谱发布GLM-5", published=PUB, body="正文", body_src="description"):
    return Entry(title=title, url=url, published=published, body=body, body_src=body_src)


def rows(db):
    return db.execute("SELECT * FROM item ORDER BY id").fetchall()


# ── archiving ────────────────────────────────────────────────────────────────

def test_an_entry_becomes_an_item(db):
    report = store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    assert report.new == 1
    row = rows(db)[0]
    assert row["id"] == item_id(GLM)
    assert row["title"] == "智谱发布GLM-5"
    assert row["lang"] == "zh"


def test_both_urls_are_kept(db):
    """The normalised one is the key; the original is the one a human can open."""
    store_entries(db, by_id("kr36"), [entry(url="https://m.36kr.com/p/9?utm_source=x")],
                  fetched_at=NOW)
    row = rows(db)[0]
    assert row["url"] == "https://m.36kr.com/p/9?utm_source=x"
    assert row["url_norm"] == "https://36kr.com/p/9"


def test_polling_twice_does_not_archive_twice(db):
    """Feeds repeat their whole window every few minutes. This runs constantly."""
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    report = store_entries(db, by_id("qbitai"), [entry()], fetched_at="2026-08-30T12:05:00Z")
    assert (report.new, report.seen) == (0, 1)
    assert len(rows(db)) == 1


def test_duplicates_inside_one_batch_collapse(db):
    report = store_entries(db, by_id("qbitai"), [entry(), entry(url=GLM + "?utm_source=w")],
                           fetched_at=NOW)
    assert (report.new, report.seen) == (1, 1)


def test_an_entry_without_a_url_is_skipped_not_fatal(db):
    """One malformed entry must cost that entry, not the other thirty-nine."""
    report = store_entries(db, by_id("qbitai"), [entry(url="  "), entry()], fetched_at=NOW)
    assert (report.new, report.skipped) == (1, 1)


# ── the cross-source count, which the schema could not express ───────────────

def test_the_same_story_from_two_sources_is_one_item_seen_twice(db):
    """The point of the whole design: GLM-5 in six feeds is one story, six sightings.

    With only item.first_source this is unrepresentable — the second source is dropped
    by the UNIQUE on url_norm and the count reads 1 forever, which looks like a
    story nobody else picked up rather than like a bug.
    """
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(title="Zhipu releases GLM-5")], fetched_at=NOW)

    assert len(rows(db)) == 1
    assert carriers(db, item_id(GLM)) == 2


def test_the_second_source_is_a_sighting_not_a_new_item(db):
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    report = store_entries(db, by_id("hn"), [entry()], fetched_at=NOW)
    assert (report.new, report.seen) == (0, 1)


def test_each_sighting_keeps_the_headline_that_source_used(db):
    """qbitai says 智谱, HN says Zhipu, for the same URL. Discarding the second
    throws away the only bridge between a Chinese story and an English query —
    and the feed will not serve it again."""
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(title="Zhipu releases GLM-5")], fetched_at=NOW)

    titles = {r["source"]: r["title"] for r in
              db.execute("SELECT source, title FROM sighting WHERE item_id = ?", (item_id(GLM),))}
    assert titles == {"qbitai": "智谱发布GLM-5", "hn": "Zhipu releases GLM-5"}


def test_one_source_repeating_itself_is_still_one_sighting(db):
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    store_entries(db, by_id("qbitai"), [entry()], fetched_at="2026-08-30T13:00:00Z")
    assert carriers(db, item_id(GLM)) == 1


# ── dates: the field that lies most easily ───────────────────────────────────

def test_a_real_date_is_stored_exact_and_in_utc(db):
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    row = rows(db)[0]
    assert row["published"] == "2026-08-30T07:12:00Z"
    assert row["date_exact"] == 1


def test_a_missing_date_is_marked_never_invented(db):
    """Filling it in silently makes hours=24 a lie, and on the first run every
    item looks new. The capture time is used, and flagged as approximate."""
    store_entries(db, by_id("qbitai"), [entry(published=None)], fetched_at=NOW)
    row = rows(db)[0]
    assert row["published"] == NOW
    assert row["date_exact"] == 0


def test_a_local_timezone_is_converted_not_stored_raw(db):
    moscow = datetime(2026, 8, 30, 10, 0, tzinfo=timezone(__import__("datetime").timedelta(hours=3)))
    store_entries(db, by_id("habr"), [entry(published=moscow)], fetched_at=NOW)
    assert rows(db)[0]["published"] == "2026-08-30T07:00:00Z"


# ── the two fields the reader uses to decide whether to open something ───────

def test_body_src_survives_from_the_parser(db):
    store_entries(db, by_id("qbitai"), [entry(body_src="content:encoded")], fetched_at=NOW)
    assert rows(db)[0]["body_src"] == "content:encoded"


def test_no_body_means_no_source(db):
    store_entries(db, by_id("qbitai"), [entry(body=None, body_src=None)], fetched_at=NOW)
    row = rows(db)[0]
    assert row["body"] is None and row["body_src"] is None


def test_target_host_is_recorded_for_aggregators(db):
    """HN links out. Without the destination, every headline looks equally local."""
    store_entries(db, by_id("hn"), [entry(url="https://github.com/foo/bar")], fetched_at=NOW)
    assert rows(db)[0]["target_host"] == "github.com"


def test_target_host_is_empty_for_a_source_that_publishes_its_own(db):
    """On qbitai the host is always qbitai. Printing it wastes tokens on every line."""
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    assert rows(db)[0]["target_host"] is None


# ── source_state: how a dead source stays visible ────────────────────────────



def test_a_failure_keeps_the_last_success(db):
    """Overwriting last_ok hides that a source has been mute for days — the one
    thing wire_sources exists to show."""
    record_attempt(db, Fetched("habr", ok=True, body=b"x", status=200,
                               fetched_at="2026-08-25T09:00:00Z"))
    record_attempt(db, Fetched("habr", ok=False, error="HTTP 503", fetched_at=NOW))

    state = db.execute("SELECT * FROM source_state WHERE source='habr'").fetchone()
    assert state["last_ok"] == "2026-08-25T09:00:00Z"
    assert state["last_try"] == NOW
    assert state["last_error"] == "HTTP 503"


def test_recovering_clears_the_error(db):
    record_attempt(db, Fetched("habr", ok=False, error="HTTP 503", fetched_at=NOW))
    record_attempt(db, Fetched("habr", ok=True, body=b"x", status=200,
                               fetched_at="2026-08-30T13:00:00Z"))
    state = db.execute("SELECT * FROM source_state WHERE source='habr'").fetchone()
    assert state["last_error"] is None




# ── third review, 2026-08-30: one bad entry must not cost the batch ──────────

def test_one_unparseable_entry_does_not_lose_the_rest(db):
    """The batch runs in one transaction, so anything that raises inside the loop
    rolls back everything already written for that source.

    normalise() raises on an unclosed IPv6 bracket — urlsplit's doing, uncaught
    anywhere. That is only the shortest demonstration: a hand-built date from the
    Telegram or cls parsers, a body of a type sqlite refuses, or a disk-full does
    the same. The parser already promises one bad item costs that item; the one
    module where loss is permanent promised it too and did the opposite.
    """
    report = store_entries(db, by_id("qbitai"), [
        entry(url="https://good.example/1", title="first"),
        entry(url="https://[oops/path", title="poison"),
        entry(url="https://good.example/2", title="third"),
    ], fetched_at=NOW)

    assert report.new == 2, "the two good entries must survive the bad one"
    assert report.failed == 1
    assert {r["title"] for r in rows(db)} == {"first", "third"}


def test_a_failed_entry_is_counted_not_hidden(db):
    """Counted separately from skipped: skipped is an entry the feed did not
    fill in, failed is one this code could not handle. They need opposite fixes."""
    report = store_entries(db, by_id("qbitai"),
                           [entry(url="  "), entry(url="https://[bad")], fetched_at=NOW)
    assert (report.skipped, report.failed, report.new) == (1, 1, 0)


# ── the first sighting should not freeze a worse version of the item ─────────

def test_a_real_date_replaces_a_guessed_one(db):
    """A sitemap entry under a build stamp carries no date, HN carries the
    real one, same URL. First-in
    wins means the item keeps '~approximate' for life and hours= filtering stays
    wrong — with the exact date sitting right there, never to be served again."""
    store_entries(db, by_id("anthropic"), [entry(published=None)], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(published=PUB)], fetched_at=NOW)

    row = rows(db)[0]
    assert row["date_exact"] == 1
    assert row["published"] == "2026-08-30T07:12:00Z"


def test_a_body_fills_in_where_there_was_none(db):
    store_entries(db, by_id("anthropic"), [entry(body=None, body_src=None)], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(body="the whole article", body_src="content:encoded")],
                  fetched_at=NOW)
    row = rows(db)[0]
    assert row["body"] == "the whole article"


def test_the_item_keeps_the_earliest_exact_date_it_was_given(db):
    """An article is published once. Every later sighting of it is somebody
    noticing, so between two exact dates for one URL the earlier is the closer
    to publication — a submission cannot precede the thing submitted.

    Measured against openai.com's own feed: `A milestone in expanding access to
    AI` is stamped 04:00 there, and Hacker News, which dates a story when it was
    submitted, had it at 13:07. hn is source three in the catalogue and openai
    is four, so in an unfiltered pass hn always wrote first and the item kept
    13:07 — nine hours out, flagged exact, printed under openai's own block.
    """
    later = datetime(2026, 8, 31, 13, 7, tzinfo=timezone.utc)
    earlier = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)

    store_entries(db, by_id("hn"), [entry(published=later)], fetched_at=NOW)
    assert rows(db)[0]["published"] == "2026-08-31T13:07:00Z"
    store_entries(db, by_id("openai"), [entry(published=earlier)], fetched_at=NOW)
    assert rows(db)[0]["published"] == "2026-08-31T04:00:00Z", \
        "the earlier exact date is the one closer to publication"

    # And it does not slide back and forth: a later one arriving after does
    # nothing, so the answer does not depend on the order sources were polled.
    store_entries(db, by_id("qbitai"), [entry(published=later)], fetched_at=NOW)
    assert rows(db)[0]["published"] == "2026-08-31T04:00:00Z"


def test_an_existing_body_is_never_replaced(db):
    store_entries(db, by_id("qbitai"), [entry(body="first body")], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(body="second body")], fetched_at=NOW)
    assert rows(db)[0]["body"] == "first body"


# ── an id collision is not idempotency ──────────────────────────────────────

def test_an_id_collision_is_reported_not_counted_as_seen(db):
    """INSERT OR IGNORE swallows every constraint violation, not just the one on
    url_norm. A collision would be filed as 'already archived' while the article
    is nowhere — and its sighting would hang off a different story entirely."""
    db.execute("INSERT INTO item(id, url_norm, url, first_source, lang, title, fetched_at, date_exact)"
               " VALUES (?, 'https://other.example/x', 'https://other.example/x',"
               " 'kr36', 'zh', 'unrelated story', ?, 1)", (item_id(GLM), NOW))
    db.commit()

    report = store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)

    assert report.failed == 1, "a collision is a failure, not a duplicate"
    assert report.seen == 0
    sightings = db.execute("SELECT source FROM sighting WHERE item_id = ?",
                           (item_id(GLM),)).fetchall()
    assert not sightings, "no sighting may be attached to somebody else's item"


# ── third review: item.first_source only ever names the first one ─────────────────

def test_asking_for_a_source_finds_what_that_source_carried(db):
    """the item row names whoever got there first, because url_norm is UNIQUE. Reading
    that column as "the source" makes the natural query wrong precisely when the
    design is working: a story several feeds carried would be listed under one
    of them and missing from the others.
    """
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(title="Zhipu releases GLM-5")], fetched_at=NOW)
    # A story neither of them carried. Without it, an implementation ignoring
    # the filter entirely passes: the fixture only ever had one item to return.
    store_entries(db, by_id("habr"), [entry(url="https://habr.com/ru/post/7",
                                            title="Другая новость")], fetched_at=NOW)

    from cablegram.store import items_of_source
    assert [r["id"] for r in items_of_source(db, "hn")] == [item_id(GLM)]
    assert [r["id"] for r in items_of_source(db, "qbitai")] == [item_id(GLM)]
    assert [r["id"] for r in items_of_source(db, "habr")] == [item_id("https://habr.com/ru/post/7")]


def test_a_source_shows_the_headline_it_used(db):
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(title="Zhipu releases GLM-5")], fetched_at=NOW)

    from cablegram.store import items_of_source
    assert items_of_source(db, "hn")[0]["title"] == "Zhipu releases GLM-5"


def test_a_rolled_back_entry_is_not_counted_as_archived(db):
    """new was incremented inside the transaction. If the commit then failed,
    the row went away and the count did not — the same entry reported as new and
    as failed at once.

    There is no logging anywhere in this project: the report is all a caller
    gets. One that contradicts itself is worse than none, because it will be
    believed.
    """
    db.execute("CREATE TRIGGER boom AFTER INSERT ON sighting"
               " BEGIN SELECT RAISE(ABORT, 'boom'); END")
    db.commit()

    report = store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)

    assert report.failed == 1
    assert report.new == 0, "nothing was archived, so nothing may be reported as new"
    assert len(rows(db)) == 0


# ── fourth review: fetch_all takes two windows per source, state did not ─────


def test_a_source_is_alive_if_any_of_its_windows_answered(db):
    """wire_sources asks about the source, not the endpoint."""
    from cablegram.store import source_health

    record_attempt(db, Fetched("cls", url="https://e.com/1", ok=True, body=b"x",
                               status=200, fetched_at="2026-08-30T12:00:00Z"))
    record_attempt(db, Fetched("cls", url="https://e.com/2", ok=False,
                               error="HTTP 503", fetched_at="2026-08-30T12:00:00Z"))

    health = source_health(db)["cls"]
    assert health["last_ok"] == "2026-08-30T12:00:00Z"
    assert health["last_error"] == "HTTP 503"


# ── a poll that archives nothing must not look like a quiet day ──────────────

def test_what_the_write_did_is_recorded_where_it_can_be_seen(db):
    """`failed` lived and died inside the return value. source_state knew
    whether the download worked and nothing knew whether the writing did, so
    four hundred entries failing looked exactly like four hundred already seen.
    That is how the v1 zombie stayed invisible."""
    from cablegram.store import record_write

    report = store_entries(db, by_id("qbitai"), [entry(), entry(url="https://[bad")],
                           fetched_at=NOW)
    record_write(db, report, url="https://www.qbitai.com/feed", at=NOW)

    state = db.execute("SELECT * FROM source_state WHERE source='qbitai'").fetchone()
    assert state["wrote_new"] == 1
    assert state["wrote_failed"] == 1
    assert state["last_write"] == NOW


def test_a_healthy_poll_clears_a_previous_write_failure(db):
    from cablegram.store import record_write

    url = "https://www.qbitai.com/feed"
    record_write(db, StoreReport("qbitai", failed=9), url=url, at="2026-08-29T12:00:00Z")
    record_write(db, StoreReport("qbitai", new=3), url=url, at=NOW)

    state = db.execute("SELECT * FROM source_state WHERE source='qbitai'").fetchone()
    assert (state["wrote_new"], state["wrote_failed"]) == (3, 0)


# ── a link inside a post counts as that source carrying the story ───────────

def test_a_linked_article_is_archived_and_credited_to_the_channel(db):
    """The decision this implements: a channel that links an article has carried
    that story. Without it, the six Telegram channels can never cross with
    anything, because their own URL is the permalink of the post."""
    linked = "https://openai.com/index/glm5"
    post = Entry("GLM-5 вышла", "https://t.me/ai_newz/1", PUB, "текст", "message",
                 links=(linked,))
    store_entries(db, by_id("ai_newz"), [post], fetched_at=NOW)

    assert carriers(db, item_id(linked)) == 1
    row = db.execute("SELECT * FROM item WHERE id = ?", (item_id(linked),)).fetchone()
    assert row["first_source"] == "ai_newz"
    assert row["url_norm"] == linked


def test_two_channels_linking_the_same_article_cross(db):
    """The case this is for: several channels covering one launch within hours."""
    linked = "https://openai.com/index/glm5"
    for channel, title in (("ai_newz", "GLM-5 вышла"), ("denissexy", "Про GLM-5")):
        store_entries(db, by_id(channel),
                      [Entry(title, f"https://t.me/{channel}/1", PUB, None, None,
                             links=(linked,))],
                      fetched_at=NOW)

    assert carriers(db, item_id(linked)) == 2


def test_a_linked_article_crosses_with_its_own_feed(db):
    """The strongest signal available: a Russian channel and the lab's own blog
    carrying the same URL within hours of each other."""
    linked = "https://openai.com/index/glm5"
    store_entries(db, by_id("ai_newz"),
                  [Entry("GLM-5 вышла", "https://t.me/ai_newz/1", PUB, None, None,
                         links=(linked,))], fetched_at=NOW)
    store_entries(db, by_id("openai"), [Entry("Introducing GLM-5", linked, PUB, "body",
                                              "description")], fetched_at=NOW)

    assert carriers(db, item_id(linked)) == 2
    row = db.execute("SELECT body FROM item WHERE id = ?", (item_id(linked),)).fetchone()
    assert row["body"] == "body", "the source's own body fills in the placeholder"


def test_the_post_itself_is_still_archived(db):
    """The link is an additional sighting, not a replacement: the post has its
    own text, which is what the channel actually wrote."""
    store_entries(db, by_id("ai_newz"),
                  [Entry("GLM-5 вышла", "https://t.me/ai_newz/1", PUB, "текст", "message",
                         links=("https://openai.com/index/glm5",))], fetched_at=NOW)

    assert db.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 2
    post = db.execute("SELECT * FROM item WHERE url LIKE '%t.me%'").fetchone()
    assert post["body"] == "текст"


def test_a_link_that_fails_does_not_lose_the_post(db):
    """The post is the thing the channel published; the link is a bonus."""
    store_entries(db, by_id("ai_newz"),
                  [Entry("GLM-5", "https://t.me/ai_newz/1", PUB, None, None,
                         links=("https://[malformed",))], fetched_at=NOW)

    assert db.execute("SELECT COUNT(*) FROM item WHERE url LIKE '%t.me%'").fetchone()[0] == 1


# ── sixth review: a link is a sighting, but not something the channel wrote ──

def test_a_channel_block_does_not_repeat_the_post_for_its_link(db):
    """The post and its link both created a sighting by the same source, and
    latest_items returns a row per sighting — so every Telegram post with a link
    appeared twice, with the same headline, and the block header claimed
    completeness over the inflated list. Measured live: 100 posts became 167
    rows across six sources, and the model counts 23 stories where there are 13.
    """
    store_entries(db, by_id("ai_newz"),
                  [Entry("Робо-утка за $399", "https://t.me/ai_newz/1", PUB, "текст",
                         "message", links=("https://pollen-robotics.com/microduck",))],
                  fetched_at=NOW)

    rows = latest_items(db, since="2020-01-01T00:00:00Z", sources=["ai_newz"])
    assert len(rows) == 1
    assert rows[0]["url"] == "https://t.me/ai_newz/1"


def test_the_linked_article_still_counts_towards_the_crossing(db):
    """Excluding it from the listing must not exclude it from the count: that
    count is the whole reason the link is recorded."""
    linked = "https://pollen-robotics.com/microduck"
    for channel in ("ai_newz", "data_secrets"):
        store_entries(db, by_id(channel),
                      [Entry("Робо-утка", f"https://t.me/{channel}/1", PUB, None, None,
                             links=(linked,))], fetched_at=NOW)

    assert carriers(db, item_id(linked)) == 2


def test_a_borrowed_headline_is_marked_as_borrowed(db):
    """wire_read on a referenced article asserted four things nobody checked:
    the source, the language, the publication date and that the outlet publishes
    headlines only. It has to say where the row came from."""
    linked = "https://qwen.ai/blog?id=qwen3"
    store_entries(db, by_id("ai_newz"),
                  [Entry("Вышла Qwen 3.8", "https://t.me/ai_newz/1", PUB, None, None,
                         links=(linked,))], fetched_at=NOW)

    row = items_by_ids(db, [item_id(linked)])[0]
    assert row["via"] == "link"


def test_a_reference_does_not_claim_to_know_the_publication_date(db):
    """The reference wrote date_exact=1 with the post's time, and the improving
    UPDATE only fires WHERE date_exact = 0 — so an article linked by a channel
    three days before its own feed carried it kept the channel's date, flagged
    exact, for good. wire_latest(hours=24) would never find it on the day it
    was actually published. That is the case these sources exist for."""
    linked = "https://openai.com/index/gpt6"
    early = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)
    store_entries(db, by_id("ai_newz"),
                  [Entry("Вышла GPT-6", "https://t.me/ai_newz/1", early, None, None,
                         links=(linked,))], fetched_at=NOW)

    row = db.execute("SELECT * FROM item WHERE id = ?", (item_id(linked),)).fetchone()
    assert row["date_exact"] == 0, "a reference does not know when the article came out"


def test_the_articles_own_feed_corrects_what_the_reference_guessed(db):
    """Title, language and source were guesses from the post. When the outlet's
    own feed arrives they are facts, and nothing updated them."""
    linked = "https://openai.com/index/gpt6"
    early = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)
    real = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)

    store_entries(db, by_id("ai_newz"),
                  [Entry("Вышла GPT-6", "https://t.me/ai_newz/1", early, None, None,
                         links=(linked,))], fetched_at=NOW)
    store_entries(db, by_id("openai"),
                  [Entry("Introducing GPT-6", linked, real, "the announcement",
                         "description")], fetched_at=NOW)

    row = db.execute("SELECT * FROM item WHERE id = ?", (item_id(linked),)).fetchone()
    assert row["title"] == "Introducing GPT-6"
    assert row["lang"] == "en"
    assert row["first_source"] == "openai"
    assert row["published"] == "2026-08-29T15:00:00Z"
    assert row["date_exact"] == 1


def test_a_second_channel_does_not_overwrite_a_real_headline(db):
    """Only a feed corrects a reference. Another reference must not."""
    linked = "https://openai.com/index/gpt6"
    store_entries(db, by_id("openai"),
                  [Entry("Introducing GPT-6", linked, PUB, "body", "description")],
                  fetched_at=NOW)
    store_entries(db, by_id("ai_newz"),
                  [Entry("Вышла GPT-6", "https://t.me/ai_newz/1", PUB, None, None,
                         links=(linked,))], fetched_at=NOW)

    row = db.execute("SELECT * FROM item WHERE id = ?", (item_id(linked),)).fetchone()
    assert row["title"] == "Introducing GPT-6"
    assert row["first_source"] == "openai"


def test_a_reference_reports_a_collision_like_the_main_path_does(db):
    """_store_one grew this check in an earlier round and the new path did not."""
    linked = "https://openai.com/index/gpt6"
    db.execute("INSERT INTO item(id, url_norm, url, first_source, lang, title,"
               " fetched_at, date_exact) VALUES (?,'https://other.example/x',"
               " 'https://other.example/x','kr36','zh','unrelated',?,1)",
               (item_id(linked), NOW))
    db.commit()

    report = store_entries(db, by_id("ai_newz"),
                           [Entry("Вышла GPT-6", "https://t.me/ai_newz/1", PUB, None,
                                  None, links=(linked,))], fetched_at=NOW)

    assert report.new == 1, "the post itself is archived"
    assert not db.execute("SELECT 1 FROM sighting WHERE item_id = ? AND source = 'ai_newz'"
                          " AND via = 'link'", (item_id(linked),)).fetchall()


def test_a_publisher_takes_over_from_the_aggregator_that_saw_it_first(db):
    """`first_source` is printed by wire_read where a reader looks for "the
    source", and it means whoever archived the item first. hn is source three in
    the catalogue and every publisher it aggregates comes after, so a story
    submitted to Hacker News before its own outlet's feed arrived came out as:

        ## 44f61163770f hn en ... x2[hn,openai]
        A milestone in expanding access to AI
        ChatGPT Ads reaches $1 billion in annualized revenue...

    naming hn above OpenAI's own headline, date and body — all of which the
    other updates had already corrected to OpenAI's. Nothing there is false, and
    the cross list names both; it is the position that reads as authorship.

    An aggregator is where a story was seen, not where it was published, and the
    catalogue already says which sources are which.
    """
    url = "https://openai.com/index/milestone"
    store_entries(db, by_id("hn"),
                  [Entry("A milestone", url, PUB, None, None)], fetched_at=NOW)
    assert rows(db)[0]["first_source"] == "hn"

    store_entries(db, by_id("openai"),
                  [Entry("A milestone in expanding access to AI", url, PUB,
                         "body", "description")], fetched_at=NOW)
    assert rows(db)[0]["first_source"] == "openai", \
        "the outlet that published it outranks the one that submitted it"

    # And it does not keep changing hands: a second publisher does not displace
    # the first, so the answer does not depend on poll order.
    store_entries(db, by_id("qbitai"),
                  [Entry("智谱", url, PUB, None, None)], fetched_at=NOW)
    assert rows(db)[0]["first_source"] == "openai"


def test_a_search_does_not_serve_a_post_and_its_link_as_two_separate_hits(db):
    """latest_items filters `via = 'feed'` and search_items did not, so the two
    tools disagreed about what a row is.

    A Telegram post creates two sightings for one story: the post, and the
    article it linked. The linked one has no headline of its own — nothing was
    downloaded — so it carries the post's, and a search matched both copies of
    the same text. Measured over three Russian channels:

        CABLEGRAM search "AI" | last 30d | 24 shown hits
        ## data_secrets  016ab99e8218 15:56 OpenAI закупает ...
                        ~d1a666a12b3d 15:56 OpenAI закупает ...

    Thirteen stories reported as twenty-four hits, every headline printed
    twice, under a header that states the count as fact — from the one tool
    whose whole purpose is stopping a number from being read as an answer.

    The cross count is unaffected: it is a subquery over every sighting, so an
    article two channels linked still counts twice.
    """
    linked = "https://openai.com/index/gpt6"
    store_entries(db, by_id("ai_newz"),
                  [Entry("Вышла GPT-6", "https://t.me/ai_newz/1", PUB, None, None,
                         links=(linked,))], fetched_at=NOW)

    both = db.execute("SELECT via FROM sighting WHERE source = 'ai_newz'"
                      " ORDER BY via").fetchall()
    assert [r["via"] for r in both] == ["feed", "link"], (
        "the fixture has to produce both sightings for the test to mean anything")

    rows, _ = search_items(db, "GPT-6", since="2026-08-01T00:00:00Z",
                           sources=["ai_newz"])
    assert len(rows) == 1, (
        f"one story, one hit; the search returned {len(rows)}: "
        f"{[(r['id'], r['title']) for r in rows]}")


def test_a_quoted_phrase_finds_what_the_bare_phrase_finds(db):
    """Doubling the quotes turned "Claude Code" into a search for the four
    characters «"Claude Code"». Measured on a week of Hacker News: the bare
    phrase found ten, the quoted one none — under a line saying nothing
    matched. Quoting a phrase is the most natural thing a caller does."""
    store_entries(db, by_id("hn"),
                  [Entry("Anthropic ships a Claude Code update", "https://hn.example/cc",
                         PUB, None, None)], fetched_at=NOW)
    since = "2026-08-01T00:00:00Z"
    bare, _ = search_items(db, "Claude Code", since=since)
    for spelled in ('"Claude Code"', "'Claude Code'", "Claude Code*", "  Claude Code  "):
        rows, _ = search_items(db, spelled, since=since)
        assert [r["id"] for r in rows] == [r["id"] for r in bare], spelled


def test_a_quoted_short_term_finds_what_the_bare_term_finds(db):
    """The fix above was measured on an eleven-character phrase, the one
    length where it holds either way. The engine is chosen by length before
    the quotes come off: «"ИИ"» measures four, went to the trigram index as a
    two-character phrase, and a phrase under three characters matches nothing
    there by construction. Live on 2026-09-04: «"ИИ"» on habr found nothing
    under eleven headlines carrying it, «"智谱"» on cls nothing over the one
    naming it. ИИ is the Russian abbreviation for AI; 智谱, 阿里 and 字节 are
    the two-character company names the substring fallback exists for."""
    store_entries(db, by_id("habr"),
                  [Entry("ИИ везде, кроме отчетов", "https://habr.example/ii",
                         PUB, None, None)], fetched_at=NOW)
    store_entries(db, by_id("cls"),
                  [Entry("天猫上线Token充值中心 首批接入阿里云、智谱、Kimi", "https://cls.example/1",
                         PUB, None, None)], fetched_at=NOW)
    since = "2026-08-01T00:00:00Z"
    for term in ("ИИ", "智谱", "Ki"):
        bare, engine = search_items(db, term, since=since)
        assert engine == "substring" and len(bare) == 1, (term, engine, bare)
        for spelled in (f'"{term}"', f"'{term}'", f"{term}*", f' "{term}" '):
            rows, engine = search_items(db, spelled, since=since)
            assert [r["id"] for r in rows] == [r["id"] for r in bare], (spelled, engine)
