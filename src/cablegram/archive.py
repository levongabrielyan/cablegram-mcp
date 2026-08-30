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

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["archive_path", "connect", "SCHEMA_VERSION"]

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS item (
    id           TEXT PRIMARY KEY,
    url_norm     TEXT NOT NULL UNIQUE,
    url          TEXT NOT NULL,
    source       TEXT NOT NULL,
    lang         TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT,
    body_kind    TEXT,
    published    TEXT,
    date_exact   INTEGER NOT NULL DEFAULT 1,
    fetched_at   TEXT NOT NULL,
    target_host  TEXT
);

CREATE INDEX IF NOT EXISTS idx_item_pub        ON item(published DESC);
CREATE INDEX IF NOT EXISTS idx_item_source_pub ON item(source, published DESC);

CREATE TABLE IF NOT EXISTS source_state (
    source      TEXT PRIMARY KEY,
    last_ok     TEXT,
    last_try    TEXT,
    last_error  TEXT,
    etag        TEXT,
    last_mod    TEXT
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

-- Only titles are indexed. Bodies are stored and served by wire_read, but
-- indexing them costs five times the disk for little recall: a product name
-- lives in the headline.
--
-- 'trigram' is not a preference. With the default tokenizer every Chinese query
-- returns zero hits, silently: Chinese has no spaces, so a whole headline
-- becomes one token. Three of fifteen sources would go mute with nobody
-- noticing. Queries under three characters still need the LIKE fallback.
CREATE VIRTUAL TABLE IF NOT EXISTS item_fts USING fts5(
    title,
    content = 'item',
    content_rowid = 'rowid',
    tokenize = 'trigram'
);

CREATE TRIGGER IF NOT EXISTS item_ai AFTER INSERT ON item BEGIN
    INSERT INTO item_fts(rowid, title) VALUES (new.rowid, new.title);
END;

CREATE TRIGGER IF NOT EXISTS item_ad AFTER DELETE ON item BEGIN
    INSERT INTO item_fts(item_fts, rowid, title) VALUES ('delete', old.rowid, old.title);
END;

CREATE TRIGGER IF NOT EXISTS item_au AFTER UPDATE ON item BEGIN
    INSERT INTO item_fts(item_fts, rowid, title) VALUES ('delete', old.rowid, old.title);
    INSERT INTO item_fts(rowid, title) VALUES (new.rowid, new.title);
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


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the archive, creating it on first run. No setup step for the user."""
    path = path or archive_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    db = sqlite3.connect(path, timeout=5.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    # Claude Code and Claude Desktop open the same file at once.
    db.execute("PRAGMA busy_timeout = 5000")
    db.executescript(_SCHEMA)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.executemany(
        "INSERT OR IGNORE INTO meta(k, v) VALUES (?, ?)",
        [("schema_version", str(SCHEMA_VERSION)), ("archive_started_at", now)],
    )
    db.commit()
    return db
