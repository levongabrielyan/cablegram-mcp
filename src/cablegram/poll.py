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

import json
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timezone

from .cls import feed_url as cls_feed_url, parse_response as parse_cls
from .fetch import fetch_all
from .rss import parse_feed
from .sources import SOURCES, Source
from .store import (StoreReport, conditional_headers, record_attempt,
                    record_write, store_entries)

__all__ = ["poll_once"]

# Kinds with an adapter. The rest are listed and never fetched: handing their
# URLs to the RSS parser would file every one as a parse failure and bury the
# real ones among them.
POLLABLE = ("rss", "cls")


async def poll_once(
    db: sqlite3.Connection,
    sources: list[Source] | None = None,
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
    # cls.cn needs a signature computed per request, so its URL is built here
    # rather than stored in the catalogue.
    requests = [(s.id, cls_feed_url() if s.kind == "cls" else s.url) for s in targets]
    results = await fetch_all(requests, conditional=conditional_headers(db))

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

        try:
            entries = (parse_cls(json.loads(fetched.body))
                       if source.kind == "cls" else parse_feed(fetched.body))
        except (ValueError, ET.ParseError, json.JSONDecodeError) as exc:
            # The download worked and the parse did not. Both facts matter, and
            # a source answering with broken XML is not a source with no news.
            report = StoreReport(source.id, state="unparseable", failed=1)
            record_write(db, report, url=source.url, at=now)
            reports.append(report)
            continue

        if not entries:
            # A valid document with no entries is what a feed looks like the day
            # it changes format. Recorded as a plain success it is identical to a
            # source with no news — the silent failure this project exists to
            # prevent, in the case most likely to occur.
            report = StoreReport(source.id, state="parsed-empty", failed=1)
            record_write(db, report, url=source.url, at=now)
            reports.append(report)
            continue

        report = store_entries(db, source, entries, fetched_at=now)
        record_write(db, report, url=source.url, at=now)
        reports.append(report)

    return reports
