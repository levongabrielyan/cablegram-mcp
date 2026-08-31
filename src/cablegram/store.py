"""The write path: parsed entries in, one call's worth of rows out.

Nothing here outlives the reply, so a mistake costs an answer rather than a
history. What it still costs is the answer: an item dropped for tidiness is an
item the model is never told about, and the model has no second source to notice
the gap with.

So the bias throughout is towards writing something rather than nothing, and
towards marking what is uncertain rather than discarding it. An item with a
doubtful date is kept and flagged; an item skipped is silently absent.

Two things here look like storage and are not. `sighting` is what turns "the
same URL came from three sources" into a count, and `source_state` is what turns
"this one failed" into a line the reply can carry. Both are about one pass.
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
           "cross_count", "cross_counts",
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
    # What happened to this source this pass. A failed source keeps its row
    # rather than vanishing from the list: nine reports for eleven sources makes
    # two disappear, and nothing downstream can tell.
    state: str = "ok"  # ok | fetch-failed | unparseable | parsed-empty
    new: int = 0
    seen: int = 0      # already archived, by this source or another
    skipped: int = 0   # the feed left it unusable: no url, or no title
    failed: int = 0    # this code could not handle it — a different problem
    referenced: int = 0  # articles archived because this source linked them
    # The source returned as many rows as it can return. "100 new" does not
    # distinguish "there were 100" from "there were more, unreachable" — and for
    # cls.cn, which cannot page backwards, what is beyond the ceiling is gone.
    at_ceiling: bool = False


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
            # describe what is actually stored.
            outcome, referenced = _store_one(db, source, entry, fetched_at)
            setattr(report, outcome, getattr(report, outcome) + 1)
            report.referenced += referenced
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
) -> tuple[str, int]:
    """Archive one entry. Returns what happened and how many links it pulled in."""
    url = (entry.url or "").strip()
    title = (entry.title or "").strip()
    if not url or not title:
        return "skipped", 0

    url_norm = normalise(url)
    iid = item_id(url)

    # A feed that gives no date is not a feed with no news. Using the capture
    # time keeps the item inside every time window; date_exact is what stops
    # that convenience from becoming a claim.
    published = _utc_iso(entry.published) if entry.published else fetched_at
    date_exact = 1 if entry.published and entry.date_exact else 0

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
            # Only ever improve — and for a date, better means earlier as well
            # as more certain. wire_read prints this one, and an article is
            # published once: every later sighting is somebody noticing it, so
            # a submission cannot precede the thing submitted. Two feeds
            # carrying the real date agree and nothing moves.
            if date_exact:
                db.execute(
                    "UPDATE item SET published = ?, date_exact = 1"
                    " WHERE id = ? AND (date_exact = 0 OR published > ?)",
                    (published, iid, published),
                )
            if entry.body:
                db.execute(
                    "UPDATE item SET body = ?, body_src = ? WHERE id = ? AND body IS NULL",
                    (entry.body, entry.body_src, iid),
                )

        # A feed arriving corrects whatever a reference had to guess. Guarded in
        # SQL rather than by a lookup: it only fires while no feed has claimed
        # this item, so a second channel linking it cannot overwrite a real
        # headline, and it runs before this source's own sighting exists.
        db.execute(
            "UPDATE item SET title = ?, lang = ?, first_source = ?,"
            "                published = ?, date_exact = ?"
            " WHERE id = ? AND NOT EXISTS"
            "   (SELECT 1 FROM sighting WHERE item_id = ? AND via = 'feed')",
            (title, source.lang, source.id, published, date_exact, iid, iid),
        )

        db.execute(
            "INSERT OR IGNORE INTO sighting"
            " (item_id, source, title, seen_at, via, published, date_exact)"
            " VALUES (?,?,?,?,'feed',?,?)",
            (iid, source.id, title, fetched_at, published, date_exact),
        )

        referenced = 0
        for link in entry.links:
            try:
                referenced += _record_reference(db, source, link, title, published,
                                                fetched_at)
            except Exception:
                # The post is what the channel published; the link is a bonus.
                # A malformed href — normalise raises on an unclosed bracket —
                # must not cost the post itself.
                pass

    return outcome, referenced


def _record_reference(
    db: sqlite3.Connection,
    source: Source,
    link: str,
    title: str,
    published: str,
    fetched_at: str,
) -> int:
    """Credit a source for an article it pointed at.

    A channel writing about a launch has carried that story, and its own URL is
    a permalink to the post rather than to the article — so without this, the six
    Telegram channels could never appear in a cross-source count at all.

    The article is archived if it is not already there, with the referring
    post's headline standing in until its own feed supplies a better one. That
    placeholder is the point rather than a compromise: it means an article
    reaches the reply on the strength of somebody linking it, days before the
    outlet's own feed carries it — which is the whole reason these sources are
    in the list.
    """
    link = (link or "").strip()
    if not link:
        return 0
    link_norm = normalise(link)
    if not link_norm:
        return 0

    link_id = item_id(link)
    cursor = db.execute(
        "INSERT OR IGNORE INTO item"
        " (id, url_norm, url, first_source, lang, title, body, body_src,"
        "  published, date_exact, fetched_at, target_host)"
        # date_exact is 0, always. A reference knows when somebody linked the
        # article, never when it was published — and writing 1 there stopped the
        # improving UPDATE from ever firing, so an article linked three days
        # before its own feed carried it kept the channel's date, flagged exact,
        # for good. That is precisely the case these sources exist for.
        " VALUES (?,?,?,?,?,?,NULL,NULL,?,0,?,?)",
        (link_id, link_norm, link, source.id, source.lang, title, published,
         fetched_at, urlsplit(link_norm).netloc),
    )
    if not cursor.rowcount:
        # Same check the main path grew earlier: OR IGNORE swallows every
        # constraint violation, so a collision would hang this sighting off an
        # unrelated story rather than being noticed.
        existing = db.execute("SELECT url_norm FROM item WHERE id = ?",
                              (link_id,)).fetchone()
        if existing is None or existing["url_norm"] != link_norm:
            raise CollisionError(f"id {link_id} already belongs to another url")

    db.execute(
        "INSERT OR IGNORE INTO sighting"
        " (item_id, source, title, seen_at, via, published, date_exact)"
        # date_exact 0: a reference knows when somebody linked the article, not
        # when it was published, exactly as the item row it creates.
        " VALUES (?,?,?,?,'link',?,0)",
        (link_id, source.id, title, fetched_at, published),
    )
    return 1 if cursor.rowcount else 0


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
    with db:
        db.execute(
            "INSERT INTO source_state(source, url, last_ok, last_try, last_error)"
            " VALUES (:source, :url,"
            "         CASE WHEN :ok THEN :now END,"
            "         :now,"
            "         CASE WHEN :ok THEN NULL ELSE :error END)"
            " ON CONFLICT(source, url) DO UPDATE SET"
            "   last_ok    = CASE WHEN :ok THEN :now ELSE last_ok END,"
            "   last_try   = :now,"
            "   last_error = CASE WHEN :ok THEN NULL ELSE :error END",
            {
                "source": fetched.source_id,
                "url": fetched.url,
                "ok": 1 if ok else 0,
                "now": fetched.fetched_at,
                "error": fetched.error,
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


def is_down(state: dict | None) -> bool:
    """Whether this source's most recent attempt failed.

    One sentence, in one place. It was written out twice — once to build the
    DOWN line of wire_latest and once to build the FAIL column of wire_sources —
    and left out of wire_search entirely, so a search over twenty-one dead
    sources came back "0 shown hits" with nothing to say they had been dead.
    Two copies of a rule get fixed one at a time; the third copy never existed
    and nobody noticed.

    A source with no state at all is NOT down. It has never been asked, which is
    a different fact and gets different words from every caller.

    `>=`, not `>`: a pass that downloads and then fails records both attempts
    with the same `fetched_at`, so a strict compare never fired and the failure
    stayed invisible. Safe, because a success clears `last_error`, so this can
    only be true after one.
    """
    if not state or not state.get("last_error"):
        return False
    return not state.get("last_ok") or (state.get("last_try") or "") >= state["last_ok"]


def source_health(db: sqlite3.Connection) -> dict[str, dict]:
    """One row per source, folding together its endpoints.

    wire_sources asks about the source; the state is kept per request. A source
    with five endpoints is alive if any of them answered, and its most recent
    error is worth showing even when another endpoint is fine.
    """
    # `at_ceiling` lives in meta rather than in a column of its own: _seal
    # compares the shape of the schema and refuses an archive whose columns
    # moved, so adding one to report a flag would kill every archive already on
    # disk. meta is (k, v) and cannot change shape.
    ceilings = {row["k"].split(":", 1)[1]: row["v"] for row in
                db.execute("SELECT k, v FROM meta WHERE k LIKE 'ceiling:%'")}
    # The newest thing each source has actually published, which is a different
    # question from whether it answered. `last_ok` asks "did the server reply?";
    # a feed frozen for a year replies 200 forever. Measured on the catalogue:
    # productradar answers OK and its newest item is 25 days old, ten items in
    # 720 days. No column needed — the pass already knows.
    newest = {row["source"]: row["newest"] for row in db.execute(
        "SELECT source, MAX(published) AS newest FROM sighting GROUP BY source")}
    return {
        row["source"]: dict(row, at_ceiling=ceilings.get(row["source"]),
                            newest=newest.get(row["source"]))
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




# ── reads ───────────────────────────────────────────────────────────────────
#
# Every query below can fail by returning less, and none of them can fail by
# raising. A window that drops a source, a limit that cuts from the wrong end, a
# search escaped wrong: all of them come back as a shorter list, which reads as
# a quiet day. So each one carries the totals needed to tell the difference.

_ITEM_COLUMNS = (
    # No date here on purpose. A listing row is one source's sighting and takes
    # that source's date; items_by_ids is about the item and takes the item's.
    # Selecting both under one name is worse than either: sqlite3.Row hands back
    # whichever came first in the SELECT, so the wrong one wins in silence.
    "i.id, i.url, i.url_norm, i.lang, i.body, i.body_src,"
    " i.target_host, i.first_source,"
    # The item's own headline, beside the sighting's. wire_read prints
    # first_source, lang and via — all facts about the item — and used to print
    # `title`, which is the headline of whichever source carried it. When two
    # sources carry one URL those belong to different sources, and the result
    # was a Russian channel's paraphrase printed under `openai en`, with a date
    # in it that OpenAI's post does not contain.
    "       i.title AS item_title,"
    # Both of these are facts about the item, and both were computed by
    # items_by_ids alone. That was invisible while wire_read only ever read the
    # file; serving live, it reads rows these other two queries produced, and a
    # column they do not select comes back as absent rather than as false.
    #
    # `via` is the one that lies. 'link' means nothing has published this under
    # its own feed, so the headline, language, source and date all belong to
    # whoever linked it — 59 items in the archive today. Missing, wire_read
    # printed a Telegram channel's Russian post as that channel's own dispatch
    # about an English blog it never wrote.
    "       CASE WHEN EXISTS (SELECT 1 FROM sighting x"
    "                         WHERE x.item_id = i.id AND x.via = 'feed')"
    "            THEN 'feed' ELSE 'link' END AS via,"
    # And `sources` is what the cross count names. Without it the count and the
    # list contradicted each other on the same line: x2[hub] for a story hn and
    # hub both carried.
    "       (SELECT GROUP_CONCAT(x.source) FROM sighting x WHERE x.item_id = i.id)"
    "           AS sources"
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
        # The row is one source's sighting of the item, so the date on it is
        # that source's. Selecting the item's put Hacker News's submission time
        # under openai's block: one URL is one item row with one date, owned by
        # whoever arrived first, and in an unfiltered pass that is catalogue
        # order. Measured against openai.com's feed: 04:00 there, printed as
        # 13:07 under `## openai`.
        f"       s.published, s.date_exact,"
        f"       COUNT(*) OVER (PARTITION BY s.source) AS source_total,"
        f"       (SELECT COUNT(*) FROM sighting x WHERE x.item_id = i.id) AS cross,"
        f"       ROW_NUMBER() OVER (PARTITION BY s.source ORDER BY s.published DESC)"
        f"           AS rank_in_source"
        f" FROM sighting s JOIN item i ON i.id = s.item_id"
        # via='feed' only: a linked article is not something the channel wrote,
        # and listing it repeats the post under the same headline.
        f" WHERE s.via = 'feed' AND s.published >= ?{clause}"
        f" ORDER BY s.source, s.published DESC",
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
            f"SELECT {_ITEM_COLUMNS}, i.title, i.published, i.date_exact,"
            f"       (SELECT COUNT(*) FROM sighting x WHERE x.item_id = i.id) AS cross"
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
) -> tuple[list[dict], str]:
    """Search the archived headlines, and say which engine answered.

    Returns (rows, engine). The engine matters to the caller: "GLM" runs on the
    trigram index and "GL" cannot, so it falls to a substring scan with
    different recall. Two queries answered by different engines are not
    comparable, and nothing in the output would otherwise say so.

    Two passes on purpose. FTS5's trigram tokenizer cannot index terms shorter
    than three characters, and the most common Chinese company names — 智谱,
    阿里, 字节 — are exactly two. Without the LIKE fallback those return nothing,
    with no error, and the answer reads as "nobody is talking about them".
    """
    query = query.strip()
    if not query:
        return [], "none"

    wanted = [s.id for s in resolve(sources)] if sources else None
    if wanted is not None and not wanted:
        return [], "none"

    clause, params = "", [since]
    if wanted is not None:
        clause = f" AND s.source IN ({','.join('?' * len(wanted))})"
        params += wanted

    if len(query) >= 3:
        engine = "index"
        matcher = ("s.rowid IN (SELECT rowid FROM sighting_fts"
                   " WHERE sighting_fts MATCH ?)")
        args = [_fts_query(query)] + params
    else:
        engine = "substring"
        matcher = "s.title LIKE ? ESCAPE '\\'"
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args = [f"%{escaped}%"] + params

    rows = db.execute(
        f"SELECT {_ITEM_COLUMNS}, s.source, s.title, s.seen_at,"
        f"       s.published, s.date_exact,"
        f"       (SELECT COUNT(*) FROM sighting x WHERE x.item_id = i.id) AS cross,"
        # Without this the renderer falls back to the number shown, so a source
        # holding 437 matches printed 3/3 — not an undeclared cut but a denied
        # one, asserting completeness in the tool built to prevent exactly that.
        f"       COUNT(*) OVER (PARTITION BY s.source) AS source_total,"
        f"       ROW_NUMBER() OVER (PARTITION BY s.source ORDER BY s.published DESC)"
        f"           AS rank_in_source"
        f" FROM sighting s JOIN item i ON i.id = s.item_id"
        f" WHERE {matcher} AND s.published >= ?{clause}"
        f" ORDER BY s.source, s.published DESC",
        args,
    ).fetchall()

    return [dict(r) for r in rows if r["rank_in_source"] <= limit_per_source], engine
