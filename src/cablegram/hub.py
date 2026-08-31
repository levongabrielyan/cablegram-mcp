"""The Hugging Face model hub — where open weights actually land.

The Hugging Face *blog* was already in the catalogue and it is not the same
thing: the blog is what somebody wrote about, the hub is the file appearing.
DeepSeek published `DeepSeek-V4-Flash-Vision-Exp` on a Tuesday and not one of
the nineteen sources saw it; `Qwen3.8-27B` had 13,412 likes and zero mentions.
This closes that, and it closes a second hole at the same time — no Chinese lab
publishes a feed, and every one of them publishes weights, so this is the only
place they speak for themselves rather than through somebody else's coverage.

One property shapes the module, and it is a deviation worth stating plainly:

* **The ordering is by trend, not by date, because date does not work.** Both
  `sort=createdAt` and `sort=lastModified` return the firehose of every repo
  anyone touched — `ElMusk/fun08`, `sergiopaniego/watercolour-grpo-v22b`,
  measured — so a date-ordered page from this endpoint carries no signal at
  all. `likes7d` is the only ordering that returns the list a person would
  recognise. This server ranks nothing itself, and here it accepts somebody
  else's ranking to get usable data. The `note` on the source says so, and the
  figure that ordering was made of travels with each entry.

  That figure is `trendingScore`, and it is not `likes`. `sort=likes7d` names a
  sort, not the field it sorts on: measured over the live top fifty,
  trendingScore is monotone in 50 rows of 50 and likes is not monotone at all —
  position 3 carries 13,421 likes above position 0's 4,473. Sending likes as
  "what the ordering was made of" printed a bigger number three lines below a
  smaller one, leaving one of two conclusions available, both false: that the
  list is sorted wrong, or that the count means nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .rss import Entry

__all__ = ["HUB", "MAX_ROWS", "models_url", "parse_models", "rows_returned"]

HUB = "https://huggingface.co"
MAX_ROWS = 50

# The tag a repo writes to say whose weights it requantised.
QUANTISED_OF = "base_model:quantized:"


def models_url(*, author: str | None = None, rows: int = MAX_ROWS) -> str:
    """Trending models, or the ones a single organisation published.

    `author=` is how a lab is heard directly: deepseek-ai, Qwen, zai-org,
    moonshotai, MiniMaxAI, tencent all answer, none of them has a feed.
    """
    query = f"sort=likes7d&direction=-1&limit={min(rows, MAX_ROWS)}"
    return f"{HUB}/api/models?{query}" + (f"&author={author}" if author else "")


def _when(item: dict) -> datetime | None:
    """When the repo was made, preferred over when it was last touched.

    The order used to be the other way round. It changed nothing in practice —
    measured, this endpoint returns lastModified in 0 of 50 rows unless it is
    asked for — which is exactly what made it dangerous: the day Hugging Face
    adds the field to the default listing, or somebody adds `full=true` to get
    something else, every old model edited today enters a 24h window as if it
    were new, and nothing in the code moves.

    A model release is a repo being created. Anyone adding a README to a model
    from March is not publishing it again.
    """
    for key in ("createdAt", "lastModified"):
        raw = item.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                timezone.utc)
        except ValueError:
            continue
    # No date rather than today's: this endpoint is not ordered by time, so an
    # invented timestamp would put the model in whatever window was asked for.
    return None


def rows_returned(payload: list) -> int:
    """How many rows the endpoint sent, which is not how many become entries.

    The ceiling means "it gave all it could, there may be more", so it has to be
    measured on what arrived rather than on what survived filtering: with the
    private and requantisation filters below, a full page of 50 yields about 33
    entries and would never reach a ceiling of 50.
    """
    return len(payload) if isinstance(payload, list) else 0


def _requantised_by_a_third_party(item: dict) -> bool:
    """A repo declaring, in its own published metadata, that it is somebody
    else's weights requantised.

    Not an editorial call and not a quality judgement: the tag
    `base_model:quantized:<org>/<model>` is written by the repo itself, and the
    only thing read off it is whether that organisation is the one publishing
    this. Qwen shipping an FP8 of its own model is Qwen publishing; sixteen
    GGUF and "uncensored" rebuilds of Qwen3.8-27B by other people are the same
    release arriving seventeen times.

    Measured over the live top fifty: 17 rows declare a quantised base, 16 of
    them from a different organisation, and one — Qwen/Qwen3.8-Flash-Next-FP8 —
    from the same one. Filtering on `base_model:` alone would have taken 13
    finetunes with it, including ibm-granite/granite-4.2-30b and
    tencent/WeMM-Embedding-9B, which are lab releases.
    """
    owner = (item.get("id") or item.get("modelId") or "").split("/")[0]
    for tag in item.get("tags") or ():
        if tag.startswith(QUANTISED_OF):
            base = tag[len(QUANTISED_OF):]
            return "/" in base and base.split("/")[0] != owner
    return False


def parse_models(payload: list) -> list[Entry]:
    """Turn one hub listing into entries.

    A list is the success shape. Anything else means the schema moved, and it
    must not read as an empty hub — the endpoint answers 200 either way.
    """
    if not isinstance(payload, list):
        raise ValueError("Hugging Face hub returned no list of models; the schema changed")

    entries: list[Entry] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("private"):
            continue
        if _requantised_by_a_third_party(item):
            continue
        model_id = (item.get("id") or item.get("modelId") or "").strip()
        if not model_id:
            continue

        likes, downloads = item.get("likes") or 0, item.get("downloads") or 0
        trend = item.get("trendingScore")
        pipeline = item.get("pipeline_tag") or ""
        # What the ordering was made of, travelling with the entry rather than
        # being folded into a position in a list. Each count is labelled with
        # its own period: `downloads` is the last 30 days and `likes` is every
        # like the repo ever got, and one sentence carrying both under "in the
        # last month" read as though the period applied to the pair.
        ordering = f"trend {trend}" if trend is not None else "trend not in this response"
        detail = (f"{ordering} (the ordering), {likes} likes all-time, "
                  f"{downloads} downloads in 30d")
        entries.append(Entry(
            title=model_id,
            url=f"{HUB}/{model_id}",
            published=_when(item),
            body=f"{pipeline}. {detail}" if pipeline else detail,
            body_src="hub",
        ))
    return entries
