"""Parsing is tested against saved samples, never the live network.

A test that fetches a URL fails when a feed is down, when a CI runner has no
egress, and on a plane. It also stops being a test of the parser and becomes a
test of somebody else's uptime.
"""

import pytest

from cablegram.rss import parse_feed

RSS2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>TestingCatalog</title>
    <item>
      <title>Perplexity is testing a new Spaces sidebar</title>
      <link>https://www.testingcatalog.com/perplexity-spaces/</link>
      <pubDate>Sat, 30 Aug 2026 06:40:00 +0000</pubDate>
      <description>&lt;p&gt;A &lt;b&gt;pinned&lt;/b&gt; threads panel.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Second story</title>
      <link>https://www.testingcatalog.com/second/</link>
      <pubDate>Fri, 29 Aug 2026 22:03:11 GMT</pubDate>
      <content:encoded>&lt;p&gt;Full body here&lt;/p&gt;</content:encoded>
    </item>
  </channel>
</rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Hugging Face</title>
  <entry>
    <title>The Open ASR Leaderboard</title>
    <link rel="alternate" href="https://huggingface.co/blog/asr"/>
    <published>2026-08-30T07:12:00Z</published>
    <summary>Benchmarks for speech models.</summary>
  </entry>
</feed>"""

CJK = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>\xe6\x99\xba\xe8\xb0\xb1\xe5\x8f\x91\xe5\xb8\x83GLM-5\xef\xbc\x8c\xe4\xb8\x8a\xe4\xb8\x8b\xe6\x96\x87\xe6\x89\xa9\xe5\xb1\x95</title>
    <link>https://www.qbitai.com/2026/08/glm5.html</link>
    <pubDate>Sat, 30 Aug 2026 07:12:00 +0800</pubDate>
  </item>
  <item>
    <title>\xd0\x92\xd1\x8b\xd1\x88\xd0\xbb\xd0\xb0 Qwen3-Max</title>
    <link>https://habr.com/ru/post/1/</link>
    <pubDate>Sat, 30 Aug 2026 05:02:00 +0300</pubDate>
  </item>
</channel></rss>"""


def test_rss2():
    entries = parse_feed(RSS2)
    assert len(entries) == 2
    first = entries[0]
    assert first.title == "Perplexity is testing a new Spaces sidebar"
    assert first.url == "https://www.testingcatalog.com/perplexity-spaces/"
    assert first.published.isoformat() == "2026-08-30T06:40:00+00:00"


def test_atom_link_lives_in_an_attribute():
    """RSS puts the URL in the element text, Atom in an href. Both must work."""
    entries = parse_feed(ATOM)
    assert entries[0].url == "https://huggingface.co/blog/asr"
    assert entries[0].published.isoformat() == "2026-08-30T07:12:00+00:00"


def test_html_is_stripped_from_bodies():
    assert parse_feed(RSS2)[0].body == "A pinned threads panel."


def test_content_encoded_beats_description():
    assert parse_feed(RSS2)[1].body == "Full body here"


def test_timezones_are_normalised_to_utc():
    """+0800 and +0300 must land on the same clock, or days get grouped wrong."""
    zh, ru = parse_feed(CJK)
    assert zh.published.isoformat() == "2026-08-29T23:12:00+00:00"
    assert ru.published.isoformat() == "2026-08-30T02:02:00+00:00"


def test_cjk_and_cyrillic_survive_intact():
    zh, ru = parse_feed(CJK)
    assert zh.title.startswith("智谱发布GLM-5")
    assert ru.title == "Вышла Qwen3-Max"


def test_titles_are_nfc_normalised():
    """Two byte sequences for the same character would archive as two items."""
    decomposed = "Вышла й".encode()  # и + combining breve
    feed = b"""<rss version="2.0"><channel><item>
        <title>""" + decomposed + b"""</title>
        <link>https://e.com/a</link></item></channel></rss>"""
    assert parse_feed(feed)[0].title == "Вышла й"


@pytest.mark.parametrize("raw_date", [b"", b"not a date", b"32 Foo 2026"])
def test_unparseable_date_becomes_none_not_now(raw_date):
    """A guessed timestamp files an item under the wrong day and nobody notices.

    None is honest: the reader marks it and knows the time is the capture time.
    """
    feed = b"""<rss version="2.0"><channel><item>
        <title>T</title><link>https://e.com/a</link>
        <pubDate>""" + raw_date + b"""</pubDate></item></channel></rss>"""
    assert parse_feed(feed)[0].published is None


def test_one_broken_item_does_not_lose_the_others():
    """A feed of forty with one bad entry must still yield thirty-nine."""
    feed = b"""<rss version="2.0"><channel>
        <item><title>No link here</title></item>
        <item><link>https://e.com/no-title</link></item>
        <item><title>Good</title><link>https://e.com/good</link></item>
    </channel></rss>"""
    entries = parse_feed(feed)
    assert len(entries) == 1
    assert entries[0].title == "Good"


def test_empty_feed_is_not_an_error():
    assert parse_feed(b'<rss version="2.0"><channel/></rss>') == []


def test_malformed_xml_raises():
    """Broken XML is a source failure, not an empty day. It must be visible."""
    import xml.etree.ElementTree as ET

    with pytest.raises(ET.ParseError):
        parse_feed(b"<rss><channel><item>")


# ── Silent failures found by an external reviewer, 2026-08-30 ────────────────

RSS1_RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel rdf:about="https://example.com/"><title>Old school feed</title></channel>
  <item rdf:about="https://example.com/a">
    <title>Published through RSS 1.0</title>
    <link>https://example.com/a</link>
    <dc:date>2026-08-30T06:40:00Z</dc:date>
    <description>Body text</description>
  </item>
</rdf:RDF>"""


def test_rss1_rdf_is_not_silently_empty():
    """RSS 1.0 puts <item> in a namespace, so a plain .//item search misses it.

    The parser returned [] with no error: a feed could switch format and the
    source would go mute for months with nobody noticing.
    """
    entries = parse_feed(RSS1_RDF)
    assert len(entries) == 1
    assert entries[0].title == "Published through RSS 1.0"
    assert entries[0].url == "https://example.com/a"
    assert entries[0].published is not None


def test_html_entities_are_decoded():
    """&nbsp; and friends survive tag stripping and reach the reader as literals."""
    feed = b"""<rss version="2.0"><channel><item>
        <title>GPT&amp;nbsp;5 &amp;mdash; released</title>
        <link>https://e.com/a</link>
        <description>&lt;p&gt;Cost&amp;nbsp;&amp;euro;5&lt;/p&gt;</description>
    </item></channel></rss>"""
    entry = parse_feed(feed)[0]
    assert "&nbsp;" not in entry.title and "&mdash;" not in entry.title
    assert "&nbsp;" not in entry.body and "&euro;" not in entry.body


def test_entity_expansion_cannot_blow_up_memory():
    """A feed is remote input. Nested entities expand geometrically ("billion laughs").

    A few hundred bytes can become gigabytes and take the server down. Feeds are
    fetched from third parties, so this is reachable by anyone who controls one.
    """
    bomb = b"""<?xml version="1.0"?>
    <!DOCTYPE rss [
      <!ENTITY a "aaaaaaaaaa">
      <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
      <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
      <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
    ]>
    <rss version="2.0"><channel><item>
      <title>&d;</title><link>https://e.com/a</link>
    </item></channel></rss>"""
    with pytest.raises(ValueError, match="entit"):
        parse_feed(bomb)


def test_normal_feeds_are_unaffected_by_the_entity_guard():
    """The guard must not reject the eleven real feeds. Standard entities
    (&amp; &lt; &quot;) are built into XML and need no declaration."""
    feed = b"""<rss version="2.0"><channel><item>
        <title>Tom &amp; Jerry &lt;live&gt; &quot;quoted&quot;</title>
        <link>https://e.com/a</link></item></channel></rss>"""
    assert parse_feed(feed)[0].title == 'Tom & Jerry <live> "quoted"' 


# ── Fixes that broke other things, found on second review, 2026-08-30 ────────

@pytest.mark.parametrize("entity", ["copy", "times", "reg", "not", "para", "sect"])
def test_unescaping_must_not_corrupt_links(entity):
    """ElementTree already unescapes once. A second pass expands HTML5 legacy
    entities that need no semicolon, so &amp;copy= inside a URL becomes ©.

    A corrupted link is a broken URL in the archive and a wrong id — in the part
    the module declares frozen.
    """
    feed = (f'<rss version="2.0"><channel><item><title>T</title>'
            f'<link>https://ex.com/a?x=1&amp;{entity}=2</link>'
            f'</item></channel></rss>').encode()
    assert parse_feed(feed)[0].url == f"https://ex.com/a?x=1&{entity}=2"


def test_entity_guard_cannot_be_bypassed_with_padding():
    """Scanning a fixed window of bytes is defeated by a long enough prologue."""
    bomb = (b'<?xml version="1.0"?>\n<!-- ' + b'x' * 8300 + b' -->\n'
            b'<!DOCTYPE rss [<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            b'<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]>'
            b'<rss version="2.0"><channel><item><title>&d;</title>'
            b'<link>https://e.com/a</link></item></channel></rss>')
    with pytest.raises(ValueError, match="entit"):
        parse_feed(bomb)


def test_the_word_entity_in_an_article_does_not_kill_the_feed():
    """Old WordPress feeds declare <!ENTITY nbsp "&#160;">, and articles talk
    about XML. Refusing on either would take a whole source down for a day."""
    feed = ('<rss version="2.0"><channel><item>'
            '<title>How an XML &lt;!ENTITY&gt; bomb works</title>'
            '<link>https://e.com/a</link></item></channel></rss>').encode()
    assert parse_feed(feed)[0].title.startswith("How an XML")


# ── how much body arrived, which only the parser can know ────────────────────

def test_the_element_is_recorded_verbatim():
    """A fact, not a verdict. A feed is free to put the whole article in
    <description> and two sentences in <atom:content>, and both happen — so
    "full" and "teaser" were guesses dressed as data."""
    assert parse_feed(RSS2)[1].body_src == "content:encoded"


def test_description_is_named_description():
    assert parse_feed(RSS2)[0].body_src == "description"


def test_atom_summary_keeps_its_own_name():
    assert parse_feed(ATOM)[0].body_src == "atom:summary"


def test_no_body_no_kind():
    feed = b"""<rss version="2.0"><channel><item>
        <title>T</title><link>https://e.com/a</link></item></channel></rss>"""
    entry = parse_feed(feed)[0]
    assert entry.body is None and entry.body_src is None


def test_an_empty_description_does_not_count_as_a_body():
    """A description of '<p> </p>' strips to nothing. Calling that a teaser
    tells the reader there is something to read when there is not."""
    feed = b"""<rss version="2.0"><channel><item>
        <title>T</title><link>https://e.com/a</link>
        <description>&lt;p&gt; &lt;/p&gt;</description></item></channel></rss>"""
    entry = parse_feed(feed)[0]
    assert entry.body is None and entry.body_src is None


# ── third review: the byte-scanning guard was still bypassable ───────────────

def test_a_comment_cannot_hide_the_real_doctype():
    """Scanning bytes for '<!DOCTYPE', then '[', then ']' latches onto a comment
    that contains all three, inspects an empty subset and waves the bomb through.
    326 bytes became a 10,000-character title."""
    bomb = (b'<?xml version="1.0"?>\n<!-- <!DOCTYPE fake [ ] -->\n'
            b'<!DOCTYPE rss [<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            b'<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]>'
            b'<rss version="2.0"><channel><item><title>&d;</title>'
            b'<link>https://e.com/a</link></item></channel></rss>')
    with pytest.raises(ValueError, match="entit"):
        parse_feed(bomb)


def test_a_wordpress_feed_declaring_nbsp_still_parses():
    """The other half of the same problem: refusing every internal subset takes
    down real feeds. A character reference expands once and is not a bomb."""
    feed = (b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE rss [<!ENTITY nbsp "&#160;"><!ENTITY mdash "&#8212;">]>\n'
            b'<rss version="2.0"><channel><item><title>GPT&nbsp;5&mdash;out</title>'
            b'<link>https://e.com/a</link></item></channel></rss>')
    assert parse_feed(feed)[0].title.startswith("GPT")


def test_an_external_entity_is_refused():
    """No feed of ours declares one, and a feed that does is asking the parser to
    fetch something on its behalf."""
    feed = (b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
            b'<rss version="2.0"><channel><item><title>T</title>'
            b'<link>https://e.com/a</link></item></channel></rss>')
    with pytest.raises(ValueError, match="entit"):
        parse_feed(feed)


# ── fourth review: three guards, three shapes, one property never checked ────

def _nested_bomb():
    return (b'<?xml version="1.0"?>\n<!DOCTYPE rss [<!ENTITY a "aaaaaaaaaa">'
            b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            b'<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]>'
            b'<rss version="2.0"><channel><item><title>&d;</title>'
            b'<link>https://e.com/a</link></item></channel></rss>')


def _flat_bomb():
    """No nesting at all. One entity, repeated — the quadratic blowup.

    Every guard so far looked for an entity naming another entity, so this
    walked straight through all three of them. expat's own limit does not stop
    it either: 100 KB x 700 is an amplification of 675 measured against the
    entity, but well inside the factor it permits for a 100 KB input.
    """
    return (b'<?xml version="1.0"?>\n<!DOCTYPE rss [<!ENTITY big "' + b"a" * 100_000 +
            b'">]>\n<rss version="2.0"><channel><item><title>' + b"&big;" * 700 +
            b'</title><link>https://e.com/a</link></item></channel></rss>')


def _padded_bomb():
    return (b'<?xml version="1.0"?>\n<!-- ' + b"x" * 8300 + b' -->\n' + _nested_bomb()[22:])


def _commented_bomb():
    return (b'<?xml version="1.0"?>\n<!-- <!DOCTYPE fake [ ] -->\n' + _nested_bomb()[22:])


@pytest.mark.parametrize("make_bomb", [_nested_bomb, _flat_bomb, _padded_bomb, _commented_bomb],
                         ids=["nested", "flat", "padded", "commented"])
def test_no_feed_expands_far_beyond_its_own_size(make_bomb):
    """The property, stated once, instead of a test per shape of attack.

    Three previous guards each closed the shape they were shown and left the
    property open — which is why the flat one survived all three with the tests
    green every time. What matters is not how a feed nests its declarations; it
    is that a 100 KB document cannot become 70 MB of title.
    """
    with pytest.raises(ValueError, match="expan|entit"):
        parse_feed(make_bomb())


def test_a_single_field_cannot_be_unbounded():
    """Second line of defence, and the one that reaches the database.

    Even an expansion small enough to pass the parser must not put a megabyte
    into item.title, sighting.title and the trigram index. This also covers a
    source that simply writes very long fields: cls.cn puts the whole dispatch
    in article_title, up to 617 characters.
    """
    from cablegram.rss import MAX_FIELD

    feed = (b'<rss version="2.0"><channel><item><title>' + b"x" * (MAX_FIELD * 3) +
            b'</title><link>https://e.com/a</link>'
            b'<description>' + b"y" * (MAX_FIELD * 3) + b'</description>'
            b'</item></channel></rss>')
    entry = parse_feed(feed)[0]
    assert len(entry.title) <= MAX_FIELD
    assert len(entry.body) <= MAX_FIELD


def test_a_normal_feed_is_untouched_by_the_cap():
    entry = parse_feed(RSS2)[0]
    assert entry.title == "Perplexity is testing a new Spaces sidebar"


def test_the_guard_does_not_depend_on_the_expat_build():
    """SetAllocTrackerMaximumAmplification exists in expat 2.8 and not in what
    Debian 13 ships. Relying on it meant the parser raised AttributeError on the
    first feed of every poll — on most machines in the world — while 329 tests
    passed on the one where it was written. Caught by running the repo on a
    clean machine, not by any test here.

    The expansion is measured directly instead: the size of a declared entity
    times how many times the document references it. That works on every build
    and is a tighter bound than the amplification ratio.
    """
    import xml.parsers.expat as expat

    real_create = expat.ParserCreate

    class Older:
        """An expat without the tracker, which is what Debian ships."""

        def __init__(self):
            self._parser = real_create()

        def __getattr__(self, name):
            if "AllocTracker" in name:
                raise AttributeError(name)
            return getattr(self._parser, name)

        def __setattr__(self, name, value):
            if name == "_parser":
                object.__setattr__(self, name, value)
            else:
                setattr(self._parser, name, value)

    expat.ParserCreate = Older
    try:
        assert parse_feed(RSS2), "must parse on a build without the tracker"
        with pytest.raises(ValueError, match="expan|entit"):
            parse_feed(_flat_bomb())
    finally:
        expat.ParserCreate = real_create


def test_the_flat_bomb_is_still_refused_without_the_tracker():
    """The measurement has to be as strong as what it replaces."""
    flat = (b'<?xml version="1.0"?>\n<!DOCTYPE rss [<!ENTITY big "' + b"a" * 100_000 +
            b'">]>\n<rss version="2.0"><channel><item><title>' + b"&big;" * 700 +
            b'</title><link>https://e.com/a</link></item></channel></rss>')
    with pytest.raises(ValueError, match="expan|entit"):
        parse_feed(flat)


ATOM_UPDATED_ONLY = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>Edited today, written in 2024</title>
    <link href="https://e.com/old-post"/>
    <updated>2026-09-03T08:00:00Z</updated></entry>
  <entry><title>Published, properly</title>
    <link href="https://e.com/new-post"/>
    <published>2026-09-03T07:00:00Z</published>
    <updated>2026-09-03T08:30:00Z</updated></entry>
</feed>"""


def test_an_entry_dated_only_by_updated_is_not_an_exact_publication_date():
    """<updated> is when the record last changed. Taken as the publication
    date it filed a 2024 post edited today as published today, exact, at the
    top of the window. The date is kept — it is the best the feed offers — and
    marked inexact, which is what the `~` on the line is for."""
    by_url = {e.url: e for e in parse_feed(ATOM_UPDATED_ONLY)}
    edited = by_url["https://e.com/old-post"]
    assert edited.published.isoformat().startswith("2026-09-03T08:00")
    assert edited.date_exact is False, "an edit time is not a publication time"
    proper = by_url["https://e.com/new-post"]
    assert proper.published.isoformat().startswith("2026-09-03T07:00"), "published wins"
    assert proper.date_exact is True
