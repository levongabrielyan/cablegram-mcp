"""The poller: what turns a library into something that fills an archive.

Its whole job is to keep going. A source that fails, a feed that will not parse,
a batch that half-writes — none of them may stop the others, and every one of
them has to leave a trace, because a poll that quietly does nothing looks
exactly like a quiet day.
"""

import asyncio
import json

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


def test_a_reference_is_counted_in_the_report(db, network):
    """Referenced articles were the only thing writing to the archive without
    appearing in any report: the CLI said 2,641 archived while the file held
    2,700. A count that does not match the archive is worse than no count."""
    page = ('<div class="tgme_widget_message" data-post="ai_newz/1">'
            '<a class="tgme_widget_message_date">'
            '<time datetime="2026-08-30T10:00:00+00:00">10:00</time></a>'
            '<div class="tgme_widget_message_text js-message_text">'
            'Новость <a href="https://openai.com/index/x">тут</a>'
            '</div></div>').encode()
    network(lambda request: httpx2.Response(200, content=page))

    reports = asyncio.run(poll_once(db, [by_id("ai_newz")]))
    assert reports[0].new == 1
    assert reports[0].referenced == 1
    assert db.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 2


def test_a_source_at_its_ceiling_says_so(db, network):
    """cls returns at most 100 rows and Hacker News 1,000. "100 new" does not
    distinguish "there were 100" from "there were more and we cannot reach
    them" — and for cls, what is beyond the ceiling is gone for good."""
    import json

    items = [{"article_id": i, "article_time": 1788078787 - i, "article_title": f"T{i}"}
             for i in range(100)]
    network(lambda request: httpx2.Response(
        200, content=json.dumps({"errno": 0, "data": items}).encode()))

    reports = asyncio.run(poll_once(db, [by_id("cls")]))
    assert reports[0].at_ceiling is True


# ── what happens after the download has to reach the tools ───────────────────

def test_a_source_that_answered_and_then_failed_to_parse_is_reported_down(db, network):
    """cls.cn answers a rejected signature with HTTP 200 and errno in the
    envelope — its own module documents exactly this — so the download succeeds
    and everything after it fails.

    `unparseable`, `parsed-empty` and `failed` were written to source_state and
    read by no renderer, and the exception message was caught and discarded. The
    result was `wire_sources: cls … OK` beside `wire_latest: 1/1 sources | 0
    items`, which a model reads as a quiet source. This is the failure the whole
    project is built to prevent, in the one source most likely to break.
    """
    from cablegram.store import source_health

    network(lambda request: httpx2.Response(200, content=b"not xml at all"))
    reports = asyncio.run(poll_once(db, [by_id("qbitai")]))

    assert reports[0].state == "unparseable"
    state = source_health(db)["qbitai"]
    assert state["last_error"], "the reason has to survive the except that caught it"
    assert state["last_try"] >= state["last_ok"] or not state["last_ok"], (
        "a failure recorded in the same pass as its download must still register")


def test_an_unforeseen_parser_error_costs_one_source_not_the_pass(db, network):
    """Only ValueError, ParseError and JSONDecodeError were caught. A float
    `article_time` raises OverflowError, a null one TypeError, a hits object
    that is not a list AttributeError — and any of them tore the pass down:
    measured, nineteen pollable sources left eight with recorded state and
    eleven with no trace at all, which is worse than the bug it hid.
    """
    from cablegram.store import source_health

    def handler(request):
        if "qbitai" in str(request.url):
            return httpx2.Response(200, content=b'{"not": "a feed"}')
        return httpx2.Response(200, content=FEED)

    network(handler)
    reports = asyncio.run(poll_once(db, [by_id("qbitai"), by_id("habr")]))

    assert [r.source for r in reports] == ["qbitai", "habr"]
    assert reports[1].new > 0, "the source after the broken one still archived"
    assert source_health(db)["qbitai"]["last_error"]


def test_a_pass_deadline_still_reports_every_source(db, network, monkeypatch):
    """One pass has to finish inside a tool call, and the unbounded worst case
    is 130 seconds: fifteen per Telegram channel, one at a time, plus the gaps.

    Running out of time may not turn into a shorter list. A channel that was
    never asked is not a channel with nothing to say, so it comes back as a
    failure with the reason on it.
    """
    import cablegram.poll as poll_mod

    monkeypatch.setattr(poll_mod, "TELEGRAM_GAP", 0.01)
    network(lambda request: httpx2.Response(200, content=FEED))

    channels = [by_id(c) for c in ("ai_newz", "denissexy", "data_secrets")]
    reports = asyncio.run(poll_once(db, [by_id("qbitai")] + channels, deadline=0.0))

    assert [r.source for r in reports] == ["qbitai", "ai_newz", "denissexy",
                                           "data_secrets"]
    skipped = [r for r in reports if r.state == "fetch-failed"]
    assert skipped, "a source that never got its request has to say so"


# ── seventh review ──────────────────────────────────────────────────────────

def test_no_batch_is_handed_more_time_than_its_own_bound(db, network, monkeypatch):
    """`left()` returned the whole remaining pass budget rather than the smaller
    of that and the step's own, so a 45s pass handed the main batch 45 seconds
    when TOTAL_DEADLINE is 25 — measured against the live catalogue:
    `fetch_all(15 sources) deadline=44.99993`.

    That bound is not a formality. httpx restarts its read timeout on every
    chunk, so a source that drips one byte every seven seconds is stopped by
    nothing else in the stack; the batch deadline is the only brake. And the
    time the batch takes above its share comes out of Telegram's, which is what
    turns six healthy channels into `skipped: pass deadline reached`.

    Reads the bound off the module rather than naming 25, so it holds if the
    number changes.
    """
    import cablegram.poll as poll_mod
    from cablegram.fetch import TOTAL_DEADLINE

    monkeypatch.setattr(poll_mod, "TELEGRAM_GAP", 0.01)
    network(lambda request: httpx2.Response(200, content=FEED))

    handed = []
    real = poll_mod.fetch_all

    async def spy(requests, **kwargs):
        handed.append((len(requests), kwargs.get("deadline")))
        return await real(requests, **kwargs)

    monkeypatch.setattr(poll_mod, "fetch_all", spy)
    asyncio.run(poll_once(db, [by_id("qbitai"), by_id("habr"), by_id("ai_newz")],
                          deadline=45.0))

    assert handed, "the pass has to have fetched something"
    batch, channels = handed[0], handed[1:]
    assert batch[1] <= TOTAL_DEADLINE, (
        f"the main batch of {batch[0]} sources was given {batch[1]}s against a "
        f"TOTAL_DEADLINE of {TOTAL_DEADLINE}s")
    assert all(d <= 15.0 for _, d in channels), (
        f"a Telegram channel was given more than its 15s: {channels}")


def test_an_entry_that_would_not_store_does_not_take_its_source_down(db, network):
    """A pass that archives most of a feed is not a pass that failed.

    record_attempt(ok=False) writes last_error with the same fetched_at as the
    success beside it, so last_try >= last_ok holds and the source is declared
    DOWN. Measured with a three-entry feed, one carrying a URL normalise()
    refuses:

        | 2 of 2 items | 0/1 sources
        DOWN  qbitai=1 of 3 entries could not be archived
        ## qbitai zh community 2/2

    Zero coverage announced above 66% of it, and `answering` is the figure
    wire_latest's own description tells the model to report as what it did not
    read. The count belongs where wire_sources already prints it: beside OK, as
    entries unarchived.
    """
    from cablegram.store import source_health

    feed = b"""<rss version="2.0"><channel>
      <item><title>Good one</title><link>https://qbitai.com/one</link>
            <pubDate>Mon, 31 Aug 2026 10:00:00 +0000</pubDate></item>
      <item><title>Broken</title><link>http://[::1/x</link>
            <pubDate>Mon, 31 Aug 2026 10:01:00 +0000</pubDate></item>
      <item><title>Good two</title><link>https://qbitai.com/two</link>
            <pubDate>Mon, 31 Aug 2026 10:02:00 +0000</pubDate></item>
    </channel></rss>"""

    network(lambda request: httpx2.Response(200, content=feed))
    report = asyncio.run(poll_once(db, [by_id("qbitai")]))[0]

    assert (report.new, report.failed) == (2, 1)
    state = source_health(db)["qbitai"]
    assert state["wrote_failed"] == 1, "the count still has to be kept"
    assert not state["last_error"], (
        f"a pass that archived {report.new} of 3 is recorded as failed "
        f"({state['last_error']!r}), which takes the source out of the coverage "
        f"tally above its own items")


def test_a_ceiling_is_measured_on_what_arrived_not_on_what_survived(db, network):
    """AT CEILING means "it returned all it could, so there may be more", and it
    was computed from the entries rather than from the rows.

    One dropped row switched it off. cls.cn returning its full hundred with one
    article missing an `article_time` reported at_ceiling=False — in the source
    that cannot page backwards, where that marker is the only warning that
    something is gone for good.
    """
    from cablegram.cls import MAX_ROWS as CLS_MAX

    full = {"errno": 0, "data": [
        {"article_id": i, "article_title": f"标题 {i}", "ctime": 1788000000 + i,
         "article_brief": ""} for i in range(CLS_MAX)]}
    # One row the parser will drop, exactly as a real payload eventually does.
    full["data"][3].pop("ctime")

    network(lambda request: httpx2.Response(200, content=json.dumps(full).encode()))
    report = asyncio.run(poll_once(db, [by_id("cls")]))[0]

    assert report.new == CLS_MAX - 1, "one row is unarchivable, as intended"
    assert report.at_ceiling, (
        f"the endpoint returned its full {CLS_MAX} and one row did not parse, "
        f"so the pass is still at the ceiling — cls cannot page backwards and "
        f"this marker is the only warning that anything past it is lost")


def test_the_hub_declares_the_ceiling_it_is_always_at(db, network):
    """models_url asks for exactly MAX_ROWS, so this source is truncated on
    every poll — and it was the only one whose ceiling was 10**9, so it was the
    only one that could never say so."""
    from cablegram.hub import MAX_ROWS as HUB_MAX

    rows = [{"id": f"org{i}/model", "likes": i, "downloads": i,
             "trendingScore": HUB_MAX - i, "createdAt": "2026-08-30T10:00:00.000Z"}
            for i in range(HUB_MAX)]
    network(lambda request: httpx2.Response(200, content=json.dumps(rows).encode()))
    report = asyncio.run(poll_once(db, [by_id("hub")]))[0]

    assert report.at_ceiling, f"{HUB_MAX} rows of a {HUB_MAX}-row page is the ceiling"
