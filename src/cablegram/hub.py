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
  like count travels with each entry so the reader can see what the ordering
  was made of.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .rss import Entry

__all__ = ["HUB", "MAX_ROWS", "models_url", "parse_models"]

HUB = "https://huggingface.co"
MAX_ROWS = 50


def models_url(*, author: str | None = None, rows: int = MAX_ROWS) -> str:
    """Trending models, or the ones a single organisation published.

    `author=` is how a lab is heard directly: deepseek-ai, Qwen, zai-org,
    moonshotai, MiniMaxAI, tencent all answer, none of them has a feed.
    """
    query = f"sort=likes7d&direction=-1&limit={min(rows, MAX_ROWS)}"
    return f"{HUB}/api/models?{query}" + (f"&author={author}" if author else "")


def _when(item: dict) -> datetime | None:
    for key in ("lastModified", "createdAt"):
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
        model_id = (item.get("id") or item.get("modelId") or "").strip()
        if not model_id:
            continue

        likes, downloads = item.get("likes") or 0, item.get("downloads") or 0
        pipeline = item.get("pipeline_tag") or ""
        # The counts are what the ordering was made of, so they travel with the
        # entry rather than being folded into a position in a list.
        detail = f"{likes} likes, {downloads} downloads in the last month"
        entries.append(Entry(
            title=model_id,
            url=f"{HUB}/{model_id}",
            published=_when(item),
            body=f"{pipeline}. {detail}" if pipeline else detail,
            body_src="hub",
        ))
    return entries
