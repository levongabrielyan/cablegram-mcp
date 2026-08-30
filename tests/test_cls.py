"""cls.cn: a signed internal API, and the source with the most to lose.

It runs days ahead of the English-language outlets, and it holds 3.34 days at
rn=100 with no way to page backwards — so a parser bug here does not cost a
request, it costs the window.

Everything is checked against a saved response from the live endpoint. Three
real items: a telegram whose headline sits in brackets, a telegram without
them, and an article.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cablegram.cls import CLS_BASE, parse_response, signed_url

SAMPLE = json.loads((Path(__file__).parent / "samples" / "cls_subject.json").read_text())


# ── the signature, which is the whole access ────────────────────────────────

def test_the_signature_matches_the_verified_constant():
    """md5(sha1(query_string)), the hexdigest of the sha1 as text, not its bytes.

    With only the three base parameters it is a known constant, captured from a
    working request — so this pins the algorithm without hitting the network.
    """
    url = signed_url("/api/subject/1321/article")
    assert "sign=10bdc2da403ad7415bb639aa22dc6cd3" in url


def test_parameters_are_sorted_so_the_signature_is_reproducible():
    a = signed_url("/api/subject/1321/article", {"Subject_Id": 1321, "rn": 100})
    b = signed_url("/api/subject/1321/article", {"rn": 100, "Subject_Id": 1321})
    assert a == b


def test_the_signature_ignores_the_path():
    """Verified against the live endpoint: two different paths, same parameters,
    same sign. Worth pinning — an implementation that folded the path in would
    still work for a while and then not."""
    one = signed_url("/v2/article/hot/list").split("sign=")[1]
    two = signed_url("/api/cache").split("sign=")[1]
    assert one == two


# ── the failure that looks like success ─────────────────────────────────────

def test_a_rejected_signature_is_an_error_despite_http_200():
    """cls.cn answers a bad signature with HTTP 200 and errno=10012. Anything
    built on raise_for_status() takes it as a good response with no items —
    the source going silent, reported as a quiet day."""
    with pytest.raises(ValueError, match="10012|signature"):
        parse_response({"errno": 10012, "msg": "签名错误", "data": []})


def test_errno_is_compared_across_types():
    """errno arrives as int 0 on success and as the string '10012' on failure,
    so int(errno) raises exactly in the case it exists to detect."""
    with pytest.raises(ValueError, match="10012|signature"):
        parse_response({"errno": "10012", "msg": "签名错误", "data": []})


def test_a_missing_errno_is_not_assumed_to_be_success():
    with pytest.raises(ValueError):
        parse_response({"data": []})


def test_success_carries_no_msg_requirement():
    """/api/cache returns errno and data with no msg at all."""
    assert parse_response({"errno": 0, "data": []}) == []


# ── the real payload ────────────────────────────────────────────────────────

def test_every_item_becomes_an_entry():
    assert len(parse_response(SAMPLE)) == 3


def test_the_headline_is_extracted_from_the_brackets():
    """article_title is not a title: for a telegram it holds the whole dispatch,
    median 241 characters and up to 617. The real headline sits inside 【】, and
    that extraction matched the article's own title field in 6 of 6 checks.

    Storing the raw field would put the body in item.title, in sighting.title
    and in the trigram index — 60,000 tokens for a page instead of 3,600.
    """
    entries = parse_response(SAMPLE)
    first = entries[0]
    assert first.title.startswith("中信证券")
    assert "【" not in first.title and "】" not in first.title
    assert len(first.title) < 60


def test_the_body_is_what_follows_the_headline():
    first = parse_response(SAMPLE)[0]
    assert first.body and "财联社" in first.body
    assert first.body != first.title


def test_a_telegram_without_brackets_keeps_its_whole_text_as_the_title():
    """Six of sixty-two carry no brackets, and are short enough that the text is
    the title in the article's own detail page too."""
    second = parse_response(SAMPLE)[1]
    assert second.title
    assert "【" not in second.title


def test_the_url_is_built_because_no_field_holds_it():
    """jump_url is an app scheme, share_url is a share landing page. The only
    address that opens is https://www.cls.cn/detail/{article_id}."""
    for entry in parse_response(SAMPLE):
        assert entry.url.startswith("https://www.cls.cn/detail/")
        assert entry.url.split("/")[-1].isdigit()


def test_timestamps_are_seconds_not_milliseconds():
    """article_time is Unix seconds UTC. Read as milliseconds every item lands
    in 1970 and the time window silently excludes the whole source."""
    entries = parse_response(SAMPLE)
    for entry in entries:
        assert 2020 < entry.published.year < 2100
        assert entry.published.tzinfo == timezone.utc


def test_an_item_missing_its_id_is_skipped_not_fatal():
    payload = {"errno": 0, "data": [{"article_title": "x", "article_time": 1788078787},
                                    SAMPLE["data"][0]]}
    assert len(parse_response(payload)) == 1


def test_an_item_missing_its_time_is_skipped():
    """Every other source can fall back to the capture time. Here the ordering
    is the only pagination there is, so an item with no timestamp would break
    the incremental stop as well as its own placement."""
    payload = {"errno": 0, "data": [{"article_id": 1, "article_title": "x"}]}
    assert parse_response(payload) == []


def test_data_can_be_a_dict_with_the_list_inside():
    """The subject endpoint returns data as a list; /api/cache and the roll list
    put it in data.roll_data. One adapter, both shapes."""
    payload = {"errno": 0, "data": {"roll_data": SAMPLE["data"]}}
    assert len(parse_response(payload)) == 3
