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
