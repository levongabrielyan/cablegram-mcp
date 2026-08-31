"""The output is the product: it is all the model ever sees.

Every convention here exists because its absence causes a specific wrong
conclusion — a dead source read as a quiet one, a truncated excerpt read as an
article, a capture time read as a publication time. The tests are named after
the wrong conclusion, not after the format.
"""

from cablegram.render import (estimate_tokens, render_latest, render_read,
                              render_search, render_sources)

ROW = {
    "id": "a3f9c2e1", "source": "qbitai", "lang": "zh", "title": "智谱发布GLM-5",
    "published": "2026-08-30T07:12:00Z", "date_exact": 1, "cross": 6,
    "source_total": 14, "target_host": None, "url": "https://qbitai.com/x",
    "body": "正文", "body_src": "description", "first_source": "qbitai",
}


def row(**over):
    return {**ROW, **over}


def test_a_dead_source_is_named_and_explained():
    """Absent from the list, the model cannot know the source exists, and reads
    the gap as nothing having happened there."""
    out = render_latest([], since="2026-08-29T09:00:00Z", until="2026-08-30T09:00:00Z",
                        down={"cls": "HTTP403"}, sources_total=19)
    assert "cls=HTTP403" in out
    assert "UNKNOWN" in out.upper()


def test_a_cut_is_declared_with_the_real_total():
    """An undeclared cut is indistinguishable from a source with little to say."""
    out = render_latest([row(source_total=57, id="b1")], since="s", until="u",
                        down={}, sources_total=19, limit_per_source=25)
    assert "qbitai=1/57" in out


def test_an_uncertain_date_is_marked():
    """The mark costs one token. Presenting a capture time as a publication time
    silently files the item under the wrong day."""
    out = render_latest([row(date_exact=0)], since="s", until="u", down={},
                        sources_total=19)
    assert "~a3f9c2e1" in out


def test_a_certain_date_carries_no_mark():
    out = render_latest([row()], since="s", until="u", down={}, sources_total=19)
    assert "~a3f9c2e1" not in out and "a3f9c2e1" in out


def test_the_destination_shows_only_for_aggregators():
    """On qbitai the host is always qbitai: printing it costs tokens per line
    and says nothing. On Hacker News it is half the information."""
    out = render_latest([row(source="hn", target_host="github.com", title="Show HN")],
                        since="s", until="u", down={}, sources_total=19)
    assert "(github.com)" in out
    plain = render_latest([row()], since="s", until="u", down={}, sources_total=19)
    assert "(" not in plain.split("---")[-1]


def test_a_story_in_several_sources_is_counted_in_the_header():
    """Arithmetic, not a ranking — and the strongest early signal there is."""
    out = render_latest([row(cross=6)], since="s", until="u", down={}, sources_total=19)
    assert "a3f9c2e1 x6" in out
    assert "NOT a ranking" in out


def test_a_story_in_one_source_is_not_counted():
    out = render_latest([row(cross=1)], since="s", until="u", down={}, sources_total=19)
    assert "CROSS" not in out

def test_an_id_that_is_not_there_is_named_with_a_way_out():
    """The only way the model recovers on its own."""
    out = render_read([], requested=["9f01aa2b"])
    assert "9f01aa2b" in out and "UNKNOWN" in out
    assert "wire_latest" in out


def test_search_says_what_it_could_not_look_in():
    """Without this, zero hits reads as "nobody is talking about it" instead of
    "not in the fifteen days we hold"."""
    out = render_search([], query="lovable", since="2026-08-23", days=7,
                        archive_start="2026-08-30", archive_items=2341)
    assert "0 hits" in out
    assert "does NOT mean" in out or "NOT" in out
    assert "2026-08-30" in out


def test_search_tells_the_model_to_retry_in_the_source_language():
    """智谱 returns nothing on Hacker News and Zhipu returns 212, for the same
    company. The model can only fix that if it is told."""
    out = render_search([], query="Zhipu", since="s", days=7,
                        archive_start="2026-08-30", archive_items=10)
    assert "translit" in out.lower() or "native" in out.lower()


def test_sources_lists_every_source_including_the_broken_ones():
    out = render_sources(health={"cls": {"last_ok": None, "last_error": "HTTP403",
                                         "last_try": "2026-08-30T02:10:00Z"}},
                         archive_items=2341, archive_start="2026-08-30",
                         archive_path="/tmp/a.db")
    assert "cls" in out and "HTTP403" in out
    assert "qbitai" in out, "a source never polled must still be listed"


def test_a_source_with_no_adapter_is_not_reported_as_broken():
    """"Never polled because nothing polls it yet" and "polled and failed" are
    different facts. Filing the first as DOWN buries the second among eight
    lines of noise, and the one that needs attention is the second."""
    out = render_latest([], since="s", until="u", down={"cls": "HTTP403"},
                        sources_total=19, no_adapter=["ai_newz", "hn"])
    assert "cls=HTTP403" in out
    assert "ai_newz" in out and "hn" in out
    assert "ai_newz=" not in out.split("PENDING")[0], "not in the DOWN line"


def test_full_detail_actually_carries_the_body():
    """detail="full" lowered the per-source limit and shipped no body, so asking
    for more returned five headlines instead of twenty-five and nothing else —
    strictly less information, from a parameter whose name promises more.

    A promise in a tool description that the code does not keep is the worst
    failure this surface has: the model believes it read the article."""
    out = render_latest([row(body="正文内容完整版", body_src="content:encoded")],
                        since="s", until="u", down={}, sources_total=19, detail="full")
    assert "正文内容完整版" in out


def test_headlines_detail_ships_no_bodies():
    out = render_latest([row(body="should not appear")], since="s", until="u",
                        down={}, sources_total=19)
    assert "should not appear" not in out

def test_a_budget_cut_names_the_sources_it_dropped():
    """Trimming the flat list beheads its alphabetical tail, so whole sources
    vanished — openai and huggingface among them — while the header still said
    11/19. This module's own docstring says that cannot happen: missing from the
    list, a source cannot be known to exist."""
    rows = [row(id=f"{i:012x}", source=s, title="x" * 300, source_total=50)
            for s in ("alternativeto", "habr", "openai", "qbitai") for i in range(50)]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        max_tokens=800)

    blocks = {line.split()[1] for line in out.splitlines() if line.startswith("## ")}
    assert blocks == {"alternativeto", "habr", "openai", "qbitai"}, \
        "every source keeps its heading, even if all its items were cut"


def test_a_budget_cut_lands_inside_the_budget():
    from cablegram.render import estimate_tokens

    rows = [row(id=f"{i:012x}", source=s, title="x" * 300, source_total=50)
            for s in ("alternativeto", "habr", "openai") for i in range(50)]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        max_tokens=1500)
    assert estimate_tokens(out) <= 1500


def test_search_declares_its_cut_like_the_listing_does():
    """Printing 3/3 for a source holding 437 does not leave the cut undeclared —
    it denies it. And this is the tool whose entire description is about not
    drawing conclusions from a small number."""
    rows = [row(id=f"{i:012x}", source="openai", source_total=437) for i in range(3)]
    out = render_search(rows, query="AI", since="s", days=7,
                        archive_start="2020-01-01", archive_items=2341)
    assert "3/437" in out
    assert "CUT" in out


def test_the_body_element_is_reported_without_a_verdict():
    """The full/teaser table was removed from the parser in the fourth round
    because it is wrong in both directions, and it came back here — stamped on
    36Kr digests of 3,334 characters, telling the model not to trust a complete
    text it has already paid for. 1,335 of 2,341 archived items carried it."""
    out = render_latest([row(body="x" * 3000, body_src="description")], since="s",
                        until="u", down={}, sources_total=19, detail="full")
    assert "NOT the full article" not in out
    assert "description" in out


def test_cross_says_when_it_is_showing_only_some():
    rows = [row(id=f"{i:012x}", cross=3) for i in range(30)]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19)
    assert "of 30" in out or "30 " in out.split("---")[0]


def test_the_archive_path_is_not_a_home_directory():
    """Pasting the output into an issue must not leak a username."""
    out = render_sources(health={}, archive_items=1, archive_start="2026-01-01",
                         archive_path="/home/someone/.local/share/cablegram/archive.db")
    assert "/home/someone" not in out
    assert "~/.local/share/cablegram" in out


def test_a_budget_too_small_for_the_sources_says_so():
    """With one item each, the headers alone can exceed a small max_tokens.
    Going over is the right call — dropping sources is worse — but going over
    silently is not: the caller set a limit and has to know it was missed."""
    from cablegram.render import estimate_tokens

    rows = [row(id=f"{i:012x}", source=s, title="x" * 200, source_total=9)
            for s in ("a", "b", "c", "d", "e", "f", "g", "h") for i in range(9)]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        max_tokens=50)
    assert estimate_tokens(out) > 50
    assert "OVER BUDGET" in out
    assert len({l.split()[1] for l in out.splitlines() if l.startswith("## ")}) == 8


def test_search_declares_which_engine_answered():
    """"GLM" runs on the trigram index and finds 4; "GL" falls to a LIKE scan and
    finds 63. Different recall, different semantics, same-looking answer — and a
    model comparing two queries has no way to know they were not comparable."""
    out = render_search([], query="GL", since="s", days=7, archive_start="2020-01-01",
                        archive_items=10, engine="substring")
    assert "substring" in out.lower()


def test_a_source_silent_for_days_is_marked_stale():
    """OK with a three-day-old date reads as OK. Nobody compares it to today,
    least of all the model, and a dead timer looks like healthy sources."""
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(hours=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = render_sources(health={"qbitai": {"last_ok": old, "last_try": old}},
                         archive_items=1, archive_start="2026-01-01",
                         archive_path="/tmp/a.db")
    assert "STALE" in out and "60h" in out


def test_a_fresh_source_is_not_marked_stale():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = render_sources(health={"qbitai": {"last_ok": now, "last_try": now}},
                         archive_items=1, archive_start="2026-01-01",
                         archive_path="/tmp/a.db")
    assert "STALE" not in out


def test_a_reverse_engineered_source_says_so():
    """cls.cn is an undocumented internal API with a signature nobody published.
    It can stop working without notice, and the honest thing is to say that
    before it does rather than after — the same disclosure a security document
    makes when it lists what it does not protect."""
    out = render_sources(health={}, archive_items=1, archive_start="2026-01-01",
                         archive_path="/tmp/a.db")
    cls_line = [line for line in out.splitlines() if line.startswith("cls ")][0]
    assert "fragile" in cls_line


def test_a_referenced_article_does_not_pretend_to_be_its_own_source():
    """Four claims nobody checked: the source, the language, the date, and that
    the outlet publishes headlines only. A model reading it would answer "per
    the Russian channel ai_newz, published on the 26th" about an Alibaba blog
    post it linked."""
    out = render_read([row(via="link", first_source="ai_newz", lang="ru", body=None,
                           title="Вышла Qwen 3.8", url="https://qwen.ai/blog?id=q3",
                           date_exact=0, sources="ai_newz")], requested=["a3f9c2e1"])
    assert "linked" in out, "it has to say the row arrived through a link"
    assert "not from its own feed" in out
    assert "headlines only" not in out, "qwen.ai does publish text; nobody fetched it"
    assert "ai_newz" in out, "and name who linked it"


# ── internal consistency ─────────────────────────────────────────────────────
#
# Every assertion below compares one region of a rendered payload against
# another region of the SAME payload. None of them names an expected value, so
# none needs touching when the format, the sources or the data change — and
# that is the whole point. The defects they catch all had one shape: a fix
# landed in the code and the sentence describing it stayed behind, while the
# test covering that area asserted only that the label was present.
# `assert "CUT" in out` is equally true of `CUT cls=25/60` and `CUT cls=1/60`,
# so it defended the bug instead of catching it.

import re

# An item line: "a3f9c2e1 07:12 title", or "~a3f9c2e1 07:12 title" when the
# date is the capture time. Deliberately does not match a day separator
# ("-- 08-30", no HH:MM), a block heading, a header line, or an inlined body
# (three leading spaces).
_ITEM = re.compile(r"^~?\S+ \d{2}:\d{2} \S", re.M)
_BLOCK = re.compile(r"^## (\S+) .*? (\d+)/(\d+)\s*$", re.M)
_CUT = re.compile(r"^CUT\s+(.*?)\s\s+\(newest kept\)\s*$", re.M)
_PAIR = re.compile(r"(\S+)=(\d+)/(\d+)")
_ALLOWANCE = re.compile(r"at most (\d+) per source")


def printed_items(out):
    """How many dispatch lines the payload actually contains."""
    return len(_ITEM.findall(out))


def block_counts(out):
    """{source: (shown, total)} straight off the `## source` headings."""
    return {m[0]: (int(m[1]), int(m[2])) for m in _BLOCK.findall(out)}


def test_the_headline_count_is_what_was_actually_printed():
    """A count computed before the budget trim and printed after it.

    The word in the search header is literally "shown", so a model reads
    "165 shown" and reasons over the seventeen lines it can see — a small
    sample taken for a broad one, which is the single failure this module
    exists to prevent. It fired on the default call: 8000 tokens is not enough
    for thirty days of one common term.
    """
    rows = [row(id=f"{i:012x}", source=s, source_total=40, title="x" * 120)
            for s in ("a", "b", "c", "d", "e") for i in range(40)]

    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        limit_per_source=25, max_tokens=400)
    counts = re.search(r"\| (\d+) of (\d+) items \|", out)
    assert counts, "the header must say both what it printed and what the window held"
    assert int(counts.group(1)) == printed_items(out), (
        f"header claims {counts.group(1)} items, payload contains {printed_items(out)}")

    out = render_search(rows, query="q", since="s", days=30,
                        archive_start="2020-01-01", archive_items=10, max_tokens=400)
    head = int(re.search(r"\| (\d+) shown", out).group(1))
    assert head == printed_items(out), (
        f'header claims {head} "shown", payload contains {printed_items(out)}')


def test_the_headline_states_the_window_total_not_only_what_it_kept():
    """"117 items" was what got printed; 910 were in the window.

    Asked how much moved today, a model quoted 117. The real figure existed
    only scattered across the CUT line, and only for the sources that were cut.
    """
    rows = [row(id=f"{i:012x}", source=s, source_total=40)
            for s in ("a", "b") for i in range(25)]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        limit_per_source=25)

    counts = re.search(r"\| (\d+) of (\d+) items \|", out)
    assert counts, "the header must say both what it printed and what the window held"
    shown, window = map(int, counts.groups())
    assert shown == printed_items(out)
    assert window == sum(t for _, t in block_counts(out).values())


def test_the_cut_line_agrees_with_the_blocks_it_summarises():
    """CUT is the line a model reads to decide whether it has seen enough.

    Built before the budget loop and never rebuilt, it announced 25 of 60 above
    a block showing 1 of 60. The error is optimistic, so the model stops
    looking — and every per-source figure in the line is wrong at once.
    """
    rows = [row(id=f"{i:012x}", source=s, source_total=60, title="x" * 120)
            for s in ("a", "b", "c") for i in range(25)]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        limit_per_source=25, max_tokens=400)

    blocks = block_counts(out)
    declared = _CUT.search(out)
    assert declared, "a payload this heavily cut must declare the cut"
    for source, shown, total in _PAIR.findall(declared.group(1)):
        assert blocks[source] == (int(shown), int(total)), (
            f"CUT says {source}={shown}/{total}, block says "
            f"{blocks[source][0]}/{blocks[source][1]}")


def test_the_announced_allowance_is_the_one_actually_applied():
    """"at most 0 per source" printed above blocks showing 1.

    `_cap_per_source` was changed to keep one placeholder row per source so no
    heading could vanish; the label kept printing the raw allowance. The safe
    reading of 0 is "nothing was kept", and something was — the only
    pessimistic lie in the output, and still a lie.
    """
    rows = [row(id=f"{i:012x}", source=s, source_total=9, title="x" * 200)
            for s in ("a", "b", "c", "d", "e", "f", "g", "h") for i in range(9)]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        max_tokens=50)

    announced = int(_ALLOWANCE.search(out).group(1))
    applied = max(n for n, _ in block_counts(out).values())
    assert announced == applied, f"announces {announced}, applies {applied}"


def test_the_coverage_note_does_not_contradict_the_date_above_it():
    """"oldest 2015-12-11" with "It holds nothing from before its first run" one
    line below it.

    Deep feeds carry their own history into the first fetch — 2,094 of 4,242
    archived items predate the server. The model got two readings and both were
    bad: either it has been running since 2015, or the dates cannot be trusted.
    """
    out = render_search([], query="q", since="s", days=7,
                        archive_start="2015-12-11", archive_items=4242)
    assert "nothing from before its first run" not in out
    assert "2015-12-11" in out


def test_an_item_with_no_body_says_nothing_about_its_source():
    """`!! this source publishes headlines only` fired on a per-ITEM fact and
    asserted a per-SOURCE property.

    Eleven of nineteen sources ship bodies for some items and not others —
    openai carries one in 91% of its items, and the line would have appeared 106
    times telling the model openai has none.
    """
    out = render_read([row(body=None, body_src=None)], requested=["a3f9c2e1"])
    assert "body=none" in out
    assert "publishes headlines only" not in out


def test_the_no_adapter_note_appears_only_when_a_source_has_none():
    """Printed unconditionally while every kind was already in POLLABLE, so it
    described an empty set on every call. The `fragile` note directly below it
    is guarded; this one was not, and a model can file a real silence under
    "that one is never polled"."""
    from cablegram.poll import POLLABLE
    from cablegram.sources import SOURCES

    out = render_sources(health={}, archive_items=0, archive_start="-",
                         archive_path="/tmp/a.db")
    if all(s.kind in POLLABLE for s in SOURCES):
        assert "no adapter yet" not in out
