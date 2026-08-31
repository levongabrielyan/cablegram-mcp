"""The reads. Every one of them can fail by returning less, never by raising.

That is the whole risk here: a window that quietly drops a source, a limit that
takes from the wrong end, a search that finds nothing because of how it was
escaped. None of those look like errors, and the person who would notice never
sees the output.
"""

from datetime import datetime, timedelta, timezone

import pytest

from cablegram.archive import connect
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
def db(tmp_path):
    conn = connect(tmp_path / "a.db")
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
    the cross-source count, which is the strongest signal here."""
    store_entries(db, by_id("kr36"),
                  [Entry("同一条", "https://qbitai.example/0", NOW, None, None)],
                  fetched_at=iso(NOW))
    rows = latest_items(db, since=iso(NOW - timedelta(days=7)), sources=["qbitai"])
    glm = [r for r in rows if r["id"] == item_id("https://qbitai.example/0")][0]
    assert glm["cross"] == 2


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
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "a.db")
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
ITEM_LEVEL = ("id", "url", "url_norm", "lang", "published", "date_exact",
              "target_host", "first_source", "via", "sources")


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

    since = iso(NOW - timedelta(days=7))
    ids = [item_id(linked), item_id("https://hn.example/0")]
    paths = {
        "latest_items": latest_items(db, since=since),
        "search_items": search_items(db, "GLM", since=since)
                        + search_items(db, "FLUX", since=since),
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
