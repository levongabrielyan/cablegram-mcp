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
from math import ceil

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .schema import connect
from .render import render_latest, render_read, render_search, render_sources
from .poll import POLLABLE, poll_once
from .sources import SOURCES, resolve
from .store import (is_down, items_by_ids, latest_items, search_items,
                    source_health)

__all__ = ["build", "serve", "main"]

# One pass has to finish inside a tool call, and the theoretical worst case
# without a bound is 130s. Forty-five is comfortably above the ~30s a full
# full pass measures, and whatever has not answered by then is
# reported DOWN rather than waited for.
LIVE_DEADLINE = 45.0

# Dispatches this process can still resolve an id for. Nothing is kept between
# runs, so this is the whole of wire_read's reach.
SEEN_LIMIT = 4000

_DETAIL = ("headlines", "full")


READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Zero-pad the year, because these strings are compared as strings.

    `%Y` writes a year under 1000 without its leading zero, so a window reaching
    back to the year 885 was written `885-11-14T16:33:21Z` and `published >= ?`
    compared "2" against "8" — excluding every row ever stored. Measured against
    Hacker News, which had 982 items in the window:

        hours=10000000  ->  0 of 0 items | 1/1 sources
                            SILENT hn (answered, published nothing in this window)

    An affirmative claim that Hacker News published nothing in eleven centuries,
    with the source marked healthy and nothing in the reply to check it against.
    With the zero it returns all 982.
    """
    return f"{dt.year:04d}-{dt:%m-%dT%H:%M:%SZ}"


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
        parsed = parsed.astimezone(timezone.utc)
        # A window that ends before it starts. `since=2027-01-01` parsed
        # cleanly, `until - start` went negative, `max(hours, -2919)` handed the
        # poller the 24h default, and every row was then filtered against a
        # timestamp in the future. Measured:
        #
        #     | 2027-01-01T00:00:00Z..2026-09-01T08:33:39Z | 0 of 0 items | 1/1
        #     SILENT hn  (answered, published nothing in this window)
        #
        # The header prints the two ends in the order given, so the impossible
        # window is on the first line and the reply still asserts underneath
        # that the source answered and published nothing inside it.
        now = _now()
        if parsed > now:
            raise ToolError(
                f"`since` is in the future: {_iso(parsed)} is after {_iso(now)}, "
                f"so it describes a window that ends before it starts. Nothing "
                f"can be published inside it, and the empty reply would have "
                f"read as a quiet period rather than as an impossible request."
            )
        return _iso(parsed)
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
    """Refuse an argument that describes no possible request.

    `max(1, hours)` turned hours=0 into an hour and said nothing, so a model
    that meant "everything" got sixty minutes and could not tell. In search it
    was worse: the header printed the *requested* days, so days=-7 came back as
    `last -7d | 55 shown`, which describes no operation at all.

    `limit_per_source` was left out of that round and is the worst of the three,
    because it empties the payload without emptying the header. Measured against
    a 24h window holding 966 items from 14 sources, limit_per_source=0 returned
    `0 of 0 items | 21/21 sources` above a SILENT line naming all twenty-one as
    having answered and published nothing. Every clause of that is false, the
    reply is perfectly well formed, and nothing in it can be checked against
    anything else in it.
    """
    if value < 1:
        raise ToolError(
            f"`{name}` must be 1 or more; got {value}. {value} {unit} is not a "
            f"request that can be answered, and silently using 1 would have "
            f"answered a different question than the one asked."
        )
    return value


def _ago(until: datetime, name: str, value: int) -> str:
    """The start of a window `value` units before `until`, or a refusal.

    Past the year 1 the subtraction raises OverflowError, and an exception that
    is not a ToolError reaches the model as "Error executing tool" with no text
    at all. Measured: `hours=87600000` and `days=3650000` both returned exactly
    that — the caller is told the call failed and nothing about why or what to
    try, which for a window argument is indistinguishable from the server being
    broken.
    """
    step = timedelta(**{name: _positive(name, value, name)})
    try:
        return _iso(until - step)
    except OverflowError:
        floor = datetime.min.replace(tzinfo=timezone.utc)
        widest = int((until - floor) / timedelta(**{name: 1}))
        raise ToolError(
            f"`{name}={value}` reaches past the calendar: that many {name} "
            f"before now is a year this server cannot write down. The widest "
            f"window it can answer is `{name}={widest}`, which reaches the "
            f"year 1 and returns everything every source served."
        ) from None


def _down_sources(health: dict, wanted: set[str]) -> dict[str, str]:
    """Which of `wanted` did not answer, and why.

    Shared by wire_latest and wire_search rather than written out in each. The
    rule for "down" already existed twice — here and in render_sources — and the
    tool where a missing source is least visible had no copy of it at all.
    """
    pollable = {s.id for s in SOURCES if s.kind in POLLABLE}
    down: dict[str, str] = {}
    for sid in sorted(wanted & pollable):
        state = health.get(sid) or {}
        if not state:
            down[sid] = "never polled"
        elif is_down(state):
            down[sid] = state["last_error"][:40]
    return down


def _window_facts(db) -> tuple[int, str]:
    """How much this fetch pulled in, and how far back it reaches.

    Both are properties of what the feeds happen to serve today, not of a
    subject: one pass over openai's feed loads 1,157 items back to 2015 and
    another source serves ten. The COVER block says so, because the floor read
    as a statement about the story is the most expensive wrong conclusion this
    surface can produce.
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


def build(rows_from=None) -> MCPServer:
    """Assemble the server.

    `rows_from` is a seam for the tests and nothing else: a factory returning a
    database already holding known rows, so the four tools can be exercised
    without a network. It must be a factory rather than a connection, because
    MCPServer runs a synchronous tool in a worker thread and a SQLite connection
    may only be used in the thread that created it — sharing one raises
    ProgrammingError from inside the SDK, where it surfaces as "Error executing
    tool" with nothing to point at.

    In production it is never passed. Every call fetches.
    """
    server = MCPServer(
        name="cablegram",
        # Empty in the handshake until now, so a client could not tell a build
        # serving nineteen sources from one serving twenty-nine.
        version=__version__,
        instructions=(
            f"Raw dispatches from {len(SOURCES)} tech, AI and Chinese/Russian sources, "
            "filtered by "
            "date and never ranked. You are the editor: this server brings the cables, "
            "you decide what matters. Headlines are never translated — each carries its "
            "language."
        ),
    )
    # What the last pass in this process saw. Nothing is kept between runs, so
    # this is the only thing that makes an id from one reply resolvable in the
    # next. Bounded, because a long-lived server would otherwise grow without
    # limit.
    seen: dict[str, dict] = {}
    session: dict[str, dict] = {}

    def remember(rows: list[dict], health: dict) -> None:
        # Oldest first, so the newest survive. The rows arrive newest-first and
        # eviction pops whatever went in first, so a single call larger than the
        # cache evicted the top of its own reply — measured with 5,000 rows: the
        # first dispatch printed no longer resolved and the last one did. Those
        # are the ones a model reads first, and with nothing kept between runs
        # this cache is the only thing that resolves an id at all.
        for row in reversed(rows):
            seen[row["id"]] = row
        while len(seen) > SEEN_LIMIT:
            seen.pop(next(iter(seen)))
        session.clear()
        session.update(health)

    def opened(selectors=None, hours: int = 24):
        """The database a call runs against.

        Built in memory, filled by one pass over the sources asked for, and
        discarded when the call ends. Nothing is written to disk and nothing
        survives the reply — the same schema, the same queries and the same
        renderer as any store, held only long enough to answer.
        """
        if rows_from is not None:
            return rows_from()
        db = connect()
        targets = list(resolve(selectors)) if selectors else None
        if selectors and not targets:
            # "You gave me no selector" and "your selector matched nothing" both
            # resolve to an empty list, and `or None` read them both as
            # everything. Free against a file; in live mode a typo cost 23
            # seconds and 22 requests to other people's servers, for a question
            # nobody asked. The UNKNOWN SELECTOR line already explains the empty
            # reply, and this build spends a paragraph teaching the model to ask
            # before spending that sweep.
            return db
        try:
            asyncio.run(poll_once(db, targets,
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
            "COST, and it decides how to call this. Everything except Telegram is "
            "fetched in parallel, so the price is set by how many Telegram channels the "
            f"selection pulls in: there are {len(resolve(['telegram']))} of them and "
            "they go one at a time, three seconds apart, because t.me drops the sixth "
            "request in a row. Measured, three passes of each:\n"
            f"  no Telegram        ~1-2s    however many: the {len(resolve(['en']))} "
            "English ones cost what two do\n"
            "  1-2 channels       ~1-13s\n"
            "  all of them        ~20-36s  which is also what sources=['ru'] costs, "
            f"because {len(resolve(['telegram']))} of the {len(resolve(['ru']))} Russian "
            "ones are channels — language is not the axis, channel count is\n"
            f"  no `sources` at all  ~23-45s  all {len(SOURCES)}, and a slow pass reaches "
            "the 45s ceiling with the last channels reported DOWN rather than dropped\n"
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
        if since:
            start = _parse_since(since)
            # The window the caller asked for, not the default. Hacker News puts
            # the window in its query and serves up to a thousand rows, so
            # `since` reaching thirty days back while 24 was handed to the
            # poller answered one day and printed a thirty-day header over it:
            # both calls came back with the same 906 items. wire_search already
            # derives this; wire_latest is where `since` lives.
            span = until - datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
            hours = max(hours, ceil(span.total_seconds() / 3600))
        else:
            start = _ago(until, "hours", hours)
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
        else:
            _positive("limit_per_source", limit_per_source, "items per source")

        with closing(opened(sources, hours)) as db:
            rows = latest_items(db, since=start, sources=sources,
                                limit_per_source=limit_per_source)
            health = source_health(db)
        remember(rows, health)

        wanted = {s.id for s in resolve(sources)}
        unknown = _unknown_selectors(sources)
        pollable = {s.id for s in SOURCES if s.kind in POLLABLE}
        down = _down_sources(health, wanted)
        # Healthy, polled, and absent from the blocks below because they
        # published nothing in this window. Neither DOWN nor PENDING covers it,
        # so seven sources vanished from a payload whose header still said
        # 19/19 — and "openai was silent for 24h" is information, not a gap.
        silent = sorted((wanted & pollable) - set(down)
                        - {r["source"] for r in rows})
        return render_latest(rows, since=start, until=_iso(until), down=down,
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
            "source. `body=hub` is not article text at all — it is a trend score and "
            "two counts, so its short length is the format rather than a truncation, "
            "and there is no fuller version to open.\n"
            "COST: bodies are the expensive path — forty long ones run to roughly "
            "40,000 tokens, eight times the listing that handed you the ids. Ask for "
            "the handful you actually need, not everything a listing offered. Whatever "
            "will not fit in max_tokens (default 12000) is named on a DEFERRED line "
            "rather than dropped, so a second call can pick it up.\n"
            "An id that does not resolve is named in the reply rather than dropped. "
            "Nothing is kept between runs, so an id resolves only while the call that "
            "produced it is still in this process's cache: re-run wire_latest or "
            "wire_search over the same window AND the same `sources` to get a current "
            "one."
        ),
        annotations=READ_ONLY,
    )
    def wire_read(ids: list[str], max_tokens: int = 12000) -> str:
        # Nothing is kept between runs, so an id resolves only against what this
        # process has already fetched. Anything else is named on the UNKNOWN
        # line, which tells the model how to get a current one.
        return render_read([seen[i] for i in ids if i in seen], requested=ids,
                           max_tokens=max_tokens)

    @server.tool(
        name="wire_search",
        title="Search the sources",
        description=(
            "Search the headlines of every source that carried a story.\n"
            "WHAT IS BEING SEARCHED: this call fetches the sources and searches what "
            "they serve right now, then throws it away. There is no archive and no "
            "history — coverage is whatever the feeds expose today, and it is wildly "
            "uneven. One serves its back catalogue to 2015, another serves ten items, "
            "and the COVER line gives the real floor.\n"
            "So this is NOT the whole internet and NOT the past. '0 hits' means 'not "
            "in what we can search'. It does NOT mean nobody is talking about it, and "
            "must never be reported as such — read the DOWN and UNKNOWN SELECTOR lines "
            "first, because a source that was never searched returns 0 hits exactly "
            "like a source with nothing to say.\n"
            "COST: in live mode this fetches, on the same terms as wire_latest — "
            f"~1-2s for any number of non-Telegram sources, ~20-45s once the "
            f"{len(resolve(['telegram']))} Telegram channels are in, which is what "
            "asking for no `sources` does. Ask before spending that, and say afterwards "
            "which sources you actually searched.\n"
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
        if not query.strip():
            # Every other impossible argument on this surface is refused —
            # hours=0, days=-7, limit_per_source=0, detail='Full', a malformed
            # `since`. The one that was not is the one most likely to arrive
            # empty by accident, and the one that decides whether anything is
            # searched at all.
            raise ToolError(
                "`query` is empty, so nothing would be searched — and the reply "
                "would come back `0 shown hits`, which reads as an answer. Pass a "
                "term, or use wire_latest if what you want is a whole window."
            )
        start = _ago(_now(), "days", days)
        _positive("limit_per_source", limit_per_source, "items per source")
        with closing(opened(sources, days * 24)) as db:
            rows, engine = search_items(db, query, since=start, sources=sources,
                                        limit_per_source=limit_per_source)
            items, began = _window_facts(db)
            health = source_health(db)
            remember(rows, health)
        # The same four facts wire_latest already carries. A search is the tool
        # where their absence costs most: a listing that comes back short still
        # shows which sources it did print, and "0 hits" shows nothing at all.
        return render_search(rows, query=query, since=start, days=days,
                             archive_start=began, archive_items=items,
                             engine=engine,
                             down=_down_sources(health, {s.id for s in resolve(sources)}),
                             unknown=_unknown_selectors(sources),
                             max_tokens=max_tokens)

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
        # Health from the last pass in this process rather than a fresh
        # thirty-second sweep for a catalogue listing. A source nobody has asked
        # for in this session has no state at all, which is a different fact
        # from a source that failed, and render_sources says so.
        return render_sources(health=dict(session))

    return server


def serve() -> None:
    build().run(transport="stdio")


def main() -> None:
    serve()
