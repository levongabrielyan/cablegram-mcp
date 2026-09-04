"""Command line: check that the sources still answer, or serve them over MCP.

There is no `poll` any more. Polling existed to fill a file on a timer, and
there is no file: every tool call fetches what it needs and keeps nothing. A
command whose only job was to feed an archive has nothing left to feed.

`check` replaces it and is a different thing. It fetches once and prints what
each source said, for a person deciding whether the catalogue still works. It
writes nothing, and it is the only output in this project meant for a human.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import __version__
from .poll import poll_once
from .schema import connect
from .sources import SOURCES, resolve

__all__ = ["main"]


def _check(args) -> int:
    selected = list(resolve(args.sources)) if args.sources else None
    if args.sources and not selected:
        # `or None` used to turn the empty tuple a typo produces into "all of
        # them", so a typo swept the whole catalogue. sources.py has a test
        # defending the opposite principle; this line undid it.
        print(f"  no source, tag or language matches {' '.join(args.sources)}")
        print("  try: cablegram sources")
        return 2

    db = connect()
    reports = asyncio.run(poll_once(db, selected))
    # Why a source failed is recorded and was never printed: a run of
    # FETCH-FAILED with no explanation is a person guessing between a dead
    # network, a blocked address and a source that has moved.
    why = {row["source"]: row["last_error"] for row in
           db.execute("SELECT source, MAX(last_try), last_error FROM source_state"
                      " WHERE last_error IS NOT NULL GROUP BY source")}

    broken = [r for r in reports
              if r.state in ("fetch-failed", "unparseable", "parsed-empty")]

    for report in reports:
        if report.state == "ok":
            line = f"{report.new:>4} items"
            if report.referenced:
                line += f"  +{report.referenced} linked"
            if report.failed:
                line += f"  {report.failed} FAILED"
            if report.at_ceiling:
                line += "  AT CEILING (there may be more)"
        else:
            line = f"     {report.state.upper()}"
            if reason := why.get(report.source):
                line += f"  {reason[:60]}"
        print(f"  {report.source:16} {line}")

    total = sum(r.new + r.referenced for r in reports)
    print(f"\n{total} items · {len(reports) - len(broken)}/{len(reports)} sources ok "
          f"· nothing written")
    # Every source failing is worth a non-zero exit: this is the command a person
    # runs to find out whether anything still works.
    return 1 if reports and len(broken) == len(reports) else 0


def _sources(args) -> int:
    for source in SOURCES:
        tags = ",".join(source.tags)
        print(f"  {source.id:16} {source.lang}  {source.kind:9} {tags:24} {source.name}")
    print(f"\n{len(SOURCES)} sources")
    return 0


def _serve(args) -> int:
    from .server import serve

    serve()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cablegram",
        description="Raw dispatches from tech, AI and Chinese/Russian sources.",
    )
    # The version is in the MCP handshake and on the first line of every
    # reply; the command line, which is what a person tries first, had no
    # way to say it.
    parser.add_argument("--version", action="version", version=f"cablegram {__version__}")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="fetch every source once and say what came "
                                         "back; stores nothing")
    check.add_argument("sources", nargs="*",
                       help="ids, tags or languages; default is all of them")
    check.set_defaults(run=_check)

    listing = sub.add_parser("sources", help="list the sources this build knows")
    listing.set_defaults(run=_sources)

    serve = sub.add_parser("serve", help="run the MCP server on stdio")
    serve.set_defaults(run=_serve)

    args = parser.parse_args(argv)
    if not hasattr(args, "run"):
        parser.print_help()
        return 2
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
