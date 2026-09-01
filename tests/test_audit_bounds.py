"""Four mutations that are equivalent only because of what is true today.

Every kind in the catalogue has an adapter, and in live mode the database holds
nothing but what the call fetched. Both are true, neither is guaranteed, and
while they hold, four expressions can be deleted with the suite green. These
tests create the condition instead of waiting for it.
"""

import re
from datetime import datetime, timezone

import httpx2
import pytest
from dates import rss_date

from cablegram.rss import Entry
from cablegram.schema import connect
from cablegram.server import build
from cablegram.sources import by_id
from cablegram.store import record_attempt, store_entries
from cablegram.fetch import Fetched

NOW = datetime.now(timezone.utc)
ISO = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(server, name, **args):
    result = await server.call_tool(name, args)
    return result.content[0].text if hasattr(result, "content") else str(result)


# ── the process cache is the only thing that resolves an id ─────────────────

def feed(n):
    items = b"".join(
        f'<item><title>story {i}</title>'
        f'<link>https://qbitai.com/{i}</link>'
        f'<pubDate>{rss_date(6)}</pubDate></item>'.encode()
        for i in range(n))
    return b'<rss version="2.0"><channel>' + items + b"</channel></rss>"


@pytest.fixture
def live(monkeypatch):
    import cablegram.poll as poll_mod

    monkeypatch.setattr(poll_mod, "TELEGRAM_GAP", 0.01)
    real = httpx2.AsyncClient
    page = feed(5000)

    def handler(request):
        if "qbitai" in str(request.url):
            return httpx2.Response(200, content=page)
        return httpx2.Response(503)

    def patched(*args, **kwargs):
        kwargs.setdefault("transport", httpx2.MockTransport(handler))
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", patched)
    return build()


@pytest.mark.anyio
async def test_the_id_cache_is_bounded_and_evicts_the_oldest_first(live):
    """Nothing is kept between runs, so this cache is the whole of what makes an
    id from one reply readable in the next — which turns its eviction from a
    memory question into a correctness one. Two halves, and only together do
    they say anything:

    * The newest ids resolve. An eviction that drops what the model was just
      handed breaks the one recovery path wire_read's UNKNOWN line points at.
    * The oldest do not. Unbounded, a server left running holds the rows of
      every listing it ever answered, and the reply that says an id is gone is
      the only thing that tells a model to ask for it again.
    """
    listing = await call(live, "wire_latest", hours=48, sources=["qbitai"],
                         limit_per_source=5000, max_tokens=10 ** 7)
    ids = re.findall(r"^(\w{12}) \d{2}:\d{2} ", listing, re.M)
    assert len(ids) > 4000, f"the listing has to overrun the bound; it offered {len(ids)}"

    newest = await call(live, "wire_read", ids=ids[:1])
    assert "1 resolved" in newest, (
        "the first id of the reply immediately before does not resolve")

    oldest = await call(live, "wire_read", ids=ids[-1:])
    assert "1 unknown" in oldest, (
        f"{len(ids)} rows went into a cache that is supposed to be bounded and "
        f"the oldest is still held; nothing evicts and the process grows for "
        f"the life of the server")


# FAILS ON THE CURRENT BUILD, and the failure is the point. `latest_items`
# orders newest first, `remember` inserts in that order, and the eviction pops
# the first key inserted — so a listing that overruns the bound on its own drops
# the rows at the TOP of the reply, which are the newest dispatches and the ones
# a model reads first. Across calls the order is right; within one oversized
# call it is exactly inverted. One line fixes it, in server.py:196:
#
#     -        for row in rows:
#     +        for row in reversed(rows):
#
# which keeps the cross-call order and makes the newest of each call the last to
# go.


# ── a source with no adapter is neither down nor silent ─────────────────────

@pytest.fixture
def no_adapter(monkeypatch):
    """A catalogue where one kind has no adapter, which is the state this build
    left behind and the next added source restores."""
    import cablegram.server as server_mod
    import cablegram.poll as poll_mod

    narrowed = tuple(k for k in poll_mod.POLLABLE if k != "nextjs")
    monkeypatch.setattr(server_mod, "POLLABLE", narrowed)
    monkeypatch.setattr(poll_mod, "POLLABLE", narrowed)
    monkeypatch.setattr(poll_mod, "TELEGRAM_GAP", 0.01)

    real = httpx2.AsyncClient

    def patched(*args, **kwargs):
        kwargs.setdefault("transport", httpx2.MockTransport(
            lambda request: httpx2.Response(503)))
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx2, "AsyncClient", patched)
    return build()


@pytest.mark.anyio
async def test_a_source_with_no_adapter_is_named_as_pending(no_adapter):
    """"Never fetched because nothing fetches it yet" and "fetched and failed"
    are different facts, and the first one has to appear at all: a source
    missing from the reply cannot be known to exist, and its absence reads as
    nothing having happened there.

    The expression that computes this set has been empty since every kind got an
    adapter, so deleting it changes nothing today and everything on the day a
    source is added."""
    out = await call(no_adapter, "wire_latest", hours=24,
                     sources=["anthropic", "qbitai"])
    pending = re.search(r"^PENDING (.*?)  \(", out, re.M)
    assert pending, f"anthropic has no adapter in this build and nothing says so:\n{out}"
    assert "anthropic" in pending.group(1).split()


@pytest.mark.anyio
async def test_a_source_with_no_adapter_is_not_also_called_silent(no_adapter):
    """SILENT says a source answered and published nothing. A source nothing
    fetches did not answer, so the two claims cannot both be about it — and the
    payload would be making them four lines apart, with only one of them true.
    """
    out = await call(no_adapter, "wire_latest", hours=24,
                     sources=["anthropic", "qbitai"])
    pending = re.search(r"^PENDING (.*?)  \(", out, re.M)
    silent = re.search(r"^SILENT (.*?)  \(", out, re.M)
    named_pending = set(pending.group(1).split()) if pending else set()
    named_silent = set(silent.group(1).split()) if silent else set()
    both = named_pending & named_silent
    assert not both, (
        f"{sorted(both)} are named as never fetched and as having answered and "
        f"published nothing, in the same header")


# ── the listing filters by source, not only by what was fetched ─────────────

@pytest.mark.anyio
async def test_a_listing_returns_only_the_sources_that_were_asked_for():
    """In live mode the database holds nothing but what the call fetched, so the
    filter on the query is doing no work and can be deleted unnoticed. It is the
    only thing standing between `sources=["qbitai"]` and a reply full of hn if
    anything ever puts more than one call's worth of rows in front of it — which
    is exactly what the tests' own seam does.
    """
    def rows():
        db = connect()
        store_entries(db, by_id("qbitai"),
                      [Entry("智谱发布GLM-5", "https://qbitai.example/glm5", NOW,
                             None, None)], fetched_at=ISO)
        store_entries(db, by_id("hn"),
                      [Entry("Zhipu releases GLM-5", "https://hn.example/glm5", NOW,
                             None, None)], fetched_at=ISO)
        for sid in ("qbitai", "hn"):
            record_attempt(db, Fetched(sid, url=by_id(sid).url, ok=True, body=b"x",
                                       status=200, fetched_at=ISO))
        return db

    out = await call(build(rows), "wire_latest", hours=24, sources=["qbitai"])
    printed = set(re.findall(r"^## (\S+) ", out, re.M))
    assert printed == {"qbitai"}, (
        f"one source was asked for and the reply carries blocks for {sorted(printed)}")
