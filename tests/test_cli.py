"""The command line is the only output in this project written for a person.

`cablegram check` is read while deciding whether the catalogue still works.
Miscounting a healthy source as broken there sends someone looking for a fault
that does not exist — and, worse, trains them to ignore the number that would
matter when one does.
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


def test_a_source_with_nothing_new_is_not_broken(reports, capsys):
    """A source that answered and had nothing to add is healthy. Counting it as
    broken reports failures on a perfectly normal run — the exact confusion this
    whole project treats as a bug everywhere else."""
    reports["reports"] = [StoreReport("qbitai", state="ok", new=0, seen=3),
                          StoreReport("habr", new=3)]

    assert main(["check"]) == 0
    assert "2/2 sources ok" in capsys.readouterr().out


def test_a_failed_source_is_counted_as_failed(reports, capsys):
    reports["reports"] = [StoreReport("qbitai", state="fetch-failed"),
                          StoreReport("habr", new=3)]

    assert main(["check"]) == 0
    assert "1/2 sources ok" in capsys.readouterr().out


def test_every_source_failing_exits_non_zero(reports):
    """A timer that never complains is a timer nobody checks."""
    reports["reports"] = [StoreReport("qbitai", state="fetch-failed")]

    assert main(["check"]) == 1


def test_sources_lists_them_all(capsys):
    assert main(["sources"]) == 0
    out = capsys.readouterr().out
    from cablegram.sources import SOURCES
    assert f"{len(SOURCES)} sources" in out


def test_an_unknown_selector_does_not_poll_everything(reports, capsys):
    """`or None` turned the empty tuple from a typo into "all of them".
    sources.py has a test defending exactly the opposite principle, and one line
    in the CLI undid it — so `cablegram poll typo` hit every source."""
    reports["reports"] = []

    assert main(["check", "typo"]) == 2
    assert "typo" in capsys.readouterr().out


def test_a_failed_source_says_why(reports, capsys, monkeypatch):
    """A column of FETCH-FAILED with no explanation leaves the person who set
    the timer up guessing between a dead network, a blocked address and a
    source that moved. The reason was already in source_state, and this is the
    one output in the project written for a human to read."""
    from cablegram.fetch import Fetched
    from cablegram.sources import by_id
    from cablegram.store import record_attempt

    # The reason is written by the pass itself, into the same database the
    # report comes from — so the double has to write it where `check` reads it.
    def fake_poll(db, sources=None):
        record_attempt(db, Fetched("qbitai", url=by_id("qbitai").url, ok=False,
                                   error="ConnectError: name or service not known",
                                   fetched_at="2026-08-31T12:00:00Z"))

        async def run():
            return [StoreReport("qbitai", state="fetch-failed")]
        return run()

    monkeypatch.setattr("cablegram.cli.poll_once", fake_poll)
    main(["check"])
    assert "ConnectError" in capsys.readouterr().out


def test_the_command_line_can_say_which_build_it_is(capsys):
    """The version was in the MCP handshake and on the first line of every
    reply, and `cablegram --version` answered `unrecognized arguments` on
    the one surface a person tries first."""
    import pytest
    from cablegram import __version__
    with pytest.raises(SystemExit) as exit_:
        main(["--version"])
    assert exit_.value.code == 0
    assert capsys.readouterr().out.strip() == f"cablegram {__version__}"
