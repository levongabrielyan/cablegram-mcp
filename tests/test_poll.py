"""The poller: what turns a library into something that fills an archive.

Its whole job is to keep going. A source that fails, a feed that will not parse,
a batch that half-writes — none of them may stop the others, and every one of
them has to leave a trace, because a poll that quietly does nothing looks
exactly like a quiet day.
"""

import asyncio

import httpx2
import pytest

from cablegram.archive import connect
from cablegram.poll import poll_once
from cablegram.sources import by_id

FEED = b"""<rss version="2.0"><channel>
  <item><title>GLM-5 released</title><link>https://qbitai.com/glm5</link>
        <pubDate>Sat, 30 Aug 2026 06:40:00 +0000</pubDate>
        <description>Body</description></item>
  <item><title>Second</title><link>https://qbitai.com/second</link>
        <pubDate>Sat, 30 Aug 2026 07:00:00 +0000</pubDate></item>
</channel></rss>"""


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "a.db")
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


def test_a_poll_fills_the_archive(db, network):
    network(lambda request: httpx2.Response(200, content=FEED))
    reports = asyncio.run(poll_once(db, [by_id("qbitai")]))

    assert reports[0].new == 2
    assert db.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 2


def test_polling_twice_changes_nothing(db, network):
    network(lambda request: httpx2.Response(200, content=FEED))
    asyncio.run(poll_once(db, [by_id("qbitai")]))
    reports = asyncio.run(poll_once(db, [by_id("qbitai")]))

    assert (reports[0].new, reports[0].seen) == (0, 2)


def test_a_dead_source_does_not_stop_the_others(db, network):
    """The whole reason this runs unattended."""
    def handler(request):
        if "qbitai" in str(request.url):
            raise httpx2.ConnectError("down")
        return httpx2.Response(200, content=FEED)

    network(handler)
    reports = asyncio.run(poll_once(db, [by_id("qbitai"), by_id("n8n")]))

    assert {r.source for r in reports} == {"qbitai", "n8n"}
    assert db.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 2


def test_a_dead_source_leaves_its_reason_behind(db, network):
    """Silently skipping it would make a broken feed look like a quiet one."""
    network(lambda request: (_ for _ in ()).throw(httpx2.ConnectError("down")))
    asyncio.run(poll_once(db, [by_id("qbitai")]))

    state = db.execute("SELECT * FROM source_state WHERE source='qbitai'").fetchone()
    assert state["last_error"] and state["last_ok"] is None


def test_an_unparseable_feed_is_recorded_not_swallowed(db, network):
    """A source answering with broken XML is not a source with no news."""
    network(lambda request: httpx2.Response(200, content=b"<rss><channel><item>"))
    reports = asyncio.run(poll_once(db, [by_id("qbitai")]))

    assert reports[0].failed == 1
    state = db.execute("SELECT * FROM source_state WHERE source='qbitai'").fetchone()
    assert state["last_ok"], "the download did work"
    assert state["wrote_failed"] == 1, "and the parse did not — both must be visible"


def test_a_304_is_not_treated_as_an_empty_feed(db, network):
    """Nothing new is not nothing there. Writing a zero over the last real
    result would erase what the source actually carries."""
    network(lambda request: httpx2.Response(200, content=FEED))
    asyncio.run(poll_once(db, [by_id("qbitai")]))

    network(lambda request: httpx2.Response(304))
    reports = asyncio.run(poll_once(db, [by_id("qbitai")]))

    assert [r.state for r in reports] == ["unchanged"]
    assert db.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 2
    state = db.execute("SELECT * FROM source_state WHERE source='qbitai'").fetchone()
    assert state["last_ok"] and state["wrote_new"] == 2


def test_a_source_with_no_adapter_is_never_attempted(db, network):
    """All nineteen have an adapter today, so this uses an invented kind.

    It matters for the next source added: handing an unknown format to the RSS
    parser would file it as a parse failure rather than as "not built yet", and
    bury the sources that really did fail among the noise.
    """
    from dataclasses import replace

    network(lambda request: httpx2.Response(200, content=FEED))
    future = replace(by_id("qbitai"), id="futuresource", kind="reddit")
    reports = asyncio.run(poll_once(db, [future, by_id("qbitai")]))

    assert {r.source for r in reports} == {"qbitai"}


def test_a_source_that_failed_still_appears_in_the_report(network, db):
    """Nine reports for eleven sources makes two vanish with nothing to say so."""
    def handler(request):
        if "qbitai" in str(request.url):
            raise httpx2.ConnectError("down")
        return httpx2.Response(200, content=FEED)

    network(handler)
    reports = asyncio.run(poll_once(db, [by_id("qbitai"), by_id("n8n")]))

    assert [(r.source, r.state) for r in reports] == [("qbitai", "fetch-failed"),
                                                      ("n8n", "ok")]


def test_a_feed_that_parses_to_nothing_is_not_a_quiet_day(db, network):
    """A valid document with no entries — the shape a feed takes when it changes
    format — was recorded as state="ok", new=0. Indistinguishable from a source
    with no news, which is the exact silent failure this project is built
    around, in the case most likely to actually happen."""
    network(lambda request: httpx2.Response(
        200, content=b'<rss version="2.0"><channel><title>t</title></channel></rss>'))
    reports = asyncio.run(poll_once(db, [by_id("qbitai")]))

    assert reports[0].state == "parsed-empty"
    state = db.execute("SELECT * FROM source_state WHERE source='qbitai'").fetchone()
    assert state["last_ok"], "the download worked"
    assert state["wrote_failed"] == 1, "and it yielded nothing, which must be visible"


def test_telegram_channels_are_spaced_out(db, network, monkeypatch):
    """t.me resets the connection on the sixth request in a row — measured, not
    assumed: channels 3 to 6 failed with ECONNRESET while 1 and 2 came back.

    It is not a 429. The socket closes, so a handler that reports what it sees
    would file four healthy channels as dead. They go in their own pass, spaced,
    which is also why they cannot share the global deadline with the rest.
    """
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("cablegram.poll.asyncio.sleep", fake_sleep)
    network(lambda request: httpx2.Response(200, content=b"<html></html>"))

    channels = [by_id(c) for c in ("ai_newz", "denissexy", "data_secrets")]
    asyncio.run(poll_once(db, channels))

    assert len(waits) >= len(channels) - 1
    assert all(w >= 2 for w in waits)


def test_the_other_sources_are_not_slowed_by_telegram(db, network, monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("cablegram.poll.asyncio.sleep", fake_sleep)
    network(lambda request: httpx2.Response(200, content=FEED))
    asyncio.run(poll_once(db, [by_id("qbitai"), by_id("n8n")]))

    assert waits == []
