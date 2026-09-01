"""Assertions for the parts of the renderer a mutation walked straight through.

Every test here was written against one mutation that the suite let pass green
on 2026-08-31. The name is the wrong conclusion the mutation produces, never the
line of code it touches — and wherever the payload holds the same fact twice,
the assertion compares those two regions instead of naming a value.
"""

import re

import pytest

from cablegram.render import estimate_tokens, render_latest, render_read, render_search

ROW = {
    "id": "a3f9c2e1", "source": "qbitai", "lang": "zh", "title": "智谱发布GLM-5",
    "published": "2026-08-30T07:12:00Z", "date_exact": 1, "cross": 6,
    "source_total": 14, "target_host": None, "url": "https://qbitai.com/x",
    "body": "正文", "body_src": "description", "first_source": "qbitai",
}


def row(**over):
    return {**ROW, **over}


_ITEM = re.compile(r"^~?\S+ \d{2}:\d{2} \S", re.M)
_CROSS_ENTRY = re.compile(r"^(?:CROSS |      )(\w+) x(\d+)$", re.M)
_CROSS_MORE = re.compile(r"^\s+(\d+) shown of (\d+) repeated stories", re.M)
_BLOCK = re.compile(r"^## (\S+) .*? (\d+)/(\d+)\s*$", re.M)
_CUT = re.compile(r"^CUT\s+(.*?)\s\s+\(newest kept\)\s*$", re.M)
_PAIR = re.compile(r"(\S+)=(\d+)/(\d+)")


def printed_items(out):
    return len(_ITEM.findall(out))


def block_counts(out):
    return {m[0]: (int(m[1]), int(m[2])) for m in _BLOCK.findall(out)}


def source_line(out, source_id):
    return [l for l in out.splitlines() if l.startswith(f"{source_id} ")][0]


def test_a_source_with_no_adapter_is_named_rather_than_left_absent():
    """The reply used to carry a tally, `answering/total`, and it counted every
    PENDING source as healthy — overstating the coverage of every answer built
    on it, three lines above the payload that named them.

    The tally is gone: a reader can add up the blocks, DOWN, PENDING and
    SILENT for itself. What it cannot do is invent a source nobody mentioned,
    so the naming is what has to hold.
    """
    out = render_latest([], since="s", until="u", down={"cls": "HTTP403"},
                        sources_total=19, no_adapter=["ai_newz", "techsparks"])

    assert re.search(r"^DOWN  cls=", out, re.M)
    pending = re.search(r"^PENDING (.*?)  \(", out, re.M)
    assert pending and sorted(pending.group(1).split()) == ["ai_newz", "techsparks"]
    assert "sources" not in out.splitlines()[0], (
        "the first line states the window and nothing it would have to count")
def test_the_cut_line_leaves_out_a_source_that_was_not_cut():
    """`CUT b=3/3` asserts that more exist, from the line built to declare that.

    The description tells the model that a declared cut means "raise
    limit_per_source or narrow the window". Printed over a source that served
    everything it had, it buys a second call that returns exactly the same
    payload — and every existing fixture cut every source, so the comparison
    had nothing to disagree with.
    """
    rows = ([row(id=f"a{i:011x}", source="a", source_total=60) for i in range(25)]
            + [row(id=f"b{i:011x}", source="b", source_total=3) for i in range(3)])
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        limit_per_source=25)

    declared = _CUT.search(out)
    named = ({s: (int(shown), int(total))
              for s, shown, total in _PAIR.findall(declared.group(1))}
             if declared else {})
    was_cut = {s: (shown, total) for s, (shown, total) in block_counts(out).items()
               if shown < total}
    assert named == was_cut, f"CUT declares {named} and the blocks were cut {was_cut}"


def test_a_search_declares_no_cut_over_a_source_it_served_whole():
    """The same line in the tool whose entire description is about not reading a
    small number as an answer. Here a false CUT says the opposite of a false
    absence and is just as unfalsifiable from inside the reply."""
    rows = ([row(id=f"a{i:011x}", source="a", source_total=437) for i in range(3)]
            + [row(id=f"b{i:011x}", source="b", source_total=2) for i in range(2)])
    out = render_search(rows, query="AI", since="s", days=7,
                        archive_start="2020-01-01")

    declared = _CUT.search(out)
    named = ({s: (int(shown), int(total))
              for s, shown, total in _PAIR.findall(declared.group(1))}
             if declared else {})
    was_cut = {s: (shown, total) for s, (shown, total) in block_counts(out).items()
               if shown < total}
    assert named == was_cut, f"CUT declares {named} and the blocks were cut {was_cut}"


# ── the budget ──────────────────────────────────────────────────────────────

def test_a_chinese_headline_is_not_priced_as_if_it_were_english():
    """Four characters per token is the Latin rate. Chinese and Russian run
    close to one.

    Every trim in this module is decided by this function, so pricing a Chinese
    payload at a quarter of its cost does not produce a smaller reply — it
    produces one the caller's own limit truncates, which is the exact silent
    loss the marking conventions all exist to prevent.
    """
    assert estimate_tokens("智" * 200) > estimate_tokens("a" * 200) * 3


def test_the_budget_line_counts_the_items_that_survived_it():
    """"(72/72 items)" above eight.

    The sentence exists to tell a caller how much of the answer the limit cost
    them. Reporting the untrimmed figure reports that it cost nothing, in the
    one line written to say otherwise.
    """
    rows = [row(id=f"{i:012x}", source=s, title="x" * 200, source_total=9)
            for s in ("a", "b", "c", "d", "e", "f", "g", "h") for i in range(9)]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        max_tokens=50)

    kept, asked = map(int, re.search(r"\((\d+)/(\d+) items\)", out).groups())
    assert kept == printed_items(out), (
        f"the budget line says it kept {kept} and the payload holds "
        f"{printed_items(out)}")
    assert asked == len(rows)


# ── wire_read ───────────────────────────────────────────────────────────────

def test_an_item_several_sources_carried_names_all_of_them():
    """It used to read `x2[hub]` — a count and a list contradicting each other
    inside one line, where a model has to pick one and both readings are wrong.

    The count is gone with the CROSS block: it was the length of the list
    printed beside it. The list stays, because on a single item somebody asked
    to read, who else carried it is the one thing that is not on the page.
    """
    out = render_read([row(sources="hn,hub", source="hub")],
                      requested=["a3f9c2e1"])

    named = re.search(r" \[([^\]]+)\]", out)
    assert named, "a story two sources carried has to name both"
    assert sorted(named.group(1).split(",")) == ["hn", "hub"]


def test_a_story_only_one_source_carried_is_not_marked_at_all():
    """`[qbitai]` on every line turns the mark into decoration: if everything
    carries it, it distinguishes nothing. The heading already names the source
    that carried a single-source item."""
    out = render_read([row(id="a" * 12, sources="qbitai"),
                       row(id="b" * 12, sources="qbitai,hn")],
                      requested=["a" * 12, "b" * 12])

    heads = {l.split()[1]: l for l in out.splitlines() if l.startswith("## ")}
    assert "[" not in heads["a" * 12], "one source is not a repeat"
    assert "[qbitai,hn]" in heads["b" * 12]


def test_a_borrowed_date_is_marked_uncertain_like_any_other():
    """The `!!` line below says the date belongs to whoever linked the article.
    The date itself is printed unmarked on the heading, which is the line a
    model quotes — and nothing on it says the two facts are related."""
    out = render_read([row(via="link", date_exact=1, first_source="ai_newz",
                           sources="ai_newz")], requested=["a3f9c2e1"])

    head = [l for l in out.splitlines() if l.startswith("## ")][0]
    assert f"~{ROW['published']}" in head, (
        f"the date on this heading is the linking post's, not the article's:\n"
        f"{head}")


# ── wire_sources ────────────────────────────────────────────────────────────

NOW = "2026-08-31T09:00:00Z"


def test_a_source_that_returned_everything_it_could_says_there_may_be_more():
    """cls.cn cannot page backwards, so what is past its hundred is gone for
    good. This marker is the only warning that it happened, and nothing else in
    any reply carries the fact — `100 new` reads exactly like a busy day."""
    out = render_sources_now({"at_ceiling": NOW, "last_write": NOW})
    assert "AT CEILING" in source_line(out, "cls")


def test_a_ceiling_reached_on_an_earlier_pass_is_not_claimed_for_this_one():
    """The flag is a fact about one pass. Read as a standing property it turns
    into a permanent tombstone: once true, the source warns of truncation on
    every quiet day for the rest of the session."""
    out = render_sources_now({"at_ceiling": "2026-08-30T09:00:00Z",
                              "last_write": NOW})
    assert "AT CEILING" not in source_line(out, "cls"), (
        "this pass did not reach the ceiling; the flag is from an earlier one")


def test_entries_that_could_not_be_archived_are_counted_beside_the_source():
    """A pass that downloaded fine and archived half of it looks identical to a
    quiet day: `record_write` stores the number precisely so this line can print
    it, and the poller deliberately does not mark the source DOWN for it. If
    this line does not carry the count, nothing anywhere does."""
    out = render_sources_now({"wrote_failed": 3})
    assert "3 entries unarchived" in source_line(out, "cls")


def test_a_source_this_call_did_not_ask_for_is_not_reported_as_unusable():
    """Nothing is kept between calls, so a source with no row either answered
    this one or was not among the ones it asked for. "never polled" reads as a
    source nobody can use, and it would be printed for most of the catalogue on
    most calls — a real silence filed under machinery that does not exist."""
    from cablegram.render import render_sources

    out = render_sources(health={"cls": {"last_ok": NOW, "last_try": NOW}})
    line = source_line(out, "qbitai")
    assert "never polled" not in line, (
        f"qbitai was not asked for; it is not a source that cannot be polled:\n"
        f"{line}")


def test_the_newest_item_and_the_last_reply_are_two_different_facts():
    """A frozen feed answers 200 for ever. `OK` beside an old `newest` is the
    shape of a source nobody has noticed died — productradar answers fine and
    its newest item is 25 days old — and the two columns are the only place in
    the whole surface where that shows. Filled from the same field they agree by
    construction and every dead source reads as current."""
    from cablegram.render import render_sources

    out = render_sources(health={"cls": {"last_ok": NOW, "last_try": NOW,
                                         "newest": "2026-08-06T10:00:00Z"}})
    line = source_line(out, "cls")
    assert "2026-08-06" in line, f"the date of the newest item it holds:\n{line}"
    assert line.count(NOW[:10]) == 1, (
        f"the reply date is printed in both columns, so a feed frozen in "
        f"August reads as current:\n{line}")


def test_a_source_quiet_for_seven_hours_is_marked_stale_and_one_quiet_for_five_is_not():
    """`OK` beside a date nobody compares to today is how a dead timer looks
    like healthy sources. Both directions, because a threshold nothing tests
    from below can be raised until it never fires."""
    from datetime import datetime, timedelta, timezone
    from cablegram.render import render_sources

    def at(hours):
        stamp = (datetime.now(timezone.utc)
                 - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return render_sources(health={"cls": {"last_ok": stamp, "last_try": stamp}})

    assert "STALE" in source_line(at(7), "cls")
    assert "STALE" not in source_line(at(5), "cls")


def render_sources_now(extra):
    from cablegram.render import render_sources

    return render_sources(health={"cls": {"last_ok": NOW, "last_try": NOW, **extra}})


# ── dates and bodies in the listing ─────────────────────────────────────────

def test_two_days_get_two_separators_and_each_names_a_whole_day():
    """The separator is the only place a date appears in a listing — item lines
    carry hh:mm and nothing else. Truncated, the 30th and the 31st print the
    same one, so a day's worth of dispatches is filed under its neighbour and
    every timestamp under it still looks right."""
    rows = [row(id="a" * 12, published="2026-08-30T07:12:00Z"),
            row(id="b" * 12, published="2026-08-31T09:30:00Z")]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19)

    days = re.findall(r"^-- (.+)$", out, re.M)
    assert len(days) == 2, f"two days, {len(days)} separators: {days}"
    for day in days:
        assert re.fullmatch(r"\d{2}-\d{2}", day), (
            f"'-- {day}' does not name a day")


def test_an_inlined_body_cannot_be_read_as_a_dispatch_line():
    """Every line of a body is indented, not only the first.

    A body's second line sat exactly where a dispatch line sits, so a stored
    body containing "reuters 09:30 Alibaba anuncia Qwen 4" is indistinguishable
    from an item with that id, at that time, from that source — to a model
    reading the payload, and to anything counting it.
    """
    out = render_latest([row(body="Primera línea\nreuters 09:30 Alibaba anuncia Qwen 4",
                             body_src="description")],
                        since="s", until="u", down={}, sources_total=19,
                        detail="full")
    assert printed_items(out) == 1, (
        f"one dispatch was rendered and the payload reads as "
        f"{printed_items(out)}:\n{out}")


def test_the_declared_body_length_is_the_length_of_the_body_it_precedes():
    """wire_read's description tells the model to judge from N: under a few
    hundred characters, cite it as an excerpt and never as the article. A wrong
    N is a wrong judgement with nothing to check it against — the text is right
    there and the number describing it is not."""
    body = "x" * 137
    out = render_latest([row(body=body, body_src="description")], since="s",
                        until="u", down={}, sources_total=19, detail="full")

    line = [l for l in out.splitlines() if l.lstrip().startswith("[description ")][0]
    declared = int(re.search(r"\[description (\d+)c\]", line).group(1))
    served = line.split("] ", 1)[1]
    assert declared == len(served), (
        f"the marker declares {declared} characters and {len(served)} follow it")
