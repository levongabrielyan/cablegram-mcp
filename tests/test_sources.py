"""The catalogue is data, so these tests guard its shape, not its contents."""

import pytest

from cablegram.sources import SOURCES, by_id, resolve


def test_ids_are_unique():
    """A duplicate id would silently shadow a source in every lookup."""
    ids = [s.id for s in SOURCES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("source", SOURCES, ids=lambda s: s.id)
def test_every_source_is_well_formed(source):
    # Read off the poller rather than written out here. The hand-written set
    # went stale the moment a kind was added, which is the drift this file
    # exists to catch, committed by the file itself.
    from cablegram.poll import POLLABLE

    assert source.kind in POLLABLE, (
        f"{source.id} is kind {source.kind!r} and no adapter handles it, so it "
        f"would be listed and never fetched. That state is legal — render_latest "
        f"prints it as PENDING — so widen this only for a source deliberately "
        f"added ahead of its adapter.")
    assert source.lang in {"en", "zh", "ru"}
    assert source.url.startswith("https://")
    assert source.name


def test_the_three_languages_are_all_present():
    """English-only would defeat the purpose: the edge is in zh and ru."""
    assert {s.lang for s in SOURCES} == {"en", "zh", "ru"}


def test_selectors_accept_ids_tags_and_languages_together():
    """Nobody remembers twenty-one ids; asking for a theme is the common case."""
    assert {s.id for s in resolve(["hn"])} == {"hn"}
    assert {s.id for s in resolve(["zh"])} == {"cls", "qbitai", "kr36"}
    assert len(resolve(["telegram"])) == 6
    assert {s.id for s in resolve(["hn", "zh"])} == {"hn", "cls", "qbitai", "kr36"}


def test_no_selector_means_everything():
    assert resolve(None) == SOURCES
    assert resolve([]) == SOURCES


def test_unknown_selector_returns_nothing_rather_than_everything():
    """Failing open here would quietly answer a different question than the one asked."""
    assert resolve(["does-not-exist"]) == ()


def test_selectors_are_case_insensitive():
    assert resolve(["ZH"]) == resolve(["zh"])


def test_qbitai_url_has_no_trailing_slash():
    """/feed/ answers 301. Following redirects works, but one hop per call adds up."""
    assert by_id("qbitai").url.endswith("/feed")


def test_hacker_news_is_marked_as_an_aggregator():
    """Its links point elsewhere, so the destination host has to be shown."""
    assert by_id("hn").aggregator is True
    assert not by_id("openai").aggregator


def test_by_id_returns_none_for_unknown():
    assert by_id("nope") is None
