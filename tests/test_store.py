"""The write path. Everything here is about what gets lost silently.

The archive is the one part of this server that cannot be rebuilt: the feeds no
longer hold what it holds. So a bug on this path does not cost a request, it
costs history — and history goes missing without an error, which is why these
tests are heavier than the code they cover.
"""

from datetime import datetime, timezone

import pytest

from cablegram.archive import connect
from cablegram.fetch import Fetched
from cablegram.rss import Entry
from cablegram.sources import by_id
from cablegram.store import (conditional_headers, cross_count, record_attempt,
                             store_entries)
from cablegram.urls import item_id

NOW = "2026-08-30T12:00:00Z"
PUB = datetime(2026, 8, 30, 7, 12, tzinfo=timezone.utc)
GLM = "https://qbitai.com/2026/08/glm5.html"


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "a.db")
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

    With only item.source this is unrepresentable — the second source is dropped
    by the UNIQUE on url_norm and the count reads 1 forever, which looks like a
    story nobody else picked up rather than like a bug.
    """
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(title="Zhipu releases GLM-5")], fetched_at=NOW)

    assert len(rows(db)) == 1
    assert cross_count(db, item_id(GLM)) == 2


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
    assert cross_count(db, item_id(GLM)) == 1


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

def test_success_records_the_validators_for_next_time(db):
    record_attempt(db, Fetched("qbitai", ok=True, body=b"x", status=200,
                               etag='W/"abc"', last_modified="Sat, 30 Aug 2026 06:00:00 GMT",
                               fetched_at=NOW))
    state = db.execute("SELECT * FROM source_state WHERE source='qbitai'").fetchone()
    assert state["last_ok"] == NOW and state["last_error"] is None
    assert state["etag"] == 'W/"abc"'


def test_304_counts_as_alive(db):
    """Not new content, but proof the source answered. Treating it as a failure
    would make a quiet week look like an outage."""
    record_attempt(db, Fetched("n8n", ok=True, status=304, unchanged=True, fetched_at=NOW))
    state = db.execute("SELECT * FROM source_state WHERE source='n8n'").fetchone()
    assert state["last_ok"] == NOW and state["last_error"] is None


def test_a_failure_keeps_the_last_success_and_the_etag(db):
    """Overwriting last_ok hides that a source has been mute for days — the one
    thing wire_sources exists to show. Wiping the etag re-downloads everything."""
    record_attempt(db, Fetched("habr", ok=True, body=b"x", status=200, etag='W/"v1"',
                               fetched_at="2026-08-25T09:00:00Z"))
    record_attempt(db, Fetched("habr", ok=False, error="HTTP 503", fetched_at=NOW))

    state = db.execute("SELECT * FROM source_state WHERE source='habr'").fetchone()
    assert state["last_ok"] == "2026-08-25T09:00:00Z"
    assert state["last_try"] == NOW
    assert state["last_error"] == "HTTP 503"
    assert state["etag"] == 'W/"v1"'


def test_recovering_clears_the_error(db):
    record_attempt(db, Fetched("habr", ok=False, error="HTTP 503", fetched_at=NOW))
    record_attempt(db, Fetched("habr", ok=True, body=b"x", status=200,
                               fetched_at="2026-08-30T13:00:00Z"))
    state = db.execute("SELECT * FROM source_state WHERE source='habr'").fetchone()
    assert state["last_error"] is None


def test_conditional_headers_are_handed_back_for_the_next_poll(db):
    record_attempt(db, Fetched("qbitai", ok=True, body=b"x", status=200, etag='W/"abc"',
                               last_modified="Sat, 30 Aug 2026 06:00:00 GMT", fetched_at=NOW))
    assert conditional_headers(db)["qbitai"] == ('W/"abc"', "Sat, 30 Aug 2026 06:00:00 GMT")


def test_an_unknown_source_asks_for_everything(db):
    assert conditional_headers(db).get("openai") is None


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
    """productradar carries no date, HN carries the real one, same URL. First-in
    wins means the item keeps '~approximate' for life and hours= filtering stays
    wrong — with the exact date sitting right there, never to be served again."""
    store_entries(db, by_id("productradar"), [entry(published=None)], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(published=PUB)], fetched_at=NOW)

    row = rows(db)[0]
    assert row["date_exact"] == 1
    assert row["published"] == "2026-08-30T07:12:00Z"


def test_a_body_fills_in_where_there_was_none(db):
    store_entries(db, by_id("productradar"), [entry(body=None, body_src=None)], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(body="the whole article", body_src="content:encoded")],
                  fetched_at=NOW)
    row = rows(db)[0]
    assert row["body"] == "the whole article"


def test_an_exact_date_is_never_overwritten(db):
    """Only ever improve. A second source's date must not displace a real one."""
    store_entries(db, by_id("qbitai"), [entry(published=PUB)], fetched_at=NOW)
    other = datetime(2020, 1, 1, tzinfo=timezone.utc)
    store_entries(db, by_id("hn"), [entry(published=other)], fetched_at=NOW)
    assert rows(db)[0]["published"] == "2026-08-30T07:12:00Z"


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


# ── third review: item.source only ever names the first one ─────────────────

def test_asking_for_a_source_finds_what_that_source_carried(db):
    """item names whoever got there first, because url_norm is UNIQUE. Reading
    that column as "the source" makes the natural query wrong precisely when the
    design is working: a story several feeds carried would be listed under one
    of them and missing from the others.
    """
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(title="Zhipu releases GLM-5")], fetched_at=NOW)

    from cablegram.store import items_of_source
    assert [r["id"] for r in items_of_source(db, "hn")] == [item_id(GLM)]
    assert [r["id"] for r in items_of_source(db, "qbitai")] == [item_id(GLM)]


def test_a_source_shows_the_headline_it_used(db):
    store_entries(db, by_id("qbitai"), [entry()], fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry(title="Zhipu releases GLM-5")], fetched_at=NOW)

    from cablegram.store import items_of_source
    assert items_of_source(db, "hn")[0]["title"] == "Zhipu releases GLM-5"


def test_cross_counts_come_back_in_one_query(db):
    """One query per item is 210 queries for a normal day's wire_latest."""
    store_entries(db, by_id("qbitai"), [entry(), entry(url="https://qbitai.com/b")],
                  fetched_at=NOW)
    store_entries(db, by_id("hn"), [entry()], fetched_at=NOW)

    from cablegram.store import cross_counts
    counts = cross_counts(db, [item_id(GLM), item_id("https://qbitai.com/b")])
    assert counts == {item_id(GLM): 2, item_id("https://qbitai.com/b"): 1}


def test_a_304_does_not_clear_the_validators(db):
    """It works today only because fetch_one echoes back the validator it sent.

    Nothing writes that coupling down, so the day a hand-written fetcher — cls
    or Telegram — reports a 304 without echoing it, record_attempt would null
    the etag on a *success* path and every poll would re-download the whole feed
    for good. No error, just a bill.
    """
    record_attempt(db, Fetched("qbitai", ok=True, body=b"x", status=200,
                               etag='W/"v1"', last_modified="Sat, 30 Aug 2026 06:00:00 GMT",
                               fetched_at="2026-08-30T12:00:00Z"))
    record_attempt(db, Fetched("qbitai", ok=True, status=304, unchanged=True,
                               fetched_at="2026-08-30T12:05:00Z"))  # no validators echoed

    state = db.execute("SELECT * FROM source_state WHERE source='qbitai'").fetchone()
    assert state["etag"] == 'W/"v1"'
    assert state["last_mod"] == "Sat, 30 Aug 2026 06:00:00 GMT"
    assert state["last_ok"] == "2026-08-30T12:05:00Z", "still a success"
