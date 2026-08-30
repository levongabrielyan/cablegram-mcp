"""The server is a translation layer, so these tests check the translation.

They call the tool functions the server registered, against a real archive in a
temp directory. What matters is that the contract the descriptions promise is
actually kept: a dead source appears, a window means what it says, an unknown id
comes back named.
"""

from datetime import datetime, timedelta, timezone

import pytest

from cablegram.archive import connect
from cablegram.rss import Entry
from cablegram.server import build
from cablegram.sources import by_id
from cablegram.store import record_attempt, store_entries
from cablegram.fetch import Fetched
from cablegram.urls import item_id

NOW = datetime.now(timezone.utc)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def server(tmp_path):
    path = tmp_path / "a.db"
    db = connect(path)
    store_entries(db, by_id("qbitai"),
                  [Entry("智谱发布GLM-5", "https://qbitai.example/glm5", NOW, "正文内容", "description")],
                  fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    store_entries(db, by_id("hn"),
                  [Entry("Zhipu releases GLM-5", "https://qbitai.example/glm5", NOW, None, None)],
                  fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    record_attempt(db, Fetched("qbitai", url=by_id("qbitai").url, ok=True, body=b"x",
                               status=200, fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")))
    # deepmind, not cls: cls has no adapter in this build, so it is PENDING
    # rather than DOWN and could never record a fetch error in the first place.
    record_attempt(db, Fetched("deepmind", url=by_id("deepmind").url, ok=False,
                               error="timeout8s",
                               fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")))
    db.close()
    # A factory, not a connection: the SDK runs sync tools in a worker thread and
    # SQLite refuses a connection created in another one.
    yield build(lambda: connect(path))


async def call(server, name, **args):
    result = await server.call_tool(name, args)
    return result.content[0].text if hasattr(result, "content") else str(result)


@pytest.mark.anyio
async def test_latest_returns_what_was_archived(server):
    out = await call(server, "wire_latest", hours=24)
    assert "智谱发布GLM-5" in out
    assert "Zhipu releases GLM-5" in out, "each source shows its own headline"


@pytest.mark.anyio
async def test_latest_names_the_failing_source(server):
    out = await call(server, "wire_latest", hours=24)
    assert "deepmind=timeout8s" in out


@pytest.mark.anyio
async def test_a_source_with_no_adapter_is_not_called_broken(server):
    """Eight PENDING lines under DOWN would bury the one source that is
    actually failing."""
    out = await call(server, "wire_latest", hours=24)
    assert "PENDING" in out and "cls" in out
    assert "cls" not in out.split("PENDING")[0]


@pytest.mark.anyio
async def test_latest_counts_the_cross_source_repeat(server):
    out = await call(server, "wire_latest", hours=24)
    assert f"{item_id('https://qbitai.example/glm5')} x2" in out


@pytest.mark.anyio
async def test_read_warns_that_a_teaser_is_not_the_article(server):
    out = await call(server, "wire_read", ids=[item_id("https://qbitai.example/glm5")])
    assert "正文内容" in out
    assert "NOT the full article" in out


@pytest.mark.anyio
async def test_read_names_an_unknown_id(server):
    out = await call(server, "wire_read", ids=["ffffffffffff"])
    assert "ffffffffffff" in out and "wire_latest" in out


@pytest.mark.anyio
async def test_search_finds_the_english_headline_of_a_chinese_story(server):
    out = await call(server, "wire_search", query="Zhipu")
    assert "Zhipu releases GLM-5" in out


@pytest.mark.anyio
async def test_search_says_zero_hits_is_not_silence(server):
    out = await call(server, "wire_search", query="nothingmatchesthis")
    assert "0 hits" in out and "does NOT mean" in out


@pytest.mark.anyio
async def test_sources_lists_all_nineteen(server):
    out = await call(server, "wire_sources")
    from cablegram.sources import SOURCES
    for source in SOURCES:
        assert source.id in out


@pytest.mark.anyio
async def test_all_four_tools_are_registered(server):
    names = {t.name for t in await server.list_tools()}
    assert names == {"wire_latest", "wire_read", "wire_search", "wire_sources"}
