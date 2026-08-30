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

    lines = []
    for i, (iid, row) in enumerate(sorted(seen.items(), key=lambda kv: -kv[1]["cross"])[:8]):
        prefix = "CROSS " if i == 0 else "      "
        lines.append(f"{prefix}{iid} x{row['cross']}")
    lines.append("      Raw count of the same normalised url across sources. NOT a ranking.")
    return lines


def _item_line(row: dict) -> str:
    mark = "" if row.get("date_exact", 1) else "~"
    host = f" ({row['target_host']})" if row.get("target_host") else ""
    return f"{mark}{row['id']} {_time(row['published'])} {row['title']}{host}"


def _body_kind(row: dict) -> str:
    if not row.get("body"):
        return "none"
    return "full" if row.get("body_src") in ("content:encoded", "atom:content") else "teaser"


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
                kind = _body_kind(item)
                out.append(f"   {item['body']}")
                if kind == "teaser":
                    out.append("   !! teaser: a truncated excerpt, NOT the full article.")
    return out, cuts


def render_latest(
    rows: list[dict],
    *,
    since: str,
    until: str,
    down: dict[str, str],
    sources_total: int,
    no_adapter: list[str] | None = None,
    detail: str = "headlines",
    limit_per_source: int | None = None,
    max_tokens: int = 12000,
) -> str:
    body, cuts = _blocks(rows, limit_per_source, detail)

    # Sources that answered: not the total minus failures, which would count
    # the eight with no adapter as healthy and overstate the coverage of every
    # answer built on this.
    answering = sources_total - len(down) - len(no_adapter or ())
    header = [
        f"CABLEGRAM {VERSION} | {since}..{until} | {len(rows)} items | "
        f"{answering}/{sources_total} sources"
    ]
    if down:
        header.append("DOWN  " + "  ".join(f"{k}={v}" for k, v in sorted(down.items())))
        header.append('      A DOWN SOURCE MEANS UNKNOWN, NOT "nothing happened".')
    if no_adapter:
        # A different fact from DOWN, and mixing them buries the one that needs
        # attention under the eight that are simply not built yet.
        header.append("PENDING " + " ".join(sorted(no_adapter))
                      + "  (no adapter in this build: never fetched, hold nothing)")
    cut = [f"{k}={s}/{t}" for k, (s, t) in sorted(cuts.items()) if s < t]
    if cut:
        header.append("CUT   " + "  ".join(cut) + "   (newest kept)")
    header += _header_cross(rows)
    header.append("COLS  id hh:mm title    times UTC | body: wire_read(ids=[...])")
    header.append("---")

    text = "\n".join(header + body)
    if estimate_tokens(text) <= max_tokens:
        return text

    # Over budget: drop whole items from the end of each block rather than
    # cutting mid-text, and say what was dropped. A payload that silently stops
    # is the one failure this format exists to prevent.
    kept = len(rows)
    while kept > 1 and estimate_tokens(text) > max_tokens:
        kept = int(kept * 0.7)
        body, cuts = _blocks(rows[:kept], limit_per_source, detail)
        text = "\n".join(header + body)
    return (f"BUDGET {kept}/{len(rows)} items fit in max_tokens={max_tokens}. "
            f"Raise it or narrow `hours`/`sources`.\n" + text)


def render_read(rows: list[dict], *, requested: list[str]) -> str:
    found = {row["id"] for row in rows}
    missing = [i for i in requested if i not in found]

    out = [f"CABLEGRAM read | {len(requested)} requested | {len(rows)} resolved "
           f"| {len(missing)} unknown"]
    if missing:
        out.append(f"UNKNOWN {' '.join(missing)} -> not in the archive (pruned, or "
                   f"server reinstalled).")
        out.append("        Re-run wire_latest for the same window, or pass urls=[...] "
                   "directly.")
    out.append("---")

    for row in rows:
        kind = "full" if row.get("body_src") in ("content:encoded", "atom:content") else "teaser"
        if not row.get("body"):
            kind = "none"
        sources = row.get("sources") or row.get("source", "")
        cross = f" x{row['cross']}[{sources}]" if row.get("cross", 1) > 1 else ""
        size = f" {len(row['body'])}c" if row.get("body") else ""
        out.append(f"\n## {row['id']} {row.get('first_source', '')} {row.get('lang','')} "
                   f"{row['published']} body={kind}{size}{cross}")
        out.append(f"url {row['url']}")
        out.append(row["title"])
        if row.get("body"):
            out.append(row["body"])
        if kind == "teaser":
            out.append("!! body=teaser: the feed only ships a truncated excerpt. "
                       "This is NOT the full article.")
        elif kind == "none":
            out.append("!! this source publishes headlines only. Open `url` for the text.")
    return "\n".join(out)


def render_search(
    rows: list[dict],
    *,
    query: str,
    since: str,
    days: int,
    archive_start: str,
    archive_items: int,
    max_tokens: int = 8000,
) -> str:
    body, _ = _blocks(rows, None)

    header = [
        f'CABLEGRAM search "{query}" | last {days}d | {len(rows)} hits',
        f"COVER local-archive {archive_items} items since {archive_start}",
        "      The local archive starts the day this server was first run.",
        '      "0 hits" = "not in what we can search". It does NOT mean nobody is '
        'talking about it.',
        "      zh/ru sources index the native term: a Chinese company is 智谱 here and "
        "Zhipu on Hacker News.",
        "      Retry transliterated or translated if this comes back empty.",
        "COLS  id hh:mm title",
        "---",
    ]
    text = "\n".join(header + body)
    if estimate_tokens(text) > max_tokens:
        kept = max(1, int(len(rows) * max_tokens / max(estimate_tokens(text), 1)))
        body, _ = _blocks(rows[:kept], None)
        text = (f"BUDGET {kept}/{len(rows)} hits fit in max_tokens={max_tokens}.\n"
                + "\n".join(header + body))
    return text


def render_sources(*, health: dict, archive_items: int, archive_start: str,
                   archive_path: str) -> str:
    out = [
        f"CABLEGRAM {VERSION} | {len(SOURCES)} sources",
        f"ARCHIVE {archive_path} | {archive_items} items | since {archive_start}",
        "",
        "id               lg kind      tags                     last_ok        state",
    ]
    for source in SOURCES:
        state = health.get(source.id, {})
        last_ok = (state.get("last_ok") or "-")[:16].replace("T", " ")
        if state.get("last_error"):
            status = f"FAIL {state['last_error'][:28]}"
        elif state.get("last_ok"):
            status = "OK"
        else:
            status = "never polled"
        tags = ",".join(source.tags)
        out.append(f"{source.id:16} {source.lang} {source.kind:9} {tags:24} "
                   f"{last_ok:14} {status}")
    out.append("")
    out.append("Sources with no adapter yet are listed and never polled: they are known "
               "to exist, and known to be empty.")
    return "\n".join(out)
