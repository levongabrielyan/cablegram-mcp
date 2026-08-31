"""The MCP surface: four tools, and nothing else in this file that thinks.

Everything here is a translation layer — parse the arguments, call the query,
hand the rows to the renderer. The reason is the same one that makes a closed
platform's indicator testable: an MCP server cannot be exercised without an MCP
client, so anything that decides something has to live where a test can reach
it. If a function here grows a second branch, it belongs in store or render.

The descriptions are not documentation. They are the only place a contract can
be installed, because the model reads them and the person never does: that a
dead source means UNKNOWN rather than silence, that headlines are not
translated, that this is filtered by date and not ranked by relevance. A
description that omits those produces confident wrong answers, and nobody is
watching.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .archive import archive_path, connect
from .render import render_latest, render_read, render_search, render_sources
from .poll import POLLABLE, poll_once
from .sources import SOURCES, resolve
from .store import (items_by_ids, latest_items, search_items, source_health)

__all__ = ["build", "serve", "main"]

# One pass has to finish inside a tool call, and the theoretical worst case
# without a bound is 130s. Forty-five is comfortably above the ~30s a full
# full pass measures, and whatever has not answered by then is
# reported DOWN rather than waited for.
LIVE_DEADLINE = 45.0

_DETAIL = ("headlines", "full")


def _archive_requested() -> bool:
    """Whether to read the file on disk instead of fetching.

    A separate variable from CABLEGRAM_DB on purpose. That one has always meant
    "where the file is"; making it also mean "use it" would hand the old
    behaviour to everyone who set it to move the file somewhere else.
    """
    return os.environ.get("CABLEGRAM_ARCHIVE", "").strip().lower() in ("1", "true", "yes")

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# Accepted spellings, widest first. Anything else is refused rather than
# silently matching nothing.
_SINCE_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                  "%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _parse_since(raw: str) -> str:
    """Normalise `since`, or refuse it.

    It went straight into `published >= ?`, which is a string comparison. So
    '2026-8-30' — no zero, what a model writes half the time — sorts above every
    real timestamp and matched nothing, and the reply came back perfectly
    formed: 0 items, every source healthy, no DOWN line. The model reports that
    nothing happened today.

    This is the only free-text parameter in the whole surface with a required
    shape, so it is the only place that can produce that particular lie.
    """
    text = (raw or "").strip()
    for fmt in _SINCE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return _iso(parsed.astimezone(timezone.utc))
    # ToolError, not ValueError: the SDK forwards its message to the client and
    # withholds the text of anything else, so a plain exception would reach the
    # model as "Error executing tool" — with the recovery instructions stripped
    # out of the one message written to make recovery possible.
    raise ToolError(
        f"`since` must be ISO-8601 UTC, e.g. 2026-08-30T09:00:00Z or 2026-08-30. "
        f"Got {raw!r}, which cannot be compared against stored timestamps — it "
        f"would have returned an empty result that looks like a quiet day. "
        f"Use `hours` instead if you want a relative window."
    )


def _positive(name: str, value: int, unit: str) -> int:
    """Refuse a window that cannot exist, instead of quietly making one up.

    `max(1, hours)` turned hours=0 into an hour and said nothing, so a model
    that meant "everything" got sixty minutes and could not tell. In search it
    was worse: the header printed the *requested* days, so days=-7 came back as
    `last -7d | 55 shown`, which describes no operation at all.
    """
    if value < 1:
        raise ToolError(
            f"`{name}` must be 1 or more; got {value}. A window of {value} {unit} "
            f"is not a window, and silently using 1 would have answered a "
            f"different question than the one asked."
        )
    return value


def _unused_archive() -> str | None:
    """A one-line description of an archive on disk that this build is not using.

    The file is never deleted and CABLEGRAM_ARCHIVE=1 returns it to service, so
    nothing is lost — but wire_search goes from searching it to searching the
    live window, and an unannounced "0 hits" is indistinguishable from a quiet
    day. That is the failure this project exists to prevent, committed by its
    own migration.
    """
    path = archive_path()
    if not path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            items, oldest, newest = db.execute(
                "SELECT COUNT(*), MIN(published), MAX(published) FROM item"
            ).fetchone()
    except sqlite3.Error:
        return None
    if not items:
        return None
    return (f"{items} items ({(oldest or '?')[:10]} .. {(newest or '?')[:10]}) are on "
            f"disk and NOT in use. This build fetches live. "
            f"Set CABLEGRAM_ARCHIVE=1 to search them again.")


def _archive_facts(db) -> tuple[int, str]:
    """How much is held, and how far back it reaches.

    The oldest item, not when the file was created: an archive holding ten years
    of a blog announced itself as starting today, so a model asked "since when
    has X been discussed" had no reason to answer.
    """
    items = db.execute("SELECT COUNT(*) FROM item").fetchone()[0]
    oldest = db.execute("SELECT MIN(published) FROM item").fetchone()[0]
    return items, (oldest[:10] if oldest else "-")


def _unknown_selectors(selectors: list[str] | None) -> list[str]:
    """Selectors that matched no source. An empty answer to a typo is plausible
    and wrong: wire_latest(sources=["deepseek"]) returned 0 items | 0/0."""
    if not selectors:
        return []
    known = {s.id for s in SOURCES} | {t for s in SOURCES for t in s.tags} | \
            {s.lang for s in SOURCES}
    return sorted(s for s in selectors if s.lower() not in known)


def build(open_db=None) -> MCPServer:
    """Assemble the server.

    `open_db` is a factory, not a connection, and that is not a testing
    convenience: MCPServer runs a synchronous tool in a worker thread, and a
    SQLite connection may only be used in the thread that created it. Sharing
    one would raise ProgrammingError on the first call — from inside the SDK,
    where it surfaces as "Error executing tool" with nothing to point at.

    A connection per call also settles the case the archive was built for: Claude
    Code and Claude Desktop reading the same file at once.
    """
    server = MCPServer(
        name="cablegram",
        instructions=(
            f"Raw dispatches from {len(SOURCES)} tech, AI and Chinese/Russian sources, "
            "filtered by "
            "date and never ranked. You are the editor: this server brings the cables, "
            "you decide what matters. Headlines are never translated — each carries its "
            "language."
        ),
    )
    # An injected factory means a caller that wants that database — the tests,
    # and anyone embedding this — so it also means archive mode.
    archive_mode = open_db is not None or _archive_requested()
    open_db = open_db or connect

    # What the last live pass in this process saw. In archive mode it stays
    # empty and nothing reads it; in live mode it is what makes an id from one
    # reply resolvable in the next, since there is no file to look it up in.
    # Bounded, because a long-lived server would otherwise grow without limit.
    seen: dict[str, dict] = {}
    session: dict[str, dict] = {}

    def remember(rows: list[dict], health: dict) -> None:
        if archive_mode:
            return
        for row in rows:
            seen[row["id"]] = row
        while len(seen) > 4000:
            seen.pop(next(iter(seen)))
        session.clear()
        session.update(health)

    def opened(selectors=None, hours: int = 24):
        """The database a call runs against.

        In archive mode it is the file the poller fills. Otherwise it is a
        throwaway in memory, filled by one pass over the sources asked for and
        discarded with the call — the same schema, the same queries, the same
        renderer. What the timer used to do an hour ago now happens here.
        """
        if archive_mode:
            return open_db()
        db = connect(memory=True)
        try:
            asyncio.run(poll_once(db, list(resolve(selectors)) or None,
                                  window_hours=max(hours, 24),
                                  deadline=LIVE_DEADLINE))
        except Exception:
            # A pass that failed outright still returns the empty database: the
            # source health it recorded on the way down is what the reply needs
            # in order to say DOWN instead of showing a short list.
            pass
        return db

    @server.tool(
        name="wire_latest",
        title="Latest dispatches",
        description=(
            f"What {len(SOURCES)} tech/AI sources published in a time window, grouped "
            "by source, "
            "newest first within each. English, Chinese and Russian, untranslated.\n"
            "Filtered by DATE ONLY — never ranked, never scored. The order says "
            "nothing about importance.\n"
            "A source listed as DOWN means UNKNOWN, not 'nothing happened there'. A "
            "declared CUT (hn=25/57) means more exist in the window; raise "
            "limit_per_source or narrow the window.\n"
            "CROSS counts how many sources carried the same URL. It is arithmetic, not "
            "a ranking — but a story in six feeds across three languages within hours "
            "is the earliest signal this server can give you.\n"
            "COST, and it decides how to call this. Fetching happens per source:\n"
            "  2-3 sources    ~1s     the ordinary move for 'anything new?'\n"
            "  one language   ~4s\n"
            f"  all {len(SOURCES)}      ~30s    Telegram is half of that on its own: six "
            "channels 3s apart, because t.me drops the sixth request in a row\n"
            "Ask before spending the expensive one. 'A proper sweep, or a quick look?' "
            "is a fair question and costs a second; thirty seconds nobody asked for is "
            "not. Either way, say what you did NOT read: 'nothing new' after three "
            "sources and 'nothing new' after all of them are different claims, and only "
            "one of them is an answer. Name what you looked at, and offer the rest.\n"
            "detail='headlines' (the default) returns titles and ids; pass those ids to "
            "wire_read for the text. detail='full' includes each stored body inline and "
            "drops to 5 per source, because bodies are expensive. Each body is prefixed "
            "[element Nc] — the feed element it came from and its length. Judge from N: "
            "under a few hundred characters it is an excerpt, whatever the element."
        ),
        annotations=READ_ONLY,
    )
    def wire_latest(
        since: str | None = None,
        hours: int = 24,
        sources: list[str] | None = None,
        detail: str = "headlines",
        limit_per_source: int | None = None,
        max_tokens: int = 12000,
    ) -> str:
        """since: ISO-8601 UTC, wins over hours. sources: ids, tags or languages."""
        until = _now()
        start = (_parse_since(since) if since
                 else _iso(until - timedelta(hours=_positive("hours", hours, "hours"))))
        if detail not in _DETAIL:
            # The only failure on this surface that disguises itself as a
            # better answer: detail="Full" fell through to headlines, so the
            # reply came back 6/6 with no CUT where the correct call returns
            # 5/6 with one. The model asked for bodies, got none, and the
            # wrong reply looked healthier than the right one.
            raise ToolError(
                f"`detail` must be exactly one of {sorted(_DETAIL)}, lowercase; "
                f"got {detail!r}. Anything else would have returned headlines "
                f"while looking like a complete answer."
            )
        if limit_per_source is None:
            limit_per_source = 5 if detail == "full" else 25

        with closing(opened(sources, hours)) as db:
            rows = latest_items(db, since=start, sources=sources,
                                limit_per_source=limit_per_source)
            health = source_health(db)
        remember(rows, health)

        wanted = {s.id for s in resolve(sources)}
        unknown = _unknown_selectors(sources)
        pollable = {s.id for s in SOURCES if s.kind in POLLABLE}
        # A source is DOWN when its most recent attempt failed — not when it has
        # never once succeeded. Looking only at last_ok let a source failing for
        # three days count as healthy here while wire_sources listed it as FAIL:
        # two tools contradicting each other, with the one called every morning
        # doing the lying.
        down = {}
        for sid in wanted & pollable:
            state = health.get(sid, {})
            if not state:
                down[sid] = "never polled"
            elif state.get("last_error") and (
                # `>=`, not `>`: a pass that downloads and then fails records
                # both attempts with the same `fetched_at`, so a strict compare
                # never fired and the failure stayed invisible. Safe, because a
                # success clears `last_error`, so this can only be true after
                # one.
                not state.get("last_ok") or state["last_try"] >= state["last_ok"]
            ):
                down[sid] = state["last_error"][:40]
        # Healthy, polled, and absent from the blocks below because they
        # published nothing in this window. Neither DOWN nor PENDING covers it,
        # so seven sources vanished from a payload whose header still said
        # 19/19 — and "openai was silent for 24h" is information, not a gap.
        silent = sorted((wanted & pollable) - set(down)
                        - {r["source"] for r in rows})
        return render_latest(rows, since=start, until=_iso(until), down=down,
                             mode="archive" if archive_mode else "live",
                             sources_total=len(wanted), silent=silent,
                             no_adapter=sorted(wanted - pollable), detail=detail,
                             unknown=unknown, limit_per_source=limit_per_source,
                             max_tokens=max_tokens)

    @server.tool(
        name="wire_read",
        title="Read dispatches",
        description=(
            "The stored text of specific dispatches, by the ids wire_latest or "
            "wire_search returned.\n"
            "Read `body=<element> <N>c` before drawing any conclusion from the text. "
            "Those are facts — the feed element the text came from, and its length — "
            "not a verdict: this server refuses to judge whether a body is a whole "
            "article, because feeds put full articles in <description> and two "
            "sentences in <atom:content>. You make that call, and N is the evidence. "
            "`body=description 36c` is a fragment and supports no conclusion at all; "
            "`body=description 3300c` is a full digest. Under a few hundred characters, "
            "cite it as an excerpt or open the url — never as the article. "
            "`body=none` means nothing was stored for THAT item; most sources ship "
            "bodies for some items and not others, so it says nothing about the "
            "source.\n"
            "COST: bodies are the expensive path — forty long ones run to roughly "
            "40,000 tokens, eight times the listing that handed you the ids. Ask for "
            "the handful you actually need, not everything a listing offered. Whatever "
            "will not fit in max_tokens (default 12000) is named on a DEFERRED line "
            "rather than dropped, so a second call can pick it up.\n"
            "Ids not in the archive are named in the reply rather than dropped: "
            "re-run wire_latest or wire_search for the same window to get current ones."
        ),
        annotations=READ_ONLY,
    )
    def wire_read(ids: list[str], max_tokens: int = 12000) -> str:
        if archive_mode:
            with closing(open_db()) as db:
                return render_read(items_by_ids(db, ids), requested=ids,
                                   max_tokens=max_tokens)
        # Live mode holds no file, so an id can only be resolved against what
        # this process has already fetched. Anything else is named on the
        # UNKNOWN line, which already tells the model to re-run the listing —
        # the recovery path was written for exactly this and needed no change.
        return render_read([seen[i] for i in ids if i in seen], requested=ids,
                           max_tokens=max_tokens)

    @server.tool(
        name="wire_search",
        title="Search the archive",
        description=(
            "Search the archived headlines of every source that carried a story.\n"
            "IMPORTANT: this searches only what this server has archived since it was "
            "first run — not the whole internet and not the sources' own history. "
            "'0 hits' means 'not in what we can search'. It does NOT mean nobody is "
            "talking about it, and must never be reported as such.\n"
            "Chinese and Russian sources are indexed in their own language: a company "
            "is 智谱 here and Zhipu on Hacker News. If a query comes back empty, retry "
            "it transliterated or translated before concluding anything."
        ),
        annotations=READ_ONLY,
    )
    def wire_search(
        query: str,
        days: int = 7,
        sources: list[str] | None = None,
        limit_per_source: int = 25,
        max_tokens: int = 8000,
    ) -> str:
        start = _iso(_now() - timedelta(days=_positive("days", days, "days")))
        with closing(opened(sources, days * 24)) as db:
            rows, engine = search_items(db, query, since=start, sources=sources,
                                        limit_per_source=limit_per_source)
            items, began = _archive_facts(db)
            remember(rows, source_health(db))
        return render_search(rows, query=query, since=start, days=days,
                             archive_start=began, archive_items=items,
                             engine=engine, max_tokens=max_tokens)

    @server.tool(
        name="wire_sources",
        title="Sources and health",
        description=(
            "The catalogue and its health: which sources exist, their language and "
            "tags, when each last answered, and which are failing.\n"
            "Read this before concluding a topic is quiet. A source that has never been "
            "polled holds nothing, which is a different fact from a source that holds "
            "nothing."
        ),
        annotations=READ_ONLY,
    )
    def wire_sources() -> str:
        if archive_mode:
            with closing(open_db()) as db:
                items, began = _archive_facts(db)
                health = source_health(db)
            return render_sources(health=health, archive_items=items,
                                  archive_start=began,
                                  archive_path=str(archive_path()))
        # Health from the last live pass in this process rather than a fresh
        # thirty-second sweep for a catalogue listing. `unused` is the one thing
        # this build must not stay quiet about: the file is still on disk and
        # wire_search no longer reads it, so its absence from an answer would
        # look like a quiet archive rather than an archive nobody opened.
        return render_sources(health=dict(session), archive_items=0,
                              archive_start="-", archive_path=str(archive_path()),
                              live=True, unused=_unused_archive())

    return server


def serve() -> None:
    build().run(transport="stdio")


def main() -> None:
    serve()
