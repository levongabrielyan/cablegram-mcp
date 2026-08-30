"""The write path: parsed entries in, archived history out.

This is the only module whose mistakes are permanent. Everything else can be
rerun — a failed fetch is retried in five minutes, a bad parse is fixed and the
feed still holds the same forty items. What is not written here is gone: the
feeds expose a window of days and no archive of their own.

So the bias throughout is towards writing something rather than nothing, and
towards marking what is uncertain rather than dropping it. An item with a
doubtful date is archived and flagged; an item skipped for tidiness is lost.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timezone
from typing import Iterable
from urllib.parse import urlsplit

from .fetch import Fetched
from .rss import Entry
from .sources import Source
from .urls import item_id, normalise

__all__ = ["StoreReport", "store_entries", "record_attempt",
           "conditional_headers", "cross_count"]


@dataclass(slots=True)
class StoreReport:
    """What one batch did. Reported per source, never summed into one number:
    'archived 400 items' hides that one source contributed nothing."""

    source: str
    new: int = 0
    seen: int = 0      # already archived, by this source or another
    skipped: int = 0   # unusable: no url, or no title


def _utc_iso(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def store_entries(
    db: sqlite3.Connection,
    source: Source,
    entries: Iterable[Entry],
    *,
    fetched_at: str,
) -> StoreReport:
    """Archive a source's entries. Idempotent: the same batch twice writes once.

    Every poll re-delivers the whole feed window, so this runs against mostly
    known items all day. Both INSERTs rely on that being cheap and silent.
    """
    report = StoreReport(source.id)

    with db:  # one transaction per source: a crash leaves no half-written batch
        for entry in entries:
            url = (entry.url or "").strip()
            title = (entry.title or "").strip()
            if not url or not title:
                report.skipped += 1
                continue

            url_norm = normalise(url)
            iid = item_id(url)

            # A feed that gives no date is not a feed with no news. Using the
            # capture time keeps the item inside every time window; date_exact
            # is what stops that convenience from becoming a claim.
            published = _utc_iso(entry.published) if entry.published else fetched_at
            date_exact = 1 if entry.published else 0

            # Only for sources that link elsewhere. On qbitai the host is always
            # qbitai: printing it would cost tokens on every line and say nothing.
            target_host = urlsplit(url_norm).netloc if source.aggregator else None

            cur = db.execute(
                "INSERT OR IGNORE INTO item"
                " (id, url_norm, url, source, lang, title, body, body_kind,"
                "  published, date_exact, fetched_at, target_host)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (iid, url_norm, url, source.id, source.lang, title, entry.body,
                 entry.body_kind, published, date_exact, fetched_at, target_host),
            )
            if cur.rowcount:
                report.new += 1
            else:
                report.seen += 1

            db.execute(
                "INSERT OR IGNORE INTO sighting(item_id, source, title, seen_at)"
                " VALUES (?,?,?,?)",
                (iid, source.id, title, fetched_at),
            )

    return report


def cross_count(db: sqlite3.Connection, iid: str) -> int:
    """How many sources carried this item. A count, not a score.

    That GLM-5 surfaced in six feeds across three languages within four hours is
    arithmetic. Ranking it would be a judgement, and judgement belongs to the
    reader, who has more context than this server ever will.
    """
    return db.execute(
        "SELECT COUNT(*) FROM sighting WHERE item_id = ?", (iid,)
    ).fetchone()[0]


def record_attempt(db: sqlite3.Connection, fetched: Fetched) -> None:
    """Update what is known about a source's health.

    Three rules, each protecting something that fails quietly:

    * A 304 is a success. The source answered; it simply had nothing new.
      Counting it as failure turns a quiet week into a fake outage.
    * A failure never touches ``last_ok``. That field is how anyone notices a
      source has been mute for days — overwrite it and the silence is invisible.
    * A failure never touches the validators either, or the next poll
      re-downloads a feed the server already has.
    """
    ok = fetched.ok
    with db:
        db.execute(
            "INSERT INTO source_state(source, last_ok, last_try, last_error, etag, last_mod)"
            " VALUES (:source,"
            "         CASE WHEN :ok THEN :now END,"
            "         :now,"
            "         CASE WHEN :ok THEN NULL ELSE :error END,"
            "         CASE WHEN :ok THEN :etag END,"
            "         CASE WHEN :ok THEN :last_mod END)"
            " ON CONFLICT(source) DO UPDATE SET"
            "   last_ok    = CASE WHEN :ok THEN :now      ELSE last_ok  END,"
            "   last_try   = :now,"
            "   last_error = CASE WHEN :ok THEN NULL      ELSE :error   END,"
            "   etag       = CASE WHEN :ok THEN :etag     ELSE etag     END,"
            "   last_mod   = CASE WHEN :ok THEN :last_mod ELSE last_mod END",
            {
                "source": fetched.source_id,
                "ok": 1 if ok else 0,
                "now": fetched.fetched_at,
                "error": fetched.error,
                "etag": fetched.etag,
                "last_mod": fetched.last_modified,
            },
        )


def conditional_headers(db: sqlite3.Connection) -> dict[str, tuple[str | None, str | None]]:
    """The validators to send on the next poll, keyed by source.

    Most feeds are unchanged between polls; sending these turns those into a 304
    with no body. Measured on the eleven live feeds: five answered 304 and 670 KB
    were never transferred.
    """
    return {
        row["source"]: (row["etag"], row["last_mod"])
        for row in db.execute("SELECT source, etag, last_mod FROM source_state")
    }
