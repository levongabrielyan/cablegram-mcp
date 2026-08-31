"""The command line is the only output in this project written for a person.

It is read by whoever set the timer up, usually while deciding whether
something is wrong. Miscounting a healthy source as broken there sends someone
looking for a fault that does not exist — and, worse, trains them to ignore the
number that would matter when one does.
"""

import pytest

from cablegram.cli import main
from cablegram.store import StoreReport


@pytest.fixture
def reports(monkeypatch):
    captured = {}

    def fake_poll(db, sources=None):
        async def run():
            return captured["reports"]
        return run()

    monkeypatch.setattr("cablegram.cli.poll_once", fake_poll)
    return captured


def test_unchanged_counts_as_healthy(reports, capsys, tmp_path, monkeypatch):
    """A 304 is the source answering that nothing is new. Counting it as broken
    reports six failures on a perfectly normal poll — the exact confusion this
    whole project treats as a bug everywhere else."""
    monkeypatch.setenv("CABLEGRAM_DB", str(tmp_path / "a.db"))
    reports["reports"] = [StoreReport("qbitai", state="unchanged"),
                          StoreReport("habr", new=3)]

    assert main(["poll"]) == 0
    assert "2/2 sources ok" in capsys.readouterr().out


def test_a_failed_source_is_counted_as_failed(reports, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("CABLEGRAM_DB", str(tmp_path / "a.db"))
    reports["reports"] = [StoreReport("qbitai", state="fetch-failed"),
                          StoreReport("habr", new=3)]

    assert main(["poll"]) == 0
    assert "1/2 sources ok" in capsys.readouterr().out


def test_every_source_failing_exits_non_zero(reports, tmp_path, monkeypatch):
    """A timer that never complains is a timer nobody checks."""
    monkeypatch.setenv("CABLEGRAM_DB", str(tmp_path / "a.db"))
    reports["reports"] = [StoreReport("qbitai", state="fetch-failed")]

    assert main(["poll"]) == 1


def test_sources_lists_them_all(capsys):
    assert main(["sources"]) == 0
    out = capsys.readouterr().out
    assert "19 sources" in out


def test_an_unknown_selector_does_not_poll_everything(reports, capsys, tmp_path, monkeypatch):
    """`or None` turned the empty tuple from a typo into "all of them".
    sources.py has a test defending exactly the opposite principle, and one line
    in the CLI undid it — so `cablegram poll typo` hit all nineteen."""
    monkeypatch.setenv("CABLEGRAM_DB", str(tmp_path / "a.db"))
    reports["reports"] = []

    assert main(["poll", "typo"]) == 2
    assert "typo" in capsys.readouterr().out


def test_a_failed_source_says_why(reports, capsys, tmp_path, monkeypatch):
    """A column of FETCH-FAILED with no explanation leaves the person who set
    the timer up guessing between a dead network, a blocked address and a
    source that moved. The reason was already in source_state, and this is the
    one output in the project written for a human to read."""
    from cablegram.archive import connect
    from cablegram.fetch import Fetched
    from cablegram.sources import by_id
    from cablegram.store import record_attempt

    path = tmp_path / "a.db"
    monkeypatch.setenv("CABLEGRAM_DB", str(path))
    db = connect(path)
    record_attempt(db, Fetched("qbitai", url=by_id("qbitai").url, ok=False,
                               error="ConnectError: name or service not known",
                               fetched_at="2026-08-31T12:00:00Z"))
    db.close()

    reports["reports"] = [StoreReport("qbitai", state="fetch-failed")]
    main(["poll"])
    assert "ConnectError" in capsys.readouterr().out
