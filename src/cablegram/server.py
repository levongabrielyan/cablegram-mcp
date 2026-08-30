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

from contextlib import closing
from datetime import datetime, timedelta, timezone

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .archive import archive_path, connect
from .render import render_latest, render_read, render_search, render_sources
from .poll import POLLABLE
from .sources import SOURCES, resolve
from .store import (items_by_ids, latest_items, search_items, source_health)

__all__ = ["build", "serve", "main"]

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _archive_facts(db) -> tuple[int, str]:
    items = db.execute("SELECT COUNT(*) FROM item").fetchone()[0]
    started = db.execute("SELECT v FROM meta WHERE k = 'archive_started_at'").fetchone()
    return items, (started[0][:10] if started else "-")


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
            "Raw dispatches from 19 tech, AI and Chinese/Russian sources, filtered by "
            "date and never ranked. You are the editor: this server brings the cables, "
            "you decide what matters. Headlines are never translated — each carries its "
            "language."
        ),
    )
    open_db = open_db or connect

    @server.tool(
        name="wire_latest",
        description=(
            "What 19 tech/AI sources published in a time window, grouped by source, "
            "newest first within each. English, Chinese and Russian, untranslated.\n"
            "Filtered by DATE ONLY — never ranked, never scored. The order says "
            "nothing about importance.\n"
            "A source listed as DOWN means UNKNOWN, not 'nothing happened there'. A "
            "declared CUT (hn=25/57) means more exist in the window; raise "
            "limit_per_source or narrow the window.\n"
            "CROSS counts how many sources carried the same URL. It is arithmetic, not "
            "a ranking — but a story in six feeds across three languages within hours "
            "is the earliest signal this server can give you.\n"
            "detail='headlines' (the default) returns titles and ids; pass those ids to "
            "wire_read for the text. detail='full' includes each stored body inline and "
            "drops to 5 per source, because bodies are expensive — a teaser is marked as "
            "one, and is NOT the article."
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
        start = since or _iso(until - timedelta(hours=max(1, hours)))
        if limit_per_source is None:
            limit_per_source = 5 if detail == "full" else 25

        with closing(open_db()) as db:
            rows = latest_items(db, since=start, sources=sources,
                                limit_per_source=limit_per_source)
            health = source_health(db)

        wanted = {s.id for s in resolve(sources)}
        pollable = {s.id for s in SOURCES if s.kind in POLLABLE}
        down = {
            sid: (health.get(sid, {}).get("last_error") or "never polled")[:40]
            for sid in wanted & pollable
            if not health.get(sid, {}).get("last_ok")
        }
        return render_latest(rows, since=start, until=_iso(until), down=down,
                             sources_total=len(wanted),
                             no_adapter=sorted(wanted - pollable), detail=detail,
                             limit_per_source=limit_per_source, max_tokens=max_tokens)

    @server.tool(
        name="wire_read",
        description=(
            "The stored text of specific dispatches, by the ids wire_latest or "
            "wire_search returned.\n"
            "Read body=teaser literally: that feed ships a truncated excerpt, and the "
            "text you get is NOT the article. Do not draw conclusions from it — open "
            "the url or say the full text was not available.\n"
            "Ids not in the archive are named in the reply rather than dropped."
        ),
        annotations=READ_ONLY,
    )
    def wire_read(ids: list[str]) -> str:
        with closing(open_db()) as db:
            return render_read(items_by_ids(db, ids), requested=ids)

    @server.tool(
        name="wire_search",
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
        start = _iso(_now() - timedelta(days=max(1, days)))
        with closing(open_db()) as db:
            rows = search_items(db, query, since=start, sources=sources,
                                limit_per_source=limit_per_source)
            items, began = _archive_facts(db)
        return render_search(rows, query=query, since=start, days=days,
                             archive_start=began, archive_items=items,
                             max_tokens=max_tokens)

    @server.tool(
        name="wire_sources",
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
        with closing(open_db()) as db:
            items, began = _archive_facts(db)
            health = source_health(db)
        return render_sources(health=health, archive_items=items,
                              archive_start=began, archive_path=str(archive_path()))

    return server


def serve() -> None:
    build().run(transport="stdio")


def main() -> None:
    serve()
