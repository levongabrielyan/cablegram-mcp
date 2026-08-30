"""Command line: poll the sources, or serve them over MCP.

Polling has to be runnable without the MCP client, because it has to happen on
a timer whether or not anyone is talking to the server. The output is for the
person who set the timer up — the only place in this project written for a human
to read — so it says which sources answered and which did not.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .archive import archive_path, connect
from .poll import poll_once
from .sources import SOURCES, resolve

__all__ = ["main"]


def _poll(args) -> int:
    db = connect()
    reports = asyncio.run(poll_once(db, list(resolve(args.sources)) or None))

    archived = sum(r.new for r in reports)
    # `unchanged` is the source answering that nothing is new — a success, and
    # the most common outcome once the archive is warm. Counting it as broken
    # would report six failures on a perfectly normal poll.
    broken = [r for r in reports if r.state in ("fetch-failed", "unparseable")]

    for report in reports:
        if report.state == "ok":
            line = f"{report.new:>4} new  {report.seen:>4} seen"
            if report.failed:
                line += f"  {report.failed} FAILED"
        elif report.state == "unchanged":
            line = "     nothing new (304)"
        else:
            line = f"     {report.state.upper()}"
        print(f"  {report.source:16} {line}")

    print(f"\n{archived} archived · {len(reports) - len(broken)}/{len(reports)} sources ok")
    print(f"{archive_path()}")
    # A poll where every source failed is worth a non-zero exit: a timer that
    # never complains is a timer nobody checks.
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
    sub = parser.add_subparsers(dest="command")

    poll = sub.add_parser("poll", help="fetch every source once and archive it")
    poll.add_argument("sources", nargs="*",
                      help="ids, tags or languages; default is all of them")
    poll.set_defaults(run=_poll)

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
