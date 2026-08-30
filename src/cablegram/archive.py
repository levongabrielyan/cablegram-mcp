"""The archive: a local SQLite file that remembers what the feeds forget.

RSS feeds expose only their last 10-40 entries. Last week is unrecoverable — no
endpoint, no pagination, no public archive. Everything else in this server can be
rewritten in an afternoon; this file is the only part whose value depends on when
it was started.

It lives outside the project directory on purpose: an installed MCP server is
launched from whatever directory the client happens to be in, so a relative path
would scatter several half-archives across the disk and silently lose history.
"""

from __future__ import annotations

import functools
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .urls import IDENTITY, id_recipe

__all__ = ["archive_path", "connect", "SCHEMA_VERSION", "ArchiveMismatch"]

SCHEMA_VERSION = 4


class ArchiveMismatch(RuntimeError):
    """The archive on disk was written by a build that disagrees with this one."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS item (
    id           TEXT PRIMARY KEY,
    url_norm     TEXT NOT NULL UNIQUE,
    url          TEXT NOT NULL,
    first_source TEXT NOT NULL,   -- whoever archived it first; see `sighting`
    lang         TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT,
    body_src     TEXT,            -- the element it came from, not a judgement
    published    TEXT,
    date_exact   INTEGER NOT NULL,
    fetched_at   TEXT NOT NULL,
    target_host  TEXT
);

CREATE INDEX IF NOT EXISTS idx_item_pub        ON item(published DESC);
-- No index on first_source on purpose: grouping by it is the mistake the
-- column's old name invited. Anything asking "what did this source carry"
-- reads `sighting`, which is the only place that knows.

-- One row per source that published the same URL. `item` can only name the
-- source that got there first, because url_norm is UNIQUE — so without this
-- table the cross-source count reads 1 for everything, which looks like a story
-- nobody else picked up rather than like a missing feature.
--
-- The headline is kept per sighting: qbitai writes 智谱 where Hacker News writes
-- Zhipu for the same link, and that pairing is the only bridge between a Chinese
-- story and an English query. The feed will not serve it again.
CREATE TABLE IF NOT EXISTS sighting (
    item_id     TEXT NOT NULL,
    source      TEXT NOT NULL,
    title       TEXT NOT NULL,
    seen_at     TEXT NOT NULL,
    -- How this source saw it. 'feed' is something the source published; 'link'
    -- is an article it pointed at from a post of its own.
    --
    -- Both count towards the cross-source total — a channel writing about a
    -- launch has carried that story — but only 'feed' belongs in that source's
    -- listing. Without the distinction every Telegram post with a link appeared
    -- twice under the same headline, and the block header claimed completeness
    -- over the inflated list: 100 posts became 167 rows.
    via         TEXT NOT NULL DEFAULT 'feed',
    PRIMARY KEY (item_id, source, via)
);

CREATE INDEX IF NOT EXISTS idx_sighting_source ON sighting(source, seen_at DESC);

-- Keyed by URL, not by source. cls.cn exposes five endpoints and Telegram pages
-- with ?before=, so one source can be several requests — and sharing a row
-- meant one endpoint's validator was sent to another, whose 304 then read as
-- "alive, nothing new" while it went mute.
--
-- The write columns matter as much as the fetch ones: source_state knew whether
-- the download worked and nothing knew whether the writing did, so four hundred
-- entries failing to archive looked exactly like four hundred already seen.
CREATE TABLE IF NOT EXISTS source_state (
    source       TEXT NOT NULL,
    url          TEXT NOT NULL,
    last_ok      TEXT,
    last_try     TEXT,
    last_error   TEXT,
    etag         TEXT,
    last_mod     TEXT,
    last_write   TEXT,
    wrote_new    INTEGER,
    wrote_failed INTEGER,
    PRIMARY KEY (source, url)
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

-- Every headline is indexed, and they live in `sighting`: one story carries a
-- different one per source, and the pairing is the point. Searching only the
-- item's title would write Hacker News's "Zhipu" down and never find it, for a
-- story qbitai filed as 智谱 — the schema documenting a bridge it did not have.
--
-- Bodies stay out. Indexing them costs five times the disk for little recall:
-- a product name lives in the headline.
--
-- 'trigram' is not a preference. With the default tokenizer every Chinese query
-- returns zero hits, silently: Chinese has no spaces, so a whole headline
-- becomes one token. Three of nineteen sources would go mute with nobody
-- noticing. Queries under three characters still need the LIKE fallback.
CREATE VIRTUAL TABLE IF NOT EXISTS sighting_fts USING fts5(
    title,
    content = 'sighting',
    content_rowid = 'rowid',
    tokenize = 'trigram'
);

CREATE TRIGGER IF NOT EXISTS sighting_ai AFTER INSERT ON sighting BEGIN
    INSERT INTO sighting_fts(rowid, title) VALUES (new.rowid, new.title);
END;

CREATE TRIGGER IF NOT EXISTS sighting_ad AFTER DELETE ON sighting BEGIN
    INSERT INTO sighting_fts(sighting_fts, rowid, title)
    VALUES ('delete', old.rowid, old.title);
END;

CREATE TRIGGER IF NOT EXISTS sighting_au AFTER UPDATE ON sighting BEGIN
    INSERT INTO sighting_fts(sighting_fts, rowid, title)
    VALUES ('delete', old.rowid, old.title);
    INSERT INTO sighting_fts(rowid, title) VALUES (new.rowid, new.title);
END;

-- Deleting an item must not leave its sightings, or the index keeps answering
-- for a story that is gone.
CREATE TRIGGER IF NOT EXISTS item_ad AFTER DELETE ON item BEGIN
    DELETE FROM sighting WHERE item_id = old.id;
END;
"""


def archive_path() -> Path:
    """Where the archive lives, per platform convention.

    Deliberately not under a cache directory: the XDG spec defines cache as
    regenerable data that the system may delete at will, and cleaners act on
    that. This file cannot be regenerated — the feeds no longer hold what it
    holds.
    """
    override = os.environ.get("CABLEGRAM_DB")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "")
        # The spec says a relative XDG_DATA_HOME must be ignored, which the
        # usual `os.environ.get(...) or default` idiom quietly gets wrong.
        base = xdg if os.path.isabs(xdg) else str(Path.home() / ".local" / "share")

    return Path(base) / "cablegram" / "archive.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the archive, creating it on first run. No setup step for the user."""
    path = Path(path) if path else archive_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(path, timeout=5.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    # Claude Code and Claude Desktop open the same file at once.
    db.execute("PRAGMA busy_timeout = 5000")
    db.executescript(_SCHEMA)

    _seal(db, path)
    return db


_TABLES = ("item", "sighting", "source_state", "meta")


def _shape(db: sqlite3.Connection) -> dict[str, set[str]]:
    """The columns actually present, per table. The fact, not the claim."""
    return {
        name: {row[1] for row in db.execute(f"PRAGMA table_info({name})")}
        for name in _TABLES
    }


@functools.lru_cache(maxsize=1)
def _expected_shape() -> dict[str, set[str]]:
    """The columns this build creates, read from the schema it would create.

    Built by running _SCHEMA into an empty in-memory database rather than
    listing the columns here a second time. A hand-written list is one more
    thing to forget to update — which is the whole failure this function exists
    to catch, and it would be embarrassing to reintroduce it in the check.
    """
    probe = sqlite3.connect(":memory:")
    try:
        probe.executescript(_SCHEMA)
        return _shape(probe)
    finally:
        probe.close()


def _rebuild(db: sqlite3.Connection) -> None:
    """Drop and recreate. Only ever called on an archive holding no items."""
    db.executescript(
        "DROP TABLE IF EXISTS sighting_fts;"
        + "".join(f"DROP TABLE IF EXISTS {name};" for name in _TABLES)
    )
    db.executescript(_SCHEMA)


def _seal(db: sqlite3.Connection, path: Path) -> None:
    """Record which recipe wrote these ids, and refuse the file if it changed.

    An id is a pure function of a URL, so the archive only holds together while
    that function does. If it changes, nothing breaks loudly: every stored id
    stops matching, every item is inserted again, and the archive quietly
    doubles while reporting nothing wrong. Six months of history would look
    like six months of duplicates.

    So the recipe is written down on creation and compared on every open. An
    empty archive is adopted — there is nothing there to be wrong about — but
    one holding items written by an unknown recipe is refused.
    """
    meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
    has_items = db.execute("SELECT EXISTS(SELECT 1 FROM item)").fetchone()[0]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    recipe = id_recipe()

    # The columns, not meta.schema_version. CREATE TABLE IF NOT EXISTS leaves an
    # older table exactly as it was, so a file could carry v1 columns under a v2
    # label: re-stamped on open, never questioned again, and every insert failing
    # for good — counted as `failed`, which nothing reads. Asking the label was
    # how that happened; asking the columns cannot be fooled by a stale one.
    actual, expected = _shape(db), _expected_shape()
    if actual != expected:
        if has_items:
            missing = sorted(expected["item"] - actual["item"])
            _refuse(path,
                    f"archive was written by an older build: item is missing "
                    f"{missing or 'columns this build writes'}")
        _rebuild(db)
        meta = {}

    if has_items:
        # The columns cannot show this one: a newer build may write different
        # meaning into the same shape. It is the one thing the label knows.
        stored_schema = meta.get("schema_version")
        if stored_schema is not None and int(stored_schema) > SCHEMA_VERSION:
            _refuse(path,
                    f"archive was written by a newer build (schema {stored_schema}, "
                    f"this build speaks {SCHEMA_VERSION})")

        stored_identity = meta.get("id_algo")
        if stored_identity is None:
            _refuse(path, "archive holds items but is unsealed: the recipe that "
                          "computed their ids is unknown")
        if stored_identity != IDENTITY:
            _refuse(path,
                    f"archive ids were computed with {stored_identity}, this build "
                    f"computes {IDENTITY}")

        # A moved recipe is not a different archive. It changes the id of the
        # URLs carrying one newly-ignored parameter and leaves the rest alone,
        # so refusing would trade months of history for a handful of duplicates.
        # It is written down instead, dated, so the change is never invisible.
        stored_recipe = meta.get("id_recipe")
        if stored_recipe is not None and stored_recipe != recipe:
            db.executemany(
                "INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)",
                [("id_recipe", recipe),
                 ("id_recipe_previous", stored_recipe),
                 ("id_recipe_changed_at", now)],
            )

    elif meta:
        # Nothing stored, so nothing can be wrong about it: re-stamp and carry
        # on rather than refuse a file that holds no history to protect.
        db.executemany(
            "INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)",
            [("schema_version", str(SCHEMA_VERSION)), ("id_algo", IDENTITY),
             ("id_recipe", recipe)],
        )

    db.executemany(
        "INSERT OR IGNORE INTO meta(k, v) VALUES (?, ?)",
        [
            ("schema_version", str(SCHEMA_VERSION)),
            ("id_algo", IDENTITY),
            ("id_recipe", recipe),
            ("archive_started_at", now),
        ],
    )
    db.commit()


def _refuse(path: Path, problem: str) -> None:
    """Raise with the way out attached.

    Nobody reads this server's ordinary output, so this message is one of the
    few that reaches a person. Stating the problem without the remedy would
    leave the archive looking broken when it is merely from another build.
    """
    raise ArchiveMismatch(
        f"{problem}.\n"
        f"Archive: {path}\n"
        "Refusing rather than writing into it, which would either duplicate "
        "everything it holds or fail on every insert. Move that file aside — it "
        "stays readable with any SQLite client — or point CABLEGRAM_DB "
        "somewhere else."
    )
