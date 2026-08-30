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


def entry(url=GLM, title="智谱发布GLM-5", published=PUB, body="正文", body_kind="teaser"):
    return Entry(title=title, url=url, published=published, body=body, body_kind=body_kind)


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

def test_body_kind_survives_from_the_parser(db):
    store_entries(db, by_id("qbitai"), [entry(body_kind="full")], fetched_at=NOW)
    assert rows(db)[0]["body_kind"] == "full"


def test_no_body_means_no_kind(db):
    store_entries(db, by_id("qbitai"), [entry(body=None, body_kind=None)], fetched_at=NOW)
    row = rows(db)[0]
    assert row["body"] is None and row["body_kind"] is None


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
