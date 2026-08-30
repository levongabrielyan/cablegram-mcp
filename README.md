# cablegram-mcp

Raw dispatches from tech, AI and Chinese/Russian sources — filtered by date,
never ranked.

A *cablegram* was the unedited message that arrived over the submarine cables,
before an editor turned it into a story. This server is the cable: it brings the
dispatches. Your model is the editor.

Nineteen sources in English, Chinese and Russian. Headlines are never
translated: each dispatch carries its language, and the model reading it has
more context for that than any translation step would.

```
CABLEGRAM v0.1 | 2026-08-29T09:00Z..2026-08-30T09:00Z | 210 items | 17/19 sources
DOWN  deepmind=timeout8s
      A DOWN SOURCE MEANS UNKNOWN, NOT "nothing happened".
CUT   habr=25/44  kr36=25/61   (newest kept)
CROSS a3f9c2e18b04 x6
      Raw count of the same normalised url across sources. NOT a ranking.
---

## qbitai zh community 14/14
-- 08-30
a3f9c2e18b04 07:12 智谱发布GLM-5，上下文窗口扩展至200万tokens

## hn en community 25/57
-- 08-30
3f9a2c1d77e0 08:12 Show HN: local-first RAG over my Obsidian vault (github.com)
```

## Status

Early development, and honest about it: **11 of the 19 sources have an adapter
today** — the RSS feeds. Hacker News, the Chinese financial wire and the six
Telegram channels are listed as `PENDING` in every reply and are never counted
as answering. They hold nothing, and the output says so rather than letting an
empty result look like quiet news.

## Install

Requires Python 3.12+.

```bash
git clone https://github.com/levongabrielyan/cablegram-mcp
cd cablegram-mcp
uv sync
```

Register it with an MCP client — for Claude Code:

```bash
claude mcp add cablegram --scope user -- \
  /path/to/cablegram-mcp/.venv/bin/python -m cablegram.cli serve
```

Then fill the archive, and keep filling it:

```bash
cablegram poll          # once, now
cablegram sources       # what it knows about
```

Feeds expose a window of days, so put `cablegram poll` on a timer — an hour
nobody polls is an hour no endpoint will serve again. A systemd user unit is in
[`deploy/`](deploy/).

## The four tools

| Tool | Question |
| --- | --- |
| `wire_latest` | What did the sources publish in the last N hours? |
| `wire_read` | Give me the stored text of these ids |
| `wire_search` | When did this term start appearing? |
| `wire_sources` | What exists, and what is currently broken? |

All four are read-only and return plain text. The same information as JSON with
indent costs roughly six times the tokens and truncates.

## The local archive

RSS feeds expose only their last few dozen entries; last week is unrecoverable.
So everything fetched is stored in a SQLite file under your platform's data
directory (`~/.local/share/cablegram/archive.db` on Linux; override with
`CABLEGRAM_DB`).

It is created on first run and grows by a few megabytes a year. Nothing is
uploaded anywhere, and no seed database ships with this repository — the server
fetches on your behalf and does not redistribute anyone's content.

**`wire_search` only reads what this archive holds, which starts the day you
first ran it.** Zero hits means "not in what we can search", never "nobody is
talking about it", and the output says so on every reply.

## Design

[`docs/design.md`](docs/design.md) covers why the identity of an item is a pure
function of its URL, why a failure is a value rather than an exception, why the
full-text index needs a trigram tokenizer for Chinese to work at all, and what
this server deliberately does not do.

## Notes

Only public endpoints are used: no credentials, no authentication bypass,
no scraping behind a login. Conditional requests (`ETag`, `If-Modified-Since`)
mean an unchanged feed is not re-downloaded. Intended for personal research —
respect each source's terms of service.

## Licence

MIT. Built by [Levon Gabrielyan](https://github.com/levongabrielyan).
