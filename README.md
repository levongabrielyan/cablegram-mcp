# cablegram-mcp

Raw dispatches from tech, AI and Chinese/Russian sources — filtered by date,
never ranked.

A *cablegram* was the unedited message that arrived over the submarine cables,
before an editor turned it into a story. This server is the cable: it brings the
dispatches. Your model is the editor.

Nineteen sources in English, Chinese and Russian. Headlines are never
translated: each dispatch carries its language, and the model reading it has
more context for that than any translation step would.

A full day of all nineteen costs a few thousand tokens. Six hours of them,
grouped by source with the cuts declared, is around 700.

```
CABLEGRAM v0.1 | 2026-08-30T09:09Z..2026-08-30T14:09Z | 11 items | 19/19 sources
CUT   habr=2/4  hn=2/159  kr36=2/3   (newest kept)
COLS  id hh:mm title    times UTC | body: wire_read(ids=[...])
---

## cls zh early,finance 1/1
-- 08-30
e60e27faa7fc 10:04 AI数据中心扩张“限制性因素”浮现 马斯克：SpaceX正铸造燃气轮机叶片

## data_secrets ru telegram 1/1
-- 08-30
a50d137e3147 10:49 Агенты OpenAI одну за одной автономно создали три цивилизации

## hn en community,searchable 2/159
-- 08-30
2929f114895f 14:06 METR and Redwood Offer Postmortem of the HuggingFace Hack (thezvi.wordpress.com)
7901d3fb4e1c 14:04 Google removed the URLs. Only for the people who resell them (scraping.club)
```

*Five hours of nineteen sources, verbatim. `CUT` says what was left out and how
much there was; `19/19` is how many answered.*


## Status

v0.1 — the nineteen sources work; the tool API may still move. All nineteen sources have an adapter and were verified
against the live endpoints: eleven RSS feeds, Hacker News through its search
index, a signed Chinese financial API, and six public Telegram channels.

Two of them are worth knowing about before you rely on them:

* **cls.cn is reverse-engineered.** An undocumented internal API with a signed
  request. It holds 3.34 days at most and cannot page backwards, so a gap in
  polling is permanent. `wire_sources` marks it `fragile`.
* **Telegram is HTML with no contract.** The public preview view can change
  without a version number to notice it by.

Both are declared in the output rather than explained afterwards.

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
