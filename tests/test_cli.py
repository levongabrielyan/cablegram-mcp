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
