# Design notes

Why this server is shaped the way it is. The code says what it does; this says
what it refused to do, and what went wrong on the way.

## The one assumption everything follows from

**A model reads this output. A person does not.**

That single fact decides most of what follows. A human operator notices when a
tool returns an empty list on a busy day, or when a summary reads oddly. Nobody
is watching here, so a wrong answer that looks well-formed is never caught. The
design is therefore biased, everywhere, towards making a failure *visible in the
payload itself* rather than towards making the payload tidy.

Concretely, the output states:

| Marking | Without it |
| --- | --- |
| `DOWN cls=HTTP403` | An absent source cannot be known to exist; its silence reads as "nothing happened there" |
| `CUT hn=25/57` | An undeclared cut is indistinguishable from a source with little to say |
| `~a3f9c2e1` | A capture time presented as a publication time files the item under the wrong day |
| `PENDING` | A source with no adapter reported as broken buries the one that is actually broken |
| `body=description 240c` | Reporting *how much* of an article arrived, without guessing whether it is all of it |
| `"0 hits" does NOT mean nobody is talking about it` | The single most likely false conclusion this server can cause |
| `SILENT openai` | A healthy source that published nothing stops appearing, and a source missing from a list cannot be known to have been asked |
| `CEILING hn` | A source that served everything it holds leaves the window in the header wider than the answer beneath it: two days asked for, about thirty hours answered, and nothing else distinguishes them |
| `COVER hn=2026-08-31` | Coverage is per source and wildly uneven. One date for the whole call is the deepest feed in it, which may have matched nothing — eleven years of apparent reach behind a miss |
| `UNKNOWN SELECTOR qbitia` | A mistyped selector returns zero exactly like a real absence, and nothing was fetched to produce it |
| `BUDGET max_tokens=3000` | Trimming to fit is a different answer from the one asked for, and every per-source total stays true only if the trim is declared |
| `DEFERRED a3f9c2e1 b7d2…` | A body that did not fit, dropped silently, is indistinguishable from an item that has none |

## Date-filtered, never ranked

The server brings dispatches; the model is the editor. Ranking would mean
deciding what matters, with far less context than the model has — and a ranked
list cannot be un-ranked by the reader, while an unranked one can be sorted.

Depth is a different matter: `detail="headlines"` trims how much of each item is
returned, uniformly and on request. Trimming depth is not selecting.

## Identity

`id = sha1(normalise(url))[:12]`, a pure function of the URL: no clock, no
network, no state. Two calls hours apart agree without the server remembering
anything.

Normalisation errs, everywhere, towards keeping URLs apart:

* **Merging two articles loses one.** `url_norm` is UNIQUE, so the second one
  never reaches the reply and nothing reports it.
* **Splitting one article is a duplicate** — recoverable, though not harmless:
  each split hides that two sources carried the same story, which is the thing
  `sighting` exists to record.

Both are bad; only one cannot be undone. Hence a *denylist* of tracking
parameters rather than an allowlist: an allowlist drops any key it has not heard
of, so `?sid=1` and `?sid=2` became the same id and the second article was
rejected in silence.

Twelve hex digits, not eight. At eight, a 50% chance of collision arrives at
77,000 items — a single sweep of Hacker News is 1,000 — and the id is a PRIMARY
KEY, so a collision is a real article silently refused.

Ids also have to survive the reply. A model is handed ids by `wire_latest` and
passes them back to `wire_read`, so the function turning a URL into an id is
frozen: it takes no clock, no counter and no state, which is why the same URL
gives the same id in the next call and on another machine.

## Nothing is kept

There is no file. Each call builds a SQLite database in memory, fills it with
one pass over the sources asked for, answers, and discards it. What SQLite is
doing there is work inside a single call, not storage — and the three things it
does are the reason it is worth having at all.

`sighting` is a separate table because `item` can only name whichever source got
there first. Without it every story looks like one nobody else picked up,
rather than like a missing feature. Each
sighting keeps the headline that source used: one outlet writes 智谱 where
another writes Zhipu for the same link, and that pairing is the only bridge
between a Chinese story and an English query.

Full-text search uses `tokenize='trigram'`. With SQLite's default tokenizer
every Chinese query returns zero hits, silently — Chinese has no spaces, so a
whole headline becomes one token. Terms shorter than three characters cannot use
a trigram index at all, and the common Chinese company names are exactly two, so
those fall back to a substring scan. Their recall differs; the reply used to
name the engine and no longer does, because a line about the server is one
more line able to contradict the rest, and it did. The tool description says
what a query is instead.

## Failure handling

* **A failure is a value, not an exception.** One source failing must not cost
  the other eighteen, and the caller receives one result per source in the order
  given — a source never disappears from the list because it failed.
* **The deadline cancels only what is still in flight.** Cancelling the batch
  turned one slow feed into eleven dead ones, which nothing downstream could
  tell apart from a real outage.
* **No conditional requests, and a 304 is a failure.** That is the opposite of
  what it means with a cache behind it, and the reason is that there is none: a
  304 carries no body, so a caller holding nothing gets a source with no items
  and reports it as having published nothing. No validator is ever sent, so a
  304 can only arrive as a protocol violation, and it is named as one.
* **A failure never touches `last_ok`.** It is how anyone notices a source has
  been mute, and overwriting it hides exactly the thing worth seeing.
* **One entry, one transaction.** A single unparseable entry used to roll back
  everything already written for that source.
* **A feed that parses to zero entries has its own state.** That is what a feed
  looks like the day it changes format, and as a plain success it is
  indistinguishable from a source with no news.

## Third-party input is hostile

Feeds are fetched from servers nobody here controls.

Entity expansion is bounded by measurement, not by inspecting declarations.
Three earlier guards each looked for the *shape* of the last attack — nested
declarations, a padded prologue, a comment hiding the DOCTYPE — and each left
the property open; a flat bomb (one entity, no nesting, referenced seven hundred
times) walked through all three. Fields are capped as a second line of defence,
the one that reaches the database and the index.

Responses are capped on bytes received rather than on `Content-Length`, which
can lie.

## Zero dependencies beyond the MCP SDK

Twenty-nine fixed sources and eleven RSS feeds do not justify a parser
dependency:
a dependency earns its place when it encapsulates knowledge that shifts or that
fails silently. The SDK ships an HTTP client, and the standard library parses
XML and RFC-822 dates.

## What this server does not do

* It does not translate. Each dispatch carries its language, and the model
  reading it has more context for that than any translation step would.
* It does not rank, score or deduplicate across sources beyond counting.
* It does not search the web. `wire_search` reads only what the sources are
  serving at the moment of the call, which for some of them is a few days.
* It does not fetch article bodies. What a feed ships is what the reply carries.
* Sources are fixed, not configurable. A configurable aggregator is a different
  product; this one is tuned for one question.
