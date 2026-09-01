"""Assertions for the store, the poller and the server surface that a mutation
walked straight through on 2026-08-31.

The chain nothing covered end to end is AT CEILING: poll_once writes it into
`meta`, source_health lifts it out, render_sources prints it, and every one of
those three links could be cut with the suite still green — for the one marker
that says an article is gone for good.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

import httpx2
import pytest
from dates import rss_date

from cablegram.fetch import Fetched
from cablegram.poll import poll_once
from cablegram.rss import Entry
from cablegram.schema import connect
from cablegram.server import build
from cablegram.sources import by_id
from cablegram.store import (latest_items, record_attempt, search_items,
                             source_health, store_entries)
from cablegram.urls import item_id

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
ISO = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db():
    conn = connect()
    yield conn
    conn.close()


@pytest.fixture
def network(monkeypatch):
    def install(handler):
        real = httpx2.AsyncClient

        def patched(*args, **kwargs):
            kwargs.setdefault("transport", httpx2.MockTransport(handler))
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx2, "AsyncClient", patched)

    return install


# ── the write path ──────────────────────────────────────────────────────────

def test_an_entry_with_no_headline_is_skipped_rather_than_stored_blank(db):
    """`title TEXT NOT NULL` accepts the empty string, so a feed item with a
    link and no headline archives as a row that renders as an id, a time and
    nothing — indistinguishable from a rendering bug, and it occupies a slot in
    a per-source limit that a real dispatch would have had."""
    report = store_entries(db, by_id("qbitai"),
                           [Entry("", "https://qbitai.com/x", NOW, None, None)],
                           fetched_at=ISO)
    assert (report.skipped, report.new) == (1, 0)
    assert db.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 0


def test_a_source_that_answered_and_then_failed_in_the_same_second_is_down(db):
    """A pass that downloads and then fails records both attempts with the same
    `fetched_at` — cls.cn answers a rejected signature with HTTP 200, so the
    failure is found after the success. Compared strictly, `last_try > last_ok`
    is false and the source reports OK with an error sitting in its own row.

    The whole of that pass is one second wide in the stamps this project keeps,
    so the strict compare does not fire rarely; it never fires.
    """
    from cablegram.store import is_down

    url = by_id("cls").url
    record_attempt(db, Fetched("cls", url=url, ok=True, body=b"x", status=200,
                               fetched_at=ISO))
    record_attempt(db, Fetched("cls", url=url, ok=False,
                               error="unparseable: ValueError: errno=10012",
                               fetched_at=ISO))
    assert is_down(source_health(db)["cls"]), (
        "the download succeeded and the parse failed in the same second; the "
        "source is not healthy")


def test_a_channel_that_linked_a_story_counts_towards_its_cross_source_total(db):
    """The six Telegram channels publish permalinks to their own posts, so they
    can never share a URL with anybody. Counting only feed sightings makes them
    incapable of appearing in a cross-source count at all — which is the whole
    reason `_record_reference` exists, and the reason those channels are in the
    catalogue.
    """
    linked = "https://qwen.ai/blog/qwen4"
    store_entries(db, by_id("ai_newz"),
                  [Entry("Вышла Qwen 4", "https://t.me/ai_newz/1", NOW,
                         "текст", "message", links=(linked,))],
                  fetched_at=ISO)
    store_entries(db, by_id("hn"),
                  [Entry("Qwen 4 released", linked, NOW, None, None)],
                  fetched_at=ISO)

    row = [r for r in latest_items(db, since="2026-08-01T00:00:00Z")
           if r["id"] == item_id(linked)][0]
    assert row["cross"] == 2, (
        "ai_newz carried this story by linking it; a count of 1 says nobody "
        "else did")


def test_an_item_published_exactly_at_the_window_edge_is_inside_it(db):
    """`since` is a floor the caller names, and a model asking "since my last
    call" hands back the timestamp of the previous reply. An exclusive compare
    drops precisely the items on that boundary, every time, and the reply is a
    well-formed shorter list."""
    store_entries(db, by_id("qbitai"),
                  [Entry("智谱发布GLM-5", "https://qbitai.com/x", NOW, None, None)],
                  fetched_at=ISO)
    assert latest_items(db, since=ISO), (
        f"an item published at {ISO} is missing from the window that starts at "
        f"{ISO}")


def test_the_cross_source_list_names_every_source_the_count_counts(db):
    """The count and the list are two columns of the same query and were built
    from different ones: `x2[hub]` reached the reply for a story hn and hub both
    carried. Whichever half the model believes, the other is wrong."""
    url = "https://qbitai.com/glm5"
    store_entries(db, by_id("qbitai"), [Entry("智谱发布GLM-5", url, NOW, None, None)],
                  fetched_at=ISO)
    store_entries(db, by_id("hn"), [Entry("Zhipu releases GLM-5", url, NOW, None, None)],
                  fetched_at=ISO)

    row = latest_items(db, since="2026-08-01T00:00:00Z")[0]
    assert row["sources"], "the row has to name the sources its count counts"
    assert len(row["sources"].split(",")) == row["cross"], (
        f"cross={row['cross']} and sources={row['sources']!r}")


def test_a_search_term_holding_a_wildcard_is_matched_literally(db):
    """Under three characters the query falls to a LIKE scan. Unescaped, `%`
    matches everything: the reply comes back full of hits for a term nothing
    contains, with a header declaring them matches and a COVER block explaining
    how to read a small number — never how to read a fabricated one."""
    store_entries(db, by_id("qbitai"),
                  [Entry("智谱发布GLM-5", "https://qbitai.com/x", NOW, None, None)],
                  fetched_at=ISO)
    rows, engine = search_items(db, "%", since="2026-08-01T00:00:00Z")
    assert engine == "substring"
    assert rows == [], "`%` is a character somebody typed, not a wildcard"


def test_source_health_carries_the_two_facts_only_it_can_report(db):
    """`newest` and `at_ceiling` are both computed here and printed nowhere
    else. Returned as None they do not break a line or shift a column: the
    catalogue simply prints "-" where the date of the newest item goes, and
    drops the marker that says an article is past a ceiling that cannot be
    paged back through."""
    with db:
        db.execute("INSERT INTO meta(k, v) VALUES (?, ?)", ("ceiling:cls", ISO))
    record_attempt(db, Fetched("cls", url=by_id("cls").url, ok=True, body=b"x",
                               status=200, fetched_at=ISO))
    store_entries(db, by_id("cls"),
                  [Entry("智谱发布GLM-5", "https://cls.cn/detail/1", NOW, None, None)],
                  fetched_at=ISO)

    state = source_health(db)["cls"]
    assert state["at_ceiling"] == ISO, "the ceiling this pass reached"
    assert state["newest"] == ISO, "the date of the newest item it holds"


# ── the poller ──────────────────────────────────────────────────────────────

HUB_PAGE = json.dumps([
    {"id": f"org/model-{i}", "createdAt": "2026-08-30T10:00:00.000Z",
     "likes": 10, "downloads": 20, "trendingScore": 5}
    for i in range(50)
]).encode()


def test_a_lab_listing_ordered_by_date_never_claims_a_ceiling(db, network):
    """The global hub listing asks for exactly MAX_ROWS and is truncated on
    every poll, so it has to say so. A lab's own namespace is ordered by date
    and holds only that lab's repos, so fifty is far more than any of them
    publishes in a window this server asks about — the marker would be
    permanently on for six of the catalogue's sources and mean nothing, which
    is how a warning stops being read where it does matter."""
    network(lambda request: httpx2.Response(200, content=HUB_PAGE))
    reports = asyncio.run(poll_once(db, [by_id("qwen")]))
    assert reports[0].at_ceiling is False, (
        "an author listing that returned fifty repos has not run out of "
        "anything; the marker would never be off")


def test_a_source_that_returned_no_entries_is_reported_as_a_failure(db, network):
    """A valid document with no entries is what a feed looks like the day it
    changes format, and it is the most likely failure this project has.
    Recorded only in the report — which nothing outside the poller reads — it
    reaches the reply as a source that answered and published nothing, which is
    the exact sentence the whole server exists to prevent being false."""
    from cablegram.store import is_down

    network(lambda request: httpx2.Response(
        200, content=b'<rss version="2.0"><channel></channel></rss>'))
    reports = asyncio.run(poll_once(db, [by_id("qbitai")]))

    assert reports[0].state == "parsed-empty"
    assert is_down(source_health(db)["qbitai"]), (
        "a feed that changed shape answered 200 and holds nothing; nothing "
        "downstream of the report can tell that from a quiet day")


def test_the_window_the_poller_is_given_is_the_window_it_asks_for(db, network):
    """Hacker News and cls.cn take the window in the request. Hard-coding it
    here answers a different question from the one the header prints — measured
    live, `since=2026-08-01` and `hours=24` came back with the same 906 items
    under two different headers, and nothing in either reply distinguishes
    them."""
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx2.Response(200, content=json.dumps({"hits": []}).encode())

    network(handler)
    asyncio.run(poll_once(db, [by_id("hn")], window_hours=720))

    floor = int(re.search(r"created_at_i%3E(\d+)", asked[0]).group(1))
    span = datetime.now(timezone.utc).timestamp() - floor
    assert span > 700 * 3600, (
        f"a 720h window asked Hacker News for the last {span / 3600:.0f}h")


def test_a_pass_whose_results_do_not_line_up_fails_rather_than_misfiling(db, network):
    """`zip` without `strict` pairs whatever is shorter and drops the rest in
    silence. Every result here is filed under a source by position, so one
    missing reply shifts every source after it onto somebody else's outcome —
    and the report list comes back short with nothing to say which two sources
    went missing. That is the failure the whole module is built around, arriving
    as a silent truncation of its own output."""
    import cablegram.poll as poll_mod

    real = poll_mod.fetch_all

    async def short(requests, **kwargs):
        return (await real(requests, **kwargs))[:-1]

    poll_mod.fetch_all, saved = short, poll_mod.fetch_all
    try:
        with pytest.raises(ValueError):
            asyncio.run(poll_once(db, [by_id("qbitai"), by_id("openai")]))
    finally:
        poll_mod.fetch_all = saved


# ── the MCP surface ─────────────────────────────────────────────────────────

FEED = f"""<rss version="2.0"><channel>
  <item><title>GLM-5 released</title><link>https://qbitai.com/glm5</link>
        <pubDate>{rss_date(6)}</pubDate>
        <description>Body of the story</description></item>
</channel></rss>""".encode()


@pytest.fixture
def live(monkeypatch):
    import cablegram.poll as poll_mod

    monkeypatch.setattr(poll_mod, "TELEGRAM_GAP", 0.01)
    real = httpx2.AsyncClient

    def handler(request):
        if "qbitai" in str(request.url):
            return httpx2.Response(200, content=FEED)
        return httpx2.Response(503)

    def patched(*args, **kwargs):
        kwargs.setdefault("transport", httpx2.MockTransport(handler))
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", patched)
    return build()


async def call(server, name, **args):
    result = await server.call_tool(name, args)
    return result.content[0].text if hasattr(result, "content") else str(result)


@pytest.mark.anyio
async def test_a_selector_the_call_honoured_is_not_also_called_a_typo(live):
    """`resolve` lowercases what it is given, so `sources=["QBITAI"]` fetches
    qbitai and prints its dispatches. A selector check that does not lowercase
    puts UNKNOWN SELECTOR over the same reply — one payload saying both that
    the selector matched nothing and that here is what it matched."""
    out = await call(live, "wire_latest", hours=48, sources=["QBITAI"])
    printed = set(re.findall(r"^## (\S+) ", out, re.M))
    unknown = re.search(r"^UNKNOWN SELECTOR (.*?)  ->", out, re.M)
    assert printed, "the call fetched and printed a source"
    assert not unknown, (
        f"the reply prints {sorted(printed)} and calls the selector that "
        f"produced them unknown: {unknown.group(1)!r}")


@pytest.mark.anyio
async def test_a_tag_is_a_selector_and_is_not_reported_as_a_typo(live):
    """"Nobody remembers twenty-eight ids" is why `resolve` takes tags at all,
    and a tag is the common case. Reported as a typo it lands under the line
    saying nothing was searched for it, above the results of searching for it."""
    out = await call(live, "wire_latest", hours=48, sources=["lab"])
    assert "UNKNOWN SELECTOR" not in out, (
        "`lab` is a tag eight sources carry; the reply calls it unmatched")


@pytest.mark.anyio
async def test_a_misspelled_detail_is_refused_rather_than_answered_as_headlines(live):
    """The only failure on this surface that disguises itself as a better
    answer. `detail="Full"` fell through to headlines, so the reply came back
    6/6 with no CUT where the correct call returns 5/6 with one: the model asked
    for bodies, got none, and the wrong reply looked healthier than the right
    one."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as raised:
        await call(live, "wire_latest", hours=48, sources=["qbitai"], detail="Full")
    assert "detail" in str(raised.value)


@pytest.mark.anyio
async def test_asking_for_bodies_lowers_the_per_source_limit_the_description_promises(live):
    """The description prices this: `detail='full'` "drops to 5 per source,
    because bodies are expensive". Left at 25 the call costs five times what the
    model was told, on the one path whose whole warning is about cost — and
    nothing in the reply names the limit that was applied."""
    server = build()
    import cablegram.server as server_mod

    captured = {}
    real = server_mod.render_latest

    def spy(rows, **kwargs):
        captured.update(kwargs)
        return real(rows, **kwargs)

    server_mod.render_latest = spy
    try:
        await call(live, "wire_latest", hours=48, sources=["qbitai"], detail="full")
        full = captured["limit_per_source"]
        await call(live, "wire_latest", hours=48, sources=["qbitai"])
        headlines = captured["limit_per_source"]
    finally:
        server_mod.render_latest = real

    assert full < headlines, (
        f"detail='full' is priced as the expensive path and asks for {full} per "
        f"source against {headlines} for headlines")


@pytest.mark.anyio
async def test_a_thirty_day_search_asks_the_sources_for_thirty_days(live, monkeypatch):
    """`days` is printed in the header as "last 30d" and decides the `since` the
    query filters on. Handing the poller a fixed day means the reply searched
    one day and says it searched thirty — the same defect `since` had in
    wire_latest, in the tool where an empty result is the expected shape."""
    import cablegram.server as server_mod

    asked = []
    real = server_mod.poll_once

    async def spy(db, sources, **kwargs):
        asked.append(kwargs.get("window_hours"))
        return await real(db, sources, **kwargs)

    monkeypatch.setattr(server_mod, "poll_once", spy)
    await call(live, "wire_search", query="GLM", days=30, sources=["qbitai"])

    assert asked and asked[0] >= 30 * 24, (
        f"a 30-day search asked the sources for {asked[0]}h")


@pytest.mark.anyio
async def test_the_catalogue_reports_the_health_of_the_call_before_it(live):
    """wire_sources is the tool the description tells the model to read before
    concluding a topic is quiet. Handed an empty health it prints "not in last
    call" for every source in the catalogue on every call — including the ones
    that just failed — so the tool built to expose a broken source is the one
    that cannot."""
    await call(live, "wire_latest", hours=48, sources=["qbitai", "openai"])
    out = await call(live, "wire_sources")

    qbitai = [l for l in out.splitlines() if l.startswith("qbitai ")][0]
    openai = [l for l in out.splitlines() if l.startswith("openai ")][0]
    assert "not in last call" not in qbitai, (
        f"qbitai answered the call before this one:\n{qbitai}")
    assert "FAIL" in openai, f"openai failed the call before this one:\n{openai}"


@pytest.mark.anyio
async def test_the_coverage_line_counts_what_this_call_actually_fetched(live):
    """COVER is the floor a model reads to decide how far back the answer
    reaches. It is the only figure in a search reply that describes the fetch
    rather than the query, so nothing else in the payload contradicts it when
    it is wrong."""
    out = await call(live, "wire_search", query="GLM", days=7, sources=["qbitai"])
    fetched = int(re.search(r"fetched (\d+) items", out).group(1))
    assert fetched == FEED.count(b"<item>"), (
        f"the feed served {FEED.count(b'<item>')} items and COVER reports "
        f"{fetched}")


def test_an_empty_query_is_refused_rather_than_answered(db):
    """A blank query has no engine and no result. Turned into a scan for a
    space it returns every headline that contains one, and the reply files them
    under `N shown` for a term nobody typed — the first tool whose whole
    description teaches the model how to read a small number, reporting a
    fabricated large one."""
    store_entries(db, by_id("qbitai"),
                  [Entry("智谱 发布 GLM-5", "https://qbitai.com/x", NOW, None, None)],
                  fetched_at=ISO)
    rows, engine = search_items(db, "   ", since="2026-08-01T00:00:00Z")
    assert (rows, engine) == ([], "none")


@pytest.mark.anyio
async def test_a_source_that_returned_all_it_could_says_so_in_the_catalogue(monkeypatch):
    """The whole chain, which nothing walked: poll_once decides the ceiling was
    reached, writes it into `meta`, source_health lifts it back out, and
    render_sources prints it. Any one of those three links could be cut with the
    suite green — for the marker that says, on the one source that cannot page
    backwards, that an article is gone for good.
    """
    import cablegram.poll as poll_mod

    monkeypatch.setattr(poll_mod, "TELEGRAM_GAP", 0.01)
    real = httpx2.AsyncClient

    def patched(*args, **kwargs):
        kwargs.setdefault("transport", httpx2.MockTransport(
            lambda request: httpx2.Response(200, content=HUB_PAGE)))
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", patched)
    server = build()

    await call(server, "wire_latest", hours=48, sources=["hub"])
    out = await call(server, "wire_sources")
    line = [l for l in out.splitlines() if l.startswith("hub ")][0]
    assert "AT CEILING" in line, (
        f"the listing returned every row it can return and the catalogue does "
        f"not say so:\n{line}")
