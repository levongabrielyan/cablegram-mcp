"""The fixtures that feed a relative window must not carry a fixed date.

Seven tests went from green to red overnight with no code change: their
dispatches were dated `Sat, 30 Aug 2026` and their calls ask for `hours=48`, so
the fixtures aged out of the window they were written for. CI stayed green only
because it runs on push and nobody had pushed since.

That is the worst kind of red. It has nothing to do with the code, so whoever
sees it goes hunting for a regression that is not there — and the day one of
these tests goes red for a real reason, it will look exactly the same.

This is the guard. It fails the moment a fixture behind a relative window is
given a fixed date again.
"""

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import test_audit_bounds
import test_audit_edges
import test_audit_pipeline
import test_live

# The fixtures reached through a `hours=`/`days=` call. Parser fixtures are
# deliberately absent: those assert that a given input yields a given
# timestamp, so for them a moving date would be the bug.
WINDOW_FIXTURES = {
    "test_live.FEED": lambda: test_live.FEED,
    "test_live.CHANNEL": lambda: test_live.CHANNEL,
    "test_audit_edges.FEED": lambda: test_audit_edges.FEED,
    "test_audit_pipeline.FEED": lambda: test_audit_pipeline.FEED,
    "test_audit_bounds.feed()": lambda: test_audit_bounds.feed(1),
}

_RSS = re.compile(r"<pubDate>(.*?)</pubDate>")
_ISO = re.compile(r'datetime="([^"]+)"')


def _dates(blob) -> list[datetime]:
    text = blob.decode() if isinstance(blob, bytes) else blob
    found = [parsedate_to_datetime(d) for d in _RSS.findall(text)]
    found += [datetime.fromisoformat(d) for d in _ISO.findall(text)]
    return found


def test_every_fixture_behind_a_relative_window_is_dated_relative_to_now():
    now = datetime.now(timezone.utc)
    for name, build in WINDOW_FIXTURES.items():
        found = _dates(build())
        assert found, f"{name}: no dates found — has the fixture changed shape?"
        for when in found:
            age = now - when
            assert timedelta(0) <= age < timedelta(hours=24), (
                f"{name} carries {when.isoformat()}, which is {age} old. The "
                f"tests behind it ask for hours=48, so a fixed date passes on "
                f"the day it is written and fails every day after. Build it "
                f"from tests/dates.py instead.")
