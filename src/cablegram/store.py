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

__all__ = ["StoreReport", "CollisionError", "store_entries", "record_attempt",
           "conditional_headers", "cross_count", "cross_counts",
           "items_of_source"]


class CollisionError(RuntimeError):
    """Two different URLs produced the same id. Counted as a failure, never as
    a duplicate: one is arithmetic working, the other is an article lost."""


@dataclass(slots=True)
class StoreReport:
    """What one batch did. Reported per source, never summed into one number:
    'archived 400 items' hides that one source contributed nothing."""

    source: str
    new: int = 0
    seen: int = 0      # already archived, by this source or another
    skipped: int = 0   # the feed left it unusable: no url, or no title
    failed: int = 0    # this code could not handle it — a different problem


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

    for entry in entries:
        try:
            # Counted only after the transaction commits. Incrementing inside it
            # meant a failed commit rolled the row back and left the count
            # standing, reporting one entry as both new and failed. The report
            # is the only channel there is — nothing here logs — so it has to
            # describe what is actually in the archive.
            outcome = _store_one(db, source, entry, fetched_at)
            setattr(report, outcome, getattr(report, outcome) + 1)
        except Exception:
            # One entry, one transaction, so a failure costs that entry alone.
            # An earlier version wrapped the whole batch, which meant a single
            # unclosed IPv6 bracket — normalise() raises on those — rolled back
            # everything already written for the source. The parser promises a
            # bad item costs that item; the module where loss is permanent has
            # more reason to keep that promise, not less.
            report.failed += 1

    return report


def _store_one(
    db: sqlite3.Connection,
    source: Source,
    entry: Entry,
    fetched_at: str,
) -> str:
    """Archive one entry and name what happened: new, seen or skipped."""
    url = (entry.url or "").strip()
    title = (entry.title or "").strip()
    if not url or not title:
        return "skipped"

    url_norm = normalise(url)
    iid = item_id(url)

    # A feed that gives no date is not a feed with no news. Using the capture
    # time keeps the item inside every time window; date_exact is what stops
    # that convenience from becoming a claim.
    published = _utc_iso(entry.published) if entry.published else fetched_at
    date_exact = 1 if entry.published else 0

    # Only for sources that link elsewhere. On qbitai the host is always qbitai:
    # printing it would cost tokens on every line and say nothing.
    target_host = urlsplit(url_norm).netloc if source.aggregator else None

    with db:
        cur = db.execute(
            "INSERT OR IGNORE INTO item"
            " (id, url_norm, url, first_source, lang, title, body, body_src,"
            "  published, date_exact, fetched_at, target_host)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, url_norm, url, source.id, source.lang, title, entry.body,
             entry.body_src, published, date_exact, fetched_at, target_host),
        )

        if cur.rowcount:
            outcome = "new"
        else:
            # OR IGNORE swallows every constraint violation, not only the one on
            # url_norm. If the id landed on a different article, filing this as
            # "already archived" would lose a real item and hang its sighting off
            # somebody else's story. Improbable at 12 hex; silent if unchecked.
            existing = db.execute(
                "SELECT url_norm FROM item WHERE id = ?", (iid,)
            ).fetchone()
            if existing is None or existing["url_norm"] != url_norm:
                raise CollisionError(
                    f"id {iid} already belongs to {existing['url_norm'] if existing else '?'}"
                )
            outcome = "seen"

            # Only ever improve, never overwrite. The same story reaches several
            # feeds and the first to arrive is often the poorest: Product Radar
            # carries no date, Hacker News carries the real one. First-in-wins
            # would keep the '~approximate' mark for life and leave wire_read
            # with nothing to serve, while the feed that had both moves on.
            if date_exact:
                db.execute(
                    "UPDATE item SET published = ?, date_exact = 1"
                    " WHERE id = ? AND date_exact = 0",
                    (published, iid),
                )
            if entry.body:
                db.execute(
                    "UPDATE item SET body = ?, body_src = ? WHERE id = ? AND body IS NULL",
                    (entry.body, entry.body_src, iid),
                )

        db.execute(
            "INSERT OR IGNORE INTO sighting(item_id, source, title, seen_at)"
            " VALUES (?,?,?,?)",
            (iid, source.id, title, fetched_at),
        )

    return outcome


def cross_count(db: sqlite3.Connection, iid: str) -> int:
    """How many sources carried this item. A count, not a score.

    That GLM-5 surfaced in six feeds across three languages within four hours is
    arithmetic. Ranking it would be a judgement, and judgement belongs to the
    reader, who has more context than this server ever will.
    """
    return db.execute(
        "SELECT COUNT(*) FROM sighting WHERE item_id = ?", (iid,)
    ).fetchone()[0]


def cross_counts(db: sqlite3.Connection, ids: list[str]) -> dict[str, int]:
    """The same, for a whole page of results. One query, not one per item —
    a normal day's listing is a couple of hundred."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {
        row["item_id"]: row["n"]
        for row in db.execute(
            f"SELECT item_id, COUNT(*) AS n FROM sighting"
            f" WHERE item_id IN ({marks}) GROUP BY item_id", ids)
    }


def items_of_source(db: sqlite3.Connection, source_id: str, limit: int = 200) -> list:
    """What a source carried, with the headline it used.

    Never `WHERE first_source = ?`. That column names whoever archived the story
    first, so filtering on it drops every source that also carried it — which is
    exactly the stories that matter, and it fails by returning a shorter list
    rather than an error.
    """
    return db.execute(
        "SELECT i.id, i.url, i.url_norm, i.lang, i.body, i.body_src, i.published,"
        "       i.date_exact, i.target_host, i.first_source,"
        "       s.source, s.title, s.seen_at"
        " FROM sighting s JOIN item i ON i.id = s.item_id"
        " WHERE s.source = ? ORDER BY i.published DESC LIMIT ?",
        (source_id, limit),
    ).fetchall()


def record_attempt(db: sqlite3.Connection, fetched: Fetched) -> None:
    """Update what is known about a source's health.

    Three rules, each protecting something that fails quietly:

    * A 304 is a success. The source answered; it simply had nothing new.
      Counting it as failure turns a quiet week into a fake outage.
    * A failure never touches ``last_ok``. That field is how anyone notices a
      source has been mute for days — overwrite it and the silence is invisible.
    * A failure never touches the validators either, or the next poll
      re-downloads a feed the server already has. Neither does a 304: there is
      no new content, so the stored validators are still the right ones — and
      relying on the fetcher to echo them back would make a hand-written
      adapter that forgets able to null them on a success path.
    """
    ok = fetched.ok
    keep_validators = not ok or fetched.unchanged
    with db:
        db.execute(
            "INSERT INTO source_state(source, last_ok, last_try, last_error, etag, last_mod)"
            " VALUES (:source,"
            "         CASE WHEN :ok THEN :now END,"
            "         :now,"
            "         CASE WHEN :ok THEN NULL ELSE :error END,"
            "         CASE WHEN :keep THEN NULL ELSE :etag END,"
            "         CASE WHEN :keep THEN NULL ELSE :last_mod END)"
            " ON CONFLICT(source) DO UPDATE SET"
            "   last_ok    = CASE WHEN :ok THEN :now      ELSE last_ok  END,"
            "   last_try   = :now,"
            "   last_error = CASE WHEN :ok THEN NULL      ELSE :error   END,"
            "   etag       = CASE WHEN :keep THEN etag     ELSE :etag     END,"
            "   last_mod   = CASE WHEN :keep THEN last_mod ELSE :last_mod END",
            {
                "source": fetched.source_id,
                "ok": 1 if ok else 0,
                "keep": 1 if keep_validators else 0,
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
