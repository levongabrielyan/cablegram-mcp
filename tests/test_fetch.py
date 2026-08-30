"""The fetcher is tested against a fake transport, never the live network.

What matters here is not that HTTP works — httpx handles that — but that a
failing source is reported rather than dropped. A source that vanishes from the
results looks exactly like a source with nothing new, and nobody would notice.
"""

import asyncio

import httpx2
import pytest

from cablegram.fetch import MAX_BYTES, USER_AGENT, fetch_all, fetch_one


def transport(handler):
    return httpx2.MockTransport(handler)


@pytest.fixture
def fake_network(monkeypatch):
    """Install a fake transport into the client fetch_all builds for itself.

    Passing a transport to fetch_one is not enough: fetch_all constructs its own
    AsyncClient, so a handler handed to it is never consulted and the test goes
    to the real network — passing on the DNS failures of a domain that does not
    exist, and passing just as well on a machine with no egress at all.
    """
    def install(handler):
        real = httpx2.AsyncClient

        def patched(*args, **kwargs):
            kwargs.setdefault("transport", httpx2.MockTransport(handler))
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx2, "AsyncClient", patched)

    return install


async def _one(handler, **kwargs):
    async with httpx2.AsyncClient(transport=transport(handler)) as client:
        return await fetch_one(client, "src", "https://e.com/feed", **kwargs)


def test_success_carries_body_and_validators():
    def handler(request):
        return httpx2.Response(200, content=b"<rss/>",
                               headers={"ETag": 'W/"abc"', "Last-Modified": "Sat, 30 Aug 2026 06:00:00 GMT"})

    result = asyncio.run(_one(handler))
    assert result.ok and result.body == b"<rss/>"
    assert result.etag == 'W/"abc"'
    assert result.last_modified.startswith("Sat, 30")


def test_browser_user_agent_is_always_sent():
    """Several Chinese sources answer differently to curl's default agent."""
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent")
        return httpx2.Response(200, content=b"ok")

    asyncio.run(_one(handler))
    assert seen["ua"] == USER_AGENT


def test_304_means_alive_with_nothing_new():
    """Not a failure and not an empty source: the distinction has to survive."""
    def handler(request):
        assert request.headers["if-none-match"] == 'W/"abc"'
        return httpx2.Response(304)

    result = asyncio.run(_one(handler, etag='W/"abc"'))
    assert result.ok and result.unchanged and result.body is None


def test_failure_is_returned_not_raised():
    """Raising would abort the whole poll because of one bad source."""
    def handler(request):
        raise httpx2.ConnectError("boom")

    result = asyncio.run(_one(handler))
    assert result.ok is False
    assert "ConnectError" in result.error


def test_client_errors_are_not_retried():
    """A 404 is the source's answer. Retrying it wastes the deadline."""
    calls = []

    def handler(request):
        calls.append(1)
        return httpx2.Response(404)

    result = asyncio.run(_one(handler))
    assert result.ok is False and result.status == 404
    assert len(calls) == 1


def test_server_errors_are_retried_once():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx2.Response(503) if len(calls) == 1 else httpx2.Response(200, content=b"ok")

    result = asyncio.run(_one(handler))
    assert result.ok and result.body == b"ok"
    assert len(calls) == 2


def test_oversized_response_is_refused():
    """Enforced on bytes received, not on Content-Length, which can lie."""
    def handler(request):
        return httpx2.Response(200, content=b"x" * (MAX_BYTES + 1))

    result = asyncio.run(_one(handler))
    assert result.ok is False and "exceeded" in result.error


def test_every_target_gets_a_result_even_when_it_fails(fake_network):
    """The caller must never have to guess which source went missing.

    This asserted only the order and the length, which held with both sources
    down — and both were, because the handler below was never installed and the
    requests went to the real network. It now checks what its name claims.
    """
    def handler(request):
        if "bad" in str(request.url):
            raise httpx2.ConnectError("down")
        return httpx2.Response(200, content=b"ok")

    fake_network(handler)
    results = asyncio.run(fetch_all([("good", "https://e.com/a"),
                                     ("bad", "https://e.com/bad")]))

    assert [r.source_id for r in results] == ["good", "bad"]
    assert results[0].ok and results[0].body == b"ok"
    assert results[1].ok is False and "ConnectError" in results[1].error


def test_a_failed_result_still_names_its_source_and_says_why():
    """Was parametrised over `ok`, which asserted that False is not None."""
    def handler(request):
        return httpx2.Response(500)

    result = asyncio.run(_one(handler))
    assert result.source_id == "src"
    assert result.ok is False
    assert "500" in result.error


# ── the deadline must cost the slow source, not the whole poll ───────────────

def test_a_hanging_source_does_not_take_the_others_with_it(fake_network):
    """The failure mode the deadline exists for is the source that never answers.

    Cancelling the whole batch turns one slow feed into eleven dead ones, and
    the caller cannot tell the difference — it reports a total outage on a day
    when ten sources were fine.
    """
    async def handler(request):
        if "slow" in str(request.url):
            await asyncio.sleep(30)
        return httpx2.Response(200, content=b"ok")

    fake_network(handler)
    results = asyncio.run(fetch_all(
        [("good", "https://e.com/a"), ("slow", "https://e.com/slow"),
         ("also_good", "https://e.com/b")],
        deadline=1.0,
    ))
    by_id = {r.source_id: r for r in results}

    assert by_id["good"].ok and by_id["good"].body == b"ok"
    assert by_id["also_good"].ok, "a healthy source must survive a slow neighbour"
    assert by_id["slow"].ok is False and "deadline" in by_id["slow"].error
    assert [r.source_id for r in results] == ["good", "slow", "also_good"]


def test_the_same_source_twice_gets_two_independent_results(fake_network):
    """cls.cn exposes five endpoints and Telegram pages with ?before=, so asking
    for two windows of one source is the normal case, not an edge case.

    Keying the tasks by source_id made the second overwrite the first: one
    request was dropped, its result reported as another's, and its task left
    running against a client that had already closed underneath it.
    """
    def handler(request):
        return httpx2.Response(200, content=str(request.url).encode())

    fake_network(handler)
    results = asyncio.run(fetch_all([("cls", "https://e.com/subject/1321"),
                                     ("cls", "https://e.com/subject/1556")]))
    assert [r.body for r in results] == [b"https://e.com/subject/1321",
                                         b"https://e.com/subject/1556"]


def test_an_exception_with_no_message_still_says_something():
    """httpx raises ConnectError('') when the connection breaks underneath it —
    seen on a real machine, where nine sources reported the bare string
    "ConnectError: " and there was nothing to diagnose from.

    source_state keeps this text, and it is the only account of why a source
    went quiet. An error with no message is barely better than no error.
    """
    def handler(request):
        raise httpx2.ConnectError("") from BrokenPipeError()

    result = asyncio.run(_one(handler))
    assert result.ok is False
    assert result.error.startswith("ConnectError")
    assert "BrokenPipeError" in result.error, "the cause is all there is here"


def test_a_message_is_kept_when_there_is_one():
    def handler(request):
        raise httpx2.ConnectError("nodename nor servname provided")

    result = asyncio.run(_one(handler))
    assert "nodename" in result.error
