"""The reads. Every one of them can fail by returning less, never by raising.

That is the whole risk here: a window that quietly drops a source, a limit that
takes from the wrong end, a search that finds nothing because of how it was
escaped. None of those look like errors, and the person who would notice never
sees the output.
"""

from datetime import datetime, timedelta, timezone

import pytest

from cablegram.schema import connect
from cablegram.rss import Entry
from cablegram.sources import by_id
from cablegram.store import (items_by_ids, latest_items, search_items as _search_items,
                             store_entries)


def search_items(*args, **kwargs):
    """Rows only; the engine label has its own tests in test_render."""
    rows, _engine = _search_items(*args, **kwargs)
    return rows
from cablegram.urls import item_id

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def db():
    conn = connect()
    for source, titles in (("qbitai", ["智谱发布GLM-5", "阿里开源新模型"]),
                           ("hn", ["Zhipu releases GLM-5", "Show HN: a RAG tool"]),
                           ("habr", ["Вышла Qwen3-Max"])):
        entries = [
            Entry(t, f"https://{source}.example/{i}", NOW - timedelta(hours=i), f"body {t}", "description")
            for i, t in enumerate(titles)
        ]
        store_entries(conn, by_id(source), entries, fetched_at=iso(NOW))
    yield conn
    conn.close()


def test_the_window_is_a_floor_not_a_guess(db):
    """Half an hour back reaches the newest of each source and nothing older."""
    rows = latest_items(db, since=iso(NOW - timedelta(minutes=30)))
    assert {r["title"] for r in rows} == {"智谱发布GLM-5", "Zhipu releases GLM-5", "Вышла Qwen3-Max"}


def test_an_empty_window_is_empty_not_everything(db):
    """An off-by-one that ignores `since` returns the whole archive and looks
    like a busy day rather than a bug."""
    assert latest_items(db, since=iso(NOW + timedelta(hours=1))) == []


def test_each_source_gets_its_own_allowance(db):
    """A global limit lets cls.cn — 40 to 80 a day — bury a blog that posts
    weekly. Splitting per source is the only cut that is not an editorial call."""
    rows = latest_items(db, since=iso(NOW - timedelta(days=7)), limit_per_source=1)
    by_source = {}
    for row in rows:
        by_source.setdefault(row["source"], []).append(row)
    assert all(len(v) == 1 for v in by_source.values())
    assert len(by_source) == 3


def test_the_cut_keeps_the_newest(db):
    rows = latest_items(db, since=iso(NOW - timedelta(days=7)),
                        sources=["qbitai"], limit_per_source=1)
    assert rows[0]["title"] == "智谱发布GLM-5"


def test_totals_report_what_was_cut(db):
    """A cut that is not declared is indistinguishable from a quiet source."""
    rows = latest_items(db, since=iso(NOW - timedelta(days=7)),
                        sources=["qbitai"], limit_per_source=1)
    assert rows[0]["source_total"] == 2


def test_an_item_carries_every_source_that_had_it(db):
    """One row per sighting would repeat the story; a row with no sources loses
    who carried it, which is what wire_read prints beside an item and the one
    thing a reader asking for a single item cannot see for itself."""
    store_entries(db, by_id("kr36"),
                  [Entry("同一条", "https://qbitai.example/0", NOW, None, None)],
                  fetched_at=iso(NOW))
    rows = latest_items(db, since=iso(NOW - timedelta(days=7)), sources=["qbitai"])
    glm = [r for r in rows if r["id"] == item_id("https://qbitai.example/0")][0]
    assert sorted(glm["sources"].split(",")) == ["kr36", "qbitai"]


def test_reading_by_id_returns_the_bodies(db):
    iid = item_id("https://hn.example/0")
    rows = items_by_ids(db, [iid])
    assert rows[0]["body"].startswith("body Zhipu")


def test_an_unknown_id_is_reported_not_dropped(db):
    """Silently returning two of three lets the model believe it read all three."""
    known = item_id("https://hn.example/0")
    rows = items_by_ids(db, [known, "ffffffffffff"])
    assert [r["id"] for r in rows] == [known]


# ── search: the tool most able to lie by omission ───────────────────────────

def test_search_finds_a_latin_term(db):
    assert [r["title"] for r in search_items(db, "Zhipu", since=iso(NOW - timedelta(days=7)))] \
        == ["Zhipu releases GLM-5"]


def test_search_finds_a_three_character_chinese_term(db):
    assert search_items(db, "新模型", since=iso(NOW - timedelta(days=7)))


def test_search_finds_a_two_character_chinese_term(db):
    """Trigram cannot index these, and 智谱, 阿里 and 字节 are the common ones.
    Without the LIKE fallback the query returns nothing, with no error, and the
    model concludes nobody is talking about the company."""
    assert [r["title"] for r in search_items(db, "智谱", since=iso(NOW - timedelta(days=7)))] \
        == ["智谱发布GLM-5"]


def test_search_finds_the_headline_of_any_source_that_carried_it(db):
    """The archive holds both 智谱 and Zhipu for one story when two sources ran
    it. Searching either has to reach it."""
    rows = search_items(db, "Zhipu", since=iso(NOW - timedelta(days=7)))
    assert rows


@pytest.mark.parametrize("query", ['a"b', "a'b", "a*b", "NEAR(x y)", "a OR b", "-x", '""'])
def test_a_query_with_punctuation_does_not_raise(query):
    """FTS5 treats quotes and operators as syntax. An unescaped one raises
    OperationalError, which reaches the model as a tool crash instead of a
    result — for a query a person could plausibly type."""
    conn = connect()
    assert search_items(conn, query, since="2020-01-01T00:00:00Z") == []
    conn.close()


def test_search_respects_its_window(db):
    assert search_items(db, "Zhipu", since=iso(NOW + timedelta(hours=1))) == []


def test_search_reports_the_total_it_cut_from(db):
    """Printing 3/3 for a source holding hundreds is not an undeclared cut: it
    is a denied one, in the tool whose description is entirely about not reading
    a small number as an answer."""
    entries = [Entry(f"AI story {i}", f"https://hn.example/ai{i}", NOW, None, None)
               for i in range(40)]
    store_entries(db, by_id("hn"), entries, fetched_at=iso(NOW))

    rows = search_items(db, "AI story", since=iso(NOW - timedelta(days=7)),
                        limit_per_source=5)
    assert len(rows) == 5
    assert rows[0]["source_total"] == 40


def test_search_says_which_engine_answered(db):
    """A three-character term uses the index and a two-character one cannot, so
    their recall differs. The caller has to be able to tell them apart."""
    _, long_engine = _search_items(db, "Zhipu", since=iso(NOW - timedelta(days=7)))
    _, short_engine = _search_items(db, "智谱", since=iso(NOW - timedelta(days=7)))
    assert long_engine == "index"
    assert short_engine == "substring"


# ── seventh review: the read paths must agree about the same item ────────────

# Facts about the item rather than about one source's sighting of it. The title
# is deliberately not here: latest_items and search_items return the headline of
# the source that carried it, items_by_ids the item's own, and that difference is
# on purpose.
# Facts about the ITEM. `published` and `date_exact` are deliberately absent:
# on a listing row they are the sighting's, by design, and differ across the
# two rows one URL two sources carried. The item's own date travels as
# `item_published`, and that is the one every path has to agree on.
ITEM_LEVEL = ("id", "url", "url_norm", "lang", "item_published", "item_date_exact",
              "item_title", "target_host", "first_source", "via", "sources")


def test_every_read_path_reports_the_same_facts_about_the_same_item(db):
    """wire_read serves rows from items_by_ids against the file and rows the
    other two queries produced against the live window, so a column only one of
    them selects is a fact that exists in one mode and not the other.

    `via` was the one that lied. It marks an item nothing has published under its
    own feed — 59 in the archive today — where the headline, language, source and
    date all belong to whoever linked it, and wire_read prints a paragraph saying
    so. Selected only by items_by_ids, it came back absent rather than false, and
    live mode printed a Russian Telegram post as that channel's own dispatch
    about an English blog it never wrote. `sources` did the same to the cross
    count, which named one carrier on a line claiming two.

    Names no expected value. It asserts that the three paths agree, whatever the
    right answer turns out to be, so it cannot go stale and it covers the next
    column somebody adds to one query and not the others.
    """
    linked = "https://bfl.ai/blog/flux-video-upscale"
    store_entries(db, by_id("ai_newz"),
                  [Entry("BFL выпустили FLUX Video Upscale", "https://t.me/ai_newz/1",
                         NOW, None, None, links=(linked,))],
                  fetched_at=iso(NOW))
    # One URL two sources carried, at different times and under different
    # headlines. The fixture had none, so this test passed while wire_read
    # printed Hacker News's submission time under OpenAI's name: the paths
    # agreed on every item that had only one sighting to disagree about.
    shared = "https://openai.example/shared-post"
    store_entries(db, by_id("openai"),
                  [Entry("What OpenAI wrote", shared, NOW - timedelta(hours=10),
                         "the post", "description")], fetched_at=iso(NOW))
    store_entries(db, by_id("hn"),
                  [Entry("Submitted to HN", shared, NOW - timedelta(hours=1),
                         None, None)], fetched_at=iso(NOW))

    since = iso(NOW - timedelta(days=7))
    # The linked article is deliberately absent: latest_items and search_items
    # both filter `via = 'feed'` now, so an article nothing published under its
    # own feed reaches wire_read by id and by no other route. Asserted below
    # rather than left implied.
    ids = [item_id("https://hn.example/0"), item_id(shared)]
    paths = {
        "latest_items": latest_items(db, since=since),
        "search_items": search_items(db, "GLM", since=since)
                        + search_items(db, "FLUX", since=since)
                        + search_items(db, "OpenAI", since=since)
                        + search_items(db, "Submitted", since=since),
        "items_by_ids": items_by_ids(db, ids),
    }

    compared = 0
    for iid in ids:
        facts = {name: {k: row[k] for k in ITEM_LEVEL if k in row}
                 for name, rows in paths.items()
                 for row in rows if row["id"] == iid}
        assert len(facts) > 1, f"{iid} reached only {list(facts)}; nothing to compare"
        first, *rest = facts.items()
        for name, seen in rest:
            assert seen == first[1], (
                f"{iid} is {seen} through {name} and {first[1]} through {first[0]}; "
                f"wire_read serves both and cannot tell which one it has")
        compared += 1
    assert compared == len(ids)

    only_by_id = item_id(linked)
    assert not [r for r in paths["latest_items"] if r["id"] == only_by_id]
    assert not [r for r in paths["search_items"] if r["id"] == only_by_id]
    assert items_by_ids(db, [only_by_id]), (
        "an article only a link reached is still readable by id; it is the "
        "listings and the search that must not repeat the post under it")


def test_an_item_only_a_link_reached_is_marked_that_way_by_every_path(db):
    """The half of the agreement above that has a right answer, stated once so a
    change that makes all three agree on the wrong value still fails."""
    linked = "https://bfl.ai/blog/flux-video-upscale"
    store_entries(db, by_id("ai_newz"),
                  [Entry("BFL выпустили FLUX Video Upscale", "https://t.me/ai_newz/1",
                         NOW, None, None, links=(linked,))],
                  fetched_at=iso(NOW))

    borrowed = items_by_ids(db, [item_id(linked)])[0]
    assert borrowed["via"] == "link"
    own = items_by_ids(db, [item_id("https://hn.example/0")])[0]
    assert own["via"] == "feed", "hn published this one itself"


def test_the_total_of_each_source_counts_only_that_source(db):
    """`source_total` is what the whole CUT machinery rests on: the header sums
    it, and every internal-consistency assertion in test_render compares two
    readings of it — so a wrong value is invisible there. Both readings are
    wrong together and agree perfectly, and the reply says `5 of 1000 items` for
    a window that held 200 while every check passes.

    That is the limit of comparing a payload against itself, and the only way
    past it is to go down to the rows. Both existing tests of this field ask for
    one source, where a total over the whole result set and a total per source
    are the same number.
    """
    from collections import Counter

    rows = latest_items(db, since=iso(NOW - timedelta(days=7)))
    per_source = {r["source"]: r["source_total"] for r in rows}
    counted = Counter(r["source"] for r in rows)
    assert len(per_source) > 1, "one source cannot tell the two totals apart"
    assert per_source == dict(counted), (
        f"source_total says {per_source}, the rows actually carry {dict(counted)}")


def test_the_headline_wire_read_prints_belongs_to_the_item_it_names(db):
    """`wire_read` prints first_source, lang and via — all facts about the item —
    beside a headline. That headline used to be the sighting's: the words of
    whichever source carried it. When two sources carry one URL those are
    different sources, and the reply attributed one's words to the other.

    Measured against the real catalogue: 20 items carried by more than one
    source, 10 of them with different headlines. The worst was a Russian
    Telegram paraphrase printed under `openai en`, carrying a date that OpenAI's
    own post does not contain, with no `!!` line — because `via` is a fact about
    the item and the item had been published by a feed.

    The block headings keep the sighting's headline, which is the right one
    there: qbitai writes 智谱 where Hacker News writes Zhipu, and that pairing is
    the bridge between a Chinese story and an English query.
    """
    url = "https://openai.com/index/pacing"
    store_entries(db, by_id("openai"),
                  [Entry("Pacing model development", url, NOW, "body", "description")],
                  fetched_at=iso(NOW))
    store_entries(db, by_id("qbitai"),
                  [Entry("OpenAI暂停两周强化学习训练", url, NOW, None, None)],
                  fetched_at=iso(NOW))

    rows = {r["source"]: r for r in latest_items(db, since=iso(NOW - timedelta(days=7)))
            if r["id"] == item_id(url)}
    assert set(rows) == {"openai", "qbitai"}, "both sources carried it"

    # Every row agrees on the item's own headline, whichever source produced it.
    assert {r["item_title"] for r in rows.values()} == {"Pacing model development"}
    # And each keeps its own, for the block it appears under.
    assert rows["qbitai"]["title"] == "OpenAI暂停两周强化学习训练"
    assert rows["openai"]["title"] == "Pacing model development"


def test_each_block_shows_when_its_own_source_carried_the_story(db):
    """`item.url_norm` is UNIQUE, so one URL is one item row with one date, and
    it belonged to whichever source arrived first. In an unfiltered pass that is
    catalogue order — hn is source three and openai is four — and Hacker News
    dates a story when somebody submitted it.

    Measured against openai.com's own feed: `A milestone in expanding access to
    AI` is stamped 04:00 there and came out as 13:07 under `## openai`, nine
    hours wrong and flagged exact. The more sources a call asked for, the worse
    the answer got: asking for openai alone gave the right time.

    A sighting is "this source carried this, then", so the date belongs on it.
    Both rows are then true at once and neither source has to be judged more
    trustworthy than the other.
    """
    url = "https://openai.com/index/milestone"
    submitted = NOW.replace(hour=13, minute=7)
    published = NOW.replace(hour=4, minute=0)

    # Fetched after both stamps: a date after the fetch is filed at the fetch.
    fetched = iso(NOW + timedelta(hours=2))
    store_entries(db, by_id("hn"), [Entry("A milestone", url, submitted, None, None)],
                  fetched_at=fetched)
    store_entries(db, by_id("openai"),
                  [Entry("A milestone in expanding access to AI", url, published,
                         "body", "description")], fetched_at=fetched)

    when = {r["source"]: r["published"] for r
            in latest_items(db, since=iso(NOW - timedelta(days=1)))
            if r["id"] == item_id(url)}
    assert when == {"hn": iso(submitted), "openai": iso(published)}, (
        f"each block states when its own source carried it; got {when}")


def test_a_date_after_the_window_ends_is_outside_it(db):
    """The window had one end. A post stamped an hour ahead sat at the top of
    every listing under a header that ended an hour earlier; one stamped 2030
    led a 48-hour window and wire_sources reported the source OK with
    `newest 2030-01-01`. Nothing said the date was impossible.

    Two things closed it. The store files a date after the fetch at the fetch,
    marked — so "written tomorrow" is inside this window, at the top, under a
    ~, rather than silently absent and its source reported SILENT. And the
    query honours an upper bound as a parameter, which is still what keeps a
    real date past `until` out when a caller asks for a window that ended
    earlier."""
    ahead = "https://qbitai.example/from-the-future"
    store_entries(db, by_id("qbitai"),
                  [Entry("Written tomorrow", ahead, NOW + timedelta(days=2),
                         None, None)], fetched_at=iso(NOW))
    since, until = iso(NOW - timedelta(days=7)), iso(NOW)
    rows = {r["id"]: r for r in latest_items(db, since=since, until=until)}
    assert item_id(ahead) in rows, "filed at the fetch, it is inside the window"
    assert rows[item_id(ahead)]["published"] == iso(NOW)
    assert rows[item_id(ahead)]["date_exact"] == 0, "and marked as not the hour"
    hits = search_items(db, "tomorrow", since=since, until=until)
    assert [r["id"] for r in hits] == [item_id(ahead)], "search sees the same row"

    # The bound itself: a real date, before the fetch, after the window's end.
    late = "https://qbitai.example/after-the-window"
    store_entries(db, by_id("qbitai"),
                  [Entry("Written after the window closed", late,
                         NOW - timedelta(hours=1), None, None)], fetched_at=iso(NOW))
    until = iso(NOW - timedelta(hours=2))
    assert item_id(late) not in {r["id"] for r in latest_items(db, since=since, until=until)}
    assert not search_items(db, "closed", since=since, until=until), (
        "search has the same two ends")
    # And without an upper bound it is still there: the bound is a parameter,
    # not a filter applied behind the caller's back.
    assert item_id(late) in {r["id"] for r in latest_items(db, since=since)}


def test_a_stored_year_under_1000_sorts_where_it_belongs():
    """server._iso learned to zero-pad the year yesterday; store._utc_iso had
    the same bug. A feed stamping the year 999 stored `999-09-03T...`, which
    sorts above every real timestamp as a string and led every window as the
    newest thing published."""
    from cablegram.store import _utc_iso
    stamped = _utc_iso(datetime(999, 9, 3, tzinfo=timezone.utc))
    assert stamped.startswith("0999-"), stamped
    assert stamped < "2026-01-01T00:00:00Z", "and therefore it sorts as the past"
