"""Turning rows into the text the model reads. This output is the product.

Nobody sees it but the model, which is exactly why every convention here is
about preventing a wrong conclusion rather than about looking tidy:

* A source that failed is printed as DOWN rather than omitted. Missing from the
  list, it cannot be known to exist, and its absence reads as nothing having
  happened there.
* A cut is declared with the real total. Undeclared, it is indistinguishable
  from a source with little to say.
* A date the feed did not give is marked. Presented plainly, a capture time
  becomes a publication time and the item is filed under the wrong day.
* A truncated excerpt is marked. Unmarked, conclusions get drawn from two
  sentences and reported as though the article had been read.

Plain text, not JSON: the same information as JSON with indent costs six times
the tokens and truncates.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .sources import SOURCES, by_id

__all__ = ["render_latest", "render_read", "render_search", "render_sources",
           "estimate_tokens"]

VERSION = "v0.1"


def estimate_tokens(text: str) -> int:
    """Rough token count without a tokeniser, deliberately pessimistic.

    Latin text runs about four characters per token; Chinese and Russian
    headlines cost far more, close to one token per character. Counting
    non-ASCII as one token each overestimates a little, which is the right
    direction: an underestimate blows the budget and truncates, and truncation
    here is exactly the silent loss everything else is built to avoid.
    """
    ascii_chars = sum(1 for ch in text if ch.isascii())
    return ascii_chars // 4 + (len(text) - ascii_chars)


_HOME_PATTERN = re.compile(r"^(?:/home|/Users|[A-Za-z]:\\Users)[/\\][^/\\]+")


def _tilde(path: str) -> str:
    """Collapse the home directory: this output gets pasted into issues.

    Matched by shape as well as against this process's own home, so a path that
    came from somewhere else — a test, another machine, an archive moved with
    CABLEGRAM_DB — does not carry a username through either.
    """
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home):]
    return _HOME_PATTERN.sub("~", path)


def _day(published: str) -> str:
    return published[5:10].replace("-", "-")


def _time(published: str) -> str:
    return published[11:16]


def _header_cross(rows: list[dict]) -> list[str]:
    """Which stories more than one source carried. A count, never a score."""
    seen: dict[str, dict] = {}
    for row in rows:
        if row.get("cross", 1) > 1:
            seen.setdefault(row["id"], row)
    if not seen:
        return []

    ranked = sorted(seen.items(), key=lambda kv: -kv[1]["cross"])
    lines = []
    for i, (iid, row) in enumerate(ranked[:8]):
        lines.append(f"{'CROSS ' if i == 0 else '      '}{iid} x{row['cross']}")
    if len(ranked) > 8:
        lines.append(f"      8 shown of {len(ranked)} repeated stories, most-carried first")
    lines.append("      Raw count of the same normalised url across sources. NOT a ranking.")
    return lines


def _item_line(row: dict) -> str:
    mark = "" if row.get("date_exact", 1) else "~"
    host = f" ({row['target_host']})" if row.get("target_host") else ""
    return f"{mark}{row['id']} {_time(row['published'])} {row['title']}{host}"


def _by_source(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["source"], []).append(row)
    return grouped


def _cap_per_source(rows: list[dict], allowance: int) -> list[dict]:
    """Keep at most `allowance` per source, and keep every source.

    A source whose items are all cut keeps one placeholder row so its heading
    and real total survive: 0/57 is a fact, absence is a lie.
    """
    out = []
    for items in _by_source(rows).values():
        out.extend(items[:max(1, allowance)])
    return out


def _window_total(rows: list[dict]) -> int:
    """How many items the window held, not how many are being printed.

    `source_total` is counted before the per-source limit, so summing it is the
    only honest answer to "how much moved today". The header used to print the
    printed count under the same label, and a model asked that question quoted
    117 for a day that held 910.
    """
    return sum(items[0].get("source_total", len(items))
               for items in _by_source(rows).values())


def _blocks(rows: list[dict], limit_per_source: int | None,
            detail: str = "headlines") -> tuple[list[str], dict[str, tuple[int, int]]]:
    """Group by source, chronological within. Neutral, and it makes cuts legible.

    Ordering by recency across sources would let one firehose own the top of the
    payload, which is an editorial decision dressed as a sort.
    """
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["source"], []).append(row)

    cuts: dict[str, tuple[int, int]] = {}
    out: list[str] = []
    for source_id, items in grouped.items():
        source = by_id(source_id)
        shown, total = len(items), items[0].get("source_total", len(items))
        cuts[source_id] = (shown, total)
        tags = ",".join(source.tags) if source else ""
        out.append(f"\n## {source_id} {source.lang if source else '??'} {tags} {shown}/{total}")
        day = None
        for item in items:
            if _day(item["published"]) != day:
                day = _day(item["published"])
                out.append(f"-- {day}")
            out.append(_item_line(item))
            if detail == "full" and item.get("body"):
                # The element and the size, never a verdict. Deciding "full" or
                # "teaser" from the tag name was removed from the parser in an
                # earlier round because it is wrong in both directions — feeds
                # put whole articles in <description> and two sentences in
                # <atom:content> — and it came back here, stamped on 36Kr
                # digests of 3,300 characters as "not the full article".
                out.append(f"   [{item.get('body_src', '?')} {len(item['body'])}c] "
                           f"{item['body']}")
    return out, cuts


def render_latest(
    rows: list[dict],
    *,
    since: str,
    until: str,
    down: dict[str, str],
    sources_total: int,
    no_adapter: list[str] | None = None,
    unknown: list[str] | None = None,
    detail: str = "headlines",
    limit_per_source: int | None = None,
    max_tokens: int = 12000,
) -> str:
    window_total = _window_total(rows)

    def header_for(cuts: dict[str, tuple[int, int]], printed: int) -> list[str]:
        """Rebuilt from whatever the body ended up holding.

        Built once before the budget loop, every per-source figure in it stayed
        at the pre-trim value: `CUT cls=25/60` stood above a block showing
        1/60. The error was optimistic, so a model stopped looking.
        """
        # Sources that answered: not the total minus failures, which would count
        # the eight with no adapter as healthy and overstate the coverage of
        # every answer built on this.
        answering = sources_total - len(down) - len(no_adapter or ())
        head = [
            f"CABLEGRAM {VERSION} | {since}..{until} | {printed} of {window_total} "
            f"items | {answering}/{sources_total} sources"
        ]
        if down:
            head.append("DOWN  " + "  ".join(f"{k}={v}" for k, v in sorted(down.items())))
            head.append('      A DOWN SOURCE MEANS UNKNOWN, NOT "nothing happened".')
        if no_adapter:
            # A different fact from DOWN, and mixing them buries the one that
            # needs attention under the ones that are simply not built yet.
            head.append("PENDING " + " ".join(sorted(no_adapter))
                        + "  (no adapter in this build: never fetched, hold nothing)")
        if unknown:
            head.append(f"UNKNOWN SELECTOR {' '.join(unknown)}  -> matched no source, "
                        f"tag or language. Call wire_sources for the catalogue.")
        cut = [f"{k}={s}/{t}" for k, (s, t) in sorted(cuts.items()) if s < t]
        if cut:
            head.append("CUT   " + "  ".join(cut) + "   (newest kept)")
        head += _header_cross(rows)
        head.append("COLS  id hh:mm title    times UTC | body: wire_read(ids=[...])")
        head.append("---")
        return head

    body, cuts = _blocks(rows, limit_per_source, detail)
    text = "\n".join(header_for(cuts, len(rows)) + body)
    if estimate_tokens(text) <= max_tokens:
        return text

    # Over budget: lower the allowance every source gets, rather than trimming
    # the end of a flat list — which is ordered by source, so it beheaded the
    # alphabetical tail and made openai and huggingface vanish entirely while
    # the header still counted them as answering. A source may lose all of its
    # items; it may never lose its heading.
    allowance = limit_per_source or max(1, max(len(v) for v in _by_source(rows).values()))
    trimmed = rows
    while allowance > 0:
        allowance = allowance - 1 if allowance <= 3 else int(allowance * 0.6)
        trimmed = _cap_per_source(rows, allowance)
        body, cuts = _blocks(trimmed, allowance, detail)
        text = "\n".join(header_for(cuts, len(trimmed)) + body)
        if estimate_tokens(text) <= max_tokens:
            break
    over = estimate_tokens(text) > max_tokens
    label = "OVER BUDGET" if over else "BUDGET"
    note = (" — one item per source already exceeds it, and dropping sources would be "
            "worse than going over" if over else "")
    # The allowance actually applied: _cap_per_source keeps one row per source
    # whatever it is told, so announcing 0 above blocks showing 1 understated
    # what the reader had been given.
    return (f"{label} max_tokens={max_tokens}: at most {max(1, allowance)} per source "
            f"({len(trimmed)}/{len(rows)} items){note}. Every source is still listed "
            f"with its real total. Raise max_tokens, narrow `hours`, or pass "
            f"`sources`.\n" + text)


def render_read(rows: list[dict], *, requested: list[str]) -> str:
    found = {row["id"] for row in rows}
    missing = [i for i in requested if i not in found]

    out = [f"CABLEGRAM read | {len(requested)} requested | {len(rows)} resolved "
           f"| {len(missing)} unknown"]
    if missing:
        # The only route to autonomous recovery, so it must name something that
        # exists: it used to suggest urls=[...], which wire_read does not accept.
        out.append(f"UNKNOWN {' '.join(missing)} -> not in the archive (pruned, or "
                   f"server reinstalled).")
        out.append("        Re-run wire_latest or wire_search for the same window to "
                   "get current ids.")
    out.append("---")

    for row in rows:
        sources = row.get("sources") or row.get("source", "")
        cross = f" x{row['cross']}[{sources}]" if row.get("cross", 1) > 1 else ""
        # The element it came from and its length. Which of those holds a whole
        # article is a property of the source, checked once against its feed —
        # not something a tag name can be asked, in either direction.
        body = (f" body={row['body_src']} {len(row['body'])}c" if row.get("body")
                else " body=none")
        borrowed = row.get("via") == "link"
        mark = "~" if borrowed or not row.get("date_exact", 1) else ""
        out.append(f"\n## {row['id']} {row.get('first_source', '')} {row.get('lang','')} "
                   f"{mark}{row['published']}{body}{cross}")
        out.append(f"url {row['url']}")
        out.append(row["title"])
        if row.get("body"):
            out.append(row["body"])
        if borrowed:
            # Everything on the line above is borrowed from whoever linked it.
            # Left unmarked, a model answers "per the Russian channel ai_newz,
            # published on the 26th" about an article that outlet never touched.
            out.append(f"!! reached the archive because {row.get('first_source')} linked "
                       f"it, not from its own feed. The headline, language, source and "
                       f"date are that post's, not the article's. Open `url` for the "
                       f"real thing.")
        elif not row.get("body"):
            # A fact about this item, which is all the renderer knows. Phrased as
            # a property of the source it was false for eleven of nineteen: openai
            # carries a body in 91% of its items, and the line would have appeared
            # 106 times telling the model openai has none. It is the same mistake
            # the full/teaser verdict was removed from the parser for, twice.
            out.append("!! no stored body for this item. Open `url` for the text.")
    return "\n".join(out)


def render_search(
    rows: list[dict],
    *,
    query: str,
    since: str,
    days: int,
    archive_start: str,
    archive_items: int,
    engine: str = "index",
    max_tokens: int = 8000,
) -> str:
    # Declaring the cut matters more here than in the listing. A source holding
    # 437 matches, printed as 3/3, does not leave the cut undeclared — it denies
    # it, asserting completeness, from the one tool whose entire purpose is to
    # stop a small number being read as an answer.
    #
    # `totals` comes from the untrimmed rows, because source_total is the real
    # match count; `shown` has to be recounted for whatever survived the budget.
    totals: dict[str, int] = {}
    for r in rows:
        totals[r["source"]] = r.get("source_total") or totals.get(r["source"], 0) + 1

    def header_for(printed: list[dict]) -> list[str]:
        """Rebuilt from the rows that are actually in the body.

        Counted once before the budget loop and printed after it, the first
        line said "165 shown" above 17 item lines — and the word is literally
        `shown`. It happened on the default call: 8000 tokens does not hold
        thirty days of a common term.
        """
        seen: dict[str, int] = {}
        for r in printed:
            seen[r["source"]] = seen.get(r["source"], 0) + 1
        cut = [f"{s}={seen[s]}/{totals[s]}" for s in sorted(seen) if totals[s] > seen[s]]
        head = [
            f'CABLEGRAM search "{query}" | last {days}d | {len(printed)} shown'
            + (f" of {sum(totals.values())} matching" if cut else " hits")
        ]
        if cut:
            head.append("CUT   " + "  ".join(cut) + "   (newest kept)")
        return head + [
            # The oldest item, not when the file was made: an archive holding
            # ten years of a blog announced itself as starting today, and a
            # model asked "since when has X been discussed" declined to answer.
            f"COVER local-archive {archive_items} items, oldest {archive_start}",
            "      Only what this server has fetched, and coverage is uneven: a few "
            "feeds served their whole",
            "      back catalogue on the first poll and most served days, so the date "
            "above is the oldest item",
            "      in the archive, not a floor under every source.",
            '      "0 hits" = "not in what we can search". It does NOT mean nobody is '
            'talking about it.',
            "      zh/ru sources index the native term: a Chinese company is 智谱 here "
            "and Zhipu on Hacker News.",
            "      Retry transliterated or translated if this comes back empty.",
            # Two queries answered by different engines are not comparable, and
            # nothing else in this output would say so.
            ("      ENGINE substring scan: terms under 3 characters cannot use the "
             "index, so recall differs from a longer query."
             if engine == "substring" else
             "      ENGINE trigram index over archived headlines."),
            "COLS  id hh:mm title",
            "---",
        ]

    body, _ = _blocks(rows, None)
    text = "\n".join(header_for(rows) + body)
    if estimate_tokens(text) <= max_tokens:
        return text

    # Trim per source and verify, rather than computing a proportion once and
    # trusting it: the old estimate ignored the header and overshot every time,
    # returning 450 tokens for max_tokens=300 while announcing that it fit.
    allowance = max((len(v) for v in _by_source(rows).values()), default=1)
    trimmed = rows
    while allowance > 0:
        allowance = allowance - 1 if allowance <= 3 else int(allowance * 0.6)
        trimmed = _cap_per_source(rows, allowance)
        body, _ = _blocks(trimmed, allowance)
        text = "\n".join(header_for(trimmed) + body)
        if estimate_tokens(text) <= max_tokens:
            break
    return (f"BUDGET max_tokens={max_tokens} reached: at most {max(1, allowance)} per "
            f"source. Every source keeps its heading and real total.\n" + text)


def render_sources(*, health: dict, archive_items: int, archive_start: str,
                   archive_path: str) -> str:
    out = [
        f"CABLEGRAM {VERSION} | {len(SOURCES)} sources",
        f"ARCHIVE {_tilde(archive_path)} | {archive_items} items | oldest {archive_start}",
        "",
        "id               lg kind      tags                     last_ok        state",
    ]
    now = datetime.now(timezone.utc)
    for source in SOURCES:
        state = health.get(source.id, {})
        last_ok = (state.get("last_ok") or "-")[:16].replace("T", " ")
        if state.get("last_error") and (
            not state.get("last_ok") or (state.get("last_try") or "") > state["last_ok"]
        ):
            status = f"FAIL {state['last_error'][:28]}"
        elif state.get("last_ok"):
            # OK beside a three-day-old date still reads as OK, and nobody
            # compares it to today — so a dead timer looks like healthy sources.
            age = (now - datetime.strptime(state["last_ok"], "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc))
            hours = int(age.total_seconds() // 3600)
            status = f"STALE {hours}h" if hours >= 6 else "OK"
        else:
            status = "never polled"
        tags = ",".join(source.tags)
        mark = "  fragile" if source.fragile else ""
        out.append(f"{source.id:16} {source.lang} {source.kind:9} {tags:24} "
                   f"{last_ok:14} {status}{mark}")
    out.append("")
    # Imported here rather than at module scope: poll pulls in the HTTP stack,
    # and rendering has no business depending on it.
    from .poll import POLLABLE

    # Guarded like the fragile note below it. Printed unconditionally once every
    # kind had an adapter, it described an empty set on every call — and a model
    # can file a real silence under "that one is never polled".
    if any(s.kind not in POLLABLE for s in SOURCES):
        out.append("Sources with no adapter yet are listed and never polled: they are "
                   "known to exist, and known to be empty.")
    if any(s.fragile for s in SOURCES):
        out.append("fragile = reverse-engineered rather than published. It works today "
                   "and may stop without notice; treat its silence as unknown.")
    return "\n".join(out)
