"""The Chinese search tests are the point of this file.

With SQLite's default tokenizer every Chinese query returns zero hits without
raising anything. Three of nineteen sources would go mute and no error would ever
say so. These tests fail loudly if that regression ever comes back.
"""

import sqlite3
from datetime import datetime, timezone

import pytest

from cablegram.archive import SCHEMA_VERSION, archive_path, connect
from cablegram.urls import item_id

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

ROWS = [
    ("https://qbitai.com/2026/08/glm5.html", "qbitai", "zh",
     "智谱发布GLM-5，上下文窗口扩展至200万tokens"),
    ("https://36kr.com/p/999", "kr36", "zh", "阿里巴巴开源Qwen3-Max"),
    ("https://habr.com/ru/post/1", "habr", "ru", "Вышла Qwen3-Max: 1T параметров"),
    ("https://news.ycombinator.com/item?id=1", "hn", "en", "Zhipu releases GLM-5"),
    ("https://openai.com/index/x", "openai", "en", "Prompt caching for the Batches API"),
]


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "archive.db")
    for url, source, lang, title in ROWS:
        conn.execute(
            "INSERT INTO item(id, url_norm, url, first_source, lang, title, fetched_at, date_exact)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (item_id(url), url, url, source, lang, title, NOW),
        )
        conn.execute(
            "INSERT INTO sighting(item_id, source, title, seen_at) VALUES (?, ?, ?, ?)",
            (item_id(url), source, title, NOW),
        )
    conn.commit()
    yield conn
    conn.close()


def _search(conn, query):
    return [r["title"] for r in conn.execute(
        "SELECT s.title FROM sighting_fts f JOIN sighting s ON s.rowid = f.rowid"
        " WHERE sighting_fts MATCH ?", (f'"{query}"',)
    )]


# ── the regression that would go unnoticed ───────────────────────────────────

def test_chinese_search_finds_something(db):
    """The default tokenizer returns [] here. That is the whole reason for trigram."""
    assert _search(db, "上下文")


def test_latin_term_inside_a_chinese_headline(db):
    """'GLM' sits inside a spaceless Chinese title. The default tokenizer misses it."""
    assert any("GLM-5" in t for t in _search(db, "GLM"))


def test_russian_search(db):
    assert _search(db, "параметров")


def test_english_search(db):
    assert _search(db, "caching")


def test_two_character_chinese_needs_the_fallback(db):
    """Trigram cannot index 2-char terms — and 智谱, 阿里, 字节 are the common ones.

    Documenting the limit here so the LIKE fallback is never mistaken for
    belt-and-braces: without it these queries return nothing.
    """
    assert _search(db, "智谱") == []
    rows = db.execute("SELECT title FROM sighting WHERE title LIKE ?", ("%智谱%",)).fetchall()
    assert rows, "LIKE fallback must catch what FTS5 structurally cannot"


# ── archive mechanics ────────────────────────────────────────────────────────

def test_created_on_first_use(tmp_path):
    path = tmp_path / "nested" / "deep" / "archive.db"
    assert not path.exists()
    connect(path).close()
    assert path.exists(), "first run must create the file, with no setup step"


def test_records_when_it_started(db):
    meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
    assert meta["schema_version"] == str(SCHEMA_VERSION)
    assert meta["archive_started_at"]


def test_same_url_cannot_archive_twice(db):
    url = ROWS[0][0]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO item(id, url_norm, url, first_source, lang, title, fetched_at, date_exact)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (item_id(url), url, url, "other", "zh", "duplicate", NOW),
        )


def test_delete_leaves_no_ghost_in_the_index(db):
    db.execute("DELETE FROM item WHERE first_source = 'habr'")
    db.commit()
    assert _search(db, "параметров") == []


def test_reopening_keeps_the_history(tmp_path):
    path = tmp_path / "archive.db"
    conn = connect(path)
    conn.execute(
        "INSERT INTO item(id, url_norm, url, first_source, lang, title, fetched_at, date_exact)"
        " VALUES ('a1b2c3d4e5f6', 'u', 'u', 's', 'en', 'kept', ?, 1)", (NOW,))
    conn.commit()
    conn.close()

    conn = connect(path)
    assert conn.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 1
    conn.close()


# ── where it lives ───────────────────────────────────────────────────────────

def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CABLEGRAM_DB", str(tmp_path / "custom.db"))
    assert archive_path() == tmp_path / "custom.db"


def test_never_under_cache(monkeypatch):
    """Cache directories are declared deletable. This archive is not regenerable."""
    monkeypatch.delenv("CABLEGRAM_DB", raising=False)
    assert ".cache" not in str(archive_path()).lower()


def test_relative_xdg_is_ignored(monkeypatch):
    """The spec says a relative XDG_DATA_HOME must be ignored.

    The common `os.environ.get(...) or default` idiom does not, and would drop
    the archive into whatever directory the client was launched from.
    """
    monkeypatch.delenv("CABLEGRAM_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")
    assert archive_path().is_absolute()


def test_a_date_must_be_declared_exact_or_not(db):
    """No default: an omitted date_exact used to mean "exact", so a forgotten
    field would have claimed certainty the feed never gave."""
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO item(id, url_norm, url, first_source, lang, title, fetched_at)"
            " VALUES ('ffffffffffff', 'x', 'x', 's', 'en', 'T', ?)", (NOW,))


# ── third review: the bridge the schema documented but did not have ─────────

def test_the_english_headline_of_a_chinese_story_is_searchable(tmp_path):
    """Storing each source's own headline is only worth anything if it can be
    found. Indexing item.title alone meant Hacker News's "Zhipu" was written
    down and unreachable — the schema documenting a capability it lacked."""
    conn = connect(tmp_path / "a.db")
    url = "https://qbitai.com/2026/08/glm5.html"
    iid = item_id(url)
    conn.execute(
        "INSERT INTO item(id, url_norm, url, first_source, lang, title, fetched_at, date_exact)"
        " VALUES (?,?,?,'qbitai','zh','智谱发布GLM-5',?,1)", (iid, url, url, NOW))
    for source, title in (("qbitai", "智谱发布GLM-5"), ("hn", "Zhipu releases GLM-5")):
        conn.execute("INSERT INTO sighting(item_id, source, title, seen_at) VALUES (?,?,?,?)",
                     (iid, source, title, NOW))
    conn.commit()

    found = [r["item_id"] for r in conn.execute(
        "SELECT DISTINCT s.item_id FROM sighting_fts f"
        " JOIN sighting s ON s.rowid = f.rowid WHERE sighting_fts MATCH ?", ('"Zhipu"',))]
    assert found == [iid]
    conn.close()


def test_an_in_memory_archive_builds_the_same_schema_and_writes_no_file(tmp_path):
    """What the live mode runs on: one pass fills it, the same queries and the
    same renderer read it, and it goes away with the call.

    The seal is skipped because there is no file that another build could have
    written, and the WAL pragmas because they describe a file.
    """
    conn = connect(memory=True)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"item", "sighting", "source_state", "meta"} <= tables
    conn.execute("INSERT INTO item(id, url_norm, url, first_source, lang, title,"
                 " fetched_at, date_exact) VALUES ('a','u','u','s','en','t','n',1)")
    assert conn.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 1
    conn.close()
    assert not list(tmp_path.iterdir()), "nothing on disk"
