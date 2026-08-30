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
    #
    # It succeeded first and fails now, which is the shape a real outage takes.
    # The fixture used to hold only a source that had never once worked — the
    # one case the old DOWN logic got right — so a source failing today after
    # working yesterday appeared in no test at all.
    record_attempt(db, Fetched("deepmind", url=by_id("deepmind").url, ok=True,
                               body=b"x", status=200, fetched_at="2026-08-20T09:00:00Z"))
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
async def test_every_source_has_an_adapter_now(server):
    """PENDING listed the sources with no adapter, separately from DOWN, so that
    eight not-yet-built ones could not bury the one actually failing. All
    nineteen have an adapter today, so nothing should be listed as pending —
    and the machinery stays for the next source added."""
    out = await call(server, "wire_latest", hours=24)
    assert "PENDING" not in out


@pytest.mark.anyio
async def test_latest_counts_the_cross_source_repeat(server):
    out = await call(server, "wire_latest", hours=24)
    assert f"{item_id('https://qbitai.example/glm5')} x2" in out


@pytest.mark.anyio
async def test_read_reports_the_element_not_a_verdict(server):
    """The full/teaser judgement was removed from the parser and came back here.
    What is printed now is the element and the size — facts — because whether a
    feed ships whole articles is a property of the source, not of a tag name."""
    out = await call(server, "wire_read", ids=[item_id("https://qbitai.example/glm5")])
    assert "正文内容" in out
    assert "NOT the full article" not in out
    assert "body=description" in out


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


# ── fifth review ────────────────────────────────────────────────────────────

@pytest.mark.anyio
@pytest.mark.parametrize("since", ["yesterday", "30/08/2026", "2026/08/30", "next week"])
async def test_a_malformed_since_is_an_error_not_an_empty_day(server, since):
    """`since` went straight into a string comparison, so '2026-8-30' — what a
    model writes half the time — is greater than every real date and matched
    nothing. The reply was perfectly formed: 0 items, 11/19 sources, no DOWN.
    The model reports that nothing happened today, and it is the parameter the
    design added on day one for "since last time"."""
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as exc:
        await call(server, "wire_latest", since=since)
    assert "since" in str(exc.value).lower() and "ISO-8601" in str(exc.value)
    assert "hours" in str(exc.value), "the error has to say what to do instead"


@pytest.mark.anyio
@pytest.mark.parametrize("since", ["2020-01-01T00:00:00Z", "2020-01-01", "2020-1-1",
                                   "2020-01-01 00:00:00", "2020-01-01T00:00"])
async def test_a_reasonable_since_is_normalised_rather_than_refused(server, since):
    """"2026-8-30" without the leading zero is what a model writes half the time.
    Refusing it would be as unhelpful as the silent empty result it used to give:
    it is unambiguous, so it is accepted and canonicalised."""
    out = await call(server, "wire_latest", since=since)
    assert "智谱发布GLM-5" in out


@pytest.mark.anyio
async def test_a_source_that_worked_and_now_fails_is_reported_down(server, tmp_path):
    """DOWN only looked at sources that had never once succeeded, so one failing
    for three days counted as healthy — while wire_sources listed it as FAIL.
    The two tools contradicted each other, and the one called every morning is
    the one that lied."""
    from cablegram.archive import connect
    from cablegram.fetch import Fetched
    from cablegram.sources import by_id
    from cablegram.store import record_attempt

    db = connect(tmp_path / "a.db")
    url = by_id("qbitai").url
    record_attempt(db, Fetched("qbitai", url=url, ok=True, body=b"x", status=200,
                               fetched_at="2026-08-27T09:00:00Z"))
    record_attempt(db, Fetched("qbitai", url=url, ok=False, error="HTTP 403",
                               fetched_at="2026-08-30T09:00:00Z"))
    db.close()

    out = await call(build(lambda: connect(tmp_path / "a.db")), "wire_latest", hours=24)
    assert "qbitai=HTTP 403" in out


@pytest.mark.anyio
async def test_search_says_how_far_back_the_archive_really_goes(server):
    """It printed the file's creation date, so an archive holding ten years of
    OpenAI posts announced itself as starting today — and a model asked "since
    when has X been discussed" refuses to answer."""
    out = await call(server, "wire_search", query="GLM")
    assert "oldest" in out, "the header states how far back the archive reaches"


@pytest.mark.anyio
async def test_an_unknown_source_selector_is_named(server):
    """wire_latest(sources=["deepseek"]) answered 0 items | 0/0 sources: a
    plausible empty reply to a typo."""
    out = await call(server, "wire_latest", sources=["deepseek"])
    assert "deepseek" in out


@pytest.mark.anyio
async def test_the_recovery_hint_names_a_parameter_that_exists(server):
    """The one route to autonomous recovery pointed at urls=[...], which
    wire_read does not accept."""
    out = await call(server, "wire_read", ids=["000000000000"])
    assert "urls=[" not in out
