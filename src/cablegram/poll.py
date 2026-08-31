"""One pass over the sources: fetch, parse, archive, and record what happened.

This is what makes the archive worth having. RSS exposes a window of days and
no history of its own, and cls.cn's is 3.34 days with no way to page backwards,
so an hour that is never polled is an hour no endpoint will ever serve again.

Everything here is built to keep going. A source that times out, a feed that
will not parse, a batch that half-writes: none of them stops the rest, and each
one leaves its reason in source_state — because a poll that quietly achieves
nothing is indistinguishable from a quiet day.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .cls import (MAX_ROWS as CLS_MAX, feed_url as cls_feed_url,
                  parse_response as parse_cls, rows_returned as cls_rows)
from .hn import (MAX_ROWS as HN_MAX, parse_search as parse_hn,
                 rows_returned as hn_rows, search_url as hn_search_url)
from .hub import (MAX_ROWS as HUB_MAX, models_url, parse_models,
                  rows_returned as hub_rows)
from .nextjs import parse_next_payload
from .telegram import channel_url, parse_channel
from .fetch import TOTAL_DEADLINE, Fetched, fetch_all
from .rss import parse_feed
from .sources import SOURCES, Source
from .store import (StoreReport, conditional_headers, record_attempt,
                    record_write, store_entries)

__all__ = ["poll_once"]

# Kinds with an adapter. The rest are listed and never fetched: handing their
# URLs to the RSS parser would file every one as a parse failure and bury the
# real ones among them.
POLLABLE = ("rss", "cls", "hn", "telegram", "hub", "nextjs")

# t.me resets the connection on the sixth request in a row. Measured: channels
# 3 to 6 failed with ECONNRESET while 1 and 2 came back fine, and three seconds
# apart all six succeed. It is not a 429 — the socket simply closes, so a
# handler reporting what it sees would file four healthy channels as dead.
TELEGRAM_GAP = 3.0


def _ceiling(source: Source) -> int:
    """Rows this source can return at most. Reaching it means there may be more.

    It matters most where it can least be recovered: cls.cn cannot page
    backwards at all, so anything past its hundred is gone.
    """
    if source.kind == "cls":
        return CLS_MAX
    if source.kind == "hn":
        return HN_MAX
    if source.kind == "hub":
        # models_url asks for exactly MAX_ROWS, so this source is truncated on
        # every single poll — and with 10**9 here it was the only one that could
        # never say so.
        return HUB_MAX
    return 10**9  # RSS feeds and Telegram pages have no comparable cap


def _request_url(source: Source, since: int) -> str:
    if source.kind == "cls":
        return cls_feed_url()
    if source.kind == "hn":
        # The whole ceiling, not the default 100: a 48-hour window was coming
        # back with three hours of stories, because the page size decided the
        # window. One request for a thousand costs the same as one for a
        # hundred, and the extra margin is what survives a poll that was missed.
        return hn_search_url(since=since, rows=HN_MAX)
    if source.kind == "telegram":
        return channel_url(source.id)
    if source.kind == "hub":
        return models_url()
    return source.url


def _mark_failed(db: sqlite3.Connection, fetched, source: Source, why: str) -> None:
    """Record a failure that happened after the download succeeded.

    Everything past the fetch — a rejected signature, a document that will not
    parse, a batch that half-writes — was stored in `wrote_failed` and read by
    nobody. cls.cn answers a bad signature with HTTP 200 and errno in the
    envelope, exactly as its own module documents, and the result was
    `wire_sources: cls … OK` beside `wire_latest: 1/1 sources | 0 items`. A
    model reads that as a quiet source.

    record_attempt(ok=False) already does the right thing: it leaves `last_ok`
    alone so the silence stays visible, keeps the validators, and writes the
    reason — which until now was caught and discarded.
    """
    record_attempt(db, replace(fetched, ok=False, url=source.url, error=why[:200]))


async def poll_once(
    db: sqlite3.Connection,
    sources: list[Source] | None = None,
    *,
    window_hours: int = 48,
    deadline: float | None = None,
) -> list[StoreReport]:
    """Fetch every source once and archive what came back.

    Returns one report per source attempted, in the order given, each carrying
    its state. A source that failed keeps its row: dropping it would return nine
    reports for eleven sources with nothing to say which two went missing, and
    that is the failure this whole project is built around.
    """
    targets = [s for s in (sources or SOURCES) if s.kind in POLLABLE]
    if not targets:
        return []


    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Telegram goes in its own pass, one at a time. Six channels at three
    # seconds apart is eighteen seconds, which would not fit inside the global
    # deadline the other sources share — and shortening the gap is what makes
    # them fail.
    channels = [s for s in targets if s.kind == "telegram"]
    targets = [s for s in targets if s.kind != "telegram"]
    # cls.cn needs a signature computed per request, so its URL is built here
    # rather than stored in the catalogue.
    # Both signed and windowed URLs are built per request rather than stored in
    # the catalogue: cls.cn needs a fresh signature, and Hacker News needs the
    # window in the query, because filtering after the fact would be filtering
    # whatever survived its 1,000-result ceiling.
    since = int((datetime.now(timezone.utc) - timedelta(hours=window_hours)).timestamp())
    conditional = conditional_headers(db)

    # A bound on the whole pass, not on each fetch. Telegram is given fifteen
    # seconds a channel and they run one at a time, so the theoretical worst
    # case was 25s for the main batch plus five 3s gaps plus six 15s channels =
    # 130s. Unbounded that is fine for a timer and unusable inside a tool call.
    started = time.monotonic()

    def left(default: float) -> float:
        """This step's own budget, or what is left of the pass, whichever is less.

        Returning the whole remainder handed the main batch 45s when its own
        TOTAL_DEADLINE is 25 — measured, `fetch_all(15 sources) deadline=45.0`.
        That bound is not decoration: httpx restarts its read timeout on every
        chunk, so a source dripping one byte every seven seconds is stopped by
        nothing else. And every second the batch spends above ~28 comes
        straight out of Telegram's share, which is what makes channels report
        `skipped: pass deadline reached` on a slow day.
        """
        if deadline is None:
            return default
        return min(default, max(0.0, deadline - (time.monotonic() - started)))

    requests = [(s.id, _request_url(s, since)) for s in targets]
    results = (await fetch_all(requests, conditional=conditional,
                               deadline=left(TOTAL_DEADLINE)) if targets else [])

    for index, channel in enumerate(channels):
        if index:
            await asyncio.sleep(left(TELEGRAM_GAP))
        budget = left(15.0)
        if deadline is not None and budget <= 0:
            # Out of time, and saying so beats a shorter list: a channel that
            # was never asked is not a channel with nothing to say.
            results.append(Fetched(channel.id, ok=False, url=_request_url(channel, since),
                                   error=f"skipped: {deadline:g}s pass deadline reached"))
        else:
            results += await fetch_all([(channel.id, _request_url(channel, since))],
                                       conditional=conditional, deadline=budget)
        targets = targets + [channel]

    reports: list[StoreReport] = []
    for source, fetched in zip(targets, results, strict=True):
        # The signature makes cls.cn's URL different every call, so the state is
        # keyed on the catalogue URL — otherwise source_state would grow a row
        # per poll and never match a stored validator.
        record_attempt(db, replace(fetched, url=source.url))

        # 304 means alive with nothing new. record_write is deliberately not
        # called: writing a zero over the last real result would erase what the
        # source actually carries.
        if not fetched.ok:
            reports.append(StoreReport(source.id, state="fetch-failed"))
            continue
        if fetched.unchanged:
            reports.append(StoreReport(source.id, state="unchanged"))
            continue

        # How many rows the endpoint sent, which is not how many survive parsing.
        # `at_ceiling` means "it returned all it could, so there may be more",
        # and measuring it on the entries made one dropped row switch it off:
        # cls returning its full hundred with one article missing a timestamp
        # reported at_ceiling=False, in the source that cannot page backwards,
        # where the marker is the only warning that something is gone for good.
        returned = None
        try:
            if source.kind == "cls":
                payload = json.loads(fetched.body)
                entries, returned = parse_cls(payload), cls_rows(payload)
            elif source.kind == "hn":
                payload = json.loads(fetched.body)
                entries, returned = parse_hn(payload), hn_rows(payload)
            elif source.kind == "hub":
                payload = json.loads(fetched.body)
                entries, returned = parse_models(payload), hub_rows(payload)
            elif source.kind == "nextjs":
                # The section URL, because the payload carries a slug and not a
                # link. One request per section, which is why /news and
                # /research are two sources rather than one with two URLs.
                entries = parse_next_payload(fetched.body, base=source.url)
            elif source.kind == "telegram":
                entries = parse_channel(fetched.body.decode("utf-8", "replace"),
                                        channel=source.id)
            else:
                entries = parse_feed(fetched.body)
        except Exception as exc:
            # Every exception, not the three that were foreseen. A float
            # `article_time` raises OverflowError, a null one TypeError, a hits
            # object that is not a list AttributeError — and any of them tore
            # down the whole pass: measured, nineteen pollable sources left
            # eight with recorded state and eleven with no trace at all.
            #
            # The download worked and the parse did not. Both facts matter, and
            # a source answering with broken XML is not a source with no news.
            report = StoreReport(source.id, state="unparseable", failed=1)
            record_write(db, report, url=source.url, at=now)
            _mark_failed(db, fetched, source, f"unparseable: {exc}")
            reports.append(report)
            continue

        if not entries:
            # A valid document with no entries is what a feed looks like the day
            # it changes format. Recorded as a plain success it is identical to a
            # source with no news — the silent failure this project exists to
            # prevent, in the case most likely to occur.
            report = StoreReport(source.id, state="parsed-empty", failed=1)
            record_write(db, report, url=source.url, at=now)
            _mark_failed(db, fetched, source,
                         "parsed-empty: valid document, no entries")
            reports.append(report)
            continue

        report = store_entries(db, source, entries, fetched_at=now)
        report.at_ceiling = (returned if returned is not None
                             else len(entries)) >= _ceiling(source)
        if report.at_ceiling:
            # Recorded in `meta`, which is (k, v) and cannot change shape.
            # source_state has no room for it and _seal refuses an archive whose
            # columns moved, so a new column would kill every archive in
            # existence to report one flag.
            with db:
                db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)",
                           (f"ceiling:{source.id}", now))
        # Deliberately no _mark_failed here, unlike the two paths above. Those
        # two archived nothing; this one archived most of it. Recording a failed
        # attempt made the source DOWN — record_attempt(ok=False) writes
        # last_error with the same fetched_at as the success, so last_try >=
        # last_ok holds — and the reply came out as
        #
        #   | 2 of 2 items | 0/1 sources
        #   DOWN  qbitai=1 of 3 entries could not be archived
        #   ## qbitai zh community 2/2
        #
        # Zero coverage declared directly above 66% of it, and `answering` is
        # what the description tells the model to report as what it did not
        # read. The count is already kept in wrote_failed and wire_sources
        # already prints it beside OK as "1 entries unarchived", which is the
        # honest shape: the source answered, and some of what it sent was not
        # storable.
        record_write(db, report, url=source.url, at=now)
        reports.append(report)

    return reports
