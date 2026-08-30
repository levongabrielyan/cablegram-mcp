"""The seal that says which recipe wrote the ids already in the archive.

An id is a pure function of a URL, so the archive only holds together while that
function stays fixed. Change it and every id already written is orphaned: still
there, never matched again, and re-inserted as a new item on the next poll. The
archive would not shrink or error — it would quietly double.

Nothing here checks that the ids are *good*. It checks that they cannot change
without somebody being told.
"""

import pytest

from cablegram.archive import SCHEMA_VERSION, ArchiveMismatch, connect
from cablegram.urls import IDENTITY, IDENTITY_RECIPE

NOW = "2026-08-30T12:00:00Z"


def _row(conn, url="https://e.com/a"):
    conn.execute(
        "INSERT INTO item(id, url_norm, url, first_source, lang, title, fetched_at, date_exact)"
        " VALUES ('deadbeef0001', ?, ?, 's', 'en', 'T', ?, 1)", (url, url, NOW))
    conn.commit()


def test_identity_is_stamped_on_creation(tmp_path):
    conn = connect(tmp_path / "a.db")
    meta = dict(conn.execute("SELECT k, v FROM meta").fetchall())
    assert meta["id_algo"] == IDENTITY
    assert meta["schema_version"] == str(SCHEMA_VERSION)
    conn.close()


def test_a_changed_recipe_refuses_to_open(tmp_path):
    """The failure this whole file exists for: silent double-archiving."""
    path = tmp_path / "a.db"
    conn = connect(path)
    _row(conn)
    conn.execute("UPDATE meta SET v = 'sha1[:8]/v1' WHERE k = 'id_algo'")
    conn.commit()
    conn.close()

    with pytest.raises(ArchiveMismatch) as exc:
        connect(path)
    assert "sha1[:8]/v1" in str(exc.value) and IDENTITY in str(exc.value)


def test_a_future_schema_refuses_to_open(tmp_path):
    """An older build must not write into a newer archive it cannot read."""
    path = tmp_path / "a.db"
    conn = connect(path)
    _row(conn)  # a guard on an empty archive is meaningless
    conn.execute("UPDATE meta SET v = '99' WHERE k = 'schema_version'")  # a future build
    conn.commit()
    conn.close()

    with pytest.raises(ArchiveMismatch, match="newer build"):
        connect(path)


def test_the_error_says_what_to_do_about_it(tmp_path):
    """Nobody reads this server's output. The one message that reaches a human
    has to carry the way out, or the archive just looks broken."""
    path = tmp_path / "a.db"
    conn = connect(path)
    _row(conn)  # a guard on an empty archive is meaningless
    conn.execute("UPDATE meta SET v = 'sha1[:8]/v1' WHERE k = 'id_algo'")
    conn.commit()
    conn.close()

    with pytest.raises(ArchiveMismatch) as exc:
        connect(path)
    assert "CABLEGRAM_DB" in str(exc.value)
    assert str(path) in str(exc.value)


def test_an_unsealed_archive_holding_items_refuses(tmp_path):
    """Rows written before the seal existed were made by an unknown recipe.

    Adopting them would stamp today's identity on ids that may not match it —
    the exact lie the seal exists to prevent.
    """
    path = tmp_path / "a.db"
    conn = connect(path)
    _row(conn)
    conn.execute("DELETE FROM meta WHERE k = 'id_algo'")
    conn.commit()
    conn.close()

    with pytest.raises(ArchiveMismatch, match="unsealed|unknown"):
        connect(path)


def test_an_unsealed_but_empty_archive_is_adopted(tmp_path):
    """With no rows there is nothing to be wrong about: seal it and carry on."""
    path = tmp_path / "a.db"
    connect(path).close()
    conn = connect(path)
    conn.execute("DELETE FROM meta WHERE k = 'id_algo'")
    conn.commit()
    conn.close()

    conn = connect(path)
    assert dict(conn.execute("SELECT k, v FROM meta"))["id_algo"] == IDENTITY
    conn.close()


def test_reopening_an_untouched_archive_is_fine(tmp_path):
    """The guard must not fire on the normal path — it runs on every open."""
    path = tmp_path / "a.db"
    conn = connect(path)
    _row(conn)
    conn.close()
    conn = connect(path)
    assert conn.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 1
    conn.close()


# ── third review: the seal missed the change most likely to happen ───────────

def test_the_recipe_moves_when_the_denylist_does():
    """The denylist is the one part of urls.py designed to grow — new tracking
    parameters appear every month — and a comment exempted exactly that from the
    seal. Adding one key changes the id of every URL carrying it, and those
    re-archive as duplicates with nothing to say so.
    """
    import cablegram.urls as urls

    before = urls.id_recipe()
    original = urls._DROP_QUERY
    try:
        urls._DROP_QUERY = frozenset(set(original) | {"ref"})
        assert urls.id_recipe() != before
    finally:
        urls._DROP_QUERY = original
    assert urls.id_recipe() == before


def test_a_changed_recipe_is_resealed_and_recorded(tmp_path):
    """Not a refusal: a new tracking key changes a handful of ids, not all of
    them, so throwing away months of history would cost more than the duplicates
    it prevents. It is written down instead, with the date and what it was."""
    path = tmp_path / "a.db"
    conn = connect(path)
    _row(conn)
    conn.execute("UPDATE meta SET v = 'oldrec' WHERE k = 'id_recipe'")
    conn.commit()
    conn.close()

    conn = connect(path)  # opens, does not raise
    meta = dict(conn.execute("SELECT k, v FROM meta").fetchall())
    assert meta["id_recipe"] == IDENTITY_RECIPE
    assert meta["id_recipe_previous"] == "oldrec"
    assert meta["id_recipe_changed_at"]
    conn.close()


def test_a_changed_algorithm_is_still_a_refusal(tmp_path):
    """The hard case stays hard: a different length or version reassigns every
    id in the archive, not a handful."""
    path = tmp_path / "a.db"
    conn = connect(path)
    _row(conn)
    conn.execute("UPDATE meta SET v = 'sha1[:8]/v1' WHERE k = 'id_algo'")
    conn.commit()
    conn.close()

    with pytest.raises(ArchiveMismatch):
        connect(path)


def test_an_empty_archive_is_adopted_whatever_it_claims(tmp_path):
    """_seal's docstring promised this and the code did not do it: it compared
    the identity before looking at whether anything was stored. The refusal then
    said 'reusing it would archive everything in it a second time' about a file
    holding nothing, and offered no way out but deleting it."""
    path = tmp_path / "a.db"
    conn = connect(path)
    conn.execute("UPDATE meta SET v = 'sha1[:8]/v1' WHERE k = 'id_algo'")
    conn.commit()
    conn.close()

    conn = connect(path)
    assert dict(conn.execute("SELECT k, v FROM meta"))["id_algo"] == IDENTITY
    conn.close()


# ── fourth review: the label said v2, the columns said v1 ────────────────────

def _old_shape(path):
    """An archive from the previous build: v1 columns, and meta saying so."""
    import sqlite3
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE item (id TEXT PRIMARY KEY, url_norm TEXT NOT NULL UNIQUE,
            url TEXT NOT NULL, source TEXT NOT NULL, lang TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT, body_kind TEXT, published TEXT,
            date_exact INTEGER NOT NULL, fetched_at TEXT NOT NULL, target_host TEXT);
        CREATE TABLE sighting (item_id TEXT NOT NULL, source TEXT NOT NULL,
            title TEXT NOT NULL, seen_at TEXT NOT NULL, PRIMARY KEY (item_id, source));
        CREATE TABLE source_state (source TEXT PRIMARY KEY, last_ok TEXT, last_try TEXT,
            last_error TEXT, etag TEXT, last_mod TEXT);
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    db.executemany("INSERT INTO meta(k, v) VALUES (?, ?)",
                   [("schema_version", "1"), ("id_algo", "sha1[:12]/v1")])
    db.commit()
    return db


def test_an_empty_archive_of_the_old_shape_is_rebuilt(tmp_path):
    """The zombie: adopting an empty archive skipped every check, but
    CREATE TABLE IF NOT EXISTS leaves the old columns in place. The file was
    re-stamped v2, never questioned again, and every insert failed forever —
    counted as `failed`, which nothing reads. A server that starts, answers,
    and returns an empty list indistinguishable from a quiet day.
    """
    path = tmp_path / "a.db"
    _old_shape(path).close()

    conn = connect(path)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(item)")}
    assert "first_source" in columns and "source" not in columns
    assert "body_src" in columns

    conn.execute("INSERT INTO item(id, url_norm, url, first_source, lang, title,"
                 " fetched_at, date_exact) VALUES ('a','u','u','s','en','t',?,1)", (NOW,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 1
    conn.close()


def test_a_populated_archive_of_the_old_shape_is_refused(tmp_path):
    """With rows in it there is history to protect, so it is not rebuilt."""
    path = tmp_path / "a.db"
    db = _old_shape(path)
    db.execute("INSERT INTO item(id, url_norm, url, source, lang, title, fetched_at,"
               " date_exact) VALUES ('a','u','u','s','en','t',?,1)", (NOW,))
    db.commit()
    db.close()

    with pytest.raises(ArchiveMismatch, match="older build"):
        connect(path)


def test_the_check_reads_the_columns_not_the_label(tmp_path):
    """A file whose meta was lost or edited but whose shape is right is usable.
    Trusting the label got this backwards in both directions at once."""
    path = tmp_path / "a.db"
    conn = connect(path)
    _row(conn)
    conn.execute("DELETE FROM meta WHERE k = 'schema_version'")
    conn.commit()
    conn.close()

    conn = connect(path)  # shape is current, so the missing label is recoverable
    assert dict(conn.execute("SELECT k, v FROM meta"))["schema_version"] == str(SCHEMA_VERSION)
    conn.close()
