# cablegram-mcp

Twenty-one sources on AI and tech — English, Chinese and Russian — written to be
read by a model rather than by a person.

A model's knowledge ends at its training cutoff, and it has no way to notice
that it has ended. It will recommend the tool that was superseded last month and
say nothing at all about the release that changes the answer. This server is
where it goes to find out what it missed: what twenty-two sources published in
the last N hours, the stored text of whichever dispatches it wants to read, and a
search over what it has fetched.

Dispatches arrive raw. They are filtered by date and never ranked, because
ranking means deciding what matters with far less context than the model reading
them has. A *cablegram* was the unedited message that came over the submarine
cables, before an editor turned it into a story: this server is the cable, and
your model is the editor.

**The point is the cable, not the news.** A launch discussed in Chinese or
Russian today reaches English-language coverage days later, filtered through
whoever decided it was worth translating — and often it never arrives at all.
Twenty-one sources in three languages, read directly, put a reader in California
in the same week as a reader in Shanghai or Moscow.

Headlines are never translated. Each dispatch carries its language, and the
model reading it has more context for that than any translation step would.
It also means the same story is kept in every language that carried it: OpenAI
titles a post *"Pacing model development in an era of cyber-critical
capabilities"* while a Russian channel titles the same URL *"OpenAI stopped RL
for two weeks on its latest models"*. Both are stored against the same id, and
either can be searched.

```
CABLEGRAM v0.1 archive | 2026-08-31T04:26Z..2026-08-31T12:26Z | 5 of 253 items | 3/3 sources
CUT   cls=2/34  hn=2/218   (newest kept)
COLS  id hh:mm title    times UTC | body: wire_read(ids=[...])
---

## cls zh early,finance 2/34
-- 08-31
7dfa8ea7a34e 11:58 国务院国资委举办中央企业“AI for Science”人才特训班
191a7a89001e 11:24 财联社8月31日电，智谱表示，其年化经常性收入（ARR）于8月突破16亿美元。

## data_secrets ru telegram 1/1
-- 08-31
f49065b348d8 08:12 Вышел OpenClaw 2.0 – крупнейшее обновление за всю историю проекта

## hn en community,searchable 2/218
-- 08-31
4d1471d69971 12:03 Advertisers are trying to influence AI bots with secret ads (theregister.com)
f94e8c31521b 12:03 Novo Mundo (news.ycombinator.com)
```

*Eight hours of three sources at `limit_per_source=2`, verbatim. `CUT` says what
was left out and how much there was; `3/3` is how many answered.*

**What it costs**, measured rather than estimated: a full day of all twenty-two
at the defaults is about **5,000 tokens**; six hours is about **3,400**; six
hours of three sources is **under 800**, and **under 200** at
`limit_per_source=2`.

## Status

v0.1 — the sources work; the tool API may still move. All twenty-two have an
adapter and were verified against the live endpoints: eleven RSS feeds, Hacker
News through its search index, a signed Chinese financial API, six public
Telegram channels, the Hugging Face model hub, and two sections of a lab that
publishes no feed, read out of the data its own pages ship. 409 tests covering
96% of 1,341 statements, on 3.12 and 3.14.

Three sources are worth knowing about before you rely on them:

* **cls.cn is reverse-engineered.** An undocumented internal API with a signed
  request. It holds 3.34 days at most and cannot page backwards, so a gap is
  permanent. `wire_sources` marks it `fragile`.
* **Anthropic has no feed at all.** Its two sections are read out of the data
  its own pages ship inline, which is an internal Next.js format with no
  contract. Also marked `fragile`. A shape change comes back as a broken
  source, never as a quiet week.
* **Telegram is HTML with no contract.** The public preview view can change
  without a version number to notice it by.

Both are declared in the output rather than explained afterwards.

## Install

Requires Python 3.12+. Nothing to clone:

```bash
uvx cablegram-mcp sources                 # what it knows about
claude mcp add cablegram --scope user -- uvx cablegram-mcp serve
```

Or from a checkout, if you would rather read it first:

```bash
git clone https://github.com/levongabrielyan/cablegram-mcp
cd cablegram-mcp && uv sync
claude mcp add cablegram --scope user -- \
  /path/to/cablegram-mcp/.venv/bin/python -m cablegram.cli serve
```

That is the whole setup. Each call fetches what it needs and keeps nothing.

## The four tools

| Tool | Question |
| --- | --- |
| `wire_latest` | What did the sources publish in the last N hours? |
| `wire_read` | Give me the stored text of these ids |
| `wire_search` | Who wrote about this term, and when — widen `days` to reach back |
| `wire_sources` | What exists, and what is currently broken? |

All four are read-only and return plain text. The same information as JSON with
indent costs roughly six times the tokens and truncates.

**Fetching is per source, and what it costs is Telegram**: those six channels
go one at a time, three seconds apart, because `t.me` drops the sixth request in
a row. Everything else is fetched in parallel, so the eleven English sources
together cost what two do — one to two seconds. Bring the channels in and it is
twenty to forty-five, which is also what `sources=["ru"]` costs, since six of
the seven Russian sources are channels. Language is not the axis; channel count
is. The tool description carries the same table, so a model can ask before
spending it.

## Keeping an archive (optional)

Nothing is stored by default. Set `CABLEGRAM_ARCHIVE=1` and the server reads a
SQLite file instead of fetching, which `cablegram poll` fills:

```bash
export CABLEGRAM_ARCHIVE=1
cablegram poll        # once now, and on a timer if you want history
```

The file lives under your platform's data directory
(`~/.local/share/cablegram/archive.db` on Linux; override with `CABLEGRAM_DB`).
It is created on first run and grows by roughly a megabyte per thousand items.
Hacker News is most of that volume, and it is the one source you can already
search at its own origin.

**What an archive buys you**, and it is narrower than it sounds. Most feeds
serve enough history that a gap in polling costs nothing: this laptop was off
for eleven hours, the sources published 325 articles, and the next pass picked
up all 325. Two exceptions matter — cls.cn holds 3.34 days and cannot page
backwards, and 36Kr is shallow — so if those two are why you are here, run the
timer. A systemd user unit is in
[`deploy/`](https://github.com/levongabrielyan/cablegram-mcp/tree/main/deploy).

**What it does not buy you** is a complete history. Feeds differ enormously:
the OpenAI blog serves back to 2015 on the first fetch, Hugging Face to 2020,
and most of the rest a few days. `wire_search` says so on every reply — zero
hits means "not in what we can search", never "nobody is talking about it".

Nothing is uploaded anywhere, and no seed database ships with this repository:
the server fetches on your behalf and does not redistribute anyone's content.

## Not built yet

Separate from what this deliberately never does — the design notes list that —
these are things it would reasonably do and does not:

* **Nothing prunes the archive.** If you enable it, it only grows. There is no
  `prune`, no retention window and no export; deleting the file is the only
  reset, and it costs the whole history.
* **Sources are fixed in code.** Adding one means editing `sources.py`, and an
  adapter too if it is not RSS. That is the design rather than an oversight, but
  it does mean a fork rather than a config file.
* **Coverage before your first call is uneven and not controllable.** A feed
  either serves its back catalogue or it does not; there is no way to ask for
  more history than the endpoint volunteers.
* **Nothing checks a source's terms for you.** The endpoints are public and the
  requests are conditional and rate-limited, but the responsibility is yours.

## Design

[`docs/design.md`](https://github.com/levongabrielyan/cablegram-mcp/blob/main/docs/design.md)
covers why the identity of an item is a pure function of its URL, why a failure
is a value rather than an exception, why the full-text index needs a trigram
tokenizer for Chinese to work at all, and what this server deliberately does not
do.

## Notes

Only public endpoints are used: no credentials, no authentication bypass, no
scraping behind a login. Conditional requests (`ETag`, `If-Modified-Since`) mean
an unchanged feed is not re-downloaded. Intended for personal research — respect
each source's terms of service.

## Licence

MIT. Built by [Levon Gabrielyan](https://github.com/levongabrielyan).
<!-- mcp-name: io.github.levongabrielyan/cablegram-mcp -->
