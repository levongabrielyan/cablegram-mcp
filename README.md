# cablegram-mcp

Twenty-eight sources on AI and tech — English, Chinese and Russian — written to
be read by a model rather than by a person. Nothing is stored: each call fetches
what it needs, answers, and discards it.

A model's knowledge ends at its training cutoff, and it has no way to notice
that it has ended. It will recommend the tool that was superseded last month and
say nothing at all about the release that changes the answer. This server is
where it goes to find out what it missed: what those sources published in the
last N hours, the text of whichever dispatches it wants to read, and a search
across what they are serving right now.

Dispatches arrive raw. They are filtered by date and never ranked, because
ranking means deciding what matters with far less context than the model reading
them has. A *cablegram* was the unedited message that came over the submarine
cables, before an editor turned it into a story: this server is the cable, and
your model is the editor.

**The point is the cable, not the news.** A launch discussed in Chinese or
Russian today reaches English-language coverage days later, filtered through
whoever decided it was worth translating — and often it never arrives at all.
Twenty-eight sources in three languages, read directly, put a reader in
California in the same week as a reader in Shanghai or Moscow.

Headlines are never translated. Each dispatch carries its language, and the
model reading it has more context for that than any translation step would.
It also means the same story is kept in every language that carried it: OpenAI
titles a post *"Pacing model development in an era of cyber-critical
capabilities"* while a Russian channel titles the same URL *"OpenAI stopped RL
for two weeks on its latest models"*. Both are stored against the same id, and
either can be searched.

```
CABLEGRAM v0.1 | 2026-08-31T08:02:26Z..2026-08-31T16:02:26Z | 6 of 500 items | 3/3 sources
CUT   cls=2/22  data_secrets=2/4  hn=2/474   (newest kept)
COLS  id hh:mm title    times UTC | body: wire_read(ids=[...])
---

## cls zh early,finance 2/22
-- 08-31
f34c19515bb2 14:31 OpenAI广告业务上线约200天 年化营收规模突破10亿美元
59768dd933bc 14:02 HBM现货价格飙至长协五倍：HBM4良率承压，长协锁产挤压现货供给

## data_secrets ru telegram 2/4
-- 08-31
016ab99e8218 15:56 OpenAI закупает десятки тысяч Mac mini и Mac Studio для RL обучения агентов
6454c66ae77d 14:03 До отправки рабочего документа в нейросеть 3… 2… 1… клик

## hn en community,searchable 2/474
-- 08-31
a3b094e0252f 15:59 Brocards for Vulnerability Triage (vulnbrocards.com)
216464036f0f 15:59 Vigil 0.5.0: threat hunting agent where a deterministic controller owns state (vigilsoc.org)
```

*Eight hours of three sources at `limit_per_source=2`, verbatim, in 2.4 seconds
and 296 tokens. `CUT` says what was left out and how much there was — 500 items
in that window, six printed; `3/3` is how many answered.*

**What it costs**, measured rather than estimated: a full day of all
twenty-eight at the defaults is about **5,000 tokens**; six hours is about
**3,400**; six hours of three sources is **under 800**, and **under 200** at
`limit_per_source=2`.

## Status

v0.1 — the sources work; the tool API may still move. All twenty-eight have an
adapter and were verified against the live endpoints: eleven RSS feeds, Hacker
News through its search index, a signed Chinese financial API, six public
Telegram channels, the Hugging Face model hub plus six labs read from their own
namespaces on it, and two sections of a lab that publishes no feed, read out of
the data its own pages ship. 371 tests covering 95% of 1,198 statements, on
3.12 and 3.14.

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
| `wire_read` | Give me the text of these ids |
| `wire_search` | Who is carrying this term right now, and since when |
| `wire_sources` | What exists, and what is currently broken? |

All four are read-only and return plain text. The same information as JSON with
indent costs roughly six times the tokens and truncates.

**Fetching is per source, and what it costs is Telegram**: those six channels
go one at a time, three seconds apart, because `t.me` drops the sixth request in
a row. Everything else is fetched in parallel, so the eighteen English sources
together cost what two do — under two seconds. Bring the channels in and it is
twenty to forty-five, which is also what `sources=["ru"]` costs, since six of
the seven Russian sources are channels. Language is not the axis; channel count
is. The tool description carries the same table, so a model can ask before
spending it.

## Nothing is kept

There is no database file, no cache directory and no state between calls. Each
tool call builds a SQLite database in memory, fills it with one pass over the
sources you asked for, answers from it, and throws it away when the reply is
sent. SQLite is there for what it does inside that one call — the trigram index
that makes a Chinese query work at all, the count of how many sources carried
the same URL — not to keep anything.

This has a cost and it is worth stating: **`wire_search` cannot reach past what
the feeds are serving today.** For English that costs almost nothing. Hacker
News and Habr can be searched at their own origin, and the OpenAI blog hands
over its whole back catalogue to 2015 in a single fetch, Hugging Face to 2020.
For Chinese it is real and it is permanent — cls.cn holds 3.34 days and cannot
page backwards, and 36Kr's live stream is about two hours deep. Anything older
than that is gone, from here and from everywhere.

Every reply says so rather than leaving it to be discovered: the COVER block
gives the oldest item the fetch actually reached and states that the floor is a
property of the feeds, never of the subject.

`cablegram check` fetches every source once and prints what each one said, for
deciding whether the catalogue still works. It stores nothing either.

Nothing is uploaded anywhere and no database ships with this repository: the
server fetches on your behalf and does not redistribute anyone's content.

## Not built yet

Separate from what this deliberately never does — the design notes list that —
these are things it would reasonably do and does not:

* **No history.** Every call starts from nothing, so the same question asked
  twice costs two fetches, and a question about last month cannot be answered
  from here at all. That is the trade for leaving no file on your disk.
* **Sources are fixed in code.** Adding one means editing `sources.py`, and an
  adapter too if it is not RSS. That is the design rather than an oversight, but
  it does mean a fork rather than a config file.
* **Coverage is uneven and not controllable.** A feed either serves its back
  catalogue or it does not; there is no way to ask for more history than the
  endpoint volunteers, and one source reaching 2015 says nothing about the next.
* **Nothing checks a source's terms for you.** The endpoints are public and the
  requests are bounded and rate-limited, but the responsibility is yours.

## Design

[`docs/design.md`](https://github.com/levongabrielyan/cablegram-mcp/blob/main/docs/design.md)
covers why the identity of an item is a pure function of its URL, why a failure
is a value rather than an exception, why the full-text index needs a trigram
tokenizer for Chinese to work at all, and what this server deliberately does not
do.

## Notes

Only public endpoints are used: no credentials, no authentication bypass, no
scraping behind a login. Keeping nothing means every call is a full download,
which is heavier on somebody else's server than a poller with a cache behind it
— that is the price of not keeping a copy of their site, and it is why the tool
descriptions teach a model to ask before spending a full sweep. Intended for
personal research — respect each source's terms of service.

## Licence

MIT. Built by [Levon Gabrielyan](https://github.com/levongabrielyan).
<!-- mcp-name: io.github.levongabrielyan/cablegram-mcp -->
