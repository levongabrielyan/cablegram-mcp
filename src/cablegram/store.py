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
from .sources import Source, resolve
from .urls import item_id, normalise

__all__ = ["StoreReport", "CollisionError", "store_entries", "record_attempt",
           "conditional_headers", "cross_count", "cross_counts",
           "items_of_source", "record_write", "source_health",
           "latest_items", "items_by_ids", "search_items"]


class CollisionError(RuntimeError):
    """Two different URLs produced the same id. Counted as a failure, never as
    a duplicate: one is arithmetic working, the other is an article lost."""


@dataclass(slots=True)
class StoreReport:
    """What one batch did. Reported per source, never summed into one number:
    'archived 400 items' hides that one source contributed nothing."""

    source: str
    # What happened to this source this pass. A failed or unchanged source keeps
    # its row rather than vanishing from the list: nine reports for eleven
    # sources makes two disappear, and nothing downstream can tell.
    state: str = "ok"  # ok | fetch-failed | unparseable | unchanged
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
    """Update what is known about one request. Keyed by URL, not by source.

    Four rules, each protecting something that fails quietly:

    * A 304 is a success. The source answered; it simply had nothing new.
      Counting it as failure turns a quiet week into a fake outage.
    * A failure never touches ``last_ok``. That field is how anyone notices a
      source has been mute for days — overwrite it and the silence is invisible.
    * A failure never touches the validators either, or the next poll
      re-downloads a feed the server already has. Neither does a 304: there is
      no new content, so the stored validators are still the right ones — and
      relying on the fetcher to echo them back would make a hand-written
      adapter that forgets able to null them on a success path.
    * One row per URL. A source can be several requests, and sharing a row sent
      one endpoint's validator to another.
    """
    ok = fetched.ok
    keep_validators = not ok or fetched.unchanged
    with db:
        db.execute(
            "INSERT INTO source_state(source, url, last_ok, last_try, last_error,"
            "                         etag, last_mod)"
            " VALUES (:source, :url,"
            "         CASE WHEN :ok THEN :now END,"
            "         :now,"
            "         CASE WHEN :ok THEN NULL ELSE :error END,"
            "         CASE WHEN :keep THEN NULL ELSE :etag END,"
            "         CASE WHEN :keep THEN NULL ELSE :last_mod END)"
            " ON CONFLICT(source, url) DO UPDATE SET"
            "   last_ok    = CASE WHEN :ok   THEN :now      ELSE last_ok  END,"
            "   last_try   = :now,"
            "   last_error = CASE WHEN :ok   THEN NULL      ELSE :error   END,"
            "   etag       = CASE WHEN :keep THEN etag      ELSE :etag    END,"
            "   last_mod   = CASE WHEN :keep THEN last_mod  ELSE :last_mod END",
            {
                "source": fetched.source_id,
                "url": fetched.url,
                "ok": 1 if ok else 0,
                "keep": 1 if keep_validators else 0,
                "now": fetched.fetched_at,
                "error": fetched.error,
                "etag": fetched.etag,
                "last_mod": fetched.last_modified,
            },
        )


def record_write(db: sqlite3.Connection, report: StoreReport, *, url: str, at: str) -> None:
    """Record what archiving did, beside what fetching did.

    Without this, `failed` lives and dies inside a return value: a poll where
    every entry failed to archive looks exactly like a poll where every entry
    was already known. That is precisely how an archive that could never be
    written to stayed invisible.
    """
    with db:
        db.execute(
            "INSERT INTO source_state(source, url, last_write, wrote_new, wrote_failed)"
            " VALUES (:source, :url, :at, :new, :failed)"
            " ON CONFLICT(source, url) DO UPDATE SET"
            "   last_write = :at, wrote_new = :new, wrote_failed = :failed",
            {"source": report.source, "url": url, "at": at,
             "new": report.new, "failed": report.failed},
        )


def source_health(db: sqlite3.Connection) -> dict[str, dict]:
    """One row per source, folding together its endpoints.

    wire_sources asks about the source; the state is kept per request. A source
    with five endpoints is alive if any of them answered, and its most recent
    error is worth showing even when another endpoint is fine.
    """
    return {
        row["source"]: dict(row)
        for row in db.execute(
            "SELECT source,"
            "       MAX(last_ok)    AS last_ok,"
            "       MAX(last_try)   AS last_try,"
            "       MAX(last_error) AS last_error,"
            "       MAX(last_write) AS last_write,"
            "       SUM(wrote_new)    AS wrote_new,"
            "       SUM(wrote_failed) AS wrote_failed,"
            "       COUNT(*) AS endpoints"
            " FROM source_state GROUP BY source"
        )
    }


def conditional_headers(db: sqlite3.Connection) -> dict[str, tuple[str | None, str | None]]:
    """The validators to send on the next poll, keyed by URL.

    Most feeds are unchanged between polls; sending these turns those into a 304
    with no body. Measured on the eleven live feeds: five answered 304 and 670 KB
    were never transferred.
    """
    return {
        row["url"]: (row["etag"], row["last_mod"])
        for row in db.execute("SELECT url, etag, last_mod FROM source_state")
    }


# ── reads ───────────────────────────────────────────────────────────────────
#
# Every query below can fail by returning less, and none of them can fail by
# raising. A window that drops a source, a limit that cuts from the wrong end, a
# search escaped wrong: all of them come back as a shorter list, which reads as
# a quiet day. So each one carries the totals needed to tell the difference.

_ITEM_COLUMNS = (
    "i.id, i.url, i.url_norm, i.lang, i.body, i.body_src, i.published,"
    " i.date_exact, i.target_host, i.first_source"
)


def latest_items(
    db: sqlite3.Connection,
    *,
    since: str,
    sources: list[str] | None = None,
    limit_per_source: int | None = None,
) -> list[dict]:
    """Items seen since `since`, grouped by the source that carried them.

    One row per (source, item): a story two feeds ran appears under both, which
    is what "what did this source carry" means. `source_total` is the count
    before the limit, so a cut can be declared rather than looking like silence.
    """
    wanted = [s.id for s in resolve(sources)] if sources else None
    params: list = [since]
    clause = ""
    if wanted is not None:
        if not wanted:
            return []
        clause = f" AND s.source IN ({','.join('?' * len(wanted))})"
        params += wanted

    rows = db.execute(
        f"SELECT {_ITEM_COLUMNS}, s.source, s.title, s.seen_at,"
        f"       COUNT(*) OVER (PARTITION BY s.source) AS source_total,"
        f"       (SELECT COUNT(*) FROM sighting x WHERE x.item_id = i.id) AS cross,"
        f"       ROW_NUMBER() OVER (PARTITION BY s.source ORDER BY i.published DESC)"
        f"           AS rank_in_source"
        f" FROM sighting s JOIN item i ON i.id = s.item_id"
        f" WHERE i.published >= ?{clause}"
        f" ORDER BY s.source, i.published DESC",
        params,
    ).fetchall()

    keep = [dict(r) for r in rows
            if limit_per_source is None or r["rank_in_source"] <= limit_per_source]
    return keep


def items_by_ids(db: sqlite3.Connection, ids: list[str]) -> list[dict]:
    """Full rows for the ids given, in the order given, skipping unknown ones.

    The caller compares what it asked for against what came back and reports the
    difference: returning two of three without a word lets the model believe it
    read all three.
    """
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    found = {
        row["id"]: dict(row)
        for row in db.execute(
            f"SELECT {_ITEM_COLUMNS}, i.title,"
            f"       (SELECT COUNT(*) FROM sighting x WHERE x.item_id = i.id) AS cross,"
            f"       (SELECT GROUP_CONCAT(x.source) FROM sighting x WHERE x.item_id = i.id)"
            f"           AS sources"
            f" FROM item i WHERE i.id IN ({marks})", ids)
    }
    return [found[i] for i in ids if i in found]


def _fts_query(query: str) -> str:
    """Wrap a user's words as one FTS5 phrase, escaping the quotes.

    FTS5 reads ", *, -, OR and NEAR as syntax, so an unescaped query raises
    OperationalError and the model gets a crash instead of a result — for
    something a person could plausibly type. Doubling the quotes inside one
    quoted phrase makes every character literal.
    """
    return '"' + query.replace('"', '""') + '"'


def search_items(
    db: sqlite3.Connection,
    query: str,
    *,
    since: str,
    sources: list[str] | None = None,
    limit_per_source: int = 25,
) -> list[dict]:
    """Search the archived headlines of every source that carried each story.

    Two passes on purpose. FTS5's trigram tokenizer cannot index terms shorter
    than three characters, and the most common Chinese company names — 智谱,
    阿里, 字节 — are exactly two. Without the LIKE fallback those return nothing,
    with no error, and the answer reads as "nobody is talking about them".
    """
    query = query.strip()
    if not query:
        return []

    wanted = [s.id for s in resolve(sources)] if sources else None
    if wanted is not None and not wanted:
        return []

    clause, params = "", [since]
    if wanted is not None:
        clause = f" AND s.source IN ({','.join('?' * len(wanted))})"
        params += wanted

    if len(query) >= 3:
        matcher = ("s.rowid IN (SELECT rowid FROM sighting_fts"
                   " WHERE sighting_fts MATCH ?)")
        args = [_fts_query(query)] + params
    else:
        matcher = "s.title LIKE ? ESCAPE '\\'"
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args = [f"%{escaped}%"] + params

    rows = db.execute(
        f"SELECT {_ITEM_COLUMNS}, s.source, s.title, s.seen_at,"
        f"       (SELECT COUNT(*) FROM sighting x WHERE x.item_id = i.id) AS cross,"
        # Without this the renderer falls back to the number shown, so a source
        # holding 437 matches printed 3/3 — not an undeclared cut but a denied
        # one, asserting completeness in the tool built to prevent exactly that.
        f"       COUNT(*) OVER (PARTITION BY s.source) AS source_total,"
        f"       ROW_NUMBER() OVER (PARTITION BY s.source ORDER BY i.published DESC)"
        f"           AS rank_in_source"
        f" FROM sighting s JOIN item i ON i.id = s.item_id"
        f" WHERE {matcher} AND i.published >= ?{clause}"
        f" ORDER BY s.source, i.published DESC",
        args,
    ).fetchall()

    return [dict(r) for r in rows if r["rank_in_source"] <= limit_per_source]
