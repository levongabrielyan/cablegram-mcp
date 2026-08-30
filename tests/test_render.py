"""The output is the product: it is all the model ever sees.

Every convention here exists because its absence causes a specific wrong
conclusion — a dead source read as a quiet one, a truncated excerpt read as an
article, a capture time read as a publication time. The tests are named after
the wrong conclusion, not after the format.
"""

from cablegram.render import render_latest, render_read, render_search, render_sources

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


def test_a_budget_cut_says_so():
    """Silently returning half is the failure this server exists to avoid."""
    rows = [row(id=f"{i:08x}", title="x" * 200) for i in range(200)]
    out = render_latest(rows, since="s", until="u", down={}, sources_total=19,
                        max_tokens=500)
    assert "BUDGET" in out
    assert len(out) < 12000


def test_a_teaser_is_never_presented_as_the_article():
    """Unmarked, the model reports conclusions drawn from two sentences, and
    nobody ever notices because nobody reads this."""
    out = render_read([row(sources="qbitai,hn", title="智谱发布GLM-5")], requested=["a3f9c2e1"])
    assert "body=teaser" in out
    assert "NOT the full article" in out


def test_a_full_body_carries_no_warning():
    out = render_read([row(body_src="content:encoded", sources="qbitai")],
                      requested=["a3f9c2e1"])
    assert "NOT the full article" not in out


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
