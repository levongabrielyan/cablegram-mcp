"""Fixture dates relative to now, because the windows under test are relative.

Every dispatch in these fixtures carried a fixed date — `Sat, 30 Aug 2026
06:40:00 +0000` — and the tests that use them ask for `hours=48`. That held on
the day they were written and stopped holding the next morning: with no code
change at all, seven tests went from green to red overnight, and CI stayed
green only because it runs on push and nobody had pushed yet.

A fixture that expires is worse than no test. It fails for a reason that has
nothing to do with the code, so whoever sees the red reads it as a regression
and goes looking for one that is not there — and the day it finally hides a
real one, nobody will be looking any more.

Parser fixtures keep their fixed dates on purpose: those tests assert that a
given input yields a given timestamp, so a moving date would be the bug.
"""

from datetime import datetime, timedelta, timezone


def _ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def rss_date(hours: float) -> str:
    """An RSS `pubDate` for a moment `hours` before now."""
    return _ago(hours).strftime("%a, %d %b %Y %H:%M:%S +0000")


def iso_date(hours: float) -> str:
    """A Telegram `<time datetime=...>` for a moment `hours` before now."""
    return _ago(hours).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def hub_date(hours: float) -> str:
    """A Hugging Face `createdAt` for a moment `hours` before now."""
    return _ago(hours).strftime("%Y-%m-%dT%H:%M:%SZ")
