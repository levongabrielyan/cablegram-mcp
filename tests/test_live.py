"""The whole path: fetch, parse, store, query, render.

Every other server test injects a factory of known rows, which skips the fetch
entirely — so for a long time nothing exercised the path that ships. Measured
then: `opened()` could be made to return an empty database without ever calling
poll_once, and the suite stayed green at 370. The server would have answered 0
items and SILENT for every source, on every call, for ever.

Several header facts were reachable only from here: the tally of sources asked
for, `silent`, the "never polled" branch of DOWN, and the process cache that
makes an id from one reply readable in the next. So were both halves of the bug
that let a Telegram channel's Russian post be served as that channel's own
dispatch.

It is now the only mode there is, so this file covers the product rather than
one half of it — but it is kept separate because it is the only one that goes
through fetch and parse, with the network answering from a fixture.
"""

import re

import httpx2
import pytest

from mcp.server.mcpserver.exceptions import ToolError

from cablegram.server import build
from cablegram.urls import item_id
from dates import iso_date, rss_date

FEED = f"""<rss version="2.0"><channel>
  <item><title>GLM-5 released</title><link>https://qbitai.com/glm5</link>
        <pubDate>{rss_date(6)}</pubDate>
        <description>Body of the story</description></item>
  <item><title>Second story</title><link>https://qbitai.com/second</link>
        <pubDate>{rss_date(5)}</pubDate></item>
</channel></rss>""".encode()

CHANNEL = f"""<div class="tgme_widget_message" data-post="ai_newz/1">
  <time datetime="{iso_date(4)}">x</time>
  <div class="tgme_widget_message_text js-message_text">GLM-5 вышла
    <a href="https://qbitai.com/glm5">тут</a></div>
</div>"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def live(monkeypatch, tmp_path):
    """A server on the default path: no factory, no file, one fetch per call."""
    import cablegram.poll as poll_mod

    monkeypatch.setattr(poll_mod, "TELEGRAM_GAP", 0.01)

    real = httpx2.AsyncClient

    def handler(request):
        url = str(request.url)
        if "qbitai" in url:
            return httpx2.Response(200, content=FEED)
        if "t.me" in url:
            return httpx2.Response(200, content=CHANNEL.encode())
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
async def test_the_default_build_actually_fetches(live):
    """`opened()` can be emptied out — return the in-memory database without
    calling poll_once — and every other test in this suite stays green. The
    server would answer 0 items and SILENT for every source on every call."""
    out = await call(live, "wire_latest", hours=48, sources=["qbitai"])
    assert "GLM-5 released" in out, "an empty database is a silent lie"


@pytest.mark.anyio
async def test_the_tally_counts_the_sources_asked_for(live):
    """`sources_total=len(SOURCES)` instead of len(wanted) passes the whole
    suite, because every other call is unfiltered and the two are equal there.
    A model that asks for one language is then told 19 of 21 sources answered
    when the truth is 0 of 3 — the figure it uses to judge how much of the
    world it just saw."""
    out = await call(live, "wire_latest", hours=48, sources=["qbitai"])
    answering, total = map(int, re.search(r"\| (\d+)/(\d+) sources", out).groups())
    assert total == 1, f"one source was asked for; the header says {total}"
    assert answering == 1


@pytest.mark.anyio
async def test_a_source_that_answered_this_very_call_is_not_never_polled(live):
    """The branch that fires for most sources in most replies, covered by
    nothing: deleting it left the suite green. In live mode it is also the
    branch most likely to be wrong, because every source starts each call with
    no recorded state at all."""
    out = await call(live, "wire_latest", hours=48, sources=["qbitai"])
    assert "never polled" not in out


@pytest.mark.anyio
async def test_a_source_that_failed_says_so_and_is_not_counted_as_answering(live):
    out = await call(live, "wire_latest", hours=48, sources=["openai", "qbitai"])
    assert "openai=" in out and "DOWN" in out
    answering, total = map(int, re.search(r"\| (\d+)/(\d+) sources", out).groups())
    assert (answering, total) == (1, 2)


@pytest.mark.anyio
async def test_an_id_from_one_reply_resolves_in_the_next(live):
    """Live mode holds no file, so the process cache is the only thing that
    makes an id readable after the call that produced it. Nothing else covers
    it, and it is the whole recovery path wire_read's UNKNOWN line points at."""
    listing = await call(live, "wire_latest", hours=48, sources=["qbitai"])
    ids = re.findall(r"^(\w{12}) \d{2}:\d{2} ", listing, re.M)
    assert len(ids) == 2, f"the listing offered {ids}"

    body = await call(live, "wire_read", ids=ids)
    assert "UNKNOWN" not in body, f"{ids} came from the reply immediately before"
    assert f"{len(ids)} resolved" in body
    assert "Body of the story" in body, "the stored body travels with the id"


@pytest.mark.anyio
async def test_a_borrowed_headline_is_marked_in_live_mode_too(live):
    """The failure this mode had all to itself. wire_read serves cached rows
    here and rows from items_by_ids against the file, and `via` was selected by
    the second query only — so the mark came out on disk and not in the mode
    that ships.

    Without it a model reports "the Russian channel ai_newz published this",
    about an article that channel linked and never wrote.
    """
    await call(live, "wire_search", query="GLM", days=7, sources=["ai_newz"])
    body = await call(live, "wire_read", ids=[item_id("https://qbitai.com/glm5")])
    # The claim, not the sentence: an assertion on the exact prose is the
    # `assert "CUT" in out` disease with better manners.
    assert "!!" in body and "not the article's" in body



@pytest.mark.anyio
async def test_a_selector_that_matches_nothing_fetches_nothing(live, monkeypatch):
    """"no selector" and "a selector that matched nothing" both resolved to an
    empty list, and `or None` read them both as everything. Against a file that
    was free; live it spent 23 seconds and 22 requests to other people's servers
    on a typo, in a build whose description spends a paragraph teaching the
    model to ask before spending exactly that.

    The reply is unchanged — UNKNOWN SELECTOR already explains it — so this
    counts the fetches rather than reading the payload.
    """
    import cablegram.poll as poll_mod

    fetched = []
    real = poll_mod.fetch_all

    async def spy(requests, **kwargs):
        fetched.extend(i for i, _ in requests)
        return await real(requests, **kwargs)

    monkeypatch.setattr(poll_mod, "fetch_all", spy)

    out = await call(live, "wire_latest", hours=24, sources=["qbitia"])
    assert "qbitia" in out and "UNKNOWN SELECTOR" in out
    assert fetched == [], f"a typo fetched {len(fetched)} sources: {fetched}"


@pytest.mark.anyio
async def test_a_search_with_a_bad_selector_fetches_nothing_either(live, monkeypatch):
    """The same hole, and worse here before wire_search learned to name the
    typo: the sweep was spent and the empty result read as a real absence."""
    import cablegram.poll as poll_mod

    fetched = []
    real = poll_mod.fetch_all

    async def spy(requests, **kwargs):
        fetched.extend(i for i, _ in requests)
        return await real(requests, **kwargs)

    monkeypatch.setattr(poll_mod, "fetch_all", spy)

    out = await call(live, "wire_search", query="GLM", sources=["qbitia"])
    assert "UNKNOWN SELECTOR" in out
    assert fetched == []


@pytest.mark.anyio
async def test_since_widens_the_window_that_is_asked_for(live, monkeypatch):
    """The header printed the window the caller asked for and the poller was
    handed the default. Measured live against Hacker News, which takes the
    window in its query and serves up to a thousand rows:

        since=2026-08-01  ->  header 2026-08-01..2026-08-31 | 906 items
        hours=24          ->  header 2026-08-30..2026-08-31 | 906 items

    The same 906. A thirty-day header over one day of data, and nothing in the
    reply distinguishes the two. wire_search already derived this from `days`;
    wire_latest is the tool `since` belongs to.
    """
    import cablegram.server as server_mod

    asked = []
    real = server_mod.poll_once

    async def spy(db, sources, **kwargs):
        asked.append(kwargs.get("window_hours"))
        return await real(db, sources, **kwargs)

    monkeypatch.setattr(server_mod, "poll_once", spy)

    await call(live, "wire_latest", since="2026-08-01T00:00:00Z", sources=["qbitai"])
    assert asked, "the call has to have polled"
    assert asked[0] >= 24 * 25, (
        f"a window reaching back to 2026-08-01 asked the sources for "
        f"{asked[0]}h; the header will claim the whole span either way")


@pytest.mark.anyio
async def test_an_unresolvable_id_is_explained_by_something_that_can_happen(live):
    """Both causes the message named were impossible in this mode. There is no
    archive to prune from — live mode holds no file — and nothing in this
    project prunes anything in either mode: no retention window, and the only
    DELETE is a trigger. Nobody had reinstalled the server either.

    It is the one message a model has to act on alone, and it was describing
    machinery that does not exist while withholding the condition that does:
    the id is not in this process's cache. The recovery step also needs the same
    `sources`, not just the same window, because a live call only caches what it
    was asked to fetch.
    """
    out = await call(live, "wire_read", ids=["deadbeef0000"])
    assert "pruned" not in out and "reinstalled" not in out
    assert "this session" in out and "cache" in out
    assert "`sources`" in out, "re-running with a different selection will not help"


@pytest.mark.anyio
async def test_a_not_modified_reply_is_a_failure_not_a_quiet_source(live, monkeypatch):
    """A 304 to a request that sent no validator is reported, not swallowed.

    It carries no body and is only usable by a caller that already holds the
    items. Nothing here does. Treated as "alive, nothing new" — which is what it
    means when there is an archive behind it — the source would come back SILENT,
    saying it published nothing, and that is the lie the whole server is built
    to avoid. No validator is ever sent, so this cannot happen except as a
    protocol violation, and it is named as one.
    """
    real = httpx2.AsyncClient

    def handler(request):
        return httpx2.Response(304)

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", patched)
    out = await call(live, "wire_latest", hours=48, sources=["qbitai"])
    assert "SILENT qbitai" not in out, (
        "a 304 with nothing held behind it is not a source that published "
        "nothing")
    assert "qbitai=HTTP 304" in out, f"it has to be named as a failure:\n{out}"


@pytest.mark.anyio
async def test_a_call_bigger_than_the_cache_keeps_its_newest_dispatches(live, monkeypatch):
    """Rows arrive newest-first and eviction popped whatever went in first, so a
    single call larger than the cache evicted the top of its own reply.

    Measured with 5,000 rows: the first dispatch printed no longer resolved and
    the last one did — exactly inverted. Those are the ones a model reads first,
    and with nothing kept between runs this cache is the only thing that
    resolves an id at all.
    """
    import cablegram.server as server_mod

    monkeypatch.setattr(server_mod, "SEEN_LIMIT", 1)
    out = await call(live, "wire_latest", hours=48, sources=["qbitai"])
    ids = re.findall(r"^(\w{12}) \d{2}:\d{2} ", out, re.M)
    assert len(ids) >= 2

    body = await call(live, "wire_read", ids=[ids[0]])
    assert "UNKNOWN" not in body, (
        f"{ids[0]} is the first dispatch of the reply that just produced it")


@pytest.mark.anyio
async def test_a_window_older_than_the_year_1000_is_not_reported_as_silence(live):
    """`%Y` drops the leading zero, and these timestamps are compared as strings.

    So `hours=10000000` wrote the window start as `885-11-14T16:33:21Z`, and
    `published >= ?` compared "2" against "8" and excluded every row stored.
    Measured live against Hacker News, which held 982 items in that window:

        0 of 0 items | 1/1 sources
        SILENT hn  (answered, published nothing in this window)

    Every clause of that is false, the source is marked healthy, and nothing in
    the reply can be checked against anything else in it. It is the exact shape
    this whole server exists to not produce.
    """
    out = await call(live, "wire_latest", hours=10000000, sources=["qbitai"])
    assert "SILENT qbitai" not in out, (
        "the source answered and published two items inside this window")
    assert "GLM-5 released" in out


@pytest.mark.anyio
async def test_a_window_past_the_calendar_says_so_instead_of_failing_blank(live):
    """OverflowError is not a ToolError, and the SDK forwards the text of
    nothing else. So `hours=87600000` reached the model as "Error executing
    tool" with no body at all: the call failed, and nothing said why or what to
    try instead. For a window argument that is indistinguishable from the
    server being broken.

    The refusal names the widest window that does work, and that number has to
    be one the caller can actually use — a message recommending a value that
    also fails is worse than no message.
    """
    with pytest.raises(ToolError) as raised:
        await call(live, "wire_latest", hours=87600000, sources=["qbitai"])
    message = str(raised.value)
    assert "hours" in message and "calendar" in message

    widest = int(re.findall(r"hours=(\d+)", message)[-1])
    out = await call(live, "wire_latest", hours=widest, sources=["qbitai"])
    assert "GLM-5 released" in out, (
        f"the refusal recommends hours={widest}; it has to be a window that "
        f"answers")

