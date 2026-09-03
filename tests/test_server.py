"""The server is a translation layer, so these tests check the translation.

They call the tool functions the server registered, against a real archive in a
temp directory. What matters is that the contract the descriptions promise is
actually kept: a dead source appears, a window means what it says, an unknown id
comes back named.
"""

import pathlib
import re

from datetime import datetime, timedelta, timezone

from pathlib import Path

import pytest

from cablegram.schema import connect
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
def server():
    """Known rows, no network.

    A builder rather than a populated connection: the SDK runs a synchronous
    tool in a worker thread and SQLite refuses a connection created in another
    one, and `opened()` closes what it is given. Each call gets its own copy of
    the same handful of rows, which costs microseconds.

    The fetch path this bypasses is covered by test_live.py, which is the mode
    that ships.
    """
    def rows():
        db = connect()
        store_entries(db, by_id("qbitai"),
                      [Entry("智谱发布GLM-5", "https://qbitai.example/glm5", NOW,
                             "正文内容", "description")],
                      fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        store_entries(db, by_id("hn"),
                      [Entry("Zhipu releases GLM-5", "https://qbitai.example/glm5",
                             NOW, None, None)],
                      fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        record_attempt(db, Fetched("qbitai", url=by_id("qbitai").url, ok=True,
                                   body=b"x", status=200,
                                   fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")))
        # hn too, because it has items here. Without it the fixture served a
        # reply declaring `hn=never polled` directly above hn's own block and
        # headline, and every test in this file validated against that payload.
        record_attempt(db, Fetched("hn", url=by_id("hn").url, ok=True, body=b"x",
                                   status=200,
                                   fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")))
        # deepmind succeeded first and fails now, which is the shape a real
        # outage takes. The fixture used to hold only a source that had never
        # once worked — the one case the old DOWN logic got right — so a source
        # failing today after working yesterday appeared in no test at all.
        record_attempt(db, Fetched("deepmind", url=by_id("deepmind").url, ok=True,
                                   body=b"x", status=200,
                                   fetched_at="2026-08-20T09:00:00Z"))
        record_attempt(db, Fetched("deepmind", url=by_id("deepmind").url, ok=False,
                                   error="timeout8s",
                                   fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")))
        return db

    return build(rows)


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
    every kind has an adapter today, so nothing should be listed as pending —
    and the machinery stays for the next source added."""
    out = await call(server, "wire_latest", hours=24)
    assert "PENDING" not in out


@pytest.mark.anyio
async def test_read_reports_the_element_not_a_verdict(server):
    """The full/teaser judgement was removed from the parser and came back here.
    What is printed now is the element and the size — facts — because whether a
    feed ships whole articles is a property of the source, not of a tag name.

    The listing comes first because nothing is kept between runs: an id is
    readable only while the call that produced it is still in this process's
    cache."""
    await call(server, "wire_latest", hours=24)
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
    assert "not the same as nobody discussing it" in out


@pytest.mark.anyio
async def test_sources_lists_every_one_of_them(server):
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
async def test_a_source_that_worked_and_now_fails_is_reported_down():
    """DOWN only looked at sources that had never once succeeded, so one failing
    for three days counted as healthy — while wire_sources listed it as FAIL.
    The two tools contradicted each other, and the one called every morning is
    the one that lied."""
    def rows():
        db = connect()
        url = by_id("qbitai").url
        record_attempt(db, Fetched("qbitai", url=url, ok=True, body=b"x", status=200,
                                   fetched_at="2026-08-27T09:00:00Z"))
        record_attempt(db, Fetched("qbitai", url=url, ok=False, error="HTTP 403",
                                   fetched_at="2026-08-30T09:00:00Z"))
        return db

    out = await call(build(rows), "wire_latest", hours=24)
    assert "qbitai=HTTP 403" in out


@pytest.mark.anyio
async def test_search_says_how_far_back_the_archive_really_goes(server):
    """It printed the file's creation date, so an archive holding ten years of
    OpenAI posts announced itself as starting today — and a model asked "since
    when has X been discussed" refuses to answer."""
    out = await call(server, "wire_search", query="GLM")
    assert re.search(r"^COVER \S+=\d{4}-\d{2}-\d{2}", out, re.M), (
        "the header states how far back each source could be searched, one "
        "floor per source: a single date was the deepest feed in the call and "
        "read as the reach of the whole search")


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


@pytest.mark.anyio
async def test_every_tool_has_a_readable_title_and_says_it_is_read_only(server):
    """Both are required to submit to Anthropic's connector directory, and the
    title is what a person sees in a client's tool list — `wire_latest` is a
    function name, not a label."""
    for tool in await server.list_tools():
        assert tool.title, f"{tool.name} has no title"
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True


def producible_body_src() -> set[str]:
    """Every marker a parser can actually emit, read off the parsers.

    This set used to be written out by hand beside the assertion, and it went
    stale in the same commit that added an adapter: `hub.py` emits
    `body_src="hub"` and the hand-written set never got it, so the test called a
    true sentence a lie from the day it shipped. `nextjs.py` added `summary`
    later and would have done it again.

    The point of the assertion is that a description may only name a marker the
    code can produce. A list of those markers maintained separately from the
    code is the same defect the assertion exists to catch.
    """
    import cablegram
    from cablegram import rss

    package = pathlib.Path(cablegram.__file__).parent
    emitted = {m for path in package.glob("*.py")
               for m in re.findall(r'body_src\s*=\s*"([^"]+)"', path.read_text())}
    # plus the elements rss.py names by table, and the renderer's literal for an
    # item that has no body at all.
    return emitted | {name for _, name in rss._BODY_ELEMENTS} | {"none"}


@pytest.mark.anyio
async def test_every_marker_a_description_names_can_actually_be_emitted(server):
    """wire_read's central instruction was "Read body=teaser literally: the text
    you get is NOT the article".

    The full/teaser verdict had been removed from the parser and then from the
    renderer two rounds earlier — deliberately, because it is wrong in both
    directions — so `teaser` could no longer appear anywhere. A model told to
    distrust an excerpt only when it sees a marker that cannot appear will trust
    every excerpt it is ever given, and report a 36-character Chinese fragment
    as the article. The instruction was built so that its absence reads as
    approval.

    This is the shape of nearly every defect this file has caught: a fix lands
    in the code and the sentence describing it stays behind. Nothing else in the
    suite compares the two.
    """
    producible = producible_body_src()
    texts = {t.name: t.description or "" for t in await server.list_tools()}
    # The instructions too. They are the half a client shows the model before
    # any description, and the sibling test already covered them: putting
    # `body=teaser` back into them left the whole suite green.
    texts["instructions"] = server.instructions or ""
    for where, text in texts.items():
        # `[\w:]+` so `body=content:encoded` matches whole, and so the
        # placeholder `body=<element> <N>c` — which starts with `<` — does not.
        for named in re.findall(r"body=[`'\"]?([\w:]+)", text):
            assert named in producible, (
                f"{where} tells the model to look for body={named}, which no "
                f"parser can produce. Producible: {sorted(producible)}")




@pytest.mark.anyio
async def test_no_description_carries_a_source_count_of_its_own(server):
    """The catalogue was 19 and the descriptions said 19, in two places, by
    hand. Adding two sources and removing two made all three numbers disagree
    with each other and with the catalogue — and a model told there are
    nineteen has no way to notice it was given twenty-one.

    The same shape as every other defect in this file: a figure written down
    once, beside a value that moves. This asserts that any count a description
    names is the count the catalogue actually has.
    """
    import re

    from cablegram.sources import SOURCES

    texts = {t.name: t.description or "" for t in await server.list_tools()}
    texts["instructions"] = server.instructions or ""
    for where, text in texts.items():
        for count in re.findall(r"\b(\d+)\s+tech", text):
            assert int(count) == len(SOURCES), (
                f"{where} tells the model there are {count} sources; "
                f"the catalogue has {len(SOURCES)}")
        # The cost table prices a sweep with the figure and no noun after it
        # ("all 21, and a slow pass..."), which the pattern above cannot see —
        # so a hand-written 19 sat there through a catalogue change of two.
        for priced in re.findall(r"\ball (\d+)\b", text):
            assert int(priced) == len(SOURCES), (
                f"{where} prices a sweep of {priced} sources; there are "
                f"{len(SOURCES)}")


# ── seventh review ──────────────────────────────────────────────────────────

@pytest.mark.anyio
@pytest.mark.parametrize("tool,args", [("wire_latest", {"hours": 24}),
                                       ("wire_search", {"query": "GLM", "days": 7})])
async def test_a_limit_of_zero_is_refused_rather_than_answered(server, tool, args):
    """`hours` and `days` were guarded and `limit_per_source` was not, though it
    produces the worse reply of the two: it empties the payload and leaves the
    header claiming full coverage.

    Measured against a 24h window of the real archive, holding 966 items from 14
    sources: `limit_per_source=0` returned `0 of 0 items | 21/21 sources` above
    a SILENT line naming all twenty-one as having answered and published nothing
    in it. A window of zero hours at least prints a window of zero hours; this
    one reads as a quiet day across every source at once, and there is no second
    figure anywhere in the reply to check the first against.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as raised:
        await call(server, tool, limit_per_source=0, **args)
    assert "limit_per_source" in str(raised.value)
    assert "0 items per source" in str(raised.value), (
        "the message has to name what was asked for, not just refuse it")


_DOWN_LINE = re.compile(r"^DOWN  (.*)$", re.M)


def down_ids(out: str) -> set[str]:
    """The sources a payload declares did not answer."""
    line = _DOWN_LINE.search(out)
    return {p.split("=")[0] for p in line.group(1).split("  ") if p} if line else set()


@pytest.mark.anyio
async def test_both_listing_tools_agree_about_which_sources_did_not_answer(server):
    """One rule, three places, and only two of them had it written down.

    wire_latest and wire_search read the same source_health and cover the same
    catalogue, so a source that did not answer did not answer for both. Only
    wire_latest said so. Measured against the real archive with every source
    failing, wire_latest returned `0/21 sources` above a DOWN line naming all
    twenty-one, and wire_search returned `0 shown hits` with no line at all —
    directly above its own boilerplate explaining that zero hits means "not in
    what we can search", which is the opposite of what had happened.

    Compares one payload against the other and names no expected value, so it
    holds whichever sources are down.
    """
    latest = await call(server, "wire_latest", hours=24)
    search = await call(server, "wire_search", query="GLM", days=1)
    assert down_ids(latest), "the fixture has to have something down to compare"
    assert down_ids(search) == down_ids(latest), (
        f"wire_latest reports {sorted(down_ids(latest))} down and wire_search "
        f"reports {sorted(down_ids(search))}; both read the same health over the "
        f"same catalogue, and a search is where a missing source is least visible")


@pytest.mark.anyio
async def test_a_search_names_the_selector_that_matched_nothing(server):
    """`sources=["qbitia"]` came back `0 shown hits` under the line telling the
    model that zero hits means "not in what we can search" — when the truth was
    that nothing had been searched. wire_latest had named the typo since the
    fifth review; the tool where an empty answer is the expected shape did not."""
    out = await call(server, "wire_search", query="GLM", sources=["qbitia"])
    assert "qbitia" in out and "UNKNOWN SELECTOR" in out
    assert "NOTHING WAS SEARCHED" in out



_BLOCK = re.compile(r"^## (\S+) ", re.M)
# Absolute claims: "hold nothing", "published nothing in this window", "never
# polled". DOWN-with-a-reason is deliberately excluded — "failed today" and
# "holds items from yesterday that are inside the window" are both true at once,
# every day.
_ABSOLUTE = re.compile(r"^(PENDING|SILENT) +(\S.*?)  \(", re.M)
_NEVER = re.compile(r"(\S+)=never polled")


@pytest.mark.anyio
async def test_a_source_the_header_says_is_holding_nothing_has_no_block(server):
    """The one header-versus-body contradiction the internal-consistency block
    claims to cover and does not.

    The false conclusion it stops: a model reads `SILENT openai`, reports that
    OpenAI published nothing today, and OpenAI's headlines are printed six lines
    below in the same reply. Both halves came from one call and only one can be
    true.

    Nothing exercised the expression that builds `silent`. render_latest takes
    it as a parameter, so every test of it hands over a list already known to be
    right; dropping a term from the set in server.py left the whole suite green.

    Names no expected value — it compares one region of the payload against
    another — so it cannot go stale, and it covers PENDING and never-polled on
    the same terms.
    """
    out = await call(server, "wire_latest", hours=24)

    printed = set(_BLOCK.findall(out))
    claimed = {name: label for label, ids in _ABSOLUTE.findall(out)
               for name in ids.split()}
    claimed.update({name: "never polled" for name in _NEVER.findall(out)})
    clash = {s: claimed[s] for s in printed & claimed.keys()}
    assert not clash, (
        f"the header files {clash} as holding nothing in this window, and the "
        f"body prints a block of dispatches for each of them")


@pytest.mark.anyio
async def test_a_source_with_no_recorded_attempt_is_named_as_never_polled(server):
    """The other half. A source nobody has fetched holds nothing, which is a
    different fact from a source that holds nothing, and the branch that says so
    fires for most sources in most replies — twenty of twenty-two here. Deleting
    it left the suite green."""
    out = await call(server, "wire_latest", hours=24)
    assert "openai=never polled" in out


@pytest.mark.anyio
async def test_the_headline_read_back_is_the_one_the_named_source_wrote():
    """wire_read prints `first_source`, `lang` and `via` — all facts about the
    item — beside a headline that was the sighting's: the words of whichever
    source carried it. When two sources carry one URL those are different
    sources.

    Measured against the real catalogue: 20 items with more than one source, 10
    of them with different headlines. The worst printed a Russian channel's
    paraphrase under `openai en`, with a date in it that OpenAI's own post does
    not contain, and no `!!` — because `via` says the item was published by a
    feed, which it was. Three fields from three places, read as one statement.

    Asserted through the server rather than against the query, because the query
    already carried both headlines and it was the renderer that chose the wrong
    one.
    """
    url = "https://openai.com/index/pacing"

    def rows():
        db = connect()
        store_entries(db, by_id("openai"),
                      [Entry("Pacing model development", url, NOW, "body", "description")],
                      fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        store_entries(db, by_id("qbitai"),
                      [Entry("OpenAI暂停两周强化学习训练", url, NOW, None, None)],
                      fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        return db

    server = build(rows)
    listing = await call(server, "wire_latest", hours=24)
    # The blocks keep each source's own words: that pairing is the bridge
    # between a Chinese story and an English query, and it is deliberate.
    assert "OpenAI暂停两周强化学习训练" in listing and "Pacing model development" in listing

    out = await call(server, "wire_read", ids=[item_id(url)])
    named = re.search(r"^## \S+ (\S+) (\S+) ", out, re.M)
    assert named and named.group(1) == "openai" and named.group(2) == "en"
    assert "Pacing model development" in out, (
        f"the reply names openai/en and prints a headline that is not openai's:\n{out}")
    assert "OpenAI暂停两周强化学习训练" not in out, (
        "qbitai's words, attributed to openai by everything around them")


@pytest.mark.anyio
async def test_every_place_that_states_a_version_states_the_same_one(server):
    """Three of them, and they disagreed. `serverInfo.version` was empty, so a
    client could not tell a build serving nineteen sources from one serving
    twenty-nine; the header of every reply said `v0.1` while the package said
    0.1.1; and nothing pinned either.

    Compares them against each other and against the installed metadata, so it
    holds at whatever the version becomes.
    """
    from cablegram import __version__
    from cablegram.render import VERSION

    assert __version__ and __version__ != "0+unknown", (
        "the test suite runs against an installed package; a checkout on "
        "PYTHONPATH alone is the one route the README does not offer")
    assert server.version == __version__, "the handshake states the build"
    assert VERSION == f"v{__version__}", "and so does every reply"

    out = await call(server, "wire_latest", hours=24)
    assert out.startswith(f"CABLEGRAM v{__version__} ")

    # The README was left out of this comparison and drifted on its own: its
    # Status section still opened with "v0.1" two releases later, twelve lines
    # under a sample reply reading `CABLEGRAM v0.2.0`. It is the first place a
    # reader looks to know which build they are getting, and it named one that
    # no longer exists. Found by the cloud review of v0.1.1..HEAD.
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    status = readme.split("## Status", 1)[1].lstrip()
    assert status.startswith(f"v{__version__} "), (
        f"the Status section opens with {status[:12]!r}; the package is "
        f"{__version__}")



@pytest.mark.anyio
@pytest.mark.parametrize("query", ["", "   ", "\t"])
async def test_an_empty_query_is_refused_rather_than_answered(server, query):
    """`search_items` returns engine='none' for an empty query and searches
    nothing. The engine line has two branches and 'none' fell through the else:

        CABLEGRAM search "" | last 7d | 0 shown hits
        COVER this call fetched 1158 items and kept none, oldest 2015-12-11.
              ENGINE trigram index over the headlines this call searched.

    A direct claim that 1,158 items were searched. Nothing was. And the fetch
    happens first, so a query that arrived empty — a variable that came back
    blank — spent a full sweep to search nothing.

    Every other impossible argument here is refused. This was the one most
    likely to arrive empty by accident.
    """
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError) as raised:
        await call(server, "wire_search", query=query, days=7)
    assert "query" in str(raised.value) and "wire_latest" in str(raised.value)


@pytest.mark.anyio
async def test_every_tool_states_the_build_that_answered(server):
    """Three of the four printed the version and wire_search printed none, so a
    client could not tell which build answered a search — the one reply whose
    coverage changes most between builds, because it is the one bounded by what
    the catalogue can reach.

    Compared against the installed metadata rather than a literal, so it holds
    at whatever the version becomes.
    """
    from cablegram import __version__

    for name, args in (("wire_latest", {"hours": 24}),
                       ("wire_search", {"query": "GLM"}),
                       ("wire_read", {"ids": ["deadbeef0000"]}),
                       ("wire_sources", {})):
        out = await call(server, name, **args)
        assert out.startswith(f"CABLEGRAM v{__version__}"), (
            f"{name} opens with {out.splitlines()[0][:40]!r}")


@pytest.mark.anyio
async def test_every_tag_a_caller_can_select_by_is_named_where_it_is_selected(server):
    """The catalogue's fifteen tags are the vocabulary for choosing a source,
    and not one of them appeared in any description. The only place to learn
    them was wire_sources, which costs about 1,500 tokens — so the map cost
    more than most of the calls it would have improved.

    What a reader did learn for free was `sources=['ru']`, the single selector
    example on the whole surface and the most expensive call there is at 20-36
    seconds, because six of the seven Russian sources are Telegram channels.
    The expensive selector arrived by accident; the cheap ones did not arrive.
    A model after the Chinese labs has `weights` — six sources, under a second
    — and no way to find out.

    Generated from the catalogue rather than written down, so a tag added
    tomorrow appears on its own.

    Which is also the limit of this test, and worth saying rather than leaving
    to be discovered: while the menu is generated it cannot fail for a new tag
    — measured, adding one to the catalogue keeps it green, because the tag
    reaches the description by the same route the test reads it. What it does
    catch is the regression that actually happens: somebody replacing the
    interpolation with a hand-written list, which then rots. Mutated that way
    it names the ten tags that fall out.
    """
    from cablegram.sources import SOURCES

    tags = {tag for s in SOURCES for tag in s.tags}
    texts = {t.name: t.description or "" for t in await server.list_tools()}
    for name in ("wire_latest", "wire_search"):
        missing = sorted(t for t in tags if t not in texts[name])
        assert not missing, (
            f"{name} takes `sources` and never names {missing}; the only way "
            f"to learn them is wire_sources at ~1,500 tokens")

    # And the languages, which are the other half of the same argument.
    for name in ("wire_latest", "wire_search"):
        for lang in sorted({s.lang for s in SOURCES}):
            assert lang in texts[name], f"{name} never names the language {lang}"


@pytest.mark.anyio
async def test_the_tag_menu_prices_a_tag_on_the_axis_the_cost_note_names(server):
    """The menu listed source counts, and the COST paragraph three lines below
    says outright that "language is not the axis, channel count is". For any
    tag holding a Telegram channel the source count is not merely incomplete,
    it points the wrong way:

        papers(1)      one channel, fetched alone, three seconds apart
        researcher(1)  one channel
        technical(5)   one of the five is a channel and pays the same penalty
        weights(6)     no channels — 0.47s measured

    The two smallest numbers in the menu were the two slowest calls per source,
    and the one that looked six times larger was the fastest. A model reading
    the menu for the cheapest option picked the most expensive.
    """
    from cablegram.sources import SOURCES

    channels = {tag for s in SOURCES if s.kind == "telegram" for tag in s.tags}
    assert channels, "the fixture needs at least one channel-bearing tag"

    texts = {t.name: t.description or "" for t in await server.list_tools()}
    for name in ("wire_latest", "wire_search"):
        for tag in sorted(channels):
            n = sum(1 for s in SOURCES if tag in s.tags)
            c = sum(1 for s in SOURCES
                    if tag in s.tags and s.kind == "telegram")
            priced = f"{tag}({n}, {c} channel{'s' if c > 1 else ''})"
            assert priced in texts[name], (
                f"{name} lists {tag} without its channel count; {n} sources "
                f"reads as cheap and {c} of them are fetched one at a time")

        # And a tag with no channels carries no such note, or the mark stops
        # marking anything.
        free = next(t for t in {tag for s in SOURCES for tag in s.tags}
                    if t not in channels)
        n = sum(1 for s in SOURCES if free in s.tags)
        assert f"{free}({n})" in texts[name]


@pytest.mark.anyio
async def test_reading_an_item_dates_it_by_its_publisher_not_by_who_carried_it_first():
    """The listing was fixed to date each block by its own source's sighting.
    wire_read was not, and it is the tool that names a publisher.

    It serves from the process cache, which holds one row per id — the last one
    `remember` saw, and rows arrive ordered by source, so for an item two
    sources carried it is whichever sorts first alphabetically. That row mixes
    two levels: `first_source` is a fact about the item, `published` is a fact
    about that one sighting. Measured, OpenAI publishing at T-10h and Hacker
    News carrying it at T-1h:

        ## e657a4dcf6ea openai en 2026-09-02T12:02:27Z ... [hn,openai]
        Lo que OpenAI escribió

    "OpenAI published this at 12:02". OpenAI published it at 03:02; 12:02 is
    when somebody submitted it to Hacker News. Nine hours wrong, under the
    publisher's name, with the publisher's headline and body, and no `~` to say
    the date is borrowed. Found by a reviewer building exactly this fixture;
    the commit that fixed the listing (0eda5d0) claimed to have covered it.

    hn sorts before openai, so hn's row is the one cached — that is the
    accident this test is built to hit, and the reason the pair is not
    symmetrical.
    """
    url = "https://openai.example/one-post"
    published = NOW - timedelta(hours=10)
    submitted = NOW - timedelta(hours=1)
    stamp = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")

    def rows():
        db = connect()
        # hn FIRST. poll stores in catalogue order and Hacker News is source
        # three, ahead of every publisher it links to; the fixture that stored
        # openai first tested the one order the bug cannot occur in, and passed
        # while wire_read printed a submitter's title under OpenAI's name.
        store_entries(db, by_id("hn"),
                      [Entry("Submitted to HN", url, submitted, None, None)],
                      fetched_at=stamp)
        store_entries(db, by_id("openai"),
                      [Entry("What OpenAI wrote", url, published, "the post",
                             "description")], fetched_at=stamp)
        return db

    server = build(rows)
    await call(server, "wire_latest", hours=24)
    out = await call(server, "wire_read", ids=[item_id(url)])
    heading = next(l for l in out.splitlines() if l.startswith("## "))

    assert " openai " in heading, heading
    assert published.strftime("%Y-%m-%dT%H:%M") in heading, (
        f"the item's date is when its publisher published it:\n{heading}")
    assert submitted.strftime("T%H:%M") not in heading, (
        f"that is when Hacker News carried it, not when OpenAI published it:\n"
        f"{heading}")
    assert "~" not in heading.split(" openai ")[1][:1], (
        "the publisher's own exact date is not borrowed")
    assert "What OpenAI wrote" in out and "Submitted to HN" not in out, (
        "and the headline is the publisher's, for the same reason")


@pytest.mark.anyio
async def test_the_cache_keeps_what_the_latest_reply_printed_even_if_it_was_cached_before():
    """Reassigning a dict key keeps its old position, so an id cached by an
    earlier call stayed at the front of the eviction order — and was the first
    evicted, even when the reply just printed it at the top. Measured: a
    25-row call followed by a 4,500-row one left the second reply's first
    three ids UNKNOWN. "Most recently printed" is what the order has to mean.
    """
    import cablegram.server as server_mod

    urls = [f"https://qbitai.example/{i}" for i in range(4)]

    def rows():
        db = connect()
        store_entries(db, by_id("qbitai"),
                      [Entry(f"story {i}", u, NOW - timedelta(minutes=i), None, None)
                       for i, u in enumerate(urls)],
                      fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        return db

    server = build(rows)
    server_mod.SEEN_LIMIT, saved = 3, server_mod.SEEN_LIMIT
    try:
        await call(server, "wire_latest", hours=24, limit_per_source=2)
        listing = await call(server, "wire_latest", hours=24, limit_per_source=4)
        first = re.findall(r"^(\w{12}) \d{2}:\d{2} ", listing, re.M)[0]
        out = await call(server, "wire_read", ids=[first])
        assert "UNKNOWN" not in out, (
            f"{first} is the first line of the reply that just produced it")
    finally:
        server_mod.SEEN_LIMIT = saved
