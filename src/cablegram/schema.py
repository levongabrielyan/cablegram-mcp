"""The database each call is answered from, and the shape it has.

Built in memory, filled by one pass over the sources asked for, and discarded
when the reply is sent. Nothing is written to disk, so there is no file to find,
none to back up, and none that grows while nobody is looking.

SQLite is here for what it does inside a single call, not for keeping anything:
the trigram index that makes a Chinese query work, the sighting table that turns
"two sources carried this" into a count, and the window functions that let a cut
be declared with its real total. Doing that in Python would be the same logic,
written worse.
"""

from __future__ import annotations

import sqlite3

__all__ = ["connect"]

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
    -- When THIS source carried it, which is not when the article was published.
    -- `item.url_norm` is UNIQUE, so one URL has one item row and one date, and
    -- it belonged to whichever source arrived first. In an unfiltered pass that
    -- is catalogue order — hn sits above openai — and Hacker News dates a story
    -- when somebody submitted it. Measured against openai.com's own feed: `A
    -- milestone in expanding access to AI` is stamped 04:00 there and came out
    -- as 13:07 under `## openai`, nine hours wrong and flagged exact.
    --
    -- A sighting is "this source carried this, then". Kept here, both blocks
    -- are true at once: openai's says 04:00 and hn's says 13:07, and neither
    -- has to be judged more trustworthy than the other.
    published   TEXT NOT NULL,
    date_exact  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (item_id, source, via)
);

CREATE INDEX IF NOT EXISTS idx_sighting_source ON sighting(source, seen_at DESC);

-- Keyed by URL, not by source. cls.cn exposes five endpoints and Telegram pages
-- with ?before=, so one source can be several requests, and one of them failing
-- is a different fact from the source failing.
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
-- becomes one token. The three Chinese sources would go mute with nobody
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


def connect() -> sqlite3.Connection:
    """A fresh database with the schema in it, in memory.

    One per call. No path, no pragmas about durability, no seal comparing this
    build against a file another build wrote — none of those have anything to
    hold together, because the database does not outlive the reply.
    """
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(_SCHEMA)
    return db
