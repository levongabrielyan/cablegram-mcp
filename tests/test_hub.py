"""The Hugging Face hub: where open weights land, as opposed to blogged about.

The blog was already in the catalogue and is not the same source. DeepSeek
published a model on a Tuesday and none of the nineteen sources saw it; the
archive had zero mentions of Qwen3.8-27B while it sat at 13,412 likes.
"""

import json
import pathlib

import pytest

from cablegram.hub import models_url, parse_models

SAMPLE = json.loads((pathlib.Path(__file__).parent / "samples" / "hub_models.json").read_text())


def test_a_model_becomes_an_entry_pointing_at_its_page():
    entries = parse_models(SAMPLE)
    assert entries
    first = entries[0]
    assert first.title == "Qwen/Qwen3.8-Flash-Next"
    assert first.url == "https://huggingface.co/Qwen/Qwen3.8-Flash-Next"
    assert first.published is not None


def test_the_counts_the_ordering_was_made_of_travel_with_the_entry():
    """This endpoint is ordered by trend and the server ranks nothing itself, so
    the numbers behind somebody else's ordering have to be visible rather than
    expressed as a position in a list."""
    body = parse_models(SAMPLE)[0].body
    assert "likes" in body and "downloads" in body


def test_the_figure_labelled_as_the_ordering_is_the_one_that_orders():
    """`sort=likes7d` names a sort, not the field it sorts on. The entries
    carried `likes`, described as what the ordering was made of, and likes are
    not what this list is ordered by: measured over the live top fifty,
    trendingScore is monotone in 50 rows of 50 and likes is monotone in none —
    position 3 shows 13,421 likes above position 0's 4,473.

    A model reading a bigger number three lines below a smaller one has two
    conclusions available and both are false: the list is sorted wrong, or the
    count means nothing.

    Names no expected value: whatever figure the body calls the ordering must
    fall down the list the endpoint returned, which is the only property that
    makes it the ordering.
    """
    import re

    printed = [re.search(r"trend (\d+) \(the ordering\)", e.body)
               for e in parse_models(SAMPLE)]
    assert all(printed), "every entry has to carry the figure it was ordered on"
    values = [int(m.group(1)) for m in printed]
    assert values == sorted(values, reverse=True), (
        f"the entries print {values} as the ordering, in the order the endpoint "
        f"returned them; a figure that does not descend is not what ordered it")


def test_the_two_counts_are_labelled_with_their_own_periods():
    """`downloads` is thirty days and `likes` is every like the repo ever got.
    One sentence carrying both under "in the last month" read as though the
    period applied to the pair."""
    body = parse_models(SAMPLE)[0].body
    assert "likes all-time" in body and "downloads in 30d" in body


def test_the_url_asks_for_the_only_ordering_that_returns_anything_real():
    """Measured against the live endpoint: sort=createdAt returns
    `bboeun/Mistral-7B-v0.1-SD-S-reffix-30k-merged`, sort=lastModified returns
    `ElMusk/fun08`. Both date orderings are the firehose of every repo anyone
    touched, so a date-ordered page from here carries no signal at all."""
    assert "sort=likes7d" in models_url()
    assert "createdAt" not in models_url() and "lastModified" not in models_url()


def test_one_lab_can_be_asked_directly():
    """No Chinese lab publishes a feed and every one of them publishes weights,
    so this is the only place they speak for themselves."""
    assert "author=deepseek-ai" in models_url(author="deepseek-ai")


def test_a_private_model_is_not_an_entry():
    assert parse_models([{"id": "x/y", "private": True}]) == []


def test_a_model_with_no_usable_date_keeps_its_place_and_loses_its_date():
    """Inventing today's would file it in whatever window was asked for, and
    this endpoint is not ordered by time in the first place."""
    entry = parse_models([{"id": "x/y", "likes": 3}])[0]
    assert entry.published is None
    assert entry.title == "x/y"


def test_a_response_that_is_not_a_list_is_an_error_not_an_empty_hub():
    """The endpoint answers 200 either way, so a changed schema would otherwise
    read as a day on which nobody published anything."""
    with pytest.raises(ValueError, match="schema"):
        parse_models({"error": "nope"})


def test_somebody_elses_requantisation_is_not_a_publication():
    """The same release arriving many times. Measured over the live top fifty:
    seventeen rows declare a quantised base and sixteen come from a different
    organisation — ten of them rebuilds of one model, Qwen3.8-27B, which alone
    took 20% of the page.

    Not an editorial call. The tag `base_model:quantized:<org>/<model>` is
    written by the repo itself, and the only thing read off it is whether that
    organisation is the one publishing this.
    """
    rows = [
        {"id": "Qwen/Qwen3.8-27B", "trendingScore": 9, "createdAt": "2026-08-05T00:00:00Z"},
        {"id": "unsloth/Qwen3.8-27B-GGUF", "trendingScore": 8,
         "createdAt": "2026-08-30T00:00:00Z",
         "tags": ["base_model:Qwen/Qwen3.8-27B", "base_model:quantized:Qwen/Qwen3.8-27B"]},
    ]
    assert [e.title for e in parse_models(rows)] == ["Qwen/Qwen3.8-27B"]


def test_a_lab_quantising_its_own_model_is_still_the_lab_publishing():
    """Qwen/Qwen3.8-Flash-Next-FP8 is Qwen shipping a build of its own weights,
    and it was one of the seventeen. Filtering every `base_model:` tag instead
    would also have dropped thirteen finetunes, among them
    ibm-granite/granite-4.2-30b and tencent/WeMM-Embedding-9B — lab releases,
    which is the whole reason this source exists."""
    rows = [{"id": "Qwen/Qwen3.8-Flash-Next-FP8", "trendingScore": 5,
             "createdAt": "2026-08-30T00:00:00Z",
             "tags": ["base_model:quantized:Qwen/Qwen3.8-Flash-Next"]},
            {"id": "ibm-granite/granite-4.2-30b", "trendingScore": 4,
             "createdAt": "2026-08-30T00:00:00Z",
             "tags": ["base_model:finetune:ibm-granite/granite-4.2-base"]}]
    assert len(parse_models(rows)) == 2


def test_the_date_is_when_the_repo_was_made_not_when_it_was_touched():
    """A model release is a repo being created; adding a README to a model from
    March is not publishing it again. The preference was the other way round and
    changed nothing in practice — this endpoint returns lastModified in 0 of 50
    rows — which is what made it dangerous: the day the field appears in the
    default listing, every edited old model enters a 24h window as new and
    nothing in the code has moved."""
    entry = parse_models([{"id": "x/y", "createdAt": "2026-03-01T00:00:00.000Z",
                           "lastModified": "2026-08-31T00:00:00.000Z"}])[0]
    assert entry.published.isoformat().startswith("2026-03-01")


def test_the_row_count_is_what_arrived_not_what_survived():
    """`rows_returned` feeds the ceiling marker, which means "it gave all it
    could". Counting the entries instead let one filtered row switch it off."""
    from cablegram.hub import rows_returned

    rows = [{"id": "a/b", "trendingScore": 2, "createdAt": "2026-08-30T00:00:00Z"},
            {"id": "c/d", "private": True}]
    assert rows_returned(rows) == 2
    assert len(parse_models(rows)) == 1


def test_a_lab_is_asked_by_date_and_the_global_list_by_trend():
    """Two questions, two orderings, and the difference is the reason both
    exist.

    Globally, date returns the firehose of every repo anyone touched, so trend
    is the only ordering that returns anything real. Inside one organisation's
    namespace there is no firehose to rank away — it holds that organisation's
    repos by construction — so a lab is asked what it published, most recent
    first, and nothing is ranked at all.

    Measured over seven days: the six labs published thirteen models and the
    global top fifty carried five. A release reaches a popularity list only if
    enough people like it fast enough, and eight did not — among them
    tencent/ContextPilot in three sizes and both BF16 builds of GLM-5.3.
    """
    assert "sort=likes7d" in models_url()
    assert "sort=createdAt" in models_url(author="deepseek-ai")
    assert "likes7d" not in models_url(author="deepseek-ai")


def test_every_lab_source_carries_the_namespace_it_asks_for():
    """The catalogue entry and the request have to agree, or a source silently
    fetches the global list under a lab's name and its block fills with other
    people's models."""
    from cablegram.poll import _request_url
    from cablegram.sources import SOURCES

    labs = [s for s in SOURCES if s.kind == "hub" and s.author]
    assert len(labs) == 6, f"six labs publish weights and no feed; found {len(labs)}"
    for source in labs:
        url = _request_url(source, since=0)
        assert f"author={source.author}" in url, f"{source.id} asks for {url}"
        assert source.author in source.url, (
            f"{source.id} is catalogued at {source.url} and fetches "
            f"{source.author}; a reader following the catalogue URL lands "
            f"somewhere else")
