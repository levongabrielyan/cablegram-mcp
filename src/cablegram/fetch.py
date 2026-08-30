"""HTTP layer: fetch many sources at once without letting one drag the rest down.

Three rules shape everything here:

* **A dead source is UNKNOWN, never "nothing happened".** Failures are returned
  as values, not raised, so the caller can report which source went quiet
  instead of silently showing a shorter list.
* **Every response is capped.** Feeds are third-party input, so a slow or
  enormous one must cost a timeout, not the process.
* **Conditional requests.** Most feeds are unchanged between polls; sending the
  stored ETag turns those into a 304 with no body, which is both faster and
  the polite way to hammer someone else's server every few minutes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx2

__all__ = ["Fetched", "fetch_all", "fetch_one", "USER_AGENT"]

# Several sources answer differently — or not at all — to curl's default agent.
# Verified: qbitai and 36kr need this; cls.cn and t.me do not, but sending it
# everywhere costs nothing and removes a class of confusing failures.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0 Safari/537.36"
)

PER_SOURCE_TIMEOUT = 8.0
TOTAL_DEADLINE = 25.0
MAX_BYTES = 8 * 1024 * 1024  # cls.cn's largest observed response is 634 KB
RETRIES = 1  # one retry: the source reports transient resets, not blocking


@dataclass(slots=True)
class Fetched:
    """What came back, or why nothing did. Never both, never neither."""

    source_id: str
    ok: bool
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
                    return Fetched(source_id, ok=True, status=304, unchanged=True,
                                   etag=etag, last_modified=last_modified)
                if response.status_code != 200:
                    # 4xx is the source's answer, not a glitch: do not retry it.
                    if 400 <= response.status_code < 500:
                        return Fetched(source_id, ok=False, status=response.status_code,
                                       error=f"HTTP {response.status_code}")
                    last_error = f"HTTP {response.status_code}"
                    continue

                body = await _read_capped(response, MAX_BYTES)
                return Fetched(
                    source_id, ok=True, body=body, status=200,
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                )
        except ValueError as exc:  # over the cap
            return Fetched(source_id, ok=False, error=str(exc))
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:80]}"
            if attempt < RETRIES:
                await asyncio.sleep(0.5 * (attempt + 1))

    return Fetched(source_id, ok=False, error=last_error)


async def fetch_all(
    targets: list[tuple[str, str]],
    *,
    conditional: dict[str, tuple[str | None, str | None]] | None = None,
    deadline: float = TOTAL_DEADLINE,
) -> list[Fetched]:
    """Fetch every target concurrently, bounded by one global deadline.

    Sources that have not answered when the deadline passes are reported as
    timed out. The caller always receives one result per target, in the order
    given — a source never disappears from the list just because it failed.
    """
    conditional = conditional or {}

    async def run() -> list[Fetched]:
        # HTTP/1.1 on purpose: http2=True needs the `h2` package, and one extra
        # dependency is not worth a few milliseconds on eleven feeds.
        async with httpx2.AsyncClient() as client:
            tasks = [
                fetch_one(client, sid, url, etag=conditional.get(sid, (None, None))[0],
                          last_modified=conditional.get(sid, (None, None))[1])
                for sid, url in targets
            ]
            return await asyncio.gather(*tasks)

    try:
        return await asyncio.wait_for(run(), timeout=deadline)
    except (asyncio.TimeoutError, TimeoutError):
        return [Fetched(sid, ok=False, error=f"deadline {deadline:g}s exceeded")
                for sid, _ in targets]
