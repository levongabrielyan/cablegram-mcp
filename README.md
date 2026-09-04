# cablegram-mcp

Twenty-nine sources on AI and tech — English, Chinese and Russian — written to
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
It runs every way: the Chinese press covers OpenAI and not a Russian Telegram
channel; the Russian press covers OpenAI and not 36Kr. No press covers the other
two blocs, and each of them is somebody else's other bloc.

Twenty-nine sources in three languages, read directly, put the reader in the
same week as all three at once — and the reader here has no home press to be
behind, because it is a model. It reads what it is handed, in whatever language
it arrives.

Headlines are never translated. Each dispatch carries its language, and the
model reading it has more context for that than any translation step would.
It also means the same story is kept in every language that carried it: OpenAI
titles a post *"Pacing model development in an era of cyber-critical
capabilities"* while a Russian channel titles the same URL *"OpenAI stopped RL
for two weeks on its latest models"*. Both are stored against the same id, and
either can be searched.

```
CABLEGRAM v0.2.2 | 2026-09-03T00:43:54Z..2026-09-03T08:43:54Z
CUT   cls=2/25  hn=2/241   (newest kept)
COLS  id hh:mm title    times UTC | body: wire_read(ids=[...])
---

## cls zh early,finance 2/25
-- 2026-09-03
6060520f9036 08:28 外滩大会下周上海开幕：首次聚焦AI新经济，观众报名人数已突破5万
f5fd40b8040a 08:02 财联社9月3日电，AI服务器制造商慧与科技（HPE）尽管财报强劲且业绩指引上调，但美股盘前跌近6%。该股今年已累涨近120%。

## data_secrets ru telegram 2/2
-- 2026-09-03
a616ad4d8e77 08:02 Продолжаем следить за главными конференциями по машинному обучению. 19 сентября команда Data Secrets поедет на Practical ML Conf, чтобы посмотреть всё вживую и рассказать вам о самом интересном. Что точно идем смотреть:
e2bf7cf25acf 06:13 Правительство США встало на сторону OpenAI в суде по иску об авторских правах NYT против OpenA

## hn en community 2/241
-- 2026-09-03
be0d90661a35 08:43 The Filesystem Explained [video] (youtube.com)
fcba7e8174fd 08:43 Ask HN: How much does licensing approach influence purchase decisions? (news.ycombinator.com)
```

*Eight hours of three sources at `limit_per_source=2`, verbatim, and
466 tokens. Three passes took 2.3s, 2.3s and 3.3s. Every line above
the rule is something the reader cannot work out for itself, and this
morning there was only one: `CUT`, saying cls held 25 dispatches in those
eight hours and hn 241, two of each printed. No `SILENT`, because all three
published; no `CEILING`, because none of them served less than the window
asked for. There is no tally, because every source asked for is either a
block or a name above.*

**What it costs.** A reply is priced by what the sources published, not by this
code, so these are ranges from repeated measurement rather than figures:

    24h, everything, defaults        ~5,900 tokens
    6h,  everything, defaults        2,500-3,400
    6h,  three busy sources          1,000-1,400
    6h,  three, limit_per_source=2   ~300

The whole-catalogue numbers hold because they average over twenty-nine feeds.
The narrow ones move by half again within an hour: two measurements of the same
call, sixty minutes apart, gave 3,324 and 2,515 with no change to the code —
Chinese wire services publish in bursts. Treat a narrow selection as costing
whatever it costs and read `CUT`, which says what was left out and how much
there was.

## Status

v0.2.2 — the sources work; the tool API may still move. All twenty-nine have an
adapter and were verified against the live endpoints: eleven RSS feeds, Hacker
News through its search index, a signed Chinese financial API, six public
Telegram channels, the Hugging Face model hub plus six labs read from their own
namespaces on it, and three sections of a lab that publishes no feed, read out
of the data its own pages ship. 459 tests at 96% coverage, on 3.12 and 3.14.

Three sources are worth knowing about before you rely on them:

* **cls.cn is reverse-engineered.** An undocumented internal API with a signed
  request. It holds 3.34 days at most and cannot page backwards, so a gap is
  permanent. `wire_sources` marks it `fragile`.
* **Anthropic has no feed at all.** Its three sections are read out of the data
  its own pages ship inline, which is an internal Next.js format with no
  contract. Also marked `fragile`. A shape change comes back as a broken
  source, never as a quiet week.
* **Telegram is HTML with no contract.** The public preview view can change
  without a version number to notice it by.

All three are declared in the output rather than explained afterwards.

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
a row. Everything else is fetched in parallel, so the nineteen English sources
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
that makes a Chinese query work at all, and the join that names every source
which carried the same URL — not to keep anything.

This has a cost and it is worth stating: **`wire_search` cannot reach past what
the feeds are serving today**, and the floor is lower than it sounds. Measured
as the oldest item each feed actually served — the same quantity `wire_search`
prints on its COVER line, one floor per source:

    openai         2015      the whole back catalogue, in one fetch
    anthropic      2021
    huggingface    2020
    producthunt    48 days
    cls.cn         3 days    at its ceiling, and it cannot page backwards, so
                             anything older is gone from everywhere
    36Kr           3 days
    qbitai         2 days
    Habr           1 day
    Hacker News    1 day     a thousand stories is the cap, so days=7 and
                             days=30 return the same rows

Three archives reach back years; everything else reaches back days, and no
parameter asks for more than the endpoint volunteers. That floor is a property
of the feeds and never of the subject.

Every reply carries its own rather than leaving it to be discovered: the COVER
line names the floor **per source**, which is what this table is made of. One
number for the whole call would be the deepest feed in it — and that feed may
have matched nothing, so a search that reached back one day would report eleven
years of coverage behind a miss.

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

## How this was built

Every reply this server sends is a claim about the world that nobody checks —
the reader is a model, and a model cannot tell a quiet day from a broken fetch.
So the work is mostly finding the places where it says something it does not
know, and the commit history is the record of that. A few of them, verbatim:

    Refuse a window that ends before it starts
    Stop the ENGINE line claiming an index ran over nothing
    Report no floor for a call that consulted no feed
    Declare the source ceiling where the window is stated, not only in the catalogue
    Date the window fixtures relative to now, so the suite stops expiring
    Serve one hit per story, not the post and the link it carried

Each one names the measurement that found it. `since=2027-01-01` produced a
window ending before it began and a line underneath reading `SILENT hn
(answered, published nothing in this window)`. `hours=10000000` wrote the year
885 without its leading zero, and because those timestamps are compared as
strings it excluded all 982 items and reported the same affirmative silence.
Seven tests went green to red overnight with no code change, and CI stayed green
only because it runs on push and nobody had pushed.

    git log --format='%s%n%n%b'

## Design

[`docs/design.md`](https://github.com/levongabrielyan/cablegram-mcp/blob/main/docs/design.md)
is the reasoning rather than the API: why the identity of an item is a pure
function of its URL and what breaks in each direction when that is wrong, why a
failure is a value rather than an exception, why the full-text index needs a
trigram tokenizer for Chinese to work at all, why third-party input is treated
as hostile, and what this server deliberately does not do. It carries a
*Marking / Without it* table for every mark a reply can print — what each one
says, and what a reader would conclude if it were absent.

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
