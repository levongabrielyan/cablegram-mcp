"""HTTP layer: fetch many sources at once without letting one drag the rest down.

Three rules shape everything here:

* **A dead source is UNKNOWN, never "nothing happened".** Failures are returned
  as values, not raised, so the caller can report which source went quiet
  instead of silently showing a shorter list.
* **Every response is capped.** Feeds are third-party input, so a slow or
  enormous one must cost a timeout, not the process.
* **Conditional requests, when there is an archive to compare against.** Most
  feeds are unchanged between polls; sending the stored ETag turns those into a
  304 with no body, which is both faster and the polite way to hammer somebody
  else's server every few minutes.

  Only in archive mode, and that is not an oversight. A 304 carries no body, so
  it is only usable by a caller that already has the items somewhere. Live mode
  builds a fresh in-memory archive per call and throws it away, so a 304 there
  would produce a source with zero items — reported as SILENT, which reads as
  "published nothing" and would be the exact lie this project exists to
  prevent. The default build therefore downloads in full on every call and is
  heavier on other people's servers than the hourly timer was. Fixing that means
  caching the responses, not sending the validators.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx2

__all__ = ["Fetched", "fetch_all", "fetch_one", "describe", "USER_AGENT"]

# Several sources answer differently — or not at all — to curl's default agent.
# Verified: qbitai and 36kr need this; cls.cn and t.me do not, but sending it
# everywhere costs nothing and removes a class of confusing failures.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0 Safari/537.36"
)

# Per operation, not per request: httpx restarts the read timeout on every
# chunk, so a server dripping a byte every seven seconds stays under it forever.
# What actually bounds a source is TOTAL_DEADLINE below.
PER_SOURCE_TIMEOUT = 8.0
TOTAL_DEADLINE = 25.0
MAX_BYTES = 8 * 1024 * 1024  # cls.cn's largest observed response is 634 KB
RETRIES = 1  # one retry: the source reports transient resets, not blocking


def describe(exc: BaseException) -> str:
    """A failure line that is worth storing.

    httpx raises ConnectError('') when a connection breaks under it, which
    reached source_state as the bare string "ConnectError: " — the only account
    of why a source went quiet, and it accounted for nothing. Seen on a real
    machine, across nine sources at once.
    """
    detail = str(exc).strip()
    cause = exc.__cause__ or exc.__context__
    if not detail and cause is not None:
        detail = str(cause).strip() or type(cause).__name__
    return f"{type(exc).__name__}: {detail[:80]}" if detail else type(exc).__name__


@dataclass(slots=True)
class Fetched:
    """What came back, or why nothing did. Never both, never neither."""

    source_id: str
    ok: bool
    url: str = ""  # which request this was: one source can be several
    body: bytes | None = None
    status: int | None = None
    error: str | None = None
    unchanged: bool = False  # 304: the source is alive and has nothing new
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


async def _read_capped(response: httpx2.Response, limit: int) -> bytes:
    """Stop reading past the cap instead of trusting Content-Length.

    A hostile or broken server can understate its length, so the cap is enforced
    on what actually arrives.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise ValueError(f"response exceeded {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_one(
    client: httpx2.AsyncClient,
    source_id: str,
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    headers: dict[str, str] | None = None,
) -> Fetched:
    """Fetch one source. Returns failures as values; raises nothing."""
    request_headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    if etag:
        request_headers["If-None-Match"] = etag
    if last_modified:
        request_headers["If-Modified-Since"] = last_modified
    if headers:
        request_headers.update(headers)

    last_error = "unknown"
    for attempt in range(RETRIES + 1):
        try:
            async with client.stream(
                "GET", url, headers=request_headers,
                follow_redirects=True, timeout=PER_SOURCE_TIMEOUT,
            ) as response:
                if response.status_code == 304:
                    return Fetched(source_id, ok=True, url=url, status=304, unchanged=True,
                                   etag=etag, last_modified=last_modified)
                if response.status_code != 200:
                    # 4xx is the source's answer, not a glitch: do not retry it.
                    if 400 <= response.status_code < 500:
                        return Fetched(source_id, ok=False, url=url,
                                       status=response.status_code,
                                       error=f"HTTP {response.status_code}")
                    last_error = f"HTTP {response.status_code}"
                    if attempt < RETRIES:
                        # A 5xx retried in the same millisecond is not a retry:
                        # whatever was overloaded still is.
                        await asyncio.sleep(0.5 * (attempt + 1))
                    continue

                body = await _read_capped(response, MAX_BYTES)
                return Fetched(
                    source_id, ok=True, url=url, body=body, status=200,
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                )
        except ValueError as exc:  # over the cap
            return Fetched(source_id, ok=False, url=url, error=str(exc))
        except Exception as exc:
            last_error = describe(exc)
            if attempt < RETRIES:
                await asyncio.sleep(0.5 * (attempt + 1))

    return Fetched(source_id, ok=False, url=url, error=last_error)


async def fetch_all(
    targets: list[tuple[str, str]],
    *,
    conditional: dict[str, tuple[str | None, str | None]] | None = None,  # by URL
    deadline: float = TOTAL_DEADLINE,
) -> list[Fetched]:
    """Fetch every target concurrently, bounded by one global deadline.

    The caller always receives one result per target, in the order given. A
    source never disappears from the list because it failed, and — the case
    that is easy to get wrong — a source that answered is never discarded
    because a different one hung. Cancelling the batch on the deadline would
    turn one slow feed into eleven dead ones, and nothing downstream could tell
    that apart from a real outage.
    """
    conditional = conditional or {}

    # HTTP/1.1 on purpose: http2=True needs the `h2` package, and one extra
    # dependency is not worth a few milliseconds on eleven feeds.
    async with httpx2.AsyncClient() as client:
        # Keyed by position, never by source id. cls.cn exposes five endpoints
        # and Telegram pages with ?before=, so two windows of one source is the
        # normal case; a dict would have the second silently replace the first,
        # report one result as the other's, and leave a task running against a
        # client that had already closed underneath it.
        tasks = []
        for source_id, url in targets:
            etag, last_modified = conditional.get(url, (None, None))
            tasks.append(asyncio.create_task(
                fetch_one(client, source_id, url, etag=etag, last_modified=last_modified)
            ))

        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=deadline)
            for task in pending:
                task.cancel()
            # Settle the cancellations before the client closes underneath them.
            await asyncio.gather(*pending, return_exceptions=True)

        results = []
        for (source_id, url), task in zip(targets, tasks, strict=True):
            if task.cancelled() or not task.done():
                results.append(Fetched(source_id, ok=False, url=url,
                                       error=f"deadline {deadline:g}s exceeded"))
                continue
            if exc := task.exception():
                results.append(Fetched(source_id, ok=False, url=url, error=describe(exc)))
                continue
            results.append(task.result())
        return results
