"""The third round: the fetch layer, the feed parser and the server's wiring.

Two of these replace tests that pass for a reason other than the one their name
gives. `test_no_feed_expands_far_beyond_its_own_size` is caught by expat's own
amplification limit on expat 2.8, so the arithmetic that function was rewritten
a fourth time to add can be deleted with the suite green — on the builds where
expat does not have that limit, which is the case the code exists for, nothing
would have said so. `test_content_encoded_beats_description` reads an item that
carries no description, so the priority it is named after is never exercised.
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx2
import pytest
from dates import rss_date

from cablegram.fetch import Fetched, fetch_all, fetch_one
from cablegram.rss import MAX_EXPANSION, parse_feed
from cablegram.server import build
from cablegram.sources import by_id


@pytest.fixture
def anyio_backend():
    return "asyncio"


def run(coro):
    return asyncio.run(coro)


# ── the fetch layer ─────────────────────────────────────────────────────────

def fetch(handler, targets):
    async def go():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as c:
            import cablegram.fetch as fetch_mod

            real = httpx2.AsyncClient

            def patched(*a, **kw):
                kw.setdefault("transport", httpx2.MockTransport(handler))
                return real(*a, **kw)

            fetch_mod.httpx2.AsyncClient = patched
            try:
                return await fetch_all(targets)
            finally:
                fetch_mod.httpx2.AsyncClient = real

    return run(go())


def test_a_target_whose_task_raised_still_comes_back_as_a_result(monkeypatch):
    """One result per target, in the order given, whatever happened to it.

    A source dropped from the list cannot be reported DOWN — it is simply not
    there, and everything above this reads the shorter list as a shorter day.
    Every result is matched to its target by position, so a missing one does
    not just lose that source: it shifts every source after it onto somebody
    else's outcome.
    """
    import cablegram.fetch as fetch_mod

    async def explode(client, source_id, url, **kwargs):
        if source_id == "b":
            raise RuntimeError("boom")
        return Fetched(source_id, ok=True, url=url, body=b"x", status=200)

    monkeypatch.setattr(fetch_mod, "fetch_one", explode)
    results = run(fetch_all([("a", "https://a/"), ("b", "https://b/"),
                             ("c", "https://c/")]))

    assert [r.source_id for r in results] == ["a", "b", "c"]
    assert results[1].ok is False and results[1].error, (
        "the source that raised has to say why, not vanish")
    assert results[2].ok is True, "and the ones after it keep their own outcome"


def test_a_source_that_redirects_is_followed_rather_than_reported_dead():
    """Feeds move: a bare http URL, a host that adds www, a blog behind a CDN
    that 301s to its canonical form. Not following the redirect reports every
    one of those as a source that stopped answering, which is the one reading
    this project spends its whole output preventing."""
    def handler(request):
        if str(request.url).endswith("/moved"):
            return httpx2.Response(301, headers={"location": "https://e.com/feed"})
        return httpx2.Response(200, content=b"<rss/>")

    results = fetch(handler, [("a", "https://e.com/moved")])
    assert results[0].ok, f"a 301 came back as {results[0].error!r}"


# ── the feed parser ─────────────────────────────────────────────────────────

def test_a_bomb_expat_permits_is_still_refused():
    """The flat bomb the four rewrites of this guard exist for is caught, on
    this machine, by expat's own amplification limit — so the arithmetic added
    in the fourth rewrite can be deleted and every feed test stays green.

    expat 2.8 has that limit; what Debian 13 ships does not, which is the build
    the guard was written for and the one no test can distinguish. This is a
    document expat parses without complaint — one megabyte expanding to twenty,
    an amplification of twenty against a limit of a hundred — so only the
    declaration-size arithmetic can refuse it.
    """
    size, uses = 1_000_000, 20
    assert size * uses > MAX_EXPANSION
    bomb = (b'<?xml version="1.0"?>\n<!DOCTYPE rss [<!ENTITY big "' + b"a" * size +
            b'">]>\n<rss version="2.0"><channel><item><title>' + b"&big;" * uses +
            b'</title><link>https://e.com/a</link></item></channel></rss>')

    import xml.parsers.expat as expat

    parser = expat.ParserCreate()
    parser.Parse(bomb, True)  # expat itself has no objection

    with pytest.raises(ValueError, match="expan"):
        parse_feed(bomb)


BOTH_BODIES = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>Second story</title>
    <link>https://e.com/second/</link>
    <pubDate>Fri, 29 Aug 2026 22:03:11 GMT</pubDate>
    <description>Two sentences of teaser.</description>
    <content:encoded>The whole article, several thousand characters of it.</content:encoded>
  </item></channel>
</rss>"""


def test_the_fuller_body_wins_when_an_item_carries_both():
    """A feed carrying both usually puts more in `content:encoded`, and the
    order in `_BODY_ELEMENTS` is the only thing that decides which one the model
    is given. Reversed, every such item ships the teaser — a real body, a real
    element name, a length the description tells the model to judge by, and the
    article it came from never reaches the reply.

    The existing fixture puts `description` on one item and `content:encoded` on
    another, so the priority it is named after is never exercised."""
    entry = parse_feed(BOTH_BODIES)[0]
    assert entry.body.startswith("The whole article")
    assert entry.body_src == "content:encoded"


ATOM_MANY_LINKS = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>The Open ASR Leaderboard</title>
    <link rel="replies" href="https://huggingface.co/blog/asr/comments"/>
    <link rel="alternate" href="https://huggingface.co/blog/asr"/>
    <published>2026-08-30T07:12:00Z</published>
  </entry>
</feed>"""


def test_an_atom_entry_with_several_links_is_archived_under_the_article():
    """Atom entries routinely carry `replies`, `enclosure` and `edit` links
    beside the article. Taking the first one archives the comment thread under
    the article's headline — a different URL, so a different id, so the
    cross-source count for the real article never fires and the model is handed
    a link that is not the story.

    The existing fixture has one link, so the preference is never tested."""
    assert parse_feed(ATOM_MANY_LINKS)[0].url == "https://huggingface.co/blog/asr"


GUID_ONLY = b"""<rss version="2.0"><channel><item>
  <title>Only a guid</title>
  <guid isPermaLink="true">https://e.com/only-guid</guid>
  <pubDate>Sat, 30 Aug 2026 06:40:00 +0000</pubDate>
</item></channel></rss>"""


def test_an_item_whose_only_address_is_its_guid_is_still_archived():
    """A `<guid isPermaLink="true">` with no `<link>` is a legal RSS item, and
    an item with no URL is skipped without a word — the source comes back short
    and reads as quiet."""
    assert [e.url for e in parse_feed(GUID_ONLY)] == ["https://e.com/only-guid"]


# ── the server's wiring ─────────────────────────────────────────────────────

FEED = f"""<rss version="2.0"><channel>
  <item><title>GLM-5 released</title><link>https://qbitai.com/glm5</link>
        <pubDate>{rss_date(6)}</pubDate></item>
  <item><title>Second</title><link>https://qbitai.com/second</link>
        <pubDate>{rss_date(5)}</pubDate></item>
  <item><title>Third</title><link>https://qbitai.com/third</link>
        <pubDate>{rss_date(4)}</pubDate></item>
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


_BLOCK = re.compile(r"^## (\S+) (\S+) .*? (\d+)/(\d+)\s*$", re.M)


@pytest.mark.anyio
async def test_a_block_heading_names_the_language_of_the_headlines_under_it(live):
    """Headlines are never translated — that is the first thing the server's own
    instructions tell the model, and the heading is where the language is said.
    Without it a Cyrillic block is legible enough to guess and a Chinese one is
    not, and nothing else in the payload carries the fact."""
    out = await call(live, "wire_latest", hours=48, sources=["qbitai"])
    heading = _BLOCK.search(out)
    assert heading, f"no block heading in:\n{out[:400]}"
    assert heading.group(2) == by_id("qbitai").lang, (
        f"the heading for {heading.group(1)} says {heading.group(2)!r} where the "
        f"catalogue says {by_id('qbitai').lang!r}")


@pytest.mark.anyio
async def test_the_read_header_counts_the_dispatches_it_actually_serves(live):
    """"3 requested | 3 resolved | 2 unknown" is a reply whose own three numbers
    do not add up, and the one a model acts on is `resolved`: it reports having
    read three articles when one was served. The UNKNOWN line below it names the
    other two, so the contradiction is inside a single header."""
    listing = await call(live, "wire_latest", hours=48, sources=["qbitai"])
    known = re.findall(r"^(\w{12}) \d{2}:\d{2} ", listing, re.M)[:1]
    out = await call(live, "wire_read", ids=known + ["ffffffffffff", "eeeeeeeeeeee"])

    requested, resolved, unknown = map(
        int, re.search(r"\| (\d+) requested \| (\d+) resolved \| (\d+) unknown",
                       out).groups())
    served = len([l for l in out.splitlines() if l.startswith("## ")])
    assert resolved == served, f"the header says {resolved} resolved and {served} follow"
    assert requested == resolved + unknown


@pytest.mark.anyio
async def test_a_per_source_limit_is_applied_and_not_only_announced(live):
    """`limit_per_source` is refused when it is zero and priced in the
    description, and it is the model's only control over what a listing costs.
    Dropped on the way to the query the payload serves everything, the CUT line
    disappears because nothing was cut, and every count in the reply agrees with
    every other — a well-formed answer to a different question."""
    out = await call(live, "wire_latest", hours=48, sources=["qbitai"],
                     limit_per_source=1)
    shown, total = _BLOCK.search(out).groups()[2:]
    assert int(shown) == 1, f"one item per source was asked for and {shown} came back"
    assert int(total) == FEED.count(b"<item>")
    assert f"qbitai=1/{total}" in out, "and the cut has to be declared"


@pytest.mark.anyio
async def test_a_search_limit_is_applied_and_not_only_announced(live):
    """The same control in the tool whose whole description is about not reading
    a small number as an answer — where serving more than was asked for is the
    cheaper mistake, and still one the caller cannot see."""
    out = await call(live, "wire_search", query="e", days=7, sources=["qbitai"],
                     limit_per_source=1)
    shown, total = _BLOCK.search(out).groups()[2:]
    assert int(shown) == 1, f"one match per source was asked for and {shown} came back"


@pytest.mark.anyio
async def test_an_offset_timestamp_is_converted_and_not_relabelled_utc(live):
    """`since` is compared as a string against stored UTC timestamps. A
    `+02:00` offset relabelled `Z` moves the window two hours in the wrong
    direction and drops every item inside it — a well-formed reply, a plausible
    number, and nothing to check it against. The whole reason this parameter is
    validated at all is that it is the one free-text field that can produce a
    quiet day out of a working server."""
    out = await call(live, "wire_latest", since="2026-08-30T09:00:00+02:00",
                     sources=["qbitai"])
    start = re.search(r"\| (\S+)\.\.", out).group(1)
    assert start == "2026-08-30T07:00:00Z", (
        f"09:00+02:00 is 07:00Z and the window starts at {start}")


@pytest.mark.anyio
async def test_a_pass_that_fails_outright_still_answers_with_a_health_report(live,
                                                                            monkeypatch):
    """The fetch is inside the tool call, so an exception escaping it reaches
    the model as "Error executing tool" with no text. The source health recorded
    on the way down is what the reply needs in order to say DOWN — and DOWN is
    information, while a failed tool call is a model retrying or giving up on
    the whole question."""
    import cablegram.server as server_mod

    async def explode(*args, **kwargs):
        raise RuntimeError("the pass fell over")

    monkeypatch.setattr(server_mod, "poll_once", explode)
    out = await call(live, "wire_latest", hours=24, sources=["qbitai"])
    assert "CABLEGRAM" in out, "the tool has to answer rather than raise"


@pytest.mark.anyio
async def test_the_fetch_inside_a_tool_call_is_bounded(live, monkeypatch):
    """The theoretical worst case for a full pass is 130 seconds. Unbounded that
    is fine for a timer and unusable inside a tool call: the client times out,
    the model gets nothing, and whatever had already answered is thrown away —
    where the bounded version reports the stragglers DOWN and serves the rest."""
    import cablegram.server as server_mod

    seen = []
    real = server_mod.poll_once

    async def spy(db, sources, **kwargs):
        seen.append(kwargs.get("deadline"))
        return await real(db, sources, **kwargs)

    monkeypatch.setattr(server_mod, "poll_once", spy)
    await call(live, "wire_latest", hours=24, sources=["qbitai"])
    assert seen and seen[0] is not None and seen[0] < 120, (
        f"the pass was given deadline={seen[0]!r}")


# ── url identity, second pass ───────────────────────────────────────────────

def test_a_linked_article_is_keyed_by_the_same_rule_as_a_published_one():
    """A reference archives an article somebody pointed at, and it is the same
    article the outlet's own feed will carry tomorrow. Keyed by the raw URL on
    one path and the normalised one on the other, the two never meet: the
    improving UPDATE never fires, the placeholder headline stands for ever, and
    the cross-source count — the thing references exist to make possible — reads
    one where it should read two."""
    from cablegram.rss import Entry
    from cablegram.schema import connect
    from cablegram.store import latest_items, store_entries
    from cablegram.urls import item_id

    now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    linked = "https://www.qwen.ai/blog/qwen4/?utm_source=telegram"
    canonical = "https://qwen.ai/blog/qwen4"

    db = connect()
    store_entries(db, by_id("ai_newz"),
                  [Entry("Вышла Qwen 4", "https://t.me/ai_newz/1", now, "текст",
                         "message", links=(linked,))], fetched_at=iso)
    store_entries(db, by_id("hn"), [Entry("Qwen 4 released", canonical, now, None, None)],
                  fetched_at=iso)

    rows = {r["id"]: r for r in latest_items(db, since="2026-08-01T00:00:00Z")}
    assert item_id(canonical) in rows, "the feed's own copy has to be there"
    assert rows[item_id(canonical)]["cross"] == 2, (
        f"the channel linked the same article; the two spellings archived as "
        f"{sorted(rows)}")
