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
from cablegram.urls import IDENTITY

NOW = "2026-08-30T12:00:00Z"


def _row(conn, url="https://e.com/a"):
    conn.execute(
        "INSERT INTO item(id, url_norm, url, source, lang, title, fetched_at, date_exact)"
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
    conn.execute("UPDATE meta SET v = '99' WHERE k = 'schema_version'")
    conn.commit()
    conn.close()

    with pytest.raises(ArchiveMismatch, match="schema"):
        connect(path)


def test_the_error_says_what_to_do_about_it(tmp_path):
    """Nobody reads this server's output. The one message that reaches a human
    has to carry the way out, or the archive just looks broken."""
    path = tmp_path / "a.db"
    conn = connect(path)
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
