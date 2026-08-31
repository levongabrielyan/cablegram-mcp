"""The server is a translation layer, so these tests check the translation.

They call the tool functions the server registered, against a real archive in a
temp directory. What matters is that the contract the descriptions promise is
actually kept: a dead source appears, a window means what it says, an unknown id
comes back named.
"""

import pathlib
import re

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
    # hn too, because it has items here. Without it the fixture served a reply
    # declaring `hn=never polled` directly above hn's own block and headline,
    # and all twenty-eight tests in this file validated against that payload.
    # The poller cannot produce that state — record_attempt runs before storing
    # and nothing deletes source_state — so it was a defect in the test data,
    # and it kept the one assertion that would have caught it from being
    # written: it would have been born red.
    record_attempt(db, Fetched("hn", url=by_id("hn").url, ok=True, body=b"x",
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
    every kind has an adapter today, so nothing should be listed as pending —
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
async def test_live_mode_names_the_archive_it_is_no_longer_reading(tmp_path, monkeypatch):
    """The file is never deleted and CABLEGRAM_ARCHIVE=1 puts it back in
    service, so nothing is lost on disk. But wire_search goes from searching
    4,299 items to searching the live window, and an unannounced "0 hits" is
    indistinguishable from a quiet day — the failure this whole project exists
    to prevent, committed by its own migration.
    """
    from cablegram.render import render_sources
    from cablegram.server import _unused_archive

    path = tmp_path / "archive.db"
    monkeypatch.setenv("CABLEGRAM_DB", str(path))
    assert _unused_archive() is None, "no file, nothing to warn about"

    db = connect(path)
    store_entries(db, by_id("qbitai"),
                  [Entry("t", "https://qbitai.example/x", NOW, None, None)],
                  fetched_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    db.close()

    note = _unused_archive()
    assert note and "1 items" in note and "CABLEGRAM_ARCHIVE=1" in note

    out = render_sources(health={}, archive_items=0, archive_start="-",
                         archive_path=str(path), live=True, unused=note)
    assert "NOTE" in out and "NOT in use" in out
    assert "not in last call" in out, (
        '"never polled" in live mode would report a source nobody asked for as '
        "one nobody can use")


@pytest.mark.anyio
async def test_an_empty_archive_is_not_worth_a_warning(tmp_path, monkeypatch):
    from cablegram.server import _unused_archive

    path = tmp_path / "archive.db"
    monkeypatch.setenv("CABLEGRAM_DB", str(path))
    connect(path).close()
    assert _unused_archive() is None


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


@pytest.mark.anyio
async def test_every_listing_names_a_mode_and_they_name_the_same_one(server):
    """"searched the archive" and "searched one live fetch and threw it away"
    are different claims about what a miss means, and only wire_latest stated
    which one it was — so a search that found nothing gave the model no way to
    know whether it had looked at 4,936 items or at one download.

    Asserts that both name a mode and that the two agree, rather than that
    either says "archive": a payload that hardcodes the word passes a test for
    the word, which is how the sentence and the code came apart everywhere else
    in this file.
    """
    modes = {}
    for tool, args in (("wire_latest", {"hours": 24}),
                       ("wire_search", {"query": "GLM"})):
        out = await call(server, tool, **args)
        named = re.match(r"CABLEGRAM[^|]*?\b(archive|live)\b", out)
        assert named, f"{tool} names no mode: {out.splitlines()[0]}"
        modes[tool] = named.group(1)
    assert len(set(modes.values())) == 1, (
        f"one process, one source of rows, two answers: {modes}")


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
